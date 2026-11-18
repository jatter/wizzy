"""Append-only correction attempts and versioned guardian judgments."""
import os

from alembic import op
from sqlalchemy import text

revision = '0012_correction_attempts'
down_revision = '0011_mistake_projection'


def upgrade():
    bind = op.get_bind()
    app = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Correction migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role'''), {'role': app}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    app = bind.dialect.identifier_preparer.quote(app)
    op.execute('''
    CREATE TABLE public.correction_attempts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        question_id uuid NOT NULL, deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        question_revision integer NOT NULL, verdict_revision integer NOT NULL,
        effective_revision bigint NOT NULL CHECK(effective_revision>0), source jsonb NOT NULL,
        student_answer text CHECK(length(student_answer) BETWEEN 1 AND 8000 AND student_answer ~ '[^[:space:]]'),
        media_id uuid, actor_kind text NOT NULL CHECK(actor_kind IN ('guardian','child')), actor_id uuid NOT NULL,
        operation_key uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((student_answer IS NOT NULL)::integer + (media_id IS NOT NULL)::integer = 1),
        UNIQUE(tenant_id,student_id,id), UNIQUE(question_id,actor_kind,actor_id,operation_key),
        FOREIGN KEY(tenant_id,student_id,question_id) REFERENCES public.questions(tenant_id,student_id,id),
        FOREIGN KEY(question_id,question_revision) REFERENCES public.question_revisions(question_id,revision),
        FOREIGN KEY(question_id,verdict_revision,question_revision)
            REFERENCES public.verdict_revisions(question_id,revision,question_revision),
        FOREIGN KEY(tenant_id,student_id,media_id) REFERENCES public.media_assets(tenant_id,student_id,id)
    );
    CREATE INDEX correction_attempts_history ON public.correction_attempts(question_id,created_at,id);
    CREATE TABLE public.correction_judgments (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        attempt_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
        expected_revision integer NOT NULL CHECK(expected_revision=revision-1),
        verdict text NOT NULL CHECK(verdict IN ('correct','incorrect','pending_review')),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000 AND reason ~ '[^[:space:]]'),
        actor_id uuid NOT NULL REFERENCES public.users(id), operation_key uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(attempt_id,revision), UNIQUE(attempt_id,actor_id,operation_key),
        FOREIGN KEY(tenant_id,student_id,attempt_id) REFERENCES public.correction_attempts(tenant_id,student_id,id)
    );
    ALTER TABLE public.correction_attempts ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.correction_attempts FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.correction_judgments ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.correction_judgments FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.correction_attempts,public.correction_judgments FROM PUBLIC;

    -- Internal helpers are not runtime capabilities.
    CREATE FUNCTION public.lock_correction_question(target uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE q public.questions%ROWTYPE; actor uuid;
    BEGIN
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        PERFORM 1 FROM public.tenants t WHERE t.id=NULLIF(current_setting('app.tenant_id',true),'')::uuid FOR SHARE;
        PERFORM 1 FROM public.memberships m WHERE m.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND m.user_id=actor FOR SHARE;
        PERFORM 1 FROM public.users u WHERE u.id=actor FOR SHARE;
        SELECT * INTO q FROM public.questions WHERE id=target;
        IF NOT FOUND OR q.tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
            RETURN NULL; END IF;
        PERFORM 1 FROM public.students st WHERE st.id=q.student_id FOR SHARE;
        PERFORM 1 FROM public.student_access a WHERE a.student_id=q.student_id AND a.user_id=actor FOR SHARE;
        PERFORM 1 FROM public.questions WHERE id=target FOR UPDATE;
        RETURN public.read_question(target);
    END $$;

    CREATE FUNCTION public.correction_attempt_view(target uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',a.id,'created_at',a.created_at,
            'student_answer',a.student_answer,'media_id',a.media_id,'source',a.source,
            'source_is_current',a.effective_revision=q.effective_revision,
            'verdict',COALESCE(j.verdict,'pending_review'),
            'verdict_provenance',CASE WHEN j.id IS NULL THEN 'unverified' ELSE 'guardian' END,
            'judgment_revision',COALESCE(j.revision,0),
            'judgment_history',COALESCE((SELECT jsonb_agg(jsonb_build_object('revision',h.revision,
                'verdict',h.verdict,'reason',h.reason,'provenance','guardian','created_at',h.created_at)
                ORDER BY h.revision) FROM public.correction_judgments h WHERE h.attempt_id=a.id),'[]'::jsonb))
        FROM public.correction_attempts a JOIN public.questions q ON q.id=a.question_id
        LEFT JOIN LATERAL (SELECT * FROM public.correction_judgments h WHERE h.attempt_id=a.id
            ORDER BY h.revision DESC LIMIT 1) j ON true
        WHERE a.id=target
    $$;

    CREATE FUNCTION public.read_correction_attempts(target uuid,maximum integer,after_date timestamptz,after_id uuid)
        RETURNS jsonb LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF public.read_question(target) IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF maximum IS NULL OR maximum NOT BETWEEN 1 AND 100 OR (after_date IS NULL)<>(after_id IS NULL) THEN
            RETURN jsonb_build_object('error','invalid_correction'); END IF;
        RETURN jsonb_build_object('items',COALESCE((SELECT jsonb_agg(public.correction_attempt_view(a.id)
            ORDER BY a.created_at,a.id) FROM (SELECT id,created_at FROM public.correction_attempts
                WHERE question_id=target AND (after_date IS NULL OR (created_at,id)>(after_date,after_id))
                ORDER BY created_at,id LIMIT maximum+1) a),'[]'::jsonb));
    END $$;

    CREATE FUNCTION public.submit_correction_attempt(target uuid,expected bigint,answer text,media uuid,op_key uuid)
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE view jsonb; q public.questions%ROWTYPE; a public.correction_attempts%ROWTYPE;
        actor uuid; kind text; identifier uuid;
    BEGIN
        view=public.lock_correction_question(target);
        IF view IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<1 OR op_key IS NULL OR (answer IS NULL)=(media IS NULL)
            OR (answer IS NOT NULL AND (length(answer) NOT BETWEEN 1 AND 8000 OR answer !~ '[^[:space:]]')) THEN
            RETURN jsonb_build_object('error','invalid_correction'); END IF;
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        kind=CASE WHEN actor IS NULL THEN 'child' ELSE 'guardian' END;
        actor=COALESCE(actor,NULLIF(current_setting('app.student_id',true),'')::uuid);
        SELECT * INTO a FROM public.correction_attempts WHERE question_id=target
            AND actor_kind=kind AND actor_id=actor AND operation_key=op_key;
        IF FOUND THEN
            IF a.effective_revision<>expected OR a.student_answer IS DISTINCT FROM answer OR a.media_id IS DISTINCT FROM media THEN
                RETURN jsonb_build_object('error','idempotency_conflict'); END IF;
            RETURN jsonb_build_object('created',false,'attempt',public.correction_attempt_view(a.id));
        END IF;
        SELECT * INTO q FROM public.questions WHERE id=target;
        IF q.effective_revision<>expected OR view->>'verdict'<>'incorrect' OR view->>'verdict_provenance'<>'guardian' THEN
            RETURN jsonb_build_object('error','source_conflict'); END IF;
        IF media IS NOT NULL THEN
            PERFORM 1 FROM public.media_assets m WHERE m.id=media AND m.tenant_id=q.tenant_id
                AND m.student_id=q.student_id AND m.deletion_epoch=q.deletion_epoch AND m.status='ready'
                AND m.clean_key IS NOT NULL FOR SHARE;
            IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
        END IF;
        INSERT INTO public.correction_attempts(tenant_id,student_id,question_id,deletion_epoch,
            question_revision,verdict_revision,effective_revision,source,student_answer,media_id,actor_kind,actor_id,operation_key)
        VALUES(q.tenant_id,q.student_id,q.id,q.deletion_epoch,q.current_revision,q.verdict_revision,q.effective_revision,
            jsonb_build_object('question_revision',q.current_revision,'verdict_revision',q.verdict_revision,
                'effective_revision',q.effective_revision,'stem',view->'stem','student_answer_raw',view->'student_answer_raw',
                'verdict',view->'verdict','verdict_provenance',view->'verdict_provenance'),answer,media,kind,actor,op_key)
        RETURNING id INTO identifier;
        RETURN jsonb_build_object('created',true,'attempt',public.correction_attempt_view(identifier));
    END $$;

    CREATE FUNCTION public.judge_correction_attempt(target uuid,attempt uuid,expected integer,
        new_verdict text,explanation text,op_key uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE view jsonb; a public.correction_attempts%ROWTYPE; j public.correction_judgments%ROWTYPE;
        actor uuid; latest integer;
    BEGIN
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        IF actor IS NULL OR NULLIF(current_setting('app.student_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        view=public.lock_correction_question(target);
        IF view IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        SELECT * INTO a FROM public.correction_attempts WHERE id=attempt AND question_id=target;
        IF NOT FOUND THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<0 OR expected=2147483647 OR op_key IS NULL
            OR new_verdict IS NULL OR new_verdict NOT IN ('correct','incorrect','pending_review')
            OR explanation IS NULL OR length(explanation) NOT BETWEEN 1 AND 1000 OR explanation !~ '[^[:space:]]' THEN
            RETURN jsonb_build_object('error','invalid_correction'); END IF;
        SELECT * INTO j FROM public.correction_judgments WHERE attempt_id=attempt AND actor_id=actor AND operation_key=op_key;
        IF FOUND THEN
            IF j.expected_revision<>expected OR j.verdict<>new_verdict OR j.reason<>explanation THEN
                RETURN jsonb_build_object('error','idempotency_conflict'); END IF;
            RETURN jsonb_build_object('created',false,'attempt',public.correction_attempt_view(a.id));
        END IF;
        IF a.effective_revision<>(view->>'effective_revision')::bigint THEN
            RETURN jsonb_build_object('error','source_conflict'); END IF;
        SELECT COALESCE(max(revision),0) INTO latest FROM public.correction_judgments WHERE attempt_id=attempt;
        IF latest<>expected THEN RETURN jsonb_build_object('error','revision_conflict'); END IF;
        INSERT INTO public.correction_judgments(tenant_id,student_id,attempt_id,revision,expected_revision,verdict,reason,actor_id,operation_key)
        VALUES(a.tenant_id,a.student_id,a.id,latest+1,expected,new_verdict,explanation,actor,op_key);
        RETURN jsonb_build_object('created',true,'attempt',public.correction_attempt_view(a.id));
    END $$;
    REVOKE ALL ON FUNCTION public.lock_correction_question(uuid),public.correction_attempt_view(uuid),
        public.read_correction_attempts(uuid,integer,timestamptz,uuid),
        public.submit_correction_attempt(uuid,bigint,text,uuid,uuid),
        public.judge_correction_attempt(uuid,uuid,integer,text,text,uuid) FROM PUBLIC;
    ''')
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.read_correction_attempts(uuid,integer,timestamptz,uuid),
        public.submit_correction_attempt(uuid,bigint,text,uuid,uuid),
        public.judge_correction_attempt(uuid,uuid,integer,text,text,uuid) TO {app}''')


def downgrade():
    op.execute('''
    DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.correction_attempts) OR EXISTS(SELECT 1 FROM public.correction_judgments) THEN
            RAISE EXCEPTION 'Cannot discard correction attempt or judgment history';
        END IF;
    END $$;
    DROP FUNCTION public.judge_correction_attempt(uuid,uuid,integer,text,text,uuid);
    DROP FUNCTION public.submit_correction_attempt(uuid,bigint,text,uuid,uuid);
    DROP FUNCTION public.read_correction_attempts(uuid,integer,timestamptz,uuid);
    DROP FUNCTION public.correction_attempt_view(uuid);
    DROP FUNCTION public.lock_correction_question(uuid);
    DROP TABLE public.correction_judgments;
    DROP TABLE public.correction_attempts;
    ''')
