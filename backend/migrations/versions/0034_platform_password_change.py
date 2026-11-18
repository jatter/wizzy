"""Self-service platform password rotation, fenced by live sessions and credentials."""
import os
import re

from alembic import op
from sqlalchemy import text

revision = '0034_platform_password_change'
down_revision = '0033_review_plan_history'
FUNCTIONS = (
    'public.platform_password_credentials(text)',
    'public.platform_change_password(text,uuid,text,text)',
)


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Password migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication) FROM pg_roles WHERE rolname=:role'''),
                        {'role': role}).scalar_one_or_none():
        raise RuntimeError('Password migration requires a restricted API role')
    quoted_role = bind.dialect.identifier_preparer.quote(role)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action IN ('password.changed','password.denied'))''')
    op.execute('''
    CREATE FUNCTION public.platform_password_credentials(p_hash text)
    RETURNS TABLE(id uuid,password_hash text,credential_version uuid)
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT a.id,a.password_hash,a.credential_version FROM public.platform_accounts a
        JOIN public.platform_sessions s ON s.account_id=a.id
        WHERE s.token_hash=p_hash AND a.status='active' AND s.revoked_at IS NULL
            AND s.expires_at>clock_timestamp() AND s.credential_version=a.credential_version
    $$;

    CREATE FUNCTION public.platform_change_password(p_hash text,p_version uuid,p_password text,p_request text)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' OR a.credential_version IS DISTINCT FROM p_version THEN
            RETURN jsonb_build_object('status',401);
        END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp()
            OR s.credential_version<>a.credential_version THEN
            RETURN jsonb_build_object('status',401);
        END IF;
        IF p_password IS NULL THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'password.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_password NOT LIKE '$argon2id$%' OR length(p_password)>512 THEN
            RETURN jsonb_build_object('status',422);
        END IF;
        UPDATE public.platform_accounts SET password_hash=p_password,credential_version=gen_random_uuid()
            WHERE id=a.id;
        UPDATE public.platform_sessions SET revoked_at=clock_timestamp()
            WHERE account_id=a.id AND revoked_at IS NULL;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'password.changed',p_request);
        RETURN jsonb_build_object('status',200);
    END $$;
    ''')
    for function in FUNCTIONS:
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION {function} TO {quoted_role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action IN ('password.changed','password.denied'))")).scalar_one():
        raise RuntimeError('Cannot discard platform password audit history')
    for function in reversed(FUNCTIONS):
        op.execute(f'DROP FUNCTION {function}')
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    actions = [a for a in re.findall(r"'([^']+)'::text", previous) if a not in ('password.changed', 'password.denied')]
    quoted = ','.join("'" + a.replace("'", "''") + "'" for a in actions)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({quoted}))')
