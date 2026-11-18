"""Private versioned model metadata; no evaluation or production approval implied."""
import os

from alembic import op
from sqlalchemy import text

revision = '0018_model_catalog'
down_revision = '0017_shared_review_plans'

CAPABILITY = 'public.model_catalog_request(text,text,text,jsonb,uuid,integer,timestamptz,uuid,text,boolean)'
HELPERS = ('public.model_metadata_valid(jsonb)', 'public.model_catalog_view(uuid,integer)',
           'public.model_revision_immutable()', 'public.model_catalog_availability(jsonb,timestamptz)')
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Model migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('Model migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    INSERT INTO public.platform_permissions VALUES ('platform.models.read'),('platform.models.write');
    INSERT INTO public.platform_role_permissions VALUES
        ('security_admin','platform.models.read'),('security_admin','platform.models.write'),
        ('operator','platform.models.read');
    ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check;
    ''')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},'models.create','models.revise'))''')
    op.execute(r'''
    CREATE FUNCTION public.model_metadata_valid(c jsonb) RETURNS boolean
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    DECLARE k text; t text;
    BEGIN
        IF c IS NULL OR jsonb_typeof(c)<>'object' OR NOT c ?& ARRAY['display_name','purposes','secret_ref',
            'contract_status','contract_reference','contract_expires_on','input_price_micros','output_price_micros','enabled']
            OR c - ARRAY['display_name','purposes','secret_ref','contract_status','contract_reference',
                'contract_expires_on','input_price_micros','output_price_micros','enabled'] <> '{}'::jsonb
            THEN RETURN false; END IF;
        IF jsonb_typeof(c->'display_name')<>'string' OR length(c->>'display_name') NOT BETWEEN 1 AND 120
            OR c->>'display_name'<>btrim(c->>'display_name') OR c->>'display_name' ~ '[[:cntrl:]]'
            OR jsonb_typeof(c->'enabled')<>'boolean' THEN RETURN false; END IF;
        IF jsonb_typeof(c->'purposes')<>'array' THEN RETURN false; END IF;
        IF jsonb_array_length(c->'purposes') NOT BETWEEN 1 AND 5 THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(c->'purposes') e
            WHERE jsonb_typeof(e)<>'string' OR e#>>'{}' NOT IN ('ocr','structure','verify','tutor','report'))
            OR (SELECT count(DISTINCT e) FROM jsonb_array_elements(c->'purposes') e)<>jsonb_array_length(c->'purposes')
            THEN RETURN false; END IF;
        IF c->'secret_ref'<>'null'::jsonb AND (jsonb_typeof(c->'secret_ref')<>'string'
            OR length(c->>'secret_ref')>240 OR c->>'secret_ref' !~ '^secret://providers/[a-z0-9][a-z0-9_-]*/[a-zA-Z0-9][a-zA-Z0-9_/-]*$')
            THEN RETURN false; END IF;
        IF jsonb_typeof(c->'contract_status')<>'string' OR c->>'contract_status' NOT IN ('pending','approved','rejected','expired')
            THEN RETURN false; END IF;
        IF c->'contract_reference'<>'null'::jsonb AND (jsonb_typeof(c->'contract_reference')<>'string'
            OR length(c->>'contract_reference') NOT BETWEEN 1 AND 200
            OR c->>'contract_reference'<>btrim(c->>'contract_reference') OR c->>'contract_reference' ~ '[[:cntrl:]]')
            THEN RETURN false; END IF;
        IF c->'contract_expires_on'<>'null'::jsonb THEN
            IF jsonb_typeof(c->'contract_expires_on')<>'string' OR c->>'contract_expires_on' !~ '^\d{4}-\d{2}-\d{2}$'
                THEN RETURN false; END IF;
            PERFORM (c->>'contract_expires_on')::date;
        END IF;
        IF c->>'contract_status'='approved' AND (c->'contract_reference'='null'::jsonb OR c->'contract_expires_on'='null'::jsonb)
            THEN RETURN false; END IF;
        IF (c->>'enabled')::boolean AND (c->>'contract_status'<>'approved' OR c->'secret_ref'='null'::jsonb)
            THEN RETURN false; END IF;
        FOREACH k IN ARRAY ARRAY['input_price_micros','output_price_micros'] LOOP
            IF c->k<>'null'::jsonb THEN
                t := c->>k;
                IF jsonb_typeof(c->k)<>'number' OR t !~ '^[0-9]+$' OR t::numeric>9007199254740991
                    THEN RETURN false; END IF;
            END IF;
        END LOOP;
        RETURN true;
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow OR numeric_value_out_of_range THEN RETURN false;
    END $$;
    CREATE FUNCTION public.model_catalog_availability(c jsonb,p_at timestamptz) RETURNS text
    LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$
        SELECT CASE
            WHEN NOT (c->>'enabled')::boolean THEN 'disabled'
            WHEN c->>'contract_status'='expired' THEN 'contract_expired'
            WHEN c->>'contract_status'<>'approved' THEN 'contract_unapproved'
            WHEN (c->>'contract_expires_on')::date < (p_at AT TIME ZONE 'Asia/Shanghai')::date THEN 'contract_expired'
            WHEN c->'secret_ref'='null'::jsonb THEN 'credential_missing'
            ELSE 'available' END
    $$;
    CREATE TABLE public.model_catalog (
        id uuid PRIMARY KEY, provider text NOT NULL CHECK(provider ~ '^[a-z0-9][a-z0-9._-]{0,79}$'),
        model_key text NOT NULL CHECK(model_key ~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'),
        fixed_version text NOT NULL CHECK(fixed_version ~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'
            AND lower(fixed_version) !~ '(^|[._/-])(latest|auto|default|stable|current|preview)([._/-]|$)'),
        region text NOT NULL CHECK(region ~ '^[a-z0-9][a-z0-9._-]{0,79}$'),
        current_revision integer NOT NULL CHECK(current_revision>0), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(provider,model_key,fixed_version,region)
    );
    CREATE TABLE public.model_catalog_revisions (
        id uuid PRIMARY KEY, model_id uuid NOT NULL REFERENCES public.model_catalog(id),
        revision integer NOT NULL CHECK(revision>0), config jsonb NOT NULL CHECK(public.model_metadata_valid(config)),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        request_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(model_id,revision), UNIQUE(actor_id,operation_id)
    );
    ALTER TABLE public.model_catalog ADD CONSTRAINT model_catalog_current_revision
        FOREIGN KEY(id,current_revision) REFERENCES public.model_catalog_revisions(model_id,revision)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX model_catalog_revision_page ON public.model_catalog_revisions(model_id,created_at DESC,id DESC);
    CREATE FUNCTION public.model_revision_immutable() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    BEGIN RAISE EXCEPTION 'Model revisions are immutable'; END $$;
    CREATE TRIGGER model_revision_immutable BEFORE UPDATE OR DELETE ON public.model_catalog_revisions
        FOR EACH ROW EXECUTE FUNCTION public.model_revision_immutable();
    CREATE FUNCTION public.model_catalog_view(p_id uuid,p_revision integer) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT (r.config-'secret_ref') || jsonb_build_object('id',m.id,'provider',m.provider,'model_key',m.model_key,
            'fixed_version',m.fixed_version,'region',m.region,'revision',r.revision,'revision_id',r.id,
            'reason',r.reason,'created_at',r.created_at,'registered_at',m.created_at,
            'credential_configured',r.config->'secret_ref'<>'null'::jsonb,
            'catalog_status',public.model_catalog_availability(r.config,clock_timestamp()),
            'accuracy',NULL,'evaluation_status','not_evaluated','production_eligible',false)
        FROM public.model_catalog m JOIN public.model_catalog_revisions r ON r.model_id=m.id AND r.revision=p_revision
        WHERE m.id=p_id
    $$;
    CREATE FUNCTION public.model_catalog_request(p_hash text,p_op text,p_request text,p_payload jsonb,p_model uuid,
        p_limit integer,p_before timestamptz,p_before_id uuid,p_q text,p_enabled boolean) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        current_model public.model_catalog%ROWTYPE; prior public.model_catalog_revisions%ROWTYPE;
        needed text; c jsonb; snapshot jsonb; operation uuid; expected integer; value jsonb; new_id uuid; n integer;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','history','create','revise') OR p_payload IS NULL
            OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR p_q IS NULL OR length(p_q)>120 OR p_q ~ '[[:cntrl:]]'
            OR (p_op IN ('detail','history','revise') AND p_model IS NULL)
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op IN ('create','revise') THEN 'platform.models.write' ELSE 'platform.models.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT m.*,public.model_catalog_view(m.id,m.current_revision) AS v FROM public.model_catalog m
                JOIN public.model_catalog_revisions r ON r.model_id=m.id AND r.revision=m.current_revision
                WHERE (p_before IS NULL OR (m.created_at,m.id)<(p_before,p_before_id))
                    AND (p_enabled IS NULL OR (r.config->>'enabled')::boolean=p_enabled)
                    AND (p_q='' OR position(lower(p_q) IN lower(m.provider||' '||m.model_key||' '||m.fixed_version||' '||(r.config->>'display_name')))>0)
                ORDER BY m.created_at DESC,m.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('detail','history') THEN
            SELECT * INTO current_model FROM public.model_catalog WHERE id=p_model;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN value := public.model_catalog_view(current_model.id,current_model.current_revision);
            ELSE
                SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                    SELECT r.id,r.created_at,public.model_catalog_view(current_model.id,r.revision) AS v FROM public.model_catalog_revisions r
                    WHERE r.model_id=current_model.id AND (p_before IS NULL OR (r.created_at,r.id)<(p_before,p_before_id))
                    ORDER BY r.created_at DESC,r.id DESC LIMIT p_limit+1) t;
            END IF;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        -- Invalid direct SQL input must not reach casts or bypass HTTP validation.
        IF jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string' OR
            (p_payload->>'operation_id') !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
            OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string'
            OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500
            OR p_payload->>'reason'<>btrim(p_payload->>'reason') OR p_payload->>'reason' ~ '[[:cntrl:]]'
            THEN RETURN jsonb_build_object('status',422); END IF;
        operation := (p_payload->>'operation_id')::uuid;
        snapshot := jsonb_build_object('operation',p_op,'model_id',p_model,'payload',p_payload);
        SELECT * INTO prior FROM public.model_catalog_revisions WHERE actor_id=a.id AND operation_id=operation;
        IF FOUND THEN
            IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409); END IF;
            RETURN jsonb_build_object('status',201,'value',public.model_catalog_view(prior.model_id,prior.revision));
        END IF;
        IF p_op='create' THEN
            IF p_payload - ARRAY['operation_id','reason','provider','model_key','fixed_version','region','display_name','purposes',
                'secret_ref','contract_status','contract_reference','contract_expires_on','input_price_micros','output_price_micros','enabled']<>'{}'::jsonb
                THEN RETURN jsonb_build_object('status',422); END IF;
            FOREACH needed IN ARRAY ARRAY['provider','model_key','fixed_version','region'] LOOP
                IF jsonb_typeof(p_payload->needed) IS DISTINCT FROM 'string' THEN RETURN jsonb_build_object('status',422); END IF;
            END LOOP;
            IF p_payload->>'provider' !~ '^[a-z0-9][a-z0-9._-]{0,79}$'
                OR p_payload->>'model_key' !~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'
                OR p_payload->>'region' !~ '^[a-z0-9][a-z0-9._-]{0,79}$'
                OR p_payload->>'fixed_version' !~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'
                OR lower(p_payload->>'fixed_version') ~ '(^|[._/-])(latest|auto|default|stable|current|preview)([._/-]|$)'
                THEN RETURN jsonb_build_object('status',422); END IF;
            c := p_payload-ARRAY['operation_id','reason','provider','model_key','fixed_version','region'];
            new_id := gen_random_uuid(); n := 1;
        ELSE
            IF jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
                OR p_payload->>'expected_revision' !~ '^[0-9]{1,9}$' THEN RETURN jsonb_build_object('status',422); END IF;
            expected := (p_payload->>'expected_revision')::integer;
            SELECT * INTO current_model FROM public.model_catalog WHERE id=p_model FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF current_model.current_revision<>expected THEN RETURN jsonb_build_object('status',409); END IF;
            c := p_payload-ARRAY['operation_id','expected_revision','reason'];
            IF NOT c ? 'secret_ref' THEN
                c := c || jsonb_build_object('secret_ref',(SELECT config->'secret_ref' FROM public.model_catalog_revisions
                    WHERE model_id=current_model.id AND revision=current_model.current_revision));
            END IF;
            new_id := current_model.id; n := current_model.current_revision+1;
        END IF;
        IF NOT public.model_metadata_valid(c) THEN RETURN jsonb_build_object('status',422); END IF;
        IF (c->>'enabled')::boolean AND public.model_catalog_availability(c,clock_timestamp())<>'available' THEN RETURN jsonb_build_object('status',422); END IF;
        IF p_op='create' THEN
            INSERT INTO public.model_catalog(id,provider,model_key,fixed_version,region,current_revision)
                VALUES(new_id,p_payload->>'provider',p_payload->>'model_key',p_payload->>'fixed_version',p_payload->>'region',n)
                ON CONFLICT(provider,model_key,fixed_version,region) DO NOTHING;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409); END IF;
        END IF;
        INSERT INTO public.model_catalog_revisions(id,model_id,revision,config,reason,actor_id,operation_id,request_snapshot)
            VALUES(gen_random_uuid(),new_id,n,c,p_payload->>'reason',a.id,operation,snapshot);
        IF p_op='revise' THEN UPDATE public.model_catalog SET current_revision=n WHERE id=new_id; END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'models.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.model_catalog_view(new_id,n));
    END $$;
    ''')
    for table in ('model_catalog', 'model_catalog_revisions'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC')
    for function in (*HELPERS, CAPABILITY):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.model_catalog)
        OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action IN ('models.create','models.revise'))''')).scalar_one():
        raise RuntimeError('Cannot discard model catalog or revision history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute(f'DROP FUNCTION {HELPERS[1]}')
    op.execute('ALTER TABLE public.model_catalog DROP CONSTRAINT model_catalog_current_revision')
    op.execute('DROP TABLE public.model_catalog_revisions')
    op.execute('DROP TABLE public.model_catalog')
    op.execute(f'DROP FUNCTION {HELPERS[0]}')
    op.execute(f'DROP FUNCTION {HELPERS[2]}')
    op.execute(f'DROP FUNCTION {HELPERS[3]}')
    op.execute("DELETE FROM public.platform_role_permissions WHERE permission IN ('platform.models.read','platform.models.write')")
    op.execute("DELETE FROM public.platform_permissions WHERE name IN ('platform.models.read','platform.models.write')")
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
