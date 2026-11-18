"""Bounded weekly/monthly collections referencing shared review tasks."""
import os

from alembic import op
from sqlalchemy import text

revision = '0017_shared_review_plans'
down_revision = '0016_learning_reports'

CAPABILITIES = (
    'generate_review_plan(uuid,text,date,integer,integer,integer,uuid,uuid)',
    'read_review_plan(uuid)',
)
HELPERS = ('lock_plan_student(uuid)', 'review_plan_view(uuid)')


def upgrade():
    bind = op.get_bind()
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Review plan migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    CREATE TABLE public.review_plans (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        type text NOT NULL CHECK(type IN ('weekly','monthly')), period_start date NOT NULL,
        period_end date NOT NULL, timezone text NOT NULL DEFAULT 'Asia/Shanghai' CHECK(timezone='Asia/Shanghai'),
        max_questions integer NOT NULL CHECK(max_questions BETWEEN 1 AND 100),
        max_minutes integer NOT NULL CHECK(max_minutes BETWEEN 1 AND 120),
        minutes_per_question integer NOT NULL CHECK(minutes_per_question BETWEEN 1 AND 30),
        report_id uuid, operation_key uuid NOT NULL,
        rules_version integer NOT NULL DEFAULT 1 CHECK(rules_version=1),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,student_id,id), UNIQUE(student_id,deletion_epoch,operation_key),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id),
        FOREIGN KEY(tenant_id,student_id,report_id) REFERENCES public.reports(tenant_id,student_id,id)
    );
    CREATE INDEX review_plans_student_history ON public.review_plans(student_id,created_at,id);
    CREATE TABLE public.review_plan_entries (
        tenant_id uuid NOT NULL, student_id uuid NOT NULL, plan_id uuid NOT NULL,
        task_id uuid NOT NULL, position integer NOT NULL CHECK(position BETWEEN 1 AND 100),
        effective_revision bigint NOT NULL, schedule_revision integer NOT NULL, due_date date NOT NULL,
        priority text NOT NULL CHECK(priority IN ('overdue','repeated_error','remaining')),
        confirmed_incorrect_attempts integer NOT NULL CHECK(confirmed_incorrect_attempts>=0),
        PRIMARY KEY(plan_id,task_id), UNIQUE(plan_id,position),
        FOREIGN KEY(tenant_id,student_id,plan_id) REFERENCES public.review_plans(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,task_id) REFERENCES public.review_tasks(tenant_id,student_id,id)
    );
    ALTER TABLE public.review_plans ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.review_plans FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.review_plan_entries ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.review_plan_entries FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.review_plans,public.review_plan_entries FROM PUBLIC;

    CREATE FUNCTION public.lock_plan_student(target uuid) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE tenant uuid; epoch bigint;
    BEGIN
        IF NULLIF(current_setting('app.user_id',true),'') IS NOT NULL THEN
            RETURN public.lock_report_student(target); END IF;
        tenant=NULLIF(current_setting('app.tenant_id',true),'')::uuid;
        IF tenant IS NULL OR target IS DISTINCT FROM NULLIF(current_setting('app.student_id',true),'')::uuid THEN
            RETURN NULL; END IF;
        PERFORM 1 FROM public.tenants WHERE id=tenant AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT deletion_epoch INTO epoch FROM public.students WHERE id=target AND tenant_id=tenant
            AND status='active' AND consent_active FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        RETURN epoch;
    END $$;

    CREATE FUNCTION public.review_plan_view(target uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p public.review_plans%ROWTYPE; entries jsonb; is_stale boolean; r public.reports%ROWTYPE;
    BEGIN
        SELECT * INTO p FROM public.review_plans WHERE id=target;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('task_id',e.task_id,'position',e.position,
            'due_date',e.due_date,'priority',e.priority,'confirmed_incorrect_attempts',e.confirmed_incorrect_attempts,
            'task',CASE WHEN v.body ? 'error' THEN NULL ELSE v.body END) ORDER BY e.position),'[]'::jsonb),
            COALESCE(bool_or(t.effective_revision<>q.effective_revision OR t.schedule_revision<>e.schedule_revision
                OR v.body IS NULL OR v.body ? 'error'),false)
        INTO entries,is_stale FROM public.review_plan_entries e JOIN public.review_tasks t ON t.id=e.task_id
            JOIN public.questions q ON q.id=t.question_id
            CROSS JOIN LATERAL (SELECT public.read_review_task(t.id) AS body) v WHERE e.plan_id=target;
        IF p.report_id IS NOT NULL THEN
            SELECT * INTO r FROM public.reports WHERE id=p.report_id;
            is_stale=is_stale OR r.source_version<>encode(sha256(convert_to(public.learning_report_evidence(
                r.student_id,r.period_start::timestamp AT TIME ZONE r.timezone,r.period_end)::text,'UTF8')),'hex');
        END IF;
        RETURN jsonb_build_object('id',p.id,'student_id',p.student_id,'type',p.type,'period_start',p.period_start,
            'period_end',p.period_end,'timezone',p.timezone,'report_id',p.report_id,'rules_version',p.rules_version,
            'limits',jsonb_build_object('max_questions',p.max_questions,'max_minutes',p.max_minutes,
                'minutes_per_question',p.minutes_per_question),
            'question_count',jsonb_array_length(entries),'estimated_minutes',jsonb_array_length(entries)*p.minutes_per_question,
            'duration_basis','planning_estimate','entries',entries,'stale',is_stale,'created_at',p.created_at);
    END $$;

    CREATE FUNCTION public.read_review_plan(target uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p public.review_plans%ROWTYPE; epoch bigint;
    BEGIN
        SELECT * INTO p FROM public.review_plans WHERE id=target;
        IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
        epoch=public.lock_plan_student(p.student_id);
        IF epoch IS NULL OR epoch<>p.deletion_epoch THEN RETURN jsonb_build_object('error','not_found'); END IF;
        RETURN public.review_plan_view(target);
    END $$;

    CREATE FUNCTION public.generate_review_plan(target uuid,kind text,starts date,question_limit integer,
        minute_limit integer,question_minutes integer,report uuid,op_key uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE epoch bigint; ends date; p public.review_plans%ROWTYPE; report_view jsonb;
        identifier uuid; candidate record; source jsonb; task public.review_tasks%ROWTYPE;
        enrolled jsonb; pos integer=0; capacity integer;
    BEGIN
        epoch=public.lock_report_student(target);
        IF epoch IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF kind IS NULL OR kind NOT IN ('weekly','monthly') OR starts IS NULL OR starts<DATE '2000-01-01'
            OR starts>=DATE '9999-01-01' OR (kind='weekly' AND extract(isodow FROM starts)<>1)
            OR (kind='monthly' AND extract(day FROM starts)<>1) OR question_limit IS NULL
            OR question_limit NOT BETWEEN 1 AND 100 OR minute_limit IS NULL OR minute_limit NOT BETWEEN 1 AND 120
            OR question_minutes IS NULL OR question_minutes NOT BETWEEN 1 AND 30 OR op_key IS NULL THEN
            RETURN jsonb_build_object('error','invalid_plan'); END IF;
        ends=(starts+CASE WHEN kind='weekly' THEN interval '7 days' ELSE interval '1 month' END)::date;
        capacity=least(question_limit,minute_limit/question_minutes);
        PERFORM pg_advisory_xact_lock(hashtextextended('review-plan:'||target::text,0));
        SELECT * INTO p FROM public.review_plans WHERE student_id=target AND deletion_epoch=epoch AND operation_key=op_key;
        IF FOUND THEN
            IF p.type<>kind OR p.period_start<>starts OR p.max_questions<>question_limit OR p.max_minutes<>minute_limit
                OR p.minutes_per_question<>question_minutes OR p.report_id IS DISTINCT FROM report THEN
                RETURN jsonb_build_object('error','idempotency_conflict'); END IF;
            RETURN jsonb_build_object('created',false,'plan',public.review_plan_view(p.id));
        END IF;
        IF report IS NOT NULL THEN
            report_view=public.read_learning_report(report);
            IF report_view ? 'error' OR (report_view->>'student_id')::uuid IS DISTINCT FROM target THEN
                RETURN jsonb_build_object('error','not_found'); END IF;
            IF (report_view->>'stale')::boolean THEN RETURN jsonb_build_object('error','source_conflict'); END IF;
        END IF;
        INSERT INTO public.review_plans(tenant_id,student_id,deletion_epoch,type,period_start,period_end,
            max_questions,max_minutes,minutes_per_question,report_id,operation_key)
        VALUES(NULLIF(current_setting('app.tenant_id',true),'')::uuid,target,epoch,kind,starts,ends,
            question_limit,minute_limit,question_minutes,report,op_key) RETURNING id INTO identifier;
        -- Select only a finite collection. Missing report tasks are enrolled only when selected.
        FOR candidate IN
            WITH candidates AS (
                SELECT q.id,q.effective_revision,t.id AS task_id,
                    COALESCE(t.due_date,(SELECT (min(v.created_at) AT TIME ZONE 'Asia/Shanghai')::date+1
                        FROM public.verdict_revisions v WHERE v.question_id=q.id AND v.question_revision=q.current_revision
                            AND v.verdict='incorrect' AND v.provenance='guardian')) AS due_date,
                    (SELECT count(*)::integer FROM public.correction_attempts a
                        JOIN LATERAL (SELECT j.verdict FROM public.correction_judgments j WHERE j.attempt_id=a.id
                            ORDER BY j.revision DESC LIMIT 1) j ON true
                        WHERE a.question_id=q.id AND a.effective_revision=q.effective_revision
                            AND a.deletion_epoch=epoch AND j.verdict='incorrect') AS errors
                FROM public.questions q LEFT JOIN public.review_tasks t ON t.question_id=q.id
                    AND t.effective_revision=q.effective_revision AND t.deletion_epoch=epoch
                WHERE q.student_id=target AND q.deletion_epoch=epoch AND (t.state='open' OR (t.id IS NULL AND EXISTS(
                    SELECT 1 FROM public.report_sources s WHERE s.report_id=report AND s.kind='original'
                        AND s.question_id=q.id AND s.snapshot->>'verdict'='incorrect'
                        AND s.snapshot->>'verdict_provenance'='guardian')))
            ) SELECT * FROM candidates WHERE due_date<ends
                ORDER BY CASE WHEN due_date<starts THEN 0 WHEN errors>1 THEN 1 ELSE 2 END,errors DESC,due_date,id
                LIMIT capacity
        LOOP
            source=public.lock_correction_question(candidate.id);
            IF source IS NULL OR (source->>'effective_revision')::bigint<>candidate.effective_revision
                OR source->>'verdict'<>'incorrect' OR source->>'verdict_provenance'<>'guardian' THEN CONTINUE; END IF;
            IF candidate.task_id IS NULL THEN
                enrolled=public.create_review_task(candidate.id,candidate.effective_revision);
                IF enrolled ? 'error' THEN CONTINUE; END IF;
                candidate.task_id=(enrolled->'task'->>'id')::uuid;
            END IF;
            SELECT * INTO task FROM public.review_tasks WHERE id=candidate.task_id FOR UPDATE;
            IF task.state<>'open' OR task.due_date>=ends THEN CONTINUE; END IF;
            pos=pos+1;
            INSERT INTO public.review_plan_entries(tenant_id,student_id,plan_id,task_id,position,effective_revision,
                schedule_revision,due_date,priority,confirmed_incorrect_attempts)
            VALUES(task.tenant_id,target,identifier,task.id,pos,task.effective_revision,task.schedule_revision,task.due_date,
                CASE WHEN task.due_date<starts THEN 'overdue' WHEN candidate.errors>1 THEN 'repeated_error' ELSE 'remaining' END,
                candidate.errors);
        END LOOP;
        RETURN jsonb_build_object('created',true,'plan',public.review_plan_view(identifier));
    END $$;
    ''')
    for function in (*CAPABILITIES, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC')
    for function in CAPABILITIES:
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{function} TO {role}')


def downgrade():
    op.execute('''DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.review_plans) OR EXISTS(SELECT 1 FROM public.review_plan_entries) THEN
            RAISE EXCEPTION 'Cannot discard review plan history'; END IF;
    END $$''')
    for function in (*CAPABILITIES[::-1], *HELPERS[::-1]):
        op.execute(f'DROP FUNCTION public.{function}')
    op.execute('DROP TABLE public.review_plan_entries,public.review_plans')
