"""Durable, idempotent verdict derivation and live-authorized mistake reads."""
import os

from alembic import op
from sqlalchemy import text

revision = '0011_mistake_projection'
down_revision = '0010_verdict_revisions'


def upgrade():
    bind = op.get_bind()
    config = op.get_context().config
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Mistake migration requires an offline RLS-bypassing owner')
    roles = {}
    for name, default in [('app', 'wizzy_app'), ('worker', 'wizzy_app_worker')]:
        role = config.attributes.get(f'{name}_role') or os.getenv(f'WIZZY_{name.upper()}_ROLE', default)
        safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
            rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
            FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
        if not safe:
            raise RuntimeError(f'A restricted {name} role is required')
        roles[name] = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    CREATE TABLE public.question_derivation_receipts (
        event_id uuid PRIMARY KEY REFERENCES public.question_verdict_events(id),
        question_id uuid NOT NULL REFERENCES public.questions(id),
        new_revision bigint NOT NULL CHECK(new_revision>1),
        state text NOT NULL CHECK(state IN ('applied','obsolete','invalidated')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(question_id,new_revision)
    );
    CREATE TABLE public.mistake_projections (
        question_id uuid PRIMARY KEY REFERENCES public.questions(id),
        effective_revision bigint NOT NULL CHECK(effective_revision>1),
        event_id uuid NOT NULL REFERENCES public.question_derivation_receipts(event_id)
    );
    ALTER TABLE public.question_derivation_receipts ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.question_derivation_receipts FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.mistake_projections ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.mistake_projections FORCE ROW LEVEL SECURITY;
    CREATE INDEX question_verdict_events_delivery ON public.question_verdict_events(created_at,id);
    CREATE INDEX questions_student_mistakes ON public.questions(student_id,submission_id);

    CREATE FUNCTION public.consume_question_derivation() RETURNS text
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE ev public.question_verdict_events%ROWTYPE; q public.questions%ROWTYPE;
        outcome text; live boolean;
    BEGIN
        SELECT e.* INTO ev FROM public.question_verdict_events e
        WHERE NOT EXISTS(SELECT 1 FROM public.question_derivation_receipts d WHERE d.event_id=e.id)
        ORDER BY e.created_at,e.id FOR UPDATE OF e SKIP LOCKED LIMIT 1;
        IF NOT FOUND THEN RETURN 'idle'; END IF;
        -- Same lock order as request writers and consent withdrawal.
        PERFORM 1 FROM public.tenants WHERE id=ev.tenant_id FOR SHARE;
        PERFORM 1 FROM public.students WHERE id=ev.student_id FOR SHARE;
        SELECT * INTO q FROM public.questions WHERE id=ev.question_id FOR UPDATE;
        SELECT EXISTS(
            SELECT 1 FROM public.grading_results r
            JOIN public.submissions s ON s.id=r.submission_id AND s.tenant_id=r.tenant_id
                AND s.student_id=r.student_id AND s.source_revision=r.source_revision
                AND s.deletion_epoch=r.deletion_epoch
            JOIN public.jobs j ON j.id=r.job_id AND j.tenant_id=r.tenant_id AND j.student_id=r.student_id
                AND j.submission_id=s.id AND j.deletion_epoch=r.deletion_epoch
            JOIN public.execution_receipts e ON e.event_id=r.event_id AND e.job_id=r.job_id
                AND e.tenant_id=r.tenant_id AND e.student_id=r.student_id AND e.submission_id=s.id
                AND e.source_revision=r.source_revision AND e.deletion_epoch=r.deletion_epoch AND e.state='settled'
            JOIN public.students st ON st.id=r.student_id AND st.tenant_id=r.tenant_id
                AND st.status='active' AND st.consent_active AND st.deletion_epoch=r.deletion_epoch
            JOIN public.tenants t ON t.id=r.tenant_id AND t.status='active'
            WHERE r.id=q.result_id AND r.submission_id=q.submission_id AND r.tenant_id=ev.tenant_id
                AND r.student_id=ev.student_id AND r.deletion_epoch=ev.deletion_epoch
                AND q.deletion_epoch=ev.deletion_epoch
                AND s.status IN ('completed','partial') AND j.status='succeeded' AND r.successful_pages>0
        ) INTO live;
        IF NOT live THEN
            outcome='invalidated';
            DELETE FROM public.mistake_projections WHERE question_id=q.id;
        ELSIF ev.new_revision<>q.effective_revision THEN
            outcome='obsolete';
        ELSE
            outcome='applied';
        END IF;
        INSERT INTO public.question_derivation_receipts(event_id,question_id,new_revision,state)
            VALUES(ev.id,q.id,ev.new_revision,outcome);
        IF outcome='applied' THEN
            IF EXISTS(SELECT 1 FROM public.verdict_revisions v WHERE v.question_id=q.id
                AND v.revision=q.effective_verdict_revision AND v.question_revision=q.current_revision
                AND v.verdict='incorrect' AND v.provenance='guardian') THEN
                INSERT INTO public.mistake_projections(question_id,effective_revision,event_id)
                    VALUES(q.id,q.effective_revision,ev.id)
                ON CONFLICT(question_id) DO UPDATE SET effective_revision=EXCLUDED.effective_revision,event_id=EXCLUDED.event_id;
            ELSE
                DELETE FROM public.mistake_projections WHERE question_id=q.id;
            END IF;
        END IF;
        RETURN outcome;
    END $$;

    CREATE FUNCTION public.read_student_mistakes(target uuid,max_rows integer,after_date timestamptz,after_id uuid)
    RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        WITH sources AS MATERIALIZED (
            SELECT s.id FROM public.submissions s WHERE s.student_id=target
                AND public.read_grading_result(s.id) IS NOT NULL
        ), visible AS MATERIALIZED (
            SELECT q.*,r.created_at,r.source_revision FROM public.questions q
            JOIN sources s ON s.id=q.submission_id
            JOIN public.grading_results r ON r.id=q.result_id AND r.submission_id=s.id
                AND r.deletion_epoch=q.deletion_epoch
            WHERE q.student_id=target
        ), entries AS (
            SELECT jsonb_build_object('question_id',q.id,'submission_id',q.submission_id,
                'student_id',q.student_id,'source_revision',q.source_revision,'page_position',q.page_position,
                'ordinal',q.ordinal,'revision',q.current_revision,'effective_revision',q.effective_revision,
                'stem',qr.stem,'student_answer_raw',qr.student_answer_raw,'verdict',v.verdict,
                'verdict_provenance',v.provenance,'created_at',q.created_at) AS item,q.created_at,q.id
            FROM visible q JOIN public.mistake_projections p ON p.question_id=q.id
                AND p.effective_revision=q.effective_revision
            JOIN public.question_revisions qr ON qr.question_id=q.id AND qr.revision=q.current_revision
            JOIN public.verdict_revisions v ON v.question_id=q.id AND v.revision=q.effective_verdict_revision
                AND v.question_revision=q.current_revision AND v.verdict='incorrect' AND v.provenance='guardian'
            WHERE max_rows BETWEEN 1 AND 100
                AND ((after_date IS NULL AND after_id IS NULL) OR (q.created_at,q.id)>(after_date,after_id))
            ORDER BY q.created_at,q.id LIMIT LEAST(GREATEST(max_rows,0),100)+1
        )
        SELECT jsonb_build_object('items',COALESCE((SELECT jsonb_agg(item ORDER BY created_at,id) FROM entries),'[]'::jsonb),
            'pending_updates',(SELECT count(*) FROM visible q WHERE q.effective_revision>1
                AND NOT EXISTS(SELECT 1 FROM public.question_derivation_receipts d
                    WHERE d.question_id=q.id AND d.new_revision=q.effective_revision)))
    $$;
    REVOKE ALL ON FUNCTION public.consume_question_derivation(),
        public.read_student_mistakes(uuid,integer,timestamptz,uuid) FROM PUBLIC;
    ''')
    op.execute(f'''GRANT EXECUTE ON FUNCTION public.consume_question_derivation() TO {roles['worker']};
        GRANT EXECUTE ON FUNCTION public.read_student_mistakes(uuid,integer,timestamptz,uuid) TO {roles['app']}''')


def downgrade():
    op.execute('''
    DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM public.question_derivation_receipts)
            OR EXISTS(SELECT 1 FROM public.mistake_projections) THEN
            RAISE EXCEPTION 'Cannot discard processed question derivations';
        END IF;
    END $$;
    DROP FUNCTION public.read_student_mistakes(uuid,integer,timestamptz,uuid);
    DROP FUNCTION public.consume_question_derivation();
    DROP INDEX public.questions_student_mistakes;
    DROP INDEX public.question_verdict_events_delivery;
    DROP TABLE public.mistake_projections;
    DROP TABLE public.question_derivation_receipts;
    ''')
