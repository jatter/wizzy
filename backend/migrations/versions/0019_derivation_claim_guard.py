"""Atomically claim derivation receipts despite stale candidate snapshots."""
from alembic import op
from sqlalchemy import text

revision = '0019_derivation_claim_guard'
down_revision = '0018_model_catalog'


CONSUMER = r'''
CREATE OR REPLACE FUNCTION public.consume_question_derivation() RETURNS text
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
        ELSIF ev.new_revision<>q.effective_revision THEN
            outcome='obsolete';
        ELSE
            outcome='applied';
        END IF;
        INSERT INTO public.question_derivation_receipts(event_id,question_id,new_revision,state)
            VALUES(ev.id,q.id,ev.new_revision,outcome)
            ON CONFLICT(event_id) DO NOTHING;
        -- A candidate can outlive the snapshot that found it, even with SKIP LOCKED.
        -- Only the transaction that inserts the receipt may mutate the projection.
        IF NOT FOUND THEN RETURN 'idle'; END IF;
        IF outcome='invalidated' THEN
            DELETE FROM public.mistake_projections WHERE question_id=q.id;
        ELSIF outcome='applied' THEN
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
'''


def upgrade():
    if not op.get_bind().execute(text(
        'SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user'
    )).scalar_one():
        raise RuntimeError('Derivation claim migration requires an offline RLS-bypassing owner')
    # CREATE OR REPLACE preserves the worker-only EXECUTE grant and private tables.
    op.execute(CONSUMER)


def downgrade():
    # Restore the historical function without deleting receipts or projections.
    historical = CONSUMER.replace(
        "VALUES(ev.id,q.id,ev.new_revision,outcome)\n"
        "            ON CONFLICT(event_id) DO NOTHING;\n"
        "        -- A candidate can outlive the snapshot that found it, even with SKIP LOCKED.\n"
        "        -- Only the transaction that inserts the receipt may mutate the projection.\n"
        "        IF NOT FOUND THEN RETURN 'idle'; END IF;",
        'VALUES(ev.id,q.id,ev.new_revision,outcome);',
    ).replace(
        "        IF outcome='invalidated' THEN\n"
        "            DELETE FROM public.mistake_projections WHERE question_id=q.id;\n"
        "        ELSIF outcome='applied' THEN",
        "        IF outcome='applied' THEN",
    ).replace(
        "            outcome='invalidated';",
        "            outcome='invalidated';\n"
        "            DELETE FROM public.mistake_projections WHERE question_id=q.id;",
    )
    op.execute(historical)
