"""First-step review tasks, persistent drafts and explicitly unverified attempts."""
import os

from alembic import op
from sqlalchemy import text

revision = '0013_review_workbench'
down_revision = '0012_correction_attempts'


def upgrade():
    bind = op.get_bind()
    app = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Review migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role'''), {'role': app}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    app = bind.dialect.identifier_preparer.quote(app)
    op.execute('''
    CREATE TABLE public.review_tasks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        question_id uuid NOT NULL, deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        effective_revision bigint NOT NULL CHECK(effective_revision>1), question_revision integer NOT NULL,
        stem text NOT NULL, step integer NOT NULL DEFAULT 1 CHECK(step=1),
        timezone text NOT NULL DEFAULT 'Asia/Shanghai' CHECK(timezone='Asia/Shanghai'),
        base_date date NOT NULL, due_date date NOT NULL, schedule_revision integer NOT NULL DEFAULT 0 CHECK(schedule_revision>=0),
        draft_answer text NOT NULL DEFAULT '' CHECK(length(draft_answer)<=8000),
        draft_revision integer NOT NULL DEFAULT 0 CHECK(draft_revision>=0),
        state text NOT NULL DEFAULT 'open' CHECK(state IN ('open','awaiting_review')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,student_id,id), UNIQUE(question_id,effective_revision,step),
        FOREIGN KEY(tenant_id,student_id,question_id) REFERENCES public.questions(tenant_id,student_id,id),
        FOREIGN KEY(question_id,question_revision) REFERENCES public.question_revisions(question_id,revision)
    );
    CREATE INDEX review_tasks_student_history ON public.review_tasks(student_id,created_at,id);
    CREATE TABLE public.review_attempts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        task_id uuid NOT NULL, expected_draft_revision integer NOT NULL CHECK(expected_draft_revision>=0),
        outcome text NOT NULL CHECK(outcome IN ('submitted','needs_help','skipped')),
        student_answer text NOT NULL CHECK(length(student_answer)<=8000),
        independent_correct boolean NOT NULL DEFAULT false CHECK(NOT independent_correct),
        operation_key uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(outcome<>'submitted' OR student_answer ~ '[^[:space:]]'),
        UNIQUE(task_id,student_id,operation_key), UNIQUE(task_id,expected_draft_revision),
        FOREIGN KEY(tenant_id,student_id,task_id) REFERENCES public.review_tasks(tenant_id,student_id,id)
    );
    CREATE TABLE public.review_reschedules (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        task_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
        expected_revision integer NOT NULL CHECK(expected_revision=revision-1),
        previous_due_date date NOT NULL, due_date date NOT NULL,
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000 AND reason ~ '[^[:space:]]'),
        actor_id uuid NOT NULL REFERENCES public.users(id), operation_key uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(task_id,revision), UNIQUE(task_id,actor_id,operation_key),
        FOREIGN KEY(tenant_id,student_id,task_id) REFERENCES public.review_tasks(tenant_id,student_id,id)
    );
    ALTER TABLE public.review_tasks ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.review_tasks FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.review_attempts ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.review_attempts FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.review_reschedules ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.review_reschedules FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.review_tasks,public.review_attempts,public.review_reschedules FROM PUBLIC;

    CREATE FUNCTION public.review_task_view(target uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',t.id,'question_id',t.question_id,'student_id',t.student_id,
            'effective_revision',t.effective_revision,'question_revision',t.question_revision,'stem',t.stem,
            'source_is_current',t.effective_revision=q.effective_revision,'step',t.step,'timezone',t.timezone,
            'base_date',t.base_date,'due_date',t.due_date,'schedule_revision',t.schedule_revision,
            'state',t.state,'created_at',t.created_at,
            'draft',jsonb_build_object('revision',t.draft_revision,'student_answer',t.draft_answer),
            'attempts',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',a.id,'created_at',a.created_at,
                'draft_revision',a.expected_draft_revision,'outcome',a.outcome,'student_answer',a.student_answer,
                'verdict','pending_review','independent_correct',a.independent_correct)
                ORDER BY a.expected_draft_revision) FROM public.review_attempts a WHERE a.task_id=t.id),'[]'::jsonb),
            'reschedules',COALESCE((SELECT jsonb_agg(jsonb_build_object('revision',r.revision,'due_date',r.due_date,
                'previous_due_date',r.previous_due_date,'reason',r.reason,'created_at',r.created_at)
                ORDER BY r.revision) FROM public.review_reschedules r WHERE r.task_id=t.id),'[]'::jsonb))
        FROM public.review_tasks t JOIN public.questions q ON q.id=t.question_id WHERE t.id=target
    $$;

    CREATE FUNCTION public.read_review_task(target uuid) RETURNS jsonb
    LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE t public.review_tasks%ROWTYPE;
    BEGIN
        SELECT * INTO t FROM public.review_tasks WHERE id=target;
        IF NOT FOUND OR public.read_question(t.question_id) IS NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        RETURN public.review_task_view(target);
    END $$;

    CREATE FUNCTION public.lock_review_task(target uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE t public.review_tasks%ROWTYPE; source jsonb;
    BEGIN
        SELECT * INTO t FROM public.review_tasks WHERE id=target;
        IF NOT FOUND THEN RETURN NULL; END IF;
        -- Reuse the established tenant/member/user/student/access/question lock order.
        source=public.lock_correction_question(t.question_id);
        IF source IS NULL THEN RETURN NULL; END IF;
        PERFORM 1 FROM public.review_tasks WHERE id=target FOR UPDATE;
        RETURN public.review_task_view(target);
    END $$;

    CREATE FUNCTION public.create_review_task(target uuid,expected bigint) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE source jsonb; q public.questions%ROWTYPE; identifier uuid; base date;
    BEGIN
        IF NULLIF(current_setting('app.user_id',true),'') IS NULL
            OR NULLIF(current_setting('app.student_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        source=public.lock_correction_question(target);
        IF source IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<1 THEN RETURN jsonb_build_object('error','invalid_review'); END IF;
        IF expected<>(source->>'effective_revision')::bigint OR source->>'verdict'<>'incorrect'
            OR source->>'verdict_provenance'<>'guardian' THEN
            RETURN jsonb_build_object('error','source_conflict'); END IF;
        SELECT id INTO identifier FROM public.review_tasks WHERE question_id=target AND effective_revision=expected AND step=1;
        IF FOUND THEN RETURN jsonb_build_object('created',false,'task',public.review_task_view(identifier)); END IF;
        SELECT * INTO q FROM public.questions WHERE id=target;
        SELECT (min(v.created_at) AT TIME ZONE 'Asia/Shanghai')::date INTO base FROM public.verdict_revisions v
            WHERE v.question_id=target AND v.question_revision=q.current_revision AND v.verdict='incorrect' AND v.provenance='guardian';
        IF base IS NULL THEN RETURN jsonb_build_object('error','source_conflict'); END IF;
        INSERT INTO public.review_tasks(tenant_id,student_id,question_id,deletion_epoch,effective_revision,
            question_revision,stem,base_date,due_date)
        VALUES(q.tenant_id,q.student_id,q.id,q.deletion_epoch,expected,q.current_revision,source->>'stem',base,base+1)
        RETURNING id INTO identifier;
        RETURN jsonb_build_object('created',true,'task',public.review_task_view(identifier));
    END $$;

    CREATE FUNCTION public.read_student_review_tasks(target uuid,maximum integer,after_date timestamptz,after_id uuid,due_only boolean)
    RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        WITH visible AS MATERIALIZED (
            SELECT t.* FROM public.review_tasks t JOIN public.questions q ON q.id=t.question_id
                AND q.effective_revision=t.effective_revision
            WHERE t.student_id=target AND public.read_question(q.id) IS NOT NULL
        ), entries AS (
            SELECT t.id,t.created_at FROM visible t
            WHERE maximum BETWEEN 1 AND 100
                AND (NOT due_only OR t.due_date<=(CURRENT_TIMESTAMP AT TIME ZONE t.timezone)::date)
                AND ((after_date IS NULL AND after_id IS NULL) OR (t.created_at,t.id)>(after_date,after_id))
            ORDER BY t.created_at,t.id LIMIT LEAST(GREATEST(maximum,0),100)+1
        ) SELECT COALESCE(jsonb_agg(public.review_task_view(id) ORDER BY created_at,id),'[]'::jsonb) FROM entries
    $$;

    CREATE FUNCTION public.save_review_draft(target uuid,expected integer,answer text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE view jsonb;
    BEGIN
        view=public.lock_review_task(target);
        IF view IS NULL OR NULLIF(current_setting('app.student_id',true),'')::uuid IS DISTINCT FROM (view->>'student_id')::uuid
            OR NULLIF(current_setting('app.user_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<0 OR expected=2147483647 OR answer IS NULL OR length(answer)>8000 THEN
            RETURN jsonb_build_object('error','invalid_review'); END IF;
        IF NOT (view->>'source_is_current')::boolean THEN RETURN jsonb_build_object('error','source_conflict'); END IF;
        IF view->>'state'<>'open' THEN RETURN jsonb_build_object('error','task_conflict'); END IF;
        IF (view->'draft'->>'revision')::integer<>expected THEN RETURN jsonb_build_object('error','revision_conflict'); END IF;
        UPDATE public.review_tasks SET draft_answer=answer,draft_revision=draft_revision+1 WHERE id=target;
        RETURN jsonb_build_object('created',true,'task',public.review_task_view(target));
    END $$;

    CREATE FUNCTION public.submit_review_attempt(target uuid,expected integer,result text,op_key uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE view jsonb; a public.review_attempts%ROWTYPE; child uuid;
    BEGIN
        view=public.lock_review_task(target);
        child=NULLIF(current_setting('app.student_id',true),'')::uuid;
        IF view IS NULL OR child IS DISTINCT FROM (view->>'student_id')::uuid
            OR NULLIF(current_setting('app.user_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<0 OR expected=2147483647 OR op_key IS NULL
            OR result IS NULL OR result NOT IN ('submitted','needs_help','skipped') THEN
            RETURN jsonb_build_object('error','invalid_review'); END IF;
        SELECT * INTO a FROM public.review_attempts WHERE task_id=target AND student_id=child AND operation_key=op_key;
        IF FOUND THEN
            IF a.expected_draft_revision<>expected OR a.outcome<>result THEN
                RETURN jsonb_build_object('error','idempotency_conflict'); END IF;
            RETURN jsonb_build_object('created',false,'task',view);
        END IF;
        IF NOT (view->>'source_is_current')::boolean THEN RETURN jsonb_build_object('error','source_conflict'); END IF;
        IF view->>'state'<>'open' THEN RETURN jsonb_build_object('error','task_conflict'); END IF;
        IF (view->'draft'->>'revision')::integer<>expected THEN RETURN jsonb_build_object('error','revision_conflict'); END IF;
        IF result='submitted' AND view->'draft'->>'student_answer' !~ '[^[:space:]]' THEN
            RETURN jsonb_build_object('error','invalid_review'); END IF;
        INSERT INTO public.review_attempts(tenant_id,student_id,task_id,expected_draft_revision,outcome,student_answer,operation_key)
        SELECT t.tenant_id,t.student_id,t.id,expected,result,t.draft_answer,op_key FROM public.review_tasks t WHERE t.id=target;
        UPDATE public.review_tasks SET draft_revision=draft_revision+1,
            state=CASE WHEN result='submitted' THEN 'awaiting_review' ELSE 'open' END WHERE id=target;
        RETURN jsonb_build_object('created',true,'task',public.review_task_view(target));
    END $$;

    CREATE FUNCTION public.reschedule_review_task(target uuid,expected integer,new_date date,explanation text,op_key uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE view jsonb; r public.review_reschedules%ROWTYPE; actor uuid;
    BEGIN
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        IF actor IS NULL OR NULLIF(current_setting('app.student_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        view=public.lock_review_task(target);
        IF view IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<0 OR expected=2147483647 OR op_key IS NULL OR new_date IS NULL
            OR explanation IS NULL OR length(explanation) NOT BETWEEN 1 AND 1000 OR explanation !~ '[^[:space:]]' THEN
            RETURN jsonb_build_object('error','invalid_review'); END IF;
        SELECT * INTO r FROM public.review_reschedules WHERE task_id=target AND actor_id=actor AND operation_key=op_key;
        IF FOUND THEN
            IF r.expected_revision<>expected OR r.due_date<>new_date OR r.reason<>explanation THEN
                RETURN jsonb_build_object('error','idempotency_conflict'); END IF;
            RETURN jsonb_build_object('created',false,'task',view);
        END IF;
        IF NOT (view->>'source_is_current')::boolean THEN RETURN jsonb_build_object('error','source_conflict'); END IF;
        IF view->>'state'<>'open' THEN RETURN jsonb_build_object('error','task_conflict'); END IF;
        IF (view->>'schedule_revision')::integer<>expected THEN RETURN jsonb_build_object('error','revision_conflict'); END IF;
        INSERT INTO public.review_reschedules(tenant_id,student_id,task_id,revision,expected_revision,
            previous_due_date,due_date,reason,actor_id,operation_key)
        SELECT t.tenant_id,t.student_id,t.id,expected+1,expected,t.due_date,new_date,explanation,actor,op_key
            FROM public.review_tasks t WHERE t.id=target;
        UPDATE public.review_tasks SET due_date=new_date,schedule_revision=schedule_revision+1 WHERE id=target;
        RETURN jsonb_build_object('created',true,'task',public.review_task_view(target));
    END $$;
    REVOKE ALL ON FUNCTION public.review_task_view(uuid),public.lock_review_task(uuid),public.read_review_task(uuid),
        public.create_review_task(uuid,bigint),public.read_student_review_tasks(uuid,integer,timestamptz,uuid,boolean),
        public.save_review_draft(uuid,integer,text),public.submit_review_attempt(uuid,integer,text,uuid),
        public.reschedule_review_task(uuid,integer,date,text,uuid) FROM PUBLIC;
    ''')
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.read_review_task(uuid),public.create_review_task(uuid,bigint),
        public.read_student_review_tasks(uuid,integer,timestamptz,uuid,boolean),public.save_review_draft(uuid,integer,text),
        public.submit_review_attempt(uuid,integer,text,uuid),public.reschedule_review_task(uuid,integer,date,text,uuid) TO {app}''')


def downgrade():
    op.execute('''
    DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.review_tasks) OR EXISTS(SELECT 1 FROM public.review_attempts)
            OR EXISTS(SELECT 1 FROM public.review_reschedules) THEN
            RAISE EXCEPTION 'Cannot discard review task, attempt or reschedule history';
        END IF;
    END $$;
    DROP FUNCTION public.reschedule_review_task(uuid,integer,date,text,uuid);
    DROP FUNCTION public.submit_review_attempt(uuid,integer,text,uuid);
    DROP FUNCTION public.save_review_draft(uuid,integer,text);
    DROP FUNCTION public.read_student_review_tasks(uuid,integer,timestamptz,uuid,boolean);
    DROP FUNCTION public.create_review_task(uuid,bigint);
    DROP FUNCTION public.lock_review_task(uuid);
    DROP FUNCTION public.read_review_task(uuid);
    DROP FUNCTION public.review_task_view(uuid);
    DROP TABLE public.review_reschedules;
    DROP TABLE public.review_attempts;
    DROP TABLE public.review_tasks;
    ''')
