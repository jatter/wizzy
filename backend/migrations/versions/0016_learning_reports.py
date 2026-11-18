"""Immutable, evidence-backed guardian learning reports."""
import os

from alembic import op
from sqlalchemy import text

revision = '0016_learning_reports'
down_revision = '0015_platform_identity'

# Only these narrow capabilities are available to the API role.
CAPABILITIES = (
    'generate_learning_report(uuid,text,date)',
    'read_learning_report(uuid)',
    'read_learning_reports(uuid,text,integer,timestamptz,uuid)',
    'read_learning_report_sources(uuid,integer,timestamptz,uuid)',
    'rebuild_learning_report(uuid)',
)
HELPERS = (
    'lock_report_student(uuid)',
    'learning_report_evidence(uuid,timestamptz,timestamptz)',
    'learning_report_summary(jsonb)',
    'learning_report_view(uuid,boolean)',
)


def upgrade():
    bind = op.get_bind()
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Report migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    CREATE TABLE public.reports (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        type text NOT NULL CHECK(type IN ('weekly','monthly')), period_start date NOT NULL,
        period_end timestamptz NOT NULL, timezone text NOT NULL DEFAULT 'Asia/Shanghai' CHECK(timezone='Asia/Shanghai'),
        rules_version integer NOT NULL DEFAULT 1 CHECK(rules_version=1),
        source_version text NOT NULL CHECK(source_version ~ '^[0-9a-f]{64}$'),
        revision integer NOT NULL CHECK(revision>0), source_count integer NOT NULL CHECK(source_count BETWEEN 0 AND 10000),
        summary jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,student_id,id),
        UNIQUE(student_id,deletion_epoch,type,period_start,source_version),
        UNIQUE(student_id,deletion_epoch,type,period_start,revision),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id)
    );
    CREATE INDEX reports_student_history ON public.reports(student_id,created_at,id);
    CREATE TABLE public.report_sources (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        report_id uuid NOT NULL, kind text NOT NULL CHECK(kind IN ('original','correction','review')),
        source_id uuid NOT NULL, question_id uuid NOT NULL, submission_id uuid NOT NULL,
        snapshot jsonb NOT NULL, created_at timestamptz NOT NULL,
        UNIQUE(report_id,kind,source_id),
        FOREIGN KEY(tenant_id,student_id,report_id) REFERENCES public.reports(tenant_id,student_id,id)
    );
    CREATE INDEX report_sources_history ON public.report_sources(report_id,created_at,id);
    ALTER TABLE public.reports ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.reports FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.report_sources ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.report_sources FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.reports,public.report_sources FROM PUBLIC;

    CREATE FUNCTION public.lock_report_student(target uuid) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE actor uuid; tenant uuid; member_role text; epoch bigint;
    BEGIN
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        tenant=NULLIF(current_setting('app.tenant_id',true),'')::uuid;
        IF actor IS NULL OR tenant IS NULL OR NULLIF(current_setting('app.student_id',true),'') IS NOT NULL THEN
            RETURN NULL; END IF;
        PERFORM 1 FROM public.tenants WHERE id=tenant AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT role INTO member_role FROM public.memberships WHERE tenant_id=tenant AND user_id=actor
            AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        PERFORM 1 FROM public.users WHERE id=actor AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT deletion_epoch INTO epoch FROM public.students WHERE id=target AND tenant_id=tenant
            AND status='active' AND consent_active FOR SHARE;
        IF NOT FOUND THEN RETURN NULL; END IF;
        IF member_role<>'owner' THEN
            PERFORM 1 FROM public.student_access WHERE tenant_id=tenant AND student_id=target AND user_id=actor FOR SHARE;
            IF NOT FOUND THEN RETURN NULL; END IF;
        END IF;
        RETURN epoch;
    END $$;

    -- One STABLE statement sees a consistent source snapshot. Selection is bounded,
    -- but overflow is rejected by callers, never reported as a truncated total.
    CREATE FUNCTION public.learning_report_evidence(target uuid,starts timestamptz,ends timestamptz) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        WITH student AS MATERIALIZED (SELECT * FROM public.students WHERE id=target),
        submissions AS MATERIALIZED (
            SELECT s.id,s.status,s.source_revision,s.created_at FROM public.submissions s JOIN student st
                ON s.student_id=st.id AND s.tenant_id=st.tenant_id AND s.deletion_epoch=st.deletion_epoch
            WHERE s.created_at>=starts AND s.created_at<ends ORDER BY s.created_at,s.id LIMIT 10001
        ), originals AS MATERIALIZED (
            SELECT q.id,q.submission_id,s.created_at,public.read_question(q.id) AS view
            FROM public.questions q JOIN submissions s ON s.id=q.submission_id
            ORDER BY s.created_at,q.id LIMIT 10001
        ), corrections AS MATERIALIZED (
            SELECT a.id,a.question_id,q.submission_id,a.created_at,public.correction_attempt_view(a.id) AS view
            FROM public.correction_attempts a JOIN student st ON a.student_id=st.id
                AND a.tenant_id=st.tenant_id AND a.deletion_epoch=st.deletion_epoch
            JOIN public.questions q ON q.id=a.question_id
            WHERE a.created_at>=starts AND a.created_at<ends AND public.read_question(q.id) IS NOT NULL
            ORDER BY a.created_at,a.id LIMIT 10001
        ), reviews AS MATERIALIZED (
            SELECT a.id,t.question_id,q.submission_id,a.created_at,jsonb_build_object(
                'task_id',t.id,'outcome',a.outcome,'student_answer',a.student_answer,
                'verdict','pending_review','independent_correct',false,
                'effective_revision',t.effective_revision,'question_revision',t.question_revision,
                'source_is_current',t.effective_revision=q.effective_revision,'stem',t.stem) AS view
            FROM public.review_attempts a JOIN public.review_tasks t ON t.id=a.task_id
            JOIN student st ON a.student_id=st.id AND a.tenant_id=st.tenant_id AND t.deletion_epoch=st.deletion_epoch
            JOIN public.questions q ON q.id=t.question_id
            WHERE a.created_at>=starts AND a.created_at<ends AND public.read_question(q.id) IS NOT NULL
            ORDER BY a.created_at,a.id LIMIT 10001
        ), sources AS (
            SELECT 'original' AS kind,id AS source_id,id AS question_id,submission_id,created_at,
                jsonb_build_object('stem',view->'stem','student_answer_raw',view->'student_answer_raw',
                    'revision',view->'revision','effective_revision',view->'effective_revision',
                    'verdict_revision',view->'verdict_revision','verdict',view->'verdict',
                    'verdict_provenance',view->'verdict_provenance','page_position',view->'page_position',
                    'ordinal',view->'ordinal') AS snapshot FROM originals WHERE view IS NOT NULL
            UNION ALL SELECT 'correction',id,question_id,submission_id,created_at,view FROM corrections
            UNION ALL SELECT 'review',id,question_id,submission_id,created_at,view FROM reviews
        ) SELECT jsonb_build_object('rules_version',1,'deletion_epoch',(SELECT deletion_epoch FROM student),
            'submissions',COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.created_at,s.id) FROM submissions s),'[]'::jsonb),
            'sources',COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY s.kind,s.created_at,s.source_id) FROM sources s),'[]'::jsonb))
    $$;

    CREATE FUNCTION public.learning_report_summary(evidence jsonb) RETURNS jsonb
    LANGUAGE sql IMMUTABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        WITH sources AS (SELECT value->>'kind' AS kind,value->'snapshot' AS snapshot
            FROM jsonb_array_elements(evidence->'sources')),
        original AS (SELECT count(*) AS accepted,
            count(*) FILTER(WHERE snapshot->>'verdict' IN ('correct','incorrect')) AS effective,
            count(*) FILTER(WHERE snapshot->>'verdict'='correct') AS correct,
            count(*) FILTER(WHERE snapshot->>'verdict'='incorrect') AS incorrect,
            count(*) FILTER(WHERE snapshot->>'verdict'='pending_review') AS pending_review,
            count(*) FILTER(WHERE snapshot->>'verdict'='unanswered') AS unanswered,
            count(*) FILTER(WHERE snapshot->>'verdict'='unsupported') AS unsupported FROM sources WHERE kind='original'),
        corrections AS (SELECT count(*) AS attempts,
            count(*) FILTER(WHERE snapshot->>'verdict'='correct') AS correct,
            count(*) FILTER(WHERE snapshot->>'verdict'='incorrect') AS incorrect,
            count(*) FILTER(WHERE snapshot->>'verdict'='pending_review') AS pending_review
            FROM sources WHERE kind='correction'),
        reviews AS (SELECT count(*) AS attempts,
            count(*) FILTER(WHERE snapshot->>'outcome'='submitted') AS submitted,
            count(*) FILTER(WHERE snapshot->>'outcome'='needs_help') AS needs_help,
            count(*) FILTER(WHERE snapshot->>'outcome'='skipped') AS skipped FROM sources WHERE kind='review')
        SELECT jsonb_build_object('submissions',jsonb_array_length(evidence->'submissions'),
            'original',(SELECT to_jsonb(o)||jsonb_build_object('accuracy',o.correct::numeric/NULLIF(o.effective,0)) FROM original o),
            'corrections',(SELECT to_jsonb(c)||jsonb_build_object('accuracy',c.correct::numeric/NULLIF(c.correct+c.incorrect,0))
                FROM corrections c),
            'reviews',(SELECT to_jsonb(r)||jsonb_build_object('awaiting_review',r.submitted,'completed',0) FROM reviews r),
            'weak_points',jsonb_build_object('status','insufficient_evidence','items','[]'::jsonb))
    $$;

    CREATE FUNCTION public.learning_report_view(target uuid,is_stale boolean) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',r.id,'student_id',r.student_id,'type',r.type,'period_start',r.period_start,
            'period_end',r.period_end,'timezone',r.timezone,'rules_version',r.rules_version,
            'source_version',r.source_version,'revision',r.revision,'source_count',r.source_count,
            'summary',r.summary,'created_at',r.created_at,'stale',is_stale) FROM public.reports r WHERE r.id=target
    $$;

    CREATE FUNCTION public.generate_learning_report(target uuid,kind text,starts date) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE epoch bigint; begins timestamptz; ends timestamptz; evidence jsonb; version text;
        identifier uuid; next_revision integer;
    BEGIN
        epoch=public.lock_report_student(target);
        IF epoch IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF kind IS NULL OR kind NOT IN ('weekly','monthly') OR starts IS NULL
            OR starts<DATE '2000-01-01' OR starts>=DATE '9999-01-01'
            OR (kind='weekly' AND extract(isodow FROM starts)<>1)
            OR (kind='monthly' AND extract(day FROM starts)<>1) THEN
            RETURN jsonb_build_object('error','invalid_report'); END IF;
        begins=starts::timestamp AT TIME ZONE 'Asia/Shanghai';
        ends=(starts::timestamp+CASE WHEN kind='weekly' THEN interval '7 days' ELSE interval '1 month' END)
            AT TIME ZONE 'Asia/Shanghai';
        -- Serialize only report generation, not question/judgment writers.
        PERFORM pg_advisory_xact_lock(hashtextextended('learning-report:'||target::text,0));
        evidence=public.learning_report_evidence(target,begins,ends);
        IF jsonb_array_length(evidence->'sources')>10000 OR jsonb_array_length(evidence->'submissions')>10000 THEN
            RETURN jsonb_build_object('error','report_too_large'); END IF;
        version=encode(sha256(convert_to(evidence::text,'UTF8')),'hex');
        SELECT id INTO identifier FROM public.reports WHERE student_id=target AND deletion_epoch=epoch
            AND type=kind AND period_start=starts AND source_version=version;
        IF FOUND THEN RETURN jsonb_build_object('created',false,'report',public.learning_report_view(identifier,false)); END IF;
        SELECT COALESCE(max(revision),0)+1 INTO next_revision FROM public.reports WHERE student_id=target
            AND deletion_epoch=epoch AND type=kind AND period_start=starts;
        INSERT INTO public.reports(tenant_id,student_id,deletion_epoch,type,period_start,period_end,
            source_version,revision,source_count,summary)
        VALUES(NULLIF(current_setting('app.tenant_id',true),'')::uuid,target,epoch,kind,starts,ends,
            version,next_revision,jsonb_array_length(evidence->'sources'),public.learning_report_summary(evidence))
        RETURNING id INTO identifier;
        INSERT INTO public.report_sources(tenant_id,student_id,report_id,kind,source_id,question_id,submission_id,snapshot,created_at)
        SELECT NULLIF(current_setting('app.tenant_id',true),'')::uuid,target,identifier,s->>'kind',
            (s->>'source_id')::uuid,(s->>'question_id')::uuid,(s->>'submission_id')::uuid,s->'snapshot',
            (s->>'created_at')::timestamptz FROM jsonb_array_elements(evidence->'sources') s;
        RETURN jsonb_build_object('created',true,'report',public.learning_report_view(identifier,false));
    END $$;

    CREATE FUNCTION public.read_learning_report(target uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r public.reports%ROWTYPE; epoch bigint; evidence jsonb; version text;
    BEGIN
        SELECT * INTO r FROM public.reports WHERE id=target;
        IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
        epoch=public.lock_report_student(r.student_id);
        IF epoch IS NULL OR epoch<>r.deletion_epoch THEN RETURN jsonb_build_object('error','not_found'); END IF;
        evidence=public.learning_report_evidence(r.student_id,r.period_start::timestamp AT TIME ZONE r.timezone,r.period_end);
        version=encode(sha256(convert_to(evidence::text,'UTF8')),'hex');
        RETURN public.learning_report_view(target,version<>r.source_version);
    END $$;

    CREATE FUNCTION public.read_learning_reports(target uuid,kind text,maximum integer,after_date timestamptz,after_id uuid)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE epoch bigint; entries jsonb;
    BEGIN
        epoch=public.lock_report_student(target);
        IF epoch IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF maximum IS NULL OR maximum NOT BETWEEN 1 AND 100 OR (after_date IS NULL)<>(after_id IS NULL)
            OR (kind IS NOT NULL AND kind NOT IN ('weekly','monthly')) THEN
            RETURN jsonb_build_object('error','invalid_report'); END IF;
        SELECT COALESCE(jsonb_agg(public.read_learning_report(r.id) ORDER BY r.created_at,r.id),'[]'::jsonb) INTO entries
            FROM (SELECT id,created_at FROM public.reports WHERE student_id=target AND deletion_epoch=epoch
                AND (kind IS NULL OR type=kind) AND (after_date IS NULL OR (created_at,id)>(after_date,after_id))
                ORDER BY created_at,id LIMIT maximum+1) r;
        RETURN jsonb_build_object('items',entries);
    END $$;

    CREATE FUNCTION public.read_learning_report_sources(target uuid,maximum integer,after_date timestamptz,after_id uuid)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r public.reports%ROWTYPE; epoch bigint; entries jsonb;
    BEGIN
        SELECT * INTO r FROM public.reports WHERE id=target;
        IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
        epoch=public.lock_report_student(r.student_id);
        IF epoch IS NULL OR epoch<>r.deletion_epoch THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF maximum IS NULL OR maximum NOT BETWEEN 1 AND 100 OR (after_date IS NULL)<>(after_id IS NULL) THEN
            RETURN jsonb_build_object('error','invalid_report'); END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object('id',s.id,'kind',s.kind,'source_id',s.source_id,
            'question_id',s.question_id,'submission_id',s.submission_id,'snapshot',s.snapshot,'created_at',s.created_at)
            ORDER BY s.created_at,s.id),'[]'::jsonb) INTO entries FROM (
                SELECT * FROM public.report_sources WHERE report_id=target
                AND (after_date IS NULL OR (created_at,id)>(after_date,after_id))
                ORDER BY created_at,id LIMIT maximum+1) s;
        RETURN jsonb_build_object('items',entries);
    END $$;

    CREATE FUNCTION public.rebuild_learning_report(target uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r public.reports%ROWTYPE; epoch bigint;
    BEGIN
        SELECT * INTO r FROM public.reports WHERE id=target;
        IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
        epoch=public.lock_report_student(r.student_id);
        IF epoch IS NULL OR epoch<>r.deletion_epoch THEN RETURN jsonb_build_object('error','not_found'); END IF;
        RETURN public.generate_learning_report(r.student_id,r.type,r.period_start);
    END $$;
    ''')
    for function in (*CAPABILITIES, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC')
    for function in CAPABILITIES:
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{function} TO {role}')


def downgrade():
    op.execute('''DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.reports) OR EXISTS(SELECT 1 FROM public.report_sources) THEN
            RAISE EXCEPTION 'Cannot discard learning report history'; END IF;
    END $$''')
    for function in (*CAPABILITIES[::-1], *HELPERS[::-1]):
        op.execute(f'DROP FUNCTION public.{function}')
    op.execute('DROP TABLE public.report_sources,public.reports')
