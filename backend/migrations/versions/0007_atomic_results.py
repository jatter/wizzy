"""Persist conservative results and successful-page quota in one fenced commit."""
import os

from alembic import op
from sqlalchemy import text

revision = "0007_atomic_results"
down_revision = "0006_provider_call_fences"


def worker_role():
    config = op.get_context().config
    app = config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    worker = config.attributes.get("worker_role") or os.getenv("WIZZY_WORKER_ROLE", app + "_worker")
    bind = op.get_bind()
    if not bind.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
        raise RuntimeError("Result settlement requires an offline RLS-bypassing migration owner")
    safe = bind.execute(text("""SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role"""), {"role": worker}).scalar_one_or_none()
    if not safe or app == worker:
        raise RuntimeError("A separate restricted worker role is required")
    return bind.dialect.identifier_preparer.quote(worker)


def upgrade():
    worker = worker_role()
    op.execute("""
    ALTER TABLE public.quota_reservations ADD COLUMN consumed_pages integer NOT NULL DEFAULT 0;
    UPDATE public.quota_reservations SET consumed_pages=pages WHERE status='consumed';
    ALTER TABLE public.quota_reservations ADD CONSTRAINT quota_consumed_pages CHECK(
        consumed_pages BETWEEN 0 AND pages AND (status='consumed')=(consumed_pages>0));
    ALTER TABLE public.execution_receipts DROP CONSTRAINT execution_receipts_state_check;
    ALTER TABLE public.execution_receipts ADD CONSTRAINT execution_receipts_state_check
        CHECK(state IN ('running','retry_wait','deadletter','settled'));
    ALTER TABLE public.outbox_events DROP CONSTRAINT outbox_events_event_type_check;
    ALTER TABLE public.outbox_events ADD CONSTRAINT outbox_events_event_type_check
        CHECK(event_type IN ('submission.queued','submission.processed'));

    -- Private shape validators: runtime roles cannot bypass the Python contract by
    -- invoking the write function with extra fields or fabricated auto-verdicts.
    CREATE FUNCTION public.result_keys(v jsonb,keys text[]) RETURNS boolean
    LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
        SELECT COALESCE(jsonb_typeof(v)='object' AND v ?& keys AND v-keys='{}'::jsonb,false)
    $$;
    CREATE FUNCTION public.result_box(v jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE n jsonb;
    BEGIN
        IF jsonb_typeof(v) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(v)<>4 THEN RETURN false; END IF;
        FOR n IN SELECT value FROM jsonb_array_elements(v) LOOP
            IF jsonb_typeof(n)<>'number' THEN RETURN false; END IF;
            IF n::numeric<0 OR n::numeric>1 THEN RETURN false; END IF;
        END LOOP;
        RETURN (v->>0)::numeric<(v->>2)::numeric AND (v->>1)::numeric<(v->>3)::numeric;
    END;
    $$;
    CREATE FUNCTION public.valid_conservative_result(doc jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p jsonb; q jsonb; e jsonb; x jsonb; v text; positions integer[]='{}';
            ordinals integer[]; total integer; count_q integer;
    BEGIN
        IF NOT public.result_keys(doc,ARRAY['schema_version','prompt_hash','rule_version',
            'eval_set_version','image_pipeline_version','pages']) THEN RETURN false; END IF;
        IF jsonb_typeof(doc->'schema_version')<>'string' OR doc->>'schema_version'<>'1.0-conservative'
            OR jsonb_typeof(doc->'prompt_hash')<>'string'
            OR (doc->>'prompt_hash') !~ '^[0-9a-f]{64}$' THEN RETURN false; END IF;
        FOREACH v IN ARRAY ARRAY['rule_version','eval_set_version','image_pipeline_version'] LOOP
            IF jsonb_typeof(doc->v)<>'string' OR length(doc->>v) NOT BETWEEN 1 AND 128
                OR (doc->>v) !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]*$' THEN RETURN false; END IF;
        END LOOP;
        IF jsonb_typeof(doc->'pages')<>'array' THEN RETURN false; END IF;
        total=jsonb_array_length(doc->'pages');
        IF total NOT BETWEEN 1 AND 9 THEN RETURN false; END IF;
        FOR p IN SELECT value FROM jsonb_array_elements(doc->'pages') LOOP
            IF jsonb_typeof(p)<>'object' OR jsonb_typeof(p->'position') IS DISTINCT FROM 'number'
                OR (p->>'position') !~ '^[1-9]$' THEN RETURN false; END IF;
            IF (p->>'position')::integer>total OR (p->>'position')::integer=ANY(positions) THEN RETURN false; END IF;
            positions=array_append(positions,(p->>'position')::integer);
            IF p->>'status'='failed' THEN
                IF NOT public.result_keys(p,ARRAY['position','status','error_code'])
                    OR p->>'error_code' NOT IN ('invalid_model_output','unsupported_input','provider_failed')
                    OR jsonb_typeof(p->'error_code')<>'string' THEN RETURN false; END IF;
                CONTINUE;
            END IF;
            IF NOT public.result_keys(p,ARRAY['position','status','model_run_id','questions'])
                OR jsonb_typeof(p->'status')<>'string' OR p->>'status'<>'succeeded' OR jsonb_typeof(p->'model_run_id')<>'string'
                OR (p->>'model_run_id') !~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                OR jsonb_typeof(p->'questions')<>'array' THEN RETURN false; END IF;
            count_q=jsonb_array_length(p->'questions');
            IF count_q NOT BETWEEN 1 AND 100 THEN RETURN false; END IF;
            ordinals='{}';
            FOR q IN SELECT value FROM jsonb_array_elements(p->'questions') LOOP
                IF NOT public.result_keys(q,ARRAY['ordinal','question_type','stem','student_answer_raw',
                    'bbox','ocr_confidence','verdict','reason_codes','needs_guardian_review','evidence']) THEN RETURN false; END IF;
                IF jsonb_typeof(q->'ordinal')<>'number' OR (q->>'ordinal') !~ '^([1-9][0-9]?|100)$'
                    THEN RETURN false; END IF;
                IF (q->>'ordinal')::integer>count_q OR (q->>'ordinal')::integer=ANY(ordinals) THEN RETURN false; END IF;
                ordinals=array_append(ordinals,(q->>'ordinal')::integer);
                IF jsonb_typeof(q->'question_type')<>'string' OR jsonb_typeof(q->'verdict')<>'string'
                    OR q->>'question_type' NOT IN ('integer_arithmetic','numeric_fill','comparison','unsupported')
                    OR q->>'verdict' NOT IN ('pending_review','unsupported')
                    OR q->'needs_guardian_review'<>'true'::jsonb
                    OR jsonb_typeof(q->'stem')<>'string' OR length(q->>'stem') NOT BETWEEN 1 AND 8000
                    OR jsonb_typeof(q->'student_answer_raw')<>'string' OR length(q->>'student_answer_raw')>8000
                    OR NOT public.result_box(q->'bbox') THEN RETURN false; END IF;
                IF q->'ocr_confidence'<>'null'::jsonb THEN
                    IF jsonb_typeof(q->'ocr_confidence')<>'number' THEN RETURN false; END IF;
                    IF (q->>'ocr_confidence')::numeric<0 OR (q->>'ocr_confidence')::numeric>1 THEN RETURN false; END IF;
                END IF;
                IF jsonb_typeof(q->'reason_codes')<>'array' OR jsonb_typeof(q->'evidence')<>'array' THEN RETURN false; END IF;
                IF jsonb_array_length(q->'reason_codes') NOT BETWEEN 1 AND 5
                    OR jsonb_array_length(q->'evidence') NOT BETWEEN 1 AND 20 THEN RETURN false; END IF;
                FOR x IN SELECT value FROM jsonb_array_elements(q->'reason_codes') LOOP
                    IF jsonb_typeof(x)<>'string' OR x #>> '{}' NOT IN ('UNCALIBRATED_GATE','UNSUPPORTED_TYPE',
                        'UNCLEAR_INPUT','MISSING_CONTEXT','CONFLICTING_EVIDENCE') THEN RETURN false; END IF;
                END LOOP;
                IF (SELECT count(DISTINCT value) FROM jsonb_array_elements(q->'reason_codes'))<>jsonb_array_length(q->'reason_codes')
                    OR (q->>'verdict'='unsupported' AND NOT q->'reason_codes' ? 'UNSUPPORTED_TYPE') THEN RETURN false; END IF;
                FOR e IN SELECT value FROM jsonb_array_elements(q->'evidence') LOOP
                    IF NOT public.result_keys(e,ARRAY['kind','page','bbox'])
                        OR jsonb_typeof(e->'kind')<>'string' OR jsonb_typeof(e->'page')<>'number'
                        OR (e->>'page') !~ '^[1-9]$' OR e->>'kind' NOT IN ('answer_region','question_region')
                        OR e->'page'<>p->'position' OR NOT public.result_box(e->'bbox') THEN RETURN false; END IF;
                END LOOP;
            END LOOP;
        END LOOP;
        RETURN true;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.result_keys(jsonb,text[]),public.result_box(jsonb),
        public.valid_conservative_result(jsonb) FROM PUBLIC;

    CREATE TABLE public.grading_results (
        id uuid PRIMARY KEY, event_id uuid NOT NULL UNIQUE REFERENCES public.execution_receipts(event_id),
        job_id uuid NOT NULL, tenant_id uuid NOT NULL, student_id uuid NOT NULL, submission_id uuid NOT NULL,
        source_revision integer NOT NULL CHECK(source_revision>0), deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        execution_token uuid NOT NULL, payload jsonb NOT NULL CHECK(public.valid_conservative_result(payload)),
        successful_pages integer NOT NULL CHECK(successful_pages BETWEEN 0 AND 9),
        failed_pages integer NOT NULL CHECK(failed_pages BETWEEN 0 AND 9),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(job_id,source_revision,deletion_epoch),
        CHECK(successful_pages+failed_pages BETWEEN 1 AND 9),
        FOREIGN KEY(tenant_id,job_id) REFERENCES public.jobs(tenant_id,id),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES public.submissions(tenant_id,student_id,id)
    );
    ALTER TABLE public.grading_results ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.grading_results FORCE ROW LEVEL SECURITY;

    CREATE FUNCTION public.complete_execution(event uuid,job uuid,version integer,epoch bigint,token uuid,doc jsonb)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE c record; r public.execution_receipts%ROWTYPE; q public.quota_reservations%ROWTYPE;
            a public.quota_accounts%ROWTYPE; saved public.grading_results%ROWTYPE;
            p jsonb; total integer; successes integer=0; final_status text;
    BEGIN
        IF event IS NULL OR job IS NULL OR version IS NULL OR version<1 OR epoch IS NULL OR epoch<0
            OR token IS NULL OR NOT public.valid_conservative_result(doc) THEN RETURN false; END IF;
        SELECT * INTO c FROM public.lock_execution_context(event);
        IF NOT FOUND OR c.job_id<>job OR c.source_revision<>version OR c.deletion_epoch<>epoch THEN RETURN false; END IF;
        SELECT * INTO r FROM public.execution_receipts x WHERE x.event_id=event FOR UPDATE;
        IF NOT FOUND OR r.job_id<>job OR r.tenant_id<>c.tenant_id OR r.student_id<>c.student_id
            OR r.submission_id<>c.submission_id OR r.source_revision<>version OR r.deletion_epoch<>epoch THEN RETURN false; END IF;
        IF r.state='settled' THEN
            SELECT * INTO saved FROM public.grading_results x WHERE x.event_id=event;
            RETURN FOUND AND saved.execution_token=token AND saved.payload=doc;
        END IF;
        IF c.job_status<>'running' OR c.submission_status<>'processing' OR r.state<>'running'
            OR r.lease_token IS DISTINCT FROM token OR r.lease_until<=clock_timestamp() THEN RETURN false; END IF;
        -- Serialize terminal accounting, without a provider/network operation in
        -- this transaction. Unknown cost is not zero; it requires reconciliation.
        PERFORM 1 FROM public.model_runs m WHERE m.event_id=event ORDER BY m.id FOR SHARE;
        IF NOT FOUND OR EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=event AND (
            m.outcome='in_flight' OR m.billing_state<>'known' OR m.job_id<>job OR m.tenant_id<>c.tenant_id
            OR m.student_id<>c.student_id OR m.submission_id<>c.submission_id OR m.source_revision<>version
            OR m.deletion_epoch<>epoch OR m.execution_token<>token)) THEN RETURN false; END IF;
        total=jsonb_array_length(doc->'pages');
        IF total<>(SELECT count(*) FROM public.submission_pages x WHERE x.tenant_id=c.tenant_id
            AND x.student_id=c.student_id AND x.submission_id=c.submission_id) THEN RETURN false; END IF;
        FOR p IN SELECT value FROM jsonb_array_elements(doc->'pages') LOOP
            IF NOT EXISTS(SELECT 1 FROM public.submission_pages x WHERE x.tenant_id=c.tenant_id
                AND x.submission_id=c.submission_id AND x.position=(p->>'position')::integer) THEN RETURN false; END IF;
            IF p->>'status'='succeeded' THEN
                IF NOT EXISTS(SELECT 1 FROM public.model_runs m WHERE m.id=(p->>'model_run_id')::uuid
                    AND m.event_id=event AND m.stage IN ('structure','verify') AND m.outcome='succeeded'
                    AND m.billing_state='known') THEN RETURN false; END IF;
                successes=successes+1;
            END IF;
        END LOOP;
        SELECT * INTO q FROM public.quota_reservations x WHERE x.tenant_id=c.tenant_id
            AND x.student_id=c.student_id AND x.submission_id=c.submission_id FOR UPDATE;
        IF NOT FOUND OR q.status<>'reserved' OR q.pages<>total THEN RETURN false; END IF;
        SELECT * INTO a FROM public.quota_accounts x WHERE x.tenant_id=q.tenant_id AND x.period_start=q.period_start FOR UPDATE;
        IF NOT FOUND OR a.reserved_pages<q.pages THEN RETURN false; END IF;
        -- A quota/accounting lock wait must not allow an expired lease to commit.
        IF r.lease_until<=clock_timestamp() THEN RETURN false; END IF;
        final_status=CASE WHEN successes=total THEN 'completed' WHEN successes=0 THEN 'failed' ELSE 'partial' END;
        INSERT INTO public.grading_results(id,event_id,job_id,tenant_id,student_id,submission_id,
            source_revision,deletion_epoch,execution_token,payload,successful_pages,failed_pages)
            VALUES(gen_random_uuid(),event,job,c.tenant_id,c.student_id,c.submission_id,version,epoch,token,doc,successes,total-successes);
        UPDATE public.quota_accounts SET reserved_pages=reserved_pages-q.pages,used_pages=used_pages+successes
            WHERE tenant_id=q.tenant_id AND period_start=q.period_start;
        UPDATE public.quota_reservations SET status=CASE WHEN successes=0 THEN 'released' ELSE 'consumed' END,
            consumed_pages=successes,settled_at=clock_timestamp() WHERE id=q.id;
        UPDATE public.jobs SET status=CASE WHEN successes=0 THEN 'failed' ELSE 'succeeded' END,
            error_code=CASE WHEN successes=0 THEN 'all_pages_failed' ELSE NULL END,finished_at=clock_timestamp() WHERE id=job;
        UPDATE public.submissions SET status=final_status WHERE id=c.submission_id;
        UPDATE public.execution_receipts SET state='settled',lease_token=NULL,lease_until=NULL,retry_at=NULL,
            updated_at=clock_timestamp() WHERE event_id=event;
        INSERT INTO public.outbox_events(id,tenant_id,student_id,aggregate_id,event_type,payload,request_id)
            VALUES(gen_random_uuid(),c.tenant_id,c.student_id,job,'submission.processed',
                jsonb_build_object('submission_id',c.submission_id,'source_revision',version,'deletion_epoch',epoch,
                    'status',final_status,'successful_pages',successes,'failed_pages',total-successes),c.request_id);
        RETURN true;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.complete_execution(uuid,uuid,integer,bigint,uuid,jsonb) FROM PUBLIC;
    """)
    op.execute(f"GRANT EXECUTE ON FUNCTION public.complete_execution(uuid,uuid,integer,bigint,uuid,jsonb) TO {worker}")


def downgrade():
    worker_role()
    if op.get_bind().execute(text('SELECT EXISTS(SELECT 1 FROM public.grading_results)')).scalar_one():
        raise RuntimeError("Cannot remove settled grading results; retain quota settlement evidence")
    op.execute("""
    DROP FUNCTION public.complete_execution(uuid,uuid,integer,bigint,uuid,jsonb);
    DROP TABLE public.grading_results;
    DROP FUNCTION public.valid_conservative_result(jsonb);
    DROP FUNCTION public.result_box(jsonb);
    DROP FUNCTION public.result_keys(jsonb,text[]);
    ALTER TABLE public.quota_reservations DROP CONSTRAINT quota_consumed_pages;
    ALTER TABLE public.quota_reservations DROP COLUMN consumed_pages;
    ALTER TABLE public.execution_receipts DROP CONSTRAINT execution_receipts_state_check;
    ALTER TABLE public.execution_receipts ADD CONSTRAINT execution_receipts_state_check
        CHECK(state IN ('running','retry_wait','deadletter'));
    ALTER TABLE public.outbox_events DROP CONSTRAINT outbox_events_event_type_check;
    ALTER TABLE public.outbox_events ADD CONSTRAINT outbox_events_event_type_check CHECK(event_type='submission.queued');
    """)
