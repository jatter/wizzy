"""Pre-provider execution leases: fenced, finite recovery with no grading result."""
import os

from alembic import op
from sqlalchemy import text

revision = "0005_execution_leases"
down_revision = "0004_outbox_dispatch"


def upgrade():
    config = op.get_context().config
    app_role = config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    dispatcher_role = config.attributes.get("dispatcher_role") or os.getenv("WIZZY_DISPATCHER_ROLE", app_role + "_dispatch")
    role = config.attributes.get("worker_role") or os.getenv("WIZZY_WORKER_ROLE", app_role + "_worker")
    bind = op.get_bind()
    info = bind.execute(text("""SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,
        rolcreaterole,rolinherit,rolreplication FROM pg_roles WHERE rolname=:role"""), {"role": role}).first()
    if not info or not info.rolcanlogin or any(info[1:]) or role in (app_role, dispatcher_role):
        raise RuntimeError("A separate restricted worker role is required; run bootstrap first")
    if not bind.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
        raise RuntimeError("Execution functions require an offline RLS-bypassing migration owner")
    quoted = bind.dialect.identifier_preparer.quote(role)
    op.execute("""
    ALTER TABLE public.outbox_events ADD COLUMN execution_dispatch_attempt integer NOT NULL DEFAULT 0
        CHECK(execution_dispatch_attempt BETWEEN 0 AND 3);
    CREATE TABLE public.execution_receipts (
        event_id uuid PRIMARY KEY REFERENCES public.outbox_events(id),
        job_id uuid NOT NULL UNIQUE, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        submission_id uuid NOT NULL, source_revision integer NOT NULL CHECK(source_revision>0),
        deletion_epoch bigint NOT NULL CHECK(deletion_epoch>=0),
        attempt integer NOT NULL CHECK(attempt BETWEEN 1 AND 3),
        state text NOT NULL CHECK(state IN ('running','retry_wait','deadletter')),
        lease_token uuid, lease_until timestamptz, retry_at timestamptz,
        error_code text CHECK(error_code='execution_abandoned'),
        updated_at timestamptz NOT NULL,
        FOREIGN KEY(tenant_id,job_id) REFERENCES public.jobs(tenant_id,id),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES public.submissions(tenant_id,student_id,id),
        CHECK((lease_token IS NULL)=(lease_until IS NULL)),
        CHECK((state='running')=(lease_token IS NOT NULL)),
        CHECK((state='retry_wait')=(retry_at IS NOT NULL))
    );
    ALTER TABLE public.execution_receipts ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.execution_receipts FORCE ROW LEVEL SECURITY;
    CREATE INDEX execution_expired ON public.execution_receipts(lease_until) WHERE state='running';

    -- All callers take the same locks as API cancellation/consent withdrawal.
    -- No locks/SQL transactions are held across external provider calls.
    CREATE FUNCTION public.lock_execution_context(event uuid)
    RETURNS TABLE(event_id uuid,job_id uuid,tenant_id uuid,student_id uuid,submission_id uuid,
                  source_revision integer,deletion_epoch bigint,request_id text,
                  job_status text,submission_status text)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE e public.outbox_events%ROWTYPE; j public.jobs%ROWTYPE;
            s public.submissions%ROWTYPE; st public.students%ROWTYPE;
    BEGIN
        SELECT * INTO e FROM public.outbox_events x WHERE x.id=event AND x.event_type='submission.queued';
        IF NOT FOUND THEN RETURN; END IF;
        PERFORM 1 FROM public.tenants t WHERE t.id=e.tenant_id AND t.status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN; END IF;
        SELECT * INTO st FROM public.students x WHERE x.id=e.student_id AND x.tenant_id=e.tenant_id FOR SHARE;
        IF NOT FOUND OR st.status<>'active' OR NOT st.consent_active THEN RETURN; END IF;
        SELECT * INTO j FROM public.jobs x WHERE x.id=e.aggregate_id AND x.tenant_id=e.tenant_id
            AND x.student_id=e.student_id FOR UPDATE;
        IF NOT FOUND OR j.deletion_epoch<>st.deletion_epoch THEN RETURN; END IF;
        SELECT * INTO s FROM public.submissions x WHERE x.id=j.submission_id AND x.tenant_id=j.tenant_id
            AND x.student_id=j.student_id FOR UPDATE;
        IF NOT FOUND OR s.deletion_epoch<>st.deletion_epoch THEN RETURN; END IF;
        RETURN QUERY SELECT e.id,j.id,j.tenant_id,j.student_id,s.id,s.source_revision,
                            j.deletion_epoch,e.request_id,j.status,s.status;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.lock_execution_context(uuid) FROM PUBLIC;

    -- Private, only called with the canonical job/submission locks already held.
    CREATE FUNCTION public.abandon_execution(event uuid)
    RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r public.execution_receipts%ROWTYPE; q public.quota_reservations%ROWTYPE;
    BEGIN
        UPDATE public.execution_receipts SET state='deadletter',lease_token=NULL,lease_until=NULL,
            retry_at=NULL,error_code='execution_abandoned',updated_at=clock_timestamp()
            WHERE event_id=event RETURNING * INTO r;
        UPDATE public.jobs SET status='failed',error_code='execution_abandoned',finished_at=clock_timestamp()
            WHERE id=r.job_id;
        UPDATE public.submissions SET status='failed' WHERE id=r.submission_id;
        UPDATE public.quota_reservations SET status='released',settled_at=clock_timestamp()
            WHERE tenant_id=r.tenant_id AND submission_id=r.submission_id AND status='reserved'
            RETURNING * INTO q;
        IF FOUND THEN
            UPDATE public.quota_accounts SET reserved_pages=reserved_pages-q.pages
                WHERE tenant_id=q.tenant_id AND period_start=q.period_start;
        END IF;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.abandon_execution(uuid) FROM PUBLIC;

    CREATE FUNCTION public.claim_execution(event uuid,job uuid,version integer,epoch bigint,request text)
    RETURNS TABLE(event_id uuid,job_id uuid,tenant_id uuid,student_id uuid,submission_id uuid,
                  source_revision integer,deletion_epoch bigint,request_id text,lease_token uuid,attempt integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE c record; r public.execution_receipts%ROWTYPE; token uuid;
    BEGIN
        IF event IS NULL OR job IS NULL OR version IS NULL OR version<1 OR epoch IS NULL OR epoch<0
            OR request IS NULL OR length(request) NOT BETWEEN 1 AND 128 THEN RETURN; END IF;
        SELECT * INTO c FROM public.lock_execution_context(event);
        IF NOT FOUND OR c.job_id<>job OR c.source_revision<>version OR c.deletion_epoch<>epoch
            OR c.request_id<>request THEN RETURN; END IF;
        SELECT * INTO r FROM public.execution_receipts x WHERE x.event_id=event FOR UPDATE;
        IF FOUND THEN
            IF r.job_id<>c.job_id OR r.tenant_id<>c.tenant_id OR r.student_id<>c.student_id
                OR r.submission_id<>c.submission_id OR r.source_revision<>version OR r.deletion_epoch<>epoch
                OR NOT ((r.state='running' AND c.job_status='running' AND c.submission_status='processing'
                         AND r.lease_until<=clock_timestamp())
                     OR (r.state='retry_wait' AND c.job_status='queued' AND c.submission_status='queued'
                         AND r.retry_at<=clock_timestamp())) THEN RETURN; END IF;
            IF r.attempt>=3 THEN
                PERFORM public.abandon_execution(event);
                RETURN;
            END IF;
        ELSIF c.job_status<>'queued' OR c.submission_status<>'queued' THEN
            RETURN;
        END IF;
        token:=gen_random_uuid();
        INSERT INTO public.execution_receipts AS x
            (event_id,job_id,tenant_id,student_id,submission_id,source_revision,deletion_epoch,
             attempt,state,lease_token,lease_until,updated_at)
            VALUES(event,c.job_id,c.tenant_id,c.student_id,c.submission_id,version,epoch,
                   1,'running',token,clock_timestamp()+interval '60 seconds',clock_timestamp())
            ON CONFLICT ON CONSTRAINT execution_receipts_pkey DO UPDATE SET
                attempt=x.attempt+1,state='running',lease_token=token,
                lease_until=clock_timestamp()+interval '60 seconds',retry_at=NULL,
                error_code=NULL,updated_at=clock_timestamp()
            RETURNING * INTO r;
        UPDATE public.jobs SET status='running' WHERE id=c.job_id;
        UPDATE public.submissions SET status='processing' WHERE id=c.submission_id;
        RETURN QUERY SELECT event,c.job_id,c.tenant_id,c.student_id,c.submission_id,version,epoch,
                            c.request_id,token,r.attempt;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text) FROM PUBLIC;

    CREATE FUNCTION public.heartbeat_execution(event uuid,job uuid,version integer,epoch bigint,token uuid)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE c record; changed integer;
    BEGIN
        SELECT * INTO c FROM public.lock_execution_context(event);
        IF NOT FOUND OR c.job_id<>job OR c.source_revision<>version OR c.deletion_epoch<>epoch
            OR c.job_status<>'running' OR c.submission_status<>'processing' THEN RETURN false; END IF;
        UPDATE public.execution_receipts r SET lease_until=clock_timestamp()+interval '60 seconds',
            updated_at=clock_timestamp()
            WHERE r.event_id=event AND r.job_id=c.job_id AND r.tenant_id=c.tenant_id
              AND r.student_id=c.student_id AND r.submission_id=c.submission_id
              AND r.source_revision=version AND r.deletion_epoch=epoch
              AND r.state='running' AND r.lease_token=token AND r.lease_until>clock_timestamp();
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.heartbeat_execution(uuid,uuid,integer,bigint,uuid) FROM PUBLIC;

    -- Safe pre-provider interruptions only. Not for an ambiguous paid call.
    CREATE FUNCTION public.release_execution(event uuid,job uuid,version integer,epoch bigint,token uuid)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE c record; r public.execution_receipts%ROWTYPE; stamp timestamptz;
    BEGIN
        SELECT * INTO c FROM public.lock_execution_context(event);
        IF NOT FOUND OR c.job_id<>job OR c.source_revision<>version OR c.deletion_epoch<>epoch
            OR c.job_status<>'running' OR c.submission_status<>'processing' THEN RETURN false; END IF;
        SELECT * INTO r FROM public.execution_receipts x WHERE x.event_id=event AND x.job_id=c.job_id
            AND x.tenant_id=c.tenant_id AND x.student_id=c.student_id AND x.submission_id=c.submission_id
            AND x.source_revision=version AND x.deletion_epoch=epoch
            AND x.state='running' AND x.lease_token=token AND x.lease_until>clock_timestamp() FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        IF r.attempt>=3 THEN
            PERFORM public.abandon_execution(event);
        ELSE
            stamp:=clock_timestamp();
            UPDATE public.execution_receipts SET state='retry_wait',lease_token=NULL,lease_until=NULL,
                retry_at=stamp+make_interval(secs=>power(2,r.attempt)::integer),
                updated_at=stamp,error_code='execution_abandoned' WHERE event_id=event;
            UPDATE public.jobs SET status='queued' WHERE id=c.job_id;
            UPDATE public.submissions SET status='queued' WHERE id=c.submission_id;
            UPDATE public.outbox_events SET next_dispatch_at=stamp+make_interval(secs=>power(2,r.attempt)::integer)
                WHERE id=event;
        END IF;
        RETURN true;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.release_execution(uuid,uuid,integer,bigint,uuid) FROM PUBLIC;

    CREATE OR REPLACE FUNCTION public.claim_outbox(batch_size integer)
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
            -- If recovery bypassed a future replay time, only the relay lease
            -- should delay recovery after a crash before settle_outbox.
            next_dispatch_at=LEAST(e.next_dispatch_at,clock_timestamp())
        FROM candidates c WHERE e.id=c.id
        RETURNING e.id,e.aggregate_id,c.source_revision,c.deletion_epoch,e.request_id,e.lease_token;
    END;
    $$;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {quoted}")
    op.execute(f"""GRANT EXECUTE ON FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text),
        public.heartbeat_execution(uuid,uuid,integer,bigint,uuid),
        public.release_execution(uuid,uuid,integer,bigint,uuid) TO {quoted}""")


def downgrade():
    op.execute("""
    CREATE OR REPLACE FUNCTION public.claim_outbox(batch_size integer)
    RETURNS TABLE(event_id uuid,job_id uuid,source_revision integer,deletion_epoch bigint,
                  request_id text,lease_token uuid)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF batch_size IS NULL OR batch_size<1 OR batch_size>100 THEN
            RAISE EXCEPTION 'batch_size must be between 1 and 100' USING ERRCODE='22023';
        END IF;
        RETURN QUERY
        WITH candidates AS (
            SELECT e.id,s.source_revision,j.deletion_epoch FROM public.outbox_events e
            JOIN public.jobs j ON j.id=e.aggregate_id AND j.tenant_id=e.tenant_id AND j.student_id=e.student_id
            JOIN public.submissions s ON s.id=j.submission_id AND s.tenant_id=j.tenant_id AND s.student_id=j.student_id
            JOIN public.students st ON st.id=j.student_id AND st.tenant_id=j.tenant_id
            JOIN public.tenants t ON t.id=j.tenant_id
            WHERE e.event_type='submission.queued' AND j.status='queued' AND s.status='queued'
              AND st.status='active' AND st.consent_active AND t.status='active'
              AND j.deletion_epoch=st.deletion_epoch AND s.deletion_epoch=st.deletion_epoch
              AND e.next_dispatch_at<=clock_timestamp()
              AND (e.lease_until IS NULL OR e.lease_until<=clock_timestamp())
            ORDER BY e.next_dispatch_at,e.created_at,e.id
            LIMIT batch_size FOR UPDATE OF e SKIP LOCKED
        )
        UPDATE public.outbox_events e
        SET lease_token=gen_random_uuid(),lease_until=clock_timestamp()+interval '60 seconds',
            dispatch_attempts=e.dispatch_attempts+1
        FROM candidates c WHERE e.id=c.id
        RETURNING e.id,e.aggregate_id,c.source_revision,c.deletion_epoch,e.request_id,e.lease_token;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.claim_outbox(integer) FROM PUBLIC;

    DROP FUNCTION public.release_execution(uuid,uuid,integer,bigint,uuid);
    DROP FUNCTION public.heartbeat_execution(uuid,uuid,integer,bigint,uuid);
    DROP FUNCTION public.claim_execution(uuid,uuid,integer,bigint,text);
    DROP FUNCTION public.abandon_execution(uuid);
    DROP FUNCTION public.lock_execution_context(uuid);
    DROP TABLE public.execution_receipts;
    ALTER TABLE public.outbox_events DROP COLUMN execution_dispatch_attempt;
    """)
