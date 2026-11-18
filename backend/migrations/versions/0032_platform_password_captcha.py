"""Platform login is password plus a one-time alphanumeric captcha."""
from alembic import op

revision = '0032_platform_password_captcha'
down_revision = '0031_account_session_revocations'


def upgrade():
    op.execute('''
    CREATE OR REPLACE FUNCTION public.platform_login(p_account uuid,p_version uuid,p_step bigint,
        p_hash text,p_session uuid,p_request text) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE;
    BEGIN
        SELECT * INTO a FROM public.platform_accounts WHERE id=p_account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' OR a.credential_version IS DISTINCT FROM p_version THEN
            RETURN NULL;
        END IF;
        UPDATE public.platform_accounts
            SET last_totp_step=floor(extract(epoch FROM clock_timestamp())/30)::bigint WHERE id=a.id;
        INSERT INTO public.platform_sessions(id,account_id,credential_version,token_hash)
            VALUES(p_session,a.id,a.credential_version,p_hash);
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'login',p_request);
        RETURN p_session;
    END $$;
    ''')


def downgrade():
    op.execute('''
    CREATE OR REPLACE FUNCTION public.platform_login(p_account uuid,p_version uuid,p_step bigint,
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
    ''')
