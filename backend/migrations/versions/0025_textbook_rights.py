"""Versioned textbook metadata and independent rights review; no content publication."""
import os

from alembic import op
from sqlalchemy import text

revision = '0025_textbook_rights'
down_revision = '0024_school_roster'
CAPABILITY = 'public.textbook_request(text,text,text,jsonb,uuid,integer,timestamptz,uuid,text,text)'
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject','schools.create','schools.revise','schools.structure.create','schools.structure.revise','schools.roster.create','schools.roster.revise'"
ACTIONS = "'textbooks.create','textbooks.revise','textbooks.submit','textbooks.approve','textbooks.reject','textbooks.withdraw'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Textbook migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('Textbook migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    INSERT INTO public.platform_permissions VALUES ('platform.textbooks.read'),('platform.textbooks.write'),('platform.textbooks.review');
    INSERT INTO public.platform_role_permissions VALUES ('security_admin','platform.textbooks.read'),
        ('security_admin','platform.textbooks.write'),('security_admin','platform.textbooks.review'),('operator','platform.textbooks.read');
    ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check;
    ''')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},{ACTIONS}))''')
    op.execute(r'''
    CREATE FUNCTION public.textbook_metadata_valid(c jsonb) RETURNS boolean LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    DECLARE k text; rights jsonb; g integer;
    BEGIN
        IF c IS NULL OR jsonb_typeof(c)<>'object'
            OR NOT c ?& ARRAY['title','stage','grade','subject','publisher','edition_label','academic_year','volume','rights']
            OR c - ARRAY['title','stage','grade','subject','publisher','edition_label','academic_year','volume','rights']<>'{}'::jsonb
            THEN RETURN false; END IF;
        FOREACH k IN ARRAY ARRAY['title','stage','subject','publisher','edition_label','academic_year','volume'] LOOP
            IF jsonb_typeof(c->k) IS DISTINCT FROM 'string' OR length(c->>k) NOT BETWEEN 1 AND 120
                OR c->>k<>btrim(c->>k) OR c->>k ~ '[[:cntrl:]]' THEN RETURN false; END IF;
        END LOOP;
        IF jsonb_typeof(c->'grade') IS DISTINCT FROM 'number' OR c->>'grade' !~ '^[0-9]{1,2}$'
            OR c->>'stage' NOT IN ('primary','junior','senior') OR c->>'volume' NOT IN ('first','second','full')
            OR c->>'academic_year' !~ '^[0-9]{4}-[0-9]{4}$' THEN RETURN false; END IF;
        g := (c->>'grade')::integer;
        IF NOT (c->>'stage'='primary' AND g BETWEEN 1 AND 6 OR c->>'stage'='junior' AND g BETWEEN 7 AND 9
            OR c->>'stage'='senior' AND g BETWEEN 10 AND 12)
            OR left(c->>'academic_year',4)::integer NOT BETWEEN 2000 AND 9998
            OR right(c->>'academic_year',4)::integer<>left(c->>'academic_year',4)::integer+1 THEN RETURN false; END IF;
        rights := c->'rights';
        IF rights='null'::jsonb THEN RETURN true; END IF;
        IF jsonb_typeof(rights)<>'object'
            OR NOT rights ?& ARRAY['basis','holder','reference','scopes','regions','valid_from','valid_until']
            OR rights - ARRAY['basis','holder','reference','scopes','regions','valid_from','valid_until']<>'{}'::jsonb
            THEN RETURN false; END IF;
        FOREACH k IN ARRAY ARRAY['basis','holder','reference','valid_from','valid_until'] LOOP
            IF jsonb_typeof(rights->k) IS DISTINCT FROM 'string' OR length(rights->>k) NOT BETWEEN 1 AND 200
                OR rights->>k<>btrim(rights->>k) OR rights->>k ~ '[[:cntrl:]]' THEN RETURN false; END IF;
        END LOOP;
        IF rights->>'basis' NOT IN ('publisher_contract','open_license','public_domain')
            OR rights->>'valid_from' !~ '^\d{4}-\d{2}-\d{2}$' OR rights->>'valid_until' !~ '^\d{4}-\d{2}-\d{2}$'
            OR (rights->>'valid_from')::date>(rights->>'valid_until')::date
            OR jsonb_typeof(rights->'scopes') IS DISTINCT FROM 'array'
            OR jsonb_typeof(rights->'regions') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(rights->'scopes') NOT BETWEEN 1 AND 4
            OR jsonb_array_length(rights->'regions') NOT BETWEEN 1 AND 20
            OR NOT (rights->'scopes' ? 'storage') THEN RETURN false; END IF;
        IF EXISTS(SELECT 1 FROM jsonb_array_elements(rights->'scopes') e
            WHERE jsonb_typeof(e)<>'string' OR e#>>'{}' NOT IN ('storage','ocr','retrieval','preview'))
            OR (SELECT count(DISTINCT e) FROM jsonb_array_elements(rights->'scopes') e)<>jsonb_array_length(rights->'scopes')
            OR EXISTS(SELECT 1 FROM jsonb_array_elements(rights->'regions') e
                WHERE jsonb_typeof(e)<>'string' OR e#>>'{}' !~ '^[A-Z]{2}(-[A-Z0-9]{1,8})?$')
            OR (SELECT count(DISTINCT e) FROM jsonb_array_elements(rights->'regions') e)<>jsonb_array_length(rights->'regions')
            THEN RETURN false; END IF;
        RETURN true;
    EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN false;
    END $$;
    CREATE FUNCTION public.textbook_availability(c jsonb,p_state text,p_at timestamptz) RETURNS text
        LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$
        SELECT CASE WHEN p_state='withdrawn' THEN 'withdrawn'
            WHEN c->'rights'='null'::jsonb THEN 'metadata_only'
            WHEN (c->'rights'->>'valid_until')::date<(p_at AT TIME ZONE 'Asia/Shanghai')::date THEN 'expired'
            WHEN p_state='pending_review' THEN 'pending_review'
            WHEN p_state<>'approved' THEN 'metadata_only'
            WHEN (c->'rights'->>'valid_from')::date>(p_at AT TIME ZONE 'Asia/Shanghai')::date THEN 'scheduled'
            ELSE 'rights_approved' END
    $$;
    CREATE TABLE public.textbook_editions (
        id uuid PRIMARY KEY, code text NOT NULL UNIQUE CHECK(code ~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'),
        created_by uuid NOT NULL REFERENCES public.platform_accounts(id),
        current_revision integer NOT NULL CHECK(current_revision BETWEEN 1 AND 999999999),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.textbook_edition_revisions (
        id uuid PRIMARY KEY, edition_id uuid NOT NULL REFERENCES public.textbook_editions(id),
        revision integer NOT NULL CHECK(revision BETWEEN 1 AND 999999999),
        state text NOT NULL CHECK(state IN ('draft','pending_review','approved','withdrawn')),
        config jsonb NOT NULL CHECK(public.textbook_metadata_valid(config)),
        action text NOT NULL CHECK(action IN ('create','revise','submit','approve','reject','withdraw')),
        editor_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        submitted_by uuid REFERENCES public.platform_accounts(id),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        request_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(state NOT IN ('pending_review','approved') OR submitted_by IS NOT NULL AND config->'rights'<>'null'::jsonb),
        UNIQUE(edition_id,revision), UNIQUE(actor_id,operation_id)
    );
    ALTER TABLE public.textbook_editions ADD CONSTRAINT textbook_current_revision
        FOREIGN KEY(id,current_revision) REFERENCES public.textbook_edition_revisions(edition_id,revision)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX textbook_revision_page ON public.textbook_edition_revisions(edition_id,created_at DESC,id DESC);
    CREATE FUNCTION public.textbook_revision_immutable() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    BEGIN RAISE EXCEPTION 'Textbook history is immutable'; END $$;
    CREATE TRIGGER textbook_revision_immutable BEFORE UPDATE OR DELETE ON public.textbook_edition_revisions
        FOR EACH ROW EXECUTE FUNCTION public.textbook_revision_immutable();
    CREATE TRIGGER textbook_revision_no_truncate BEFORE TRUNCATE ON public.textbook_edition_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION public.textbook_revision_immutable();
    CREATE FUNCTION public.textbook_view(p_id uuid,p_revision integer) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT r.config || jsonb_build_object('id',e.id,'code',e.code,'creator_id',e.created_by,
            'revision',r.revision,'revision_id',r.id,'state',r.state,'action',r.action,'editor_id',r.editor_id,
            'submitted_by',r.submitted_by,'actor_id',r.actor_id,'reason',r.reason,
            'created_at',r.created_at,'registered_at',e.created_at,
            'availability',public.textbook_availability(r.config,r.state,clock_timestamp()),'content_available',false)
        FROM public.textbook_editions e JOIN public.textbook_edition_revisions r ON r.edition_id=e.id AND r.revision=p_revision
        WHERE e.id=p_id
    $$;
    CREATE FUNCTION public.textbook_request(p_hash text,p_op text,p_request text,p_payload jsonb,p_edition uuid,
        p_limit integer,p_before timestamptz,p_before_id uuid,p_q text,p_state text) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        edition public.textbook_editions%ROWTYPE; current public.textbook_edition_revisions%ROWTYPE;
        prior public.textbook_edition_revisions%ROWTYPE; needed text; c jsonb; snapshot jsonb; operation uuid;
        expected integer; value jsonb; new_id uuid; n integer; new_state text; editor uuid; submitter uuid;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','history','create','revise','submit','approve','reject','withdraw')
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR p_q IS NULL OR length(p_q)>120 OR p_q ~ '[[:cntrl:]]'
            OR (p_state IS NOT NULL AND p_state NOT IN ('draft','pending_review','approved','withdrawn'))
            OR (p_op NOT IN ('list','create') AND p_edition IS NULL) OR (p_op='create' AND p_edition IS NOT NULL)
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100 THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op IN ('approve','reject') THEN 'platform.textbooks.review'
            WHEN p_op IN ('create','revise','submit','withdraw') THEN 'platform.textbooks.write' ELSE 'platform.textbooks.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT e.id,e.created_at,public.textbook_view(e.id,e.current_revision) AS v FROM public.textbook_editions e
                JOIN public.textbook_edition_revisions r ON r.edition_id=e.id AND r.revision=e.current_revision
                WHERE (p_before IS NULL OR (e.created_at,e.id)<(p_before,p_before_id)) AND (p_state IS NULL OR r.state=p_state)
                    AND (p_q='' OR position(lower(p_q) IN lower(e.code||' '||(r.config->>'title')||' '||
                        (r.config->>'publisher')||' '||(r.config->>'edition_label')||' '||(r.config->>'subject')))>0)
                ORDER BY e.created_at DESC,e.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('detail','history') THEN
            SELECT * INTO edition FROM public.textbook_editions WHERE id=p_edition;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN value := public.textbook_view(edition.id,edition.current_revision);
            ELSE
                SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                    SELECT r.id,r.created_at,public.textbook_view(edition.id,r.revision) AS v FROM public.textbook_edition_revisions r
                    WHERE r.edition_id=edition.id AND (p_before IS NULL OR (r.created_at,r.id)<(p_before,p_before_id))
                    ORDER BY r.created_at DESC,r.id DESC LIMIT p_limit+1) t;
            END IF;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
            OR p_payload->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
            OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string' OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500
            OR p_payload->>'reason'<>btrim(p_payload->>'reason') OR p_payload->>'reason' ~ '[[:cntrl:]]'
            THEN RETURN jsonb_build_object('status',422); END IF;
        operation := (p_payload->>'operation_id')::uuid;
        snapshot := jsonb_build_object('operation',p_op,'edition_id',p_edition,'payload',p_payload);
        SELECT * INTO prior FROM public.textbook_edition_revisions WHERE actor_id=a.id AND operation_id=operation;
        IF FOUND THEN
            IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',public.textbook_view(prior.edition_id,prior.revision));
        END IF;
        IF p_op='create' THEN
            IF jsonb_typeof(p_payload->'code') IS DISTINCT FROM 'string'
                OR p_payload->>'code' !~ '^[A-Z0-9][A-Z0-9_-]{1,39}$' THEN RETURN jsonb_build_object('status',422); END IF;
            c := p_payload - ARRAY['operation_id','reason','code']; new_id := gen_random_uuid(); n := 1;
            new_state := 'draft'; editor := a.id; submitter := NULL;
        ELSE
            IF jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
                OR p_payload->>'expected_revision' !~ '^[0-9]{1,9}$' THEN RETURN jsonb_build_object('status',422); END IF;
            expected := (p_payload->>'expected_revision')::integer;
            IF expected NOT BETWEEN 1 AND 999999998 THEN RETURN jsonb_build_object('status',422); END IF;
            SELECT * INTO edition FROM public.textbook_editions WHERE id=p_edition FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF edition.current_revision<>expected THEN RETURN jsonb_build_object('status',409,'code','textbook_revision_conflict'); END IF;
            SELECT * INTO current FROM public.textbook_edition_revisions WHERE edition_id=edition.id AND revision=edition.current_revision;
            c := current.config; new_id := edition.id; n := expected+1; new_state := current.state;
            editor := current.editor_id; submitter := current.submitted_by;
            IF p_op='revise' THEN
                IF current.state NOT IN ('draft','withdrawn') THEN RETURN jsonb_build_object('status',409,'code','textbook_state_conflict'); END IF;
                c := p_payload - ARRAY['operation_id','reason','expected_revision']; new_state := 'draft'; editor := a.id; submitter := NULL;
            ELSE
                IF p_payload - ARRAY['operation_id','reason','expected_revision']<>'{}'::jsonb THEN RETURN jsonb_build_object('status',422); END IF;
                IF p_op IN ('submit','approve') AND (c->'rights'='null'::jsonb
                    OR (c->'rights'->>'valid_until')::date<(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date)
                    THEN RETURN jsonb_build_object('status',422,'code','textbook_rights_incomplete'); END IF;
                IF p_op='submit' THEN
                    IF current.state<>'draft' THEN RETURN jsonb_build_object('status',409,'code','textbook_state_conflict'); END IF;
                    new_state := 'pending_review'; submitter := a.id;
                ELSIF p_op IN ('approve','reject') THEN
                    IF current.state<>'pending_review' THEN RETURN jsonb_build_object('status',409,'code','textbook_state_conflict'); END IF;
                    IF a.id IN (edition.created_by,current.editor_id,current.submitted_by)
                        THEN RETURN jsonb_build_object('status',409,'code','independent_reviewer_required'); END IF;
                    new_state := CASE WHEN p_op='approve' THEN 'approved' ELSE 'draft' END;
                ELSIF p_op='withdraw' THEN
                    IF current.state NOT IN ('pending_review','approved') THEN RETURN jsonb_build_object('status',409,'code','textbook_state_conflict'); END IF;
                    new_state := 'withdrawn';
                END IF;
            END IF;
        END IF;
        IF NOT public.textbook_metadata_valid(c) THEN RETURN jsonb_build_object('status',422); END IF;
        IF p_op='create' THEN
            INSERT INTO public.textbook_editions(id,code,created_by,current_revision) VALUES(new_id,p_payload->>'code',a.id,n)
                ON CONFLICT(code) DO NOTHING;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409,'code','textbook_code_conflict'); END IF;
        END IF;
        INSERT INTO public.textbook_edition_revisions(id,edition_id,revision,state,config,action,editor_id,submitted_by,reason,actor_id,operation_id,request_snapshot)
            VALUES(gen_random_uuid(),new_id,n,new_state,c,p_op,editor,submitter,p_payload->>'reason',a.id,operation,snapshot);
        IF p_op<>'create' THEN UPDATE public.textbook_editions SET current_revision=n WHERE id=new_id; END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'textbooks.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.textbook_view(new_id,n));
    END $$;
    ''')
    for table in ('textbook_editions', 'textbook_edition_revisions'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
    for function in (CAPABILITY, 'public.textbook_metadata_valid(jsonb)', 'public.textbook_availability(jsonb,text,timestamptz)',
                     'public.textbook_view(uuid,integer)', 'public.textbook_revision_immutable()'):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.textbook_editions)
        OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action LIKE 'textbooks.%')''')).scalar_one():
        raise RuntimeError('Cannot discard textbook editions or review history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute('DROP FUNCTION public.textbook_view(uuid,integer)')
    op.execute('ALTER TABLE public.textbook_editions DROP CONSTRAINT textbook_current_revision')
    op.execute('DROP TABLE public.textbook_edition_revisions')
    op.execute('DROP TABLE public.textbook_editions')
    op.execute('DROP FUNCTION public.textbook_revision_immutable()')
    op.execute('DROP FUNCTION public.textbook_availability(jsonb,text,timestamptz)')
    op.execute('DROP FUNCTION public.textbook_metadata_valid(jsonb)')
    op.execute("DELETE FROM public.platform_role_permissions WHERE permission LIKE 'platform.textbooks.%'")
    op.execute("DELETE FROM public.platform_permissions WHERE name LIKE 'platform.textbooks.%'")
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
