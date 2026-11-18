"""Simple callable model configs: name, endpoint, encrypted key and call priority."""
import os
import re

from alembic import op
from sqlalchemy import text

revision = '0035_model_configs'
down_revision = '0034_platform_password_change'
CAPABILITY = 'public.model_config_request(text,text,text,jsonb,uuid)'
VIEW = 'public.model_config_view(public.model_configs)'
ACTIONS = ('model_configs.create', 'model_configs.update', 'model_configs.delete', 'model_configs.test')


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Model config migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole)
        FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('Model config migration requires a restricted API role')
    quoted_role = bind.dialect.identifier_preparer.quote(role)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    added = ','.join(f"'{action}'" for action in ACTIONS)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action IN ({added}))''')
    op.execute(r'''
    CREATE TABLE public.model_configs (
        id uuid PRIMARY KEY,
        name text NOT NULL CHECK(length(name) BETWEEN 1 AND 120 AND name=btrim(name) AND name !~ '[[:cntrl:]]'),
        base_url text NOT NULL CHECK(length(base_url) BETWEEN 8 AND 500 AND base_url ~ '^https?://[^[:space:][:cntrl:]]+$'),
        api_key_cipher text CHECK(api_key_cipher IS NULL OR length(api_key_cipher) BETWEEN 1 AND 4000),
        api_key_hint text CHECK(api_key_hint IS NULL OR length(api_key_hint) BETWEEN 1 AND 12),
        priority integer NOT NULL CHECK(priority BETWEEN 1 AND 9999),
        last_test_ok boolean,
        last_test_at timestamptz,
        last_test_message text CHECK(last_test_message IS NULL OR length(last_test_message) <= 300),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_by uuid NOT NULL REFERENCES public.platform_accounts(id),
        CHECK((api_key_cipher IS NULL) = (api_key_hint IS NULL)),
        UNIQUE(name, base_url)
    );
    CREATE INDEX model_configs_priority ON public.model_configs(priority, created_at, id);

    CREATE FUNCTION public.model_config_view(m public.model_configs) RETURNS jsonb
    LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',m.id,'name',m.name,'base_url',m.base_url,'priority',m.priority,
            'api_key_configured',m.api_key_cipher IS NOT NULL,'api_key_hint',m.api_key_hint,
            'last_test_ok',m.last_test_ok,'last_test_at',m.last_test_at,'last_test_message',m.last_test_message,
            'created_at',m.created_at,'updated_at',m.updated_at)
    $$;

    CREATE FUNCTION public.model_config_request(p_hash text,p_op text,p_request text,p_payload jsonb,p_id uuid)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        m public.model_configs%ROWTYPE; needed text; value jsonb; has_key boolean;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR SHARE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','create','update','delete','secret','record')
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
            OR (p_op IN ('update','delete','secret','record') AND p_id IS NULL)
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op='list' THEN 'platform.models.read' ELSE 'platform.models.write' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(public.model_config_view(c) ORDER BY c.priority,c.created_at,c.id),'[]'::jsonb)
                INTO value FROM public.model_configs c;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('create','update') THEN
            IF p_payload - ARRAY['name','base_url','priority','api_key_cipher','api_key_hint']<>'{}'::jsonb
                OR jsonb_typeof(p_payload->'name') IS DISTINCT FROM 'string'
                OR jsonb_typeof(p_payload->'base_url') IS DISTINCT FROM 'string'
                OR jsonb_typeof(p_payload->'priority') IS DISTINCT FROM 'number'
                OR p_payload->>'priority' !~ '^[0-9]{1,4}$'
                OR (p_payload ? 'api_key_cipher')<>(p_payload ? 'api_key_hint')
                OR (p_payload ? 'api_key_cipher' AND (jsonb_typeof(p_payload->'api_key_cipher')<>'string'
                    OR jsonb_typeof(p_payload->'api_key_hint')<>'string'))
                THEN RETURN jsonb_build_object('status',422); END IF;
            has_key := p_payload ? 'api_key_cipher';
            BEGIN
                IF p_op='create' THEN
                    INSERT INTO public.model_configs(id,name,base_url,api_key_cipher,api_key_hint,priority,updated_by)
                        VALUES(gen_random_uuid(),p_payload->>'name',p_payload->>'base_url',
                            p_payload->>'api_key_cipher',p_payload->>'api_key_hint',(p_payload->>'priority')::integer,a.id)
                        RETURNING * INTO m;
                ELSE
                    UPDATE public.model_configs SET name=p_payload->>'name',base_url=p_payload->>'base_url',
                        priority=(p_payload->>'priority')::integer,
                        api_key_cipher=CASE WHEN has_key THEN p_payload->>'api_key_cipher' ELSE api_key_cipher END,
                        api_key_hint=CASE WHEN has_key THEN p_payload->>'api_key_hint' ELSE api_key_hint END,
                        last_test_ok=NULL,last_test_at=NULL,last_test_message=NULL,
                        updated_at=clock_timestamp(),updated_by=a.id
                        WHERE id=p_id RETURNING * INTO m;
                    IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
                END IF;
            EXCEPTION
                WHEN unique_violation THEN RETURN jsonb_build_object('status',409);
                WHEN check_violation OR numeric_value_out_of_range THEN RETURN jsonb_build_object('status',422);
            END;
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'model_configs.'||p_op,p_request);
            RETURN jsonb_build_object('status',CASE WHEN p_op='create' THEN 201 ELSE 200 END,
                'value',public.model_config_view(m));
        END IF;
        IF p_op='delete' THEN
            DELETE FROM public.model_configs WHERE id=p_id RETURNING * INTO m;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'model_configs.delete',p_request);
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('id',m.id));
        END IF;
        IF p_op='secret' THEN
            SELECT * INTO m FROM public.model_configs WHERE id=p_id;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('name',m.name,'base_url',m.base_url,
                'api_key_cipher',m.api_key_cipher));
        END IF;
        IF jsonb_typeof(p_payload->'ok') IS DISTINCT FROM 'boolean'
            OR jsonb_typeof(p_payload->'message') IS DISTINCT FROM 'string'
            OR length(p_payload->>'message') NOT BETWEEN 1 AND 300
            THEN RETURN jsonb_build_object('status',422); END IF;
        UPDATE public.model_configs SET last_test_ok=(p_payload->>'ok')::boolean,last_test_at=clock_timestamp(),
            last_test_message=p_payload->>'message' WHERE id=p_id RETURNING * INTO m;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'model_configs.test',p_request);
        RETURN jsonb_build_object('status',200,'value',public.model_config_view(m));
    END $$;
    ''')
    op.execute('ALTER TABLE public.model_configs ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public.model_configs FORCE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON public.model_configs FROM PUBLIC')
    for function in (VIEW, CAPABILITY):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {quoted_role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text('SELECT EXISTS(SELECT 1 FROM public.model_configs)')).scalar_one():
        raise RuntimeError('Cannot discard configured models')
    if bind.execute(text('SELECT EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action = ANY(:actions))'),
                    {'actions': list(ACTIONS)}).scalar_one():
        raise RuntimeError('Cannot discard model config audit history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute(f'DROP FUNCTION {VIEW}')
    op.execute('DROP TABLE public.model_configs')
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    actions = [a for a in re.findall(r"'([^']+)'::text", previous) if a not in ACTIONS]
    quoted = ','.join("'" + a.replace("'", "''") + "'" for a in actions)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({quoted}))')
