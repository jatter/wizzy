"""Separate platform credentials, MFA-fenced sessions and read-only RBAC."""
import os

from alembic import op
from sqlalchemy import text

revision = '0015_platform_identity'
down_revision = '0014_timetable_core'

FUNCTIONS = (
    'public.platform_credentials(text)',
    'public.platform_login(uuid,uuid,bigint,text,uuid,text)',
    'public.platform_request(text,text,text,integer,timestamptz,uuid)',
)


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Platform migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication) FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    CREATE TABLE public.platform_roles (name text PRIMARY KEY);
    CREATE TABLE public.platform_permissions (name text PRIMARY KEY);
    CREATE TABLE public.platform_role_permissions (
        role text NOT NULL REFERENCES public.platform_roles(name),
        permission text NOT NULL REFERENCES public.platform_permissions(name),
        PRIMARY KEY(role,permission)
    );
    INSERT INTO public.platform_roles VALUES ('security_admin'),('operator');
    INSERT INTO public.platform_permissions VALUES ('platform.accounts.read'),('platform.audit.read');
    INSERT INTO public.platform_role_permissions VALUES
        ('security_admin','platform.accounts.read'),('security_admin','platform.audit.read');
    CREATE TABLE public.platform_accounts (
        id uuid PRIMARY KEY, email text NOT NULL UNIQUE
            CHECK(length(email) BETWEEN 3 AND 254 AND email=lower(btrim(email)) AND email LIKE '%@%' AND email !~ '[[:space:]]'),
        password_hash text NOT NULL, encrypted_totp text NOT NULL,
        credential_version uuid NOT NULL, role text NOT NULL REFERENCES public.platform_roles(name),
        status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
        last_totp_step bigint NOT NULL DEFAULT -1 CHECK(last_totp_step>=-1),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.platform_sessions (
        id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        credential_version uuid NOT NULL, token_hash text NOT NULL UNIQUE CHECK(token_hash ~ '^[a-f0-9]{64}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL DEFAULT now()+interval '30 minutes', revoked_at timestamptz
    );
    CREATE INDEX platform_sessions_account ON public.platform_sessions(account_id);
    CREATE TABLE public.platform_audit_events (
        id uuid PRIMARY KEY, actor_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        action text NOT NULL CHECK(action IN ('bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied')),
        request_id text NOT NULL CHECK(length(request_id) BETWEEN 1 AND 80),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE INDEX platform_accounts_page ON public.platform_accounts(created_at DESC,id DESC);
    CREATE INDEX platform_audit_page ON public.platform_audit_events(created_at DESC,id DESC);

    CREATE FUNCTION public.platform_credentials(p_email text)
    RETURNS TABLE(id uuid,password_hash text,encrypted_totp text,credential_version uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT a.id,a.password_hash,a.encrypted_totp,a.credential_version
        FROM public.platform_accounts a WHERE a.email=p_email AND a.status='active'
    $$;

    CREATE FUNCTION public.platform_login(p_account uuid,p_version uuid,p_step bigint,
        p_hash text,p_session uuid,p_request text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; current_step bigint;
    BEGIN
        SELECT * INTO a FROM public.platform_accounts WHERE id=p_account FOR UPDATE;
        current_step := floor(extract(epoch FROM clock_timestamp())/30)::bigint;
        IF NOT FOUND OR a.status<>'active' OR a.credential_version IS DISTINCT FROM p_version
            OR p_step IS NULL OR p_step<=a.last_totp_step OR p_step NOT BETWEEN current_step-1 AND current_step+1 THEN
            RETURN NULL;
        END IF;
        UPDATE public.platform_accounts SET last_totp_step=p_step WHERE id=a.id;
        INSERT INTO public.platform_sessions(id,account_id,credential_version,token_hash)
            VALUES(p_session,a.id,a.credential_version,p_hash);
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'login',p_request);
        RETURN p_session;
    END $$;

    CREATE FUNCTION public.platform_request(p_hash text,p_operation text,p_request text,
        p_limit integer,p_before timestamptz,p_before_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE;
        account uuid; required_permission text; value jsonb; permissions jsonb;
    BEGIN
        IF p_operation IS NULL OR p_operation NOT IN ('me','roles','accounts','audit','logout','logout_all')
            OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) THEN
            RETURN jsonb_build_object('status',422);
        END IF;
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        -- Same account -> session lock order as login/logout, recheck after waiting.
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp()
            OR s.credential_version<>a.credential_version THEN
            RETURN jsonb_build_object('status',401);
        END IF;
        required_permission := CASE p_operation WHEN 'accounts' THEN 'platform.accounts.read'
            WHEN 'audit' THEN 'platform.audit.read' ELSE NULL END;
        IF required_permission IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.platform_role_permissions
            WHERE role=a.role AND permission=required_permission) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,p_operation,p_request);
        IF p_operation='me' THEN
            SELECT coalesce(jsonb_agg(permission ORDER BY permission),'[]'::jsonb) INTO permissions
                FROM public.platform_role_permissions WHERE role=a.role;
            value := jsonb_build_object('id',a.id,'email',a.email,'role',a.role,'audience','platform',
                'permissions',permissions,'session_id',s.id,'expires_at',s.expires_at);
        ELSIF p_operation='roles' THEN
            SELECT coalesce(jsonb_agg(t ORDER BY t.name),'[]'::jsonb) INTO value FROM (
                SELECT r.name,coalesce((SELECT jsonb_agg(permission ORDER BY permission)
                    FROM public.platform_role_permissions WHERE role=r.name),'[]'::jsonb) AS permissions
                FROM public.platform_roles r) t;
            value := jsonb_build_object('items',value);
        ELSIF p_operation='accounts' THEN
            SELECT coalesce(jsonb_agg(t ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT id,email,role,status,created_at FROM public.platform_accounts
                WHERE p_before IS NULL OR (created_at,id)<(p_before,p_before_id)
                ORDER BY created_at DESC,id DESC LIMIT p_limit+1) t;
        ELSIF p_operation='audit' THEN
            SELECT coalesce(jsonb_agg(t ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT id,actor_id,action,request_id,created_at FROM public.platform_audit_events
                WHERE p_before IS NULL OR (created_at,id)<(p_before,p_before_id)
                ORDER BY created_at DESC,id DESC LIMIT p_limit+1) t;
        ELSE
            UPDATE public.platform_sessions SET revoked_at=clock_timestamp()
                WHERE account_id=a.id AND revoked_at IS NULL AND (p_operation='logout_all' OR id=s.id);
            value := jsonb_build_object('revoked',true);
        END IF;
        RETURN jsonb_build_object('status',200,'value',value);
    END $$;
    ''')
    for table in ('platform_roles', 'platform_permissions', 'platform_role_permissions',
                  'platform_accounts', 'platform_sessions', 'platform_audit_events'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC')
    for function in FUNCTIONS:
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION {function} TO {role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text('''SELECT EXISTS(SELECT 1 FROM public.platform_accounts)
        OR EXISTS(SELECT 1 FROM public.platform_sessions) OR EXISTS(SELECT 1 FROM public.platform_audit_events)''')).scalar_one():
        raise RuntimeError('Cannot discard platform identity or audit history')
    for function in reversed(FUNCTIONS):
        op.execute(f'DROP FUNCTION {function}')
    for table in ('platform_audit_events', 'platform_sessions', 'platform_accounts',
                  'platform_role_permissions', 'platform_permissions', 'platform_roles'):
        op.execute(f'DROP TABLE public.{table}')
