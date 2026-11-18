"""Least-privilege cross-tenant discovery; no business-table grants to dispatcher."""
import os

from alembic import op
from sqlalchemy import text

revision = "0004_outbox_dispatch"
down_revision = "0003_submissions"


def upgrade():
    config = op.get_context().config
    app_role = config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    role = config.attributes.get("dispatcher_role") or os.getenv("WIZZY_DISPATCHER_ROLE", app_role + "_dispatch")
    bind = op.get_bind()
    info = bind.execute(text("""SELECT rolcanlogin,rolsuper,rolbypassrls,rolcreatedb,
        rolcreaterole,rolinherit,rolreplication FROM pg_roles WHERE rolname=:role"""), {"role": role}).first()
    if not info or not info.rolcanlogin or any(info[1:]) or role == app_role:
        raise RuntimeError("A separate restricted dispatcher role is required; run bootstrap first")
    # FORCE RLS applies to table owners too. This migration's definer must be an
    # offline administrative identity, never one of the runtime roles.
    if not bind.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
        raise RuntimeError("Outbox functions require an offline RLS-bypassing migration owner")
    quoted = bind.dialect.identifier_preparer.quote(role)
    op.execute("""
    ALTER TABLE public.outbox_events
        ADD COLUMN next_dispatch_at timestamptz NOT NULL DEFAULT now(),
        ADD COLUMN lease_token uuid,
        ADD COLUMN lease_until timestamptz,
        ADD COLUMN dispatch_attempts bigint NOT NULL DEFAULT 0 CHECK(dispatch_attempts>=0),
        ADD COLUMN consecutive_failures integer NOT NULL DEFAULT 0 CHECK(consecutive_failures BETWEEN 0 AND 9),
        ADD COLUMN last_error_code text CHECK(last_error_code='broker_unavailable'),
        ADD CONSTRAINT outbox_lease_pair CHECK((lease_token IS NULL)=(lease_until IS NULL));
    DROP INDEX public.outbox_pending;
    CREATE INDEX outbox_dispatch_due ON public.outbox_events(next_dispatch_at,created_at,id);

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

    CREATE FUNCTION public.settle_outbox(event uuid,token uuid,published boolean)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE changed integer;
    BEGIN
        IF event IS NULL OR token IS NULL OR published IS NULL THEN
            RETURN false;
        END IF;
        UPDATE public.outbox_events e
        SET lease_token=NULL,lease_until=NULL,
            published_at=CASE WHEN published THEN clock_timestamp() ELSE e.published_at END,
            next_dispatch_at=clock_timestamp()+CASE WHEN published THEN interval '300 seconds'
                ELSE make_interval(secs=>LEAST(300,power(2,LEAST(e.consecutive_failures+1,9))::integer)) END,
            consecutive_failures=CASE WHEN published THEN 0 ELSE LEAST(e.consecutive_failures+1,9) END,
            last_error_code=CASE WHEN published THEN NULL ELSE 'broker_unavailable' END
        WHERE e.id=event AND e.lease_token=token AND e.lease_until>clock_timestamp();
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;
    REVOKE ALL ON FUNCTION public.settle_outbox(uuid,uuid,boolean) FROM PUBLIC;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {quoted}")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.claim_outbox(integer),public.settle_outbox(uuid,uuid,boolean) TO {quoted}")


def downgrade():
    op.execute("""
    DROP FUNCTION public.settle_outbox(uuid,uuid,boolean);
    DROP FUNCTION public.claim_outbox(integer);
    DROP INDEX public.outbox_dispatch_due;
    ALTER TABLE public.outbox_events DROP CONSTRAINT outbox_lease_pair,
        DROP COLUMN last_error_code,DROP COLUMN consecutive_failures,DROP COLUMN dispatch_attempts,
        DROP COLUMN lease_until,DROP COLUMN lease_token,DROP COLUMN next_dispatch_at;
    CREATE INDEX outbox_pending ON public.outbox_events(created_at,id) WHERE published_at IS NULL;
    """)
