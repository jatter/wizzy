"""Audited cross-account session revocation with immutable idempotent receipts."""
import os
import re

from alembic import op
from sqlalchemy import text

revision = '0031_account_session_revocations'
down_revision = '0030_evaluation_runs'
CAPABILITY = 'public.platform_account_session_revocations(text,text,text,uuid,jsonb,integer,timestamptz,uuid)'
PERMISSION = 'platform.accounts.sessions.revoke'
TABLE = 'platform_session_revocations'


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Session revocation migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('Session revocation migration requires a restricted app role')
    quoted_role = bind.dialect.identifier_preparer.quote(role)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action='accounts.sessions.revoke')''')
    op.execute(r'''
    INSERT INTO public.platform_permissions VALUES ('platform.accounts.sessions.revoke');
    INSERT INTO public.platform_role_permissions VALUES ('security_admin','platform.accounts.sessions.revoke');
    CREATE TABLE public.platform_session_revocations(
        id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        revoked_count bigint NOT NULL CHECK(revoked_count>=0),
        created_at timestamptz NOT NULL, UNIQUE(actor_id,operation_id), CHECK(account_id<>actor_id)
    );
    CREATE INDEX platform_session_revocation_page ON public.platform_session_revocations(account_id,created_at DESC,id DESC);
    CREATE TRIGGER platform_session_revocation_immutable BEFORE UPDATE OR DELETE ON public.platform_session_revocations
        FOR EACH ROW EXECUTE FUNCTION public.model_revision_immutable();
    ALTER TABLE public.platform_session_revocations ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.platform_session_revocations FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.platform_session_revocations FROM PUBLIC;

    CREATE FUNCTION public.platform_account_session_revocations(p_hash text,p_op text,p_request text,p_id uuid,
        p_payload jsonb,p_limit integer,p_before timestamptz,p_before_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE;
        target public.platform_accounts%ROWTYPE; receipt public.platform_session_revocations%ROWTYPE;
        account uuid; operation uuid; needed text; value jsonb; affected bigint; boundary timestamptz;
    BEGIN
        -- Match governance lock ordering before actor/target/session locks.
        PERFORM pg_advisory_xact_lock(872109,21);
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('history','revoke') OR p_id IS NULL
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 80
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed:=CASE WHEN p_op='revoke' THEN 'platform.accounts.sessions.revoke' ELSE 'platform.accounts.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        SELECT * INTO target FROM public.platform_accounts WHERE id=p_id FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        IF p_op='history' THEN
            IF p_payload<>'{}' THEN RETURN jsonb_build_object('status',422); END IF;
            SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT * FROM public.platform_session_revocations
                WHERE account_id=p_id AND (p_before IS NULL OR (created_at,id)<(p_before,p_before_id))
                ORDER BY created_at DESC,id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_id=a.id THEN RETURN jsonb_build_object('status',409,'code','self_session_revoke'); END IF;
        IF NOT p_payload ?& ARRAY['operation_id','reason'] OR p_payload-ARRAY['operation_id','reason']<>'{}'
            OR jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
            OR p_payload->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
            OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string'
            OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500
            OR p_payload->>'reason'<>btrim(p_payload->>'reason') OR p_payload->>'reason' ~ '[[:cntrl:]]'
            THEN RETURN jsonb_build_object('status',422); END IF;
        operation:=(p_payload->>'operation_id')::uuid;
        SELECT * INTO receipt FROM public.platform_session_revocations WHERE actor_id=a.id AND operation_id=operation;
        IF FOUND THEN
            IF receipt.account_id<>p_id OR receipt.reason<>p_payload->>'reason'
                THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',to_jsonb(receipt));
        END IF;
        -- Login locks this same target account; a login after this boundary survives replay.
        boundary:=clock_timestamp();
        WITH revoked AS (
            UPDATE public.platform_sessions SET revoked_at=boundary WHERE account_id=p_id AND revoked_at IS NULL
            RETURNING expires_at,credential_version
        ) SELECT count(*) FILTER(WHERE expires_at>boundary AND credential_version=target.credential_version)
            INTO affected FROM revoked;
        INSERT INTO public.platform_session_revocations(id,account_id,actor_id,operation_id,reason,revoked_count,created_at)
            VALUES(gen_random_uuid(),p_id,a.id,operation,p_payload->>'reason',affected,boundary) RETURNING * INTO receipt;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'accounts.sessions.revoke',p_request);
        RETURN jsonb_build_object('status',201,'value',to_jsonb(receipt));
    END $$;
    ''')
    op.execute(f'REVOKE ALL ON public.{TABLE} FROM {quoted_role}')
    op.execute(f'REVOKE ALL ON FUNCTION {CAPABILITY} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {quoted_role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT EXISTS(SELECT 1 FROM public.platform_session_revocations) OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action='accounts.sessions.revoke')")).scalar_one():
        raise RuntimeError('Cannot discard session revocation history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute(f'DROP TABLE public.{TABLE}')
    op.execute(f"DELETE FROM public.platform_role_permissions WHERE permission='{PERMISSION}'")
    op.execute(f"DELETE FROM public.platform_permissions WHERE name='{PERMISSION}'")
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    actions = [a for a in re.findall(r"'([^']+)'::text", previous) if a != 'accounts.sessions.revoke']
    quoted = ','.join("'" + a.replace("'", "''") + "'" for a in actions)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({quoted}))')
