"""Materialize immutable machine questions and guardian OCR revisions."""
import os

from alembic import op
from sqlalchemy import text

revision = "0009_question_revisions"
down_revision = "0008_result_reader"


def upgrade():
    bind = op.get_bind()
    config = op.get_context().config
    app = config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    if not bind.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
        raise RuntimeError("Question migration requires an offline RLS-bypassing owner")
    safe = bind.execute(text("""SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role"""), {"role": app}).scalar_one_or_none()
    if not safe:
        raise RuntimeError("A restricted API role is required")
    app = bind.dialect.identifier_preparer.quote(app)
    op.execute("""
    ALTER TABLE public.grading_results ADD CONSTRAINT result_question_scope
        UNIQUE(tenant_id,student_id,submission_id,id);
    CREATE TABLE public.questions (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        submission_id uuid NOT NULL, result_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        page_position integer NOT NULL CHECK(page_position BETWEEN 1 AND 9),
        ordinal integer NOT NULL CHECK(ordinal>0),
        current_revision integer NOT NULL CHECK(current_revision>0),
        machine_question jsonb NOT NULL,
        UNIQUE(result_id,page_position,ordinal), UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,submission_id,result_id)
            REFERENCES public.grading_results(tenant_id,student_id,submission_id,id)
    );
    CREATE INDEX questions_submission ON public.questions(submission_id,page_position,ordinal);
    CREATE TABLE public.question_revisions (
        question_id uuid NOT NULL, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        revision integer NOT NULL CHECK(revision>0),
        stem text NOT NULL CHECK(length(stem) BETWEEN 1 AND 8000),
        student_answer_raw text NOT NULL CHECK(length(student_answer_raw)<=8000),
        origin text NOT NULL CHECK(origin IN ('machine','guardian')),
        actor_id uuid REFERENCES public.users(id), reason text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(question_id,revision),
        FOREIGN KEY(tenant_id,student_id,question_id) REFERENCES public.questions(tenant_id,student_id,id),
        CHECK((revision=1 AND origin='machine' AND actor_id IS NULL AND reason IS NULL)
            OR (revision>1 AND origin='guardian' AND actor_id IS NOT NULL AND length(btrim(reason)) BETWEEN 1 AND 1000))
    );
    ALTER TABLE public.questions ADD CONSTRAINT question_current_revision
        FOREIGN KEY(id,current_revision) REFERENCES public.question_revisions(question_id,revision)
        DEFERRABLE INITIALLY DEFERRED;
    ALTER TABLE public.questions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.questions FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.question_revisions ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.question_revisions FORCE ROW LEVEL SECURITY;
    -- No runtime table privileges or permissive policies. Writes are narrow
    -- SECURITY DEFINER capabilities, not client-selected tenant/actor columns.
    CREATE FUNCTION public.seed_result_questions(r public.grading_results) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p jsonb; q jsonb; identifier uuid;
    BEGIN
        FOR p IN SELECT value FROM jsonb_array_elements(r.payload->'pages') WHERE value->>'status'='succeeded' LOOP
            FOR q IN SELECT value FROM jsonb_array_elements(p->'questions') LOOP
                identifier=gen_random_uuid();
                INSERT INTO public.questions(id,tenant_id,student_id,submission_id,result_id,deletion_epoch,
                    page_position,ordinal,current_revision,machine_question)
                VALUES(identifier,r.tenant_id,r.student_id,r.submission_id,r.id,r.deletion_epoch,
                    (p->>'position')::integer,(q->>'ordinal')::integer,1,q);
                INSERT INTO public.question_revisions(question_id,tenant_id,student_id,revision,stem,student_answer_raw,origin,created_at)
                VALUES(identifier,r.tenant_id,r.student_id,1,q->>'stem',q->>'student_answer_raw','machine',r.created_at);
            END LOOP;
        END LOOP;
    END $$;
    CREATE FUNCTION public.seed_inserted_questions() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        PERFORM public.seed_result_questions(NEW);
        RETURN NEW;
    END $$;
    CREATE TRIGGER result_questions AFTER INSERT ON public.grading_results
        FOR EACH ROW EXECUTE FUNCTION public.seed_inserted_questions();
    DO $$ DECLARE r public.grading_results%ROWTYPE;
    BEGIN
        FOR r IN SELECT * FROM public.grading_results LOOP
            PERFORM public.seed_result_questions(r);
        END LOOP;
    END $$;

    -- Expose only the enclosing resource ID, so the API can apply its existing
    -- live consent/epoch checks before materializing any question text.
    CREATE FUNCTION public.question_submission(target uuid) RETURNS uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT q.submission_id FROM public.questions q
        WHERE q.id=target AND q.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND ((NULLIF(current_setting('app.user_id',true),'') IS NULL
                    AND q.student_id=NULLIF(current_setting('app.student_id',true),'')::uuid)
                OR (NULLIF(current_setting('app.student_id',true),'') IS NULL AND EXISTS(
                    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id AND u.status='active'
                    WHERE m.tenant_id=q.tenant_id AND m.user_id=NULLIF(current_setting('app.user_id',true),'')::uuid
                        AND m.status='active' AND (m.role='owner' OR EXISTS(
                            SELECT 1 FROM public.student_access a WHERE a.tenant_id=q.tenant_id
                                AND a.student_id=q.student_id AND a.user_id=m.user_id)))))
    $$;
    CREATE FUNCTION public.read_question(target uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('question_id',q.id,'submission_id',q.submission_id,'student_id',q.student_id,
            'source_revision',r.source_revision,'page_position',q.page_position,'ordinal',q.ordinal,
            'revision',q.current_revision,'stem',v.stem,'student_answer_raw',v.student_answer_raw,
            'verdict',CASE WHEN q.current_revision>1 THEN 'pending_review' ELSE q.machine_question->>'verdict' END,
            'needs_guardian_review',true,'machine_question',q.machine_question,
            'history',(SELECT jsonb_agg(jsonb_build_object('revision',h.revision,'stem',h.stem,
                'student_answer_raw',h.student_answer_raw,'origin',h.origin,'reason',h.reason,'created_at',h.created_at)
                ORDER BY h.revision) FROM public.question_revisions h WHERE h.question_id=q.id))
        FROM public.questions q JOIN public.question_revisions v ON v.question_id=q.id AND v.revision=q.current_revision
        JOIN public.grading_results r ON r.id=q.result_id AND r.deletion_epoch=q.deletion_epoch
        WHERE q.id=target AND public.read_grading_result(q.submission_id) IS NOT NULL
    $$;
    CREATE FUNCTION public.read_submission_questions(target uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT CASE WHEN public.read_grading_result(target) IS NOT NULL THEN
            jsonb_build_object('submission_id',target,'questions',COALESCE((SELECT jsonb_agg(public.read_question(q.id)
                ORDER BY q.page_position,q.ordinal) FROM public.questions q WHERE q.submission_id=target),'[]'::jsonb)) END
    $$;
    CREATE FUNCTION public.correct_question_ocr(target uuid,expected integer,new_stem text,new_answer text,explanation text)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE q public.questions%ROWTYPE; actor uuid; result jsonb;
    BEGIN
        actor=NULLIF(current_setting('app.user_id',true),'')::uuid;
        IF actor IS NULL OR NULLIF(current_setting('app.student_id',true),'') IS NOT NULL THEN
            RETURN jsonb_build_object('error','not_found'); END IF;
        -- Lock in the same tenant -> membership -> student -> question order as
        -- authenticated requests and consent withdrawal. Recheck after waiting.
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
        IF expected IS NULL OR expected<1 OR new_stem IS NULL OR length(btrim(new_stem)) NOT BETWEEN 1 AND 8000
            OR new_answer IS NULL OR length(new_answer)>8000 OR explanation IS NULL
            OR length(btrim(explanation)) NOT BETWEEN 1 AND 1000 THEN
            RETURN jsonb_build_object('error','invalid_correction'); END IF;
        IF q.current_revision<>expected THEN RETURN jsonb_build_object('error','revision_conflict'); END IF;
        INSERT INTO public.question_revisions(question_id,tenant_id,student_id,revision,stem,student_answer_raw,origin,actor_id,reason)
        VALUES(q.id,q.tenant_id,q.student_id,expected+1,new_stem,new_answer,'guardian',actor,explanation);
        UPDATE public.questions SET current_revision=expected+1 WHERE id=target;
        result=public.read_question(target);
        RETURN result;
    END $$;
    REVOKE ALL ON FUNCTION public.seed_result_questions(public.grading_results),public.seed_inserted_questions(),
        public.question_submission(uuid),public.read_question(uuid),public.read_submission_questions(uuid),public.correct_question_ocr(uuid,integer,text,text,text) FROM PUBLIC;
    """)
    op.execute(f"""GRANT EXECUTE ON FUNCTION public.question_submission(uuid),public.read_question(uuid),public.read_submission_questions(uuid),
        public.correct_question_ocr(uuid,integer,text,text,text) TO {app}""")


def downgrade():
    op.execute("""
    DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.question_revisions WHERE revision>1) THEN
            RAISE EXCEPTION 'Cannot discard guardian OCR revision history';
        END IF;
    END $$;
    DROP FUNCTION public.correct_question_ocr(uuid,integer,text,text,text);
    DROP FUNCTION public.read_submission_questions(uuid);
    DROP FUNCTION public.read_question(uuid);
    DROP FUNCTION public.question_submission(uuid);
    DROP TRIGGER result_questions ON public.grading_results;
    DROP FUNCTION public.seed_inserted_questions();
    DROP FUNCTION public.seed_result_questions(public.grading_results);
    ALTER TABLE public.questions DROP CONSTRAINT question_current_revision;
    DROP TABLE public.question_revisions;
    DROP TABLE public.questions;
    ALTER TABLE public.grading_results DROP CONSTRAINT result_question_scope;
    """)
