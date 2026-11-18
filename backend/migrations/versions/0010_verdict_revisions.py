"""Guardian verdict history and atomic, source-versioned change events."""
import os

from alembic import op
from sqlalchemy import text

revision = "0010_verdict_revisions"
down_revision = "0009_question_revisions"


def upgrade():
    bind = op.get_bind()
    app = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Verdict migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role'''), {'role': app}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    app = bind.dialect.identifier_preparer.quote(app)
    # 0009 may have backfilled questions in this same migration transaction.
    # Flush its deferred FK checks before ALTER, then restore deferred seeding.
    op.execute('SET CONSTRAINTS public.question_current_revision IMMEDIATE')
    op.execute('SET CONSTRAINTS public.question_current_revision DEFERRED')
    op.execute('''
    CREATE TABLE public.verdict_revisions (
        question_id uuid NOT NULL, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        revision integer NOT NULL CHECK(revision>0), question_revision integer NOT NULL CHECK(question_revision>0),
        verdict text NOT NULL CHECK(verdict IN ('correct','incorrect','pending_review','unanswered','unsupported')),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000 AND reason ~ '[^[:space:]]'),
        provenance text NOT NULL DEFAULT 'guardian' CHECK(provenance='guardian'),
        actor_id uuid NOT NULL REFERENCES public.users(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(question_id,revision), UNIQUE(question_id,revision,question_revision),
        FOREIGN KEY(tenant_id,student_id,question_id) REFERENCES public.questions(tenant_id,student_id,id),
        FOREIGN KEY(question_id,question_revision) REFERENCES public.question_revisions(question_id,revision)
    );
    ALTER TABLE public.questions
        ADD COLUMN verdict_revision integer NOT NULL DEFAULT 0 CHECK(verdict_revision>=0),
        ADD COLUMN effective_verdict_revision integer,
        ADD COLUMN effective_revision bigint NOT NULL DEFAULT 1 CHECK(effective_revision>0),
        ADD CONSTRAINT question_effective_verdict FOREIGN KEY(id,effective_verdict_revision,current_revision)
            REFERENCES public.verdict_revisions(question_id,revision,question_revision) DEFERRABLE INITIALLY DEFERRED,
        ADD CONSTRAINT question_verdict_pointer CHECK(effective_verdict_revision IS NULL OR effective_verdict_revision=verdict_revision);
    CREATE TABLE public.question_verdict_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        question_id uuid NOT NULL, deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        old_revision bigint NOT NULL CHECK(old_revision>0), new_revision bigint NOT NULL CHECK(new_revision=old_revision+1),
        payload jsonb NOT NULL, request_id text NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(question_id,new_revision),
        FOREIGN KEY(tenant_id,student_id,question_id) REFERENCES public.questions(tenant_id,student_id,id)
    );
    ALTER TABLE public.verdict_revisions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.verdict_revisions FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.question_verdict_events ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.question_verdict_events FORCE ROW LEVEL SECURITY;

    ALTER FUNCTION public.read_question(uuid) RENAME TO read_question_ocr;
    CREATE FUNCTION public.read_question(target uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT base.view || jsonb_build_object(
            'verdict_revision',q.verdict_revision,'effective_verdict_revision',q.effective_verdict_revision,
            'effective_revision',q.effective_revision,
            'verdict',COALESCE(v.verdict,base.view->>'verdict'),
            'verdict_provenance',CASE WHEN v.revision IS NOT NULL THEN 'guardian'
                WHEN q.current_revision>1 THEN 'ocr_pending' ELSE 'machine' END,
            'needs_guardian_review',v.revision IS NULL OR v.verdict IN ('pending_review','unsupported'),
            'verdict_history',COALESCE((SELECT jsonb_agg(jsonb_build_object('revision',h.revision,
                'question_revision',h.question_revision,'verdict',h.verdict,'reason',h.reason,
                'provenance',h.provenance,'created_at',h.created_at) ORDER BY h.revision)
                FROM public.verdict_revisions h WHERE h.question_id=q.id),'[]'::jsonb))
        FROM public.questions q LEFT JOIN public.verdict_revisions v
            ON v.question_id=q.id AND v.revision=q.effective_verdict_revision AND v.question_revision=q.current_revision
        CROSS JOIN LATERAL (SELECT public.read_question_ocr(q.id) AS view) base
        WHERE q.id=target AND base.view IS NOT NULL
    $$;
    -- OCR changes invalidate human conclusions; preserve the old immutable history.
    CREATE FUNCTION public.invalidate_question_verdict() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF NEW.current_revision<>OLD.current_revision THEN
            NEW.effective_verdict_revision=NULL;
            NEW.effective_revision=OLD.effective_revision+1;
        END IF;
        RETURN NEW;
    END $$;
    CREATE TRIGGER invalidate_question_verdict BEFORE UPDATE OF current_revision ON public.questions
        FOR EACH ROW EXECUTE FUNCTION public.invalidate_question_verdict();
    CREATE FUNCTION public.emit_verdict_changed() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        INSERT INTO public.question_verdict_events(tenant_id,student_id,question_id,deletion_epoch,
            old_revision,new_revision,request_id,payload)
        VALUES(NEW.tenant_id,NEW.student_id,NEW.id,NEW.deletion_epoch,OLD.effective_revision,NEW.effective_revision,
            COALESCE(NULLIF(current_setting('app.request_id',true),''),'db_'||gen_random_uuid()::text),
            jsonb_build_object('type','VerdictChanged','schema_version',1,'question_id',NEW.id,
                'old_revision',OLD.effective_revision,'new_revision',NEW.effective_revision,
                'question_revision',NEW.current_revision,'verdict_revision',NEW.verdict_revision,
                'deletion_epoch',NEW.deletion_epoch));
        RETURN NEW;
    END $$;
    CREATE TRIGGER emit_verdict_changed AFTER UPDATE ON public.questions
        FOR EACH ROW WHEN (OLD.effective_revision IS DISTINCT FROM NEW.effective_revision)
        EXECUTE FUNCTION public.emit_verdict_changed();

    CREATE FUNCTION public.override_question_verdict(target uuid,expected integer,expected_verdict integer,
        new_verdict text,explanation text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE q public.questions%ROWTYPE; actor uuid;
    BEGIN
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        IF actor IS NULL OR NULLIF(current_setting('app.student_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        PERFORM 1 FROM public.tenants t WHERE t.id=NULLIF(current_setting('app.tenant_id',true),'')::uuid FOR SHARE;
        PERFORM 1 FROM public.memberships m WHERE m.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND m.user_id=actor FOR SHARE;
        SELECT * INTO q FROM public.questions WHERE id=target;
        IF NOT FOUND OR q.tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id',true),'')::uuid THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        PERFORM 1 FROM public.students st WHERE st.id=q.student_id FOR SHARE;
        PERFORM 1 FROM public.student_access a WHERE a.student_id=q.student_id AND a.user_id=actor FOR SHARE;
        SELECT * INTO q FROM public.questions WHERE id=target FOR UPDATE;
        IF public.read_question(target) IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF expected IS NULL OR expected<1 OR expected_verdict IS NULL OR expected_verdict<0
            OR new_verdict IS NULL OR new_verdict NOT IN ('correct','incorrect','pending_review','unanswered','unsupported')
            OR explanation IS NULL OR length(explanation) NOT BETWEEN 1 AND 1000
            OR explanation !~ '[^[:space:]]' THEN
            RETURN jsonb_build_object('error','invalid_override'); END IF;
        IF q.current_revision<>expected OR q.verdict_revision<>expected_verdict THEN
            RETURN jsonb_build_object('error','revision_conflict'); END IF;
        INSERT INTO public.verdict_revisions(question_id,tenant_id,student_id,revision,question_revision,verdict,reason,actor_id)
        VALUES(q.id,q.tenant_id,q.student_id,q.verdict_revision+1,q.current_revision,new_verdict,explanation,actor);
        UPDATE public.questions SET verdict_revision=q.verdict_revision+1,effective_verdict_revision=q.verdict_revision+1,
            effective_revision=q.effective_revision+1 WHERE id=target;
        RETURN public.read_question(target);
    END $$;
    REVOKE ALL ON FUNCTION public.read_question(uuid),public.invalidate_question_verdict(),public.emit_verdict_changed(),
        public.override_question_verdict(uuid,integer,integer,text,text) FROM PUBLIC;
    ''')
    op.execute(f'''REVOKE ALL ON FUNCTION public.read_question_ocr(uuid) FROM {app};
        GRANT EXECUTE ON FUNCTION public.read_question(uuid),public.override_question_verdict(uuid,integer,integer,text,text) TO {app}''')


def downgrade():
    op.execute('''
    DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.verdict_revisions) THEN
            RAISE EXCEPTION 'Cannot discard guardian verdict revision history';
        END IF;
        IF EXISTS(SELECT 1 FROM public.question_verdict_events) THEN
            RAISE EXCEPTION 'Cannot discard guardian OCR revision history or verdict change events';
        END IF;
    END $$;
    DROP FUNCTION public.override_question_verdict(uuid,integer,integer,text,text);
    DROP TRIGGER emit_verdict_changed ON public.questions;
    DROP FUNCTION public.emit_verdict_changed();
    DROP TRIGGER invalidate_question_verdict ON public.questions;
    DROP FUNCTION public.invalidate_question_verdict();
    DROP FUNCTION public.read_question(uuid);
    ALTER FUNCTION public.read_question_ocr(uuid) RENAME TO read_question;
    DROP TABLE public.question_verdict_events;
    ALTER TABLE public.questions DROP CONSTRAINT question_effective_verdict,
        DROP CONSTRAINT question_verdict_pointer,
        DROP COLUMN verdict_revision,DROP COLUMN effective_verdict_revision,DROP COLUMN effective_revision;
    DROP TABLE public.verdict_revisions;
    ''')
    app = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    app = op.get_bind().dialect.identifier_preparer.quote(app)
    op.execute(f'GRANT EXECUTE ON FUNCTION public.read_question(uuid) TO {app}')
