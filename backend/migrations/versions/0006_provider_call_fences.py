"""Persist one-shot external-call permission; unknown costs never trigger replay."""
import os

from alembic import op
from sqlalchemy import text

revision = "0006_provider_call_fences"
down_revision = "0005_execution_leases"


def runtime_roles():
    config = op.get_context().config
    app = config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    worker = config.attributes.get("worker_role") or os.getenv("WIZZY_WORKER_ROLE", app + "_worker")
    dispatcher = config.attributes.get("dispatcher_role") or os.getenv("WIZZY_DISPATCHER_ROLE", app + "_dispatch")
    bind = op.get_bind()
    if not bind.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
        raise RuntimeError("Call fences require an offline RLS-bypassing migration owner")
    for role in (worker, dispatcher):
        safe = bind.execute(text("""SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
            rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
            FROM pg_roles WHERE rolname=:role"""), {"role": role}).scalar_one_or_none()
        if not safe or len({app, worker, dispatcher}) != 3:
            raise RuntimeError("Separate restricted runtime roles are required")
    quote = bind.dialect.identifier_preparer.quote
    return quote(worker), quote(dispatcher)


def upgrade():
    worker, dispatcher = runtime_roles()
    op.execute("""
    CREATE TABLE public.model_runs (
        id uuid PRIMARY KEY, operation_key uuid NOT NULL UNIQUE,
        event_id uuid NOT NULL REFERENCES public.execution_receipts(event_id),
        job_id uuid NOT NULL, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        submission_id uuid NOT NULL, source_revision integer NOT NULL CHECK(source_revision>0),
        deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0), execution_token uuid NOT NULL,
        stage text NOT NULL CHECK(stage IN ('ocr','structure','verify','tutor')),
        provider text NOT NULL CHECK(provider ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        model text NOT NULL CHECK(model ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'),
        model_version text NOT NULL CHECK(model_version ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'),
        outcome text NOT NULL DEFAULT 'in_flight' CHECK(outcome IN ('in_flight','succeeded','failed')),
        provider_request_id text CHECK(provider_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'),
        billing_state text NOT NULL DEFAULT 'unknown' CHECK(billing_state IN ('unknown','known')),
        cost_microyuan bigint CHECK(cost_microyuan>=0),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(job_id,source_revision,deletion_epoch,stage),
        FOREIGN KEY(tenant_id,job_id) REFERENCES public.jobs(tenant_id,id),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES public.submissions(tenant_id,student_id,id),
        CHECK((billing_state='known')=(cost_microyuan IS NOT NULL)),
        CHECK((outcome='in_flight')=(finished_at IS NULL)),
        CHECK((outcome='in_flight')=(provider_request_id IS NULL)),
        CHECK(outcome<>'in_flight' OR billing_state='unknown')
    );
    ALTER TABLE public.model_runs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.model_runs FORCE ROW LEVEL SECURITY;
    CREATE INDEX model_runs_event ON public.model_runs(event_id);

    -- Keep the tested pre-call implementation private. Wrappers acquire the same
    -- canonical context locks BEFORE checking the durable fence, so begin/replay/
    -- cancellation serialize. Revoke old grants; renaming alone retains them.
    ALTER FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text) RENAME TO claim_execution_unfenced;
    ALTER FUNCTION public.release_execution(uuid,uuid,integer,bigint,uuid) RENAME TO release_execution_unfenced;
    ALTER FUNCTION public.claim_outbox(integer) RENAME TO claim_outbox_unfenced;

    CREATE FUNCTION public.claim_execution(event uuid,job uuid,version integer,epoch bigint,request text)
    RETURNS TABLE(event_id uuid,job_id uuid,tenant_id uuid,student_id uuid,submission_id uuid,
                  source_revision integer,deletion_epoch bigint,request_id text,lease_token uuid,attempt integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        PERFORM 1 FROM public.lock_execution_context(event);
        IF NOT FOUND OR EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=event) THEN RETURN; END IF;
        RETURN QUERY SELECT * FROM public.claim_execution_unfenced(event,job,version,epoch,request);
    END;
    $$;
    CREATE FUNCTION public.release_execution(event uuid,job uuid,version integer,epoch bigint,token uuid)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        PERFORM 1 FROM public.lock_execution_context(event);
        IF NOT FOUND OR EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=event) THEN RETURN false; END IF;
        RETURN public.release_execution_unfenced(event,job,version,epoch,token);
    END;
    $$;

    CREATE FUNCTION public.begin_model_call(event uuid,job uuid,version integer,epoch bigint,token uuid,
        call_stage text,call_provider text,call_model text,call_version text)
    RETURNS TABLE(run_id uuid,operation_key uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE c record; r public.execution_receipts%ROWTYPE;
    BEGIN
        IF call_stage IS NULL OR call_stage NOT IN ('ocr','structure','verify','tutor')
            OR call_provider IS NULL OR call_provider !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'
            OR call_model IS NULL OR call_model !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            OR call_version IS NULL OR call_version !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            THEN RETURN; END IF;
        SELECT * INTO c FROM public.lock_execution_context(event);
        IF NOT FOUND OR c.job_id<>job OR c.source_revision<>version OR c.deletion_epoch<>epoch
            OR c.job_status<>'running' OR c.submission_status<>'processing' THEN RETURN; END IF;
        SELECT * INTO r FROM public.execution_receipts x WHERE x.event_id=event FOR UPDATE;
        IF NOT FOUND OR r.job_id<>job OR r.tenant_id<>c.tenant_id OR r.student_id<>c.student_id
            OR r.submission_id<>c.submission_id OR r.source_revision<>version OR r.deletion_epoch<>epoch
            OR r.state<>'running' OR r.lease_token IS DISTINCT FROM token
            OR r.lease_until<=clock_timestamp() THEN RETURN; END IF;
        IF EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=event
            AND (m.stage=call_stage OR m.outcome<>'succeeded' OR m.billing_state='unknown')) THEN RETURN; END IF;
        -- Unknown from BEFORE the network send: a crash after commit may or may
        -- not have reached the provider. No caller may obtain this permission twice.
        RETURN QUERY INSERT INTO public.model_runs AS m
            (id,operation_key,event_id,job_id,tenant_id,student_id,submission_id,
             source_revision,deletion_epoch,execution_token,stage,provider,model,model_version)
            VALUES(gen_random_uuid(),gen_random_uuid(),event,job,c.tenant_id,c.student_id,c.submission_id,
                   version,epoch,token,call_stage,call_provider,call_model,call_version)
            ON CONFLICT DO NOTHING RETURNING m.id,m.operation_key;
    END;
    $$;

    CREATE FUNCTION public.finish_model_call(event uuid,job uuid,version integer,epoch bigint,token uuid,
        run uuid,operation uuid,call_outcome text,provider_request text,cost bigint)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE m public.model_runs%ROWTYPE;
    BEGIN
        IF call_outcome IS NULL OR call_outcome NOT IN ('succeeded','failed')
            OR provider_request IS NULL OR provider_request !~ '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$'
            OR cost<0 THEN RETURN false; END IF;
        SELECT * INTO m FROM public.model_runs x WHERE x.id=run FOR UPDATE;
        IF NOT FOUND OR m.event_id IS DISTINCT FROM event OR m.job_id IS DISTINCT FROM job
            OR m.source_revision IS DISTINCT FROM version OR m.deletion_epoch IS DISTINCT FROM epoch
            OR m.execution_token IS DISTINCT FROM token OR m.operation_key IS DISTINCT FROM operation THEN RETURN false; END IF;
        -- Accounting only: accept a late receipt after cancellation/revocation or
        -- token expiry. Never write child results, jobs, submissions or user quota.
        IF m.outcome<>'in_flight' AND (m.outcome<>call_outcome OR m.provider_request_id<>provider_request)
            THEN RETURN false; END IF;
        IF m.billing_state='known' THEN
            RETURN m.cost_microyuan IS NOT DISTINCT FROM cost;
        END IF;
        UPDATE public.model_runs SET outcome=call_outcome,provider_request_id=provider_request,
            cost_microyuan=cost,billing_state=CASE WHEN cost IS NULL THEN 'unknown' ELSE 'known' END,
            finished_at=COALESCE(finished_at,clock_timestamp()),updated_at=clock_timestamp()
            WHERE id=run;
        RETURN true;
    END;
    $$;

    CREATE FUNCTION public.claim_outbox(batch_size integer)
    RETURNS TABLE(event_id uuid,job_id uuid,source_revision integer,deletion_epoch bigint,
                  request_id text,lease_token uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF batch_size IS NULL OR batch_size<1 OR batch_size>100 THEN
            RAISE EXCEPTION 'batch_size must be between 1 and 100' USING ERRCODE='22023';
        END IF;
        RETURN QUERY
        WITH candidates AS (
            SELECT e.id,s.source_revision,j.deletion_epoch,COALESCE(r.attempt,0) AS execution_attempt FROM public.outbox_events e
            JOIN public.jobs j ON j.id=e.aggregate_id AND j.tenant_id=e.tenant_id AND j.student_id=e.student_id
            JOIN public.submissions s ON s.id=j.submission_id AND s.tenant_id=j.tenant_id AND s.student_id=j.student_id
            JOIN public.students st ON st.id=j.student_id AND st.tenant_id=j.tenant_id
            JOIN public.tenants t ON t.id=j.tenant_id
            LEFT JOIN public.execution_receipts r ON r.event_id=e.id
            WHERE e.event_type='submission.queued'
              AND NOT EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=e.id)
              AND st.status='active' AND st.consent_active AND t.status='active'
              AND j.deletion_epoch=st.deletion_epoch AND s.deletion_epoch=st.deletion_epoch
              AND (e.lease_until IS NULL OR e.lease_until<=clock_timestamp())
              AND ((j.status='queued' AND s.status='queued' AND e.next_dispatch_at<=clock_timestamp()
                    AND (r.event_id IS NULL OR (r.state='retry_wait' AND r.retry_at<=clock_timestamp())))
                OR (j.status='running' AND s.status='processing' AND r.state='running'
                    AND r.lease_until<=clock_timestamp()
                    AND (r.attempt>e.execution_dispatch_attempt OR e.next_dispatch_at<=clock_timestamp())))
            ORDER BY e.next_dispatch_at,e.created_at,e.id
            LIMIT batch_size FOR UPDATE OF e SKIP LOCKED
        )
        UPDATE public.outbox_events e
        SET lease_token=gen_random_uuid(),lease_until=clock_timestamp()+interval '60 seconds',
            dispatch_attempts=e.dispatch_attempts+1,execution_dispatch_attempt=c.execution_attempt,
            next_dispatch_at=LEAST(e.next_dispatch_at,clock_timestamp())
        FROM candidates c WHERE e.id=c.id
        RETURNING e.id,e.aggregate_id,c.source_revision,c.deletion_epoch,e.request_id,e.lease_token;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text),
        public.release_execution(uuid,uuid,integer,bigint,uuid),public.claim_outbox(integer),
        public.begin_model_call(uuid,uuid,integer,bigint,uuid,text,text,text,text),
        public.finish_model_call(uuid,uuid,integer,bigint,uuid,uuid,uuid,text,text,bigint) FROM PUBLIC;
    """)
    op.execute(f"""REVOKE ALL ON FUNCTION public.claim_execution_unfenced(uuid,uuid,integer,bigint,text),
        public.release_execution_unfenced(uuid,uuid,integer,bigint,uuid),public.claim_outbox_unfenced(integer)
        FROM PUBLIC,{worker},{dispatcher}""")
    op.execute(f"""GRANT EXECUTE ON FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text),
        public.release_execution(uuid,uuid,integer,bigint,uuid),
        public.begin_model_call(uuid,uuid,integer,bigint,uuid,text,text,text,text),
        public.finish_model_call(uuid,uuid,integer,bigint,uuid,uuid,uuid,text,text,bigint) TO {worker}""")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.claim_outbox(integer) TO {dispatcher}")


def downgrade():
    worker, dispatcher = runtime_roles()
    if op.get_bind().execute(text('SELECT EXISTS(SELECT 1 FROM public.model_runs)')).scalar_one():
        raise RuntimeError("Cannot remove durable paid-call fences; reconcile and retain accounting records")
    op.execute("""
    DROP FUNCTION public.begin_model_call(uuid,uuid,integer,bigint,uuid,text,text,text,text);
    DROP FUNCTION public.finish_model_call(uuid,uuid,integer,bigint,uuid,uuid,uuid,text,text,bigint);
    DROP FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text);
    DROP FUNCTION public.release_execution(uuid,uuid,integer,bigint,uuid);
    DROP FUNCTION public.claim_outbox(integer);
    ALTER FUNCTION public.claim_execution_unfenced(uuid,uuid,integer,bigint,text) RENAME TO claim_execution;
    ALTER FUNCTION public.release_execution_unfenced(uuid,uuid,integer,bigint,uuid) RENAME TO release_execution;
    ALTER FUNCTION public.claim_outbox_unfenced(integer) RENAME TO claim_outbox;
    DROP TABLE public.model_runs;
    """)
    op.execute(f"""GRANT EXECUTE ON FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text),
        public.release_execution(uuid,uuid,integer,bigint,uuid) TO {worker}""")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.claim_outbox(integer) TO {dispatcher}")
