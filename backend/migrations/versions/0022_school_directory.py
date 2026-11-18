"""Versioned school directory, independent from household or organization access."""
import os

from alembic import op
from sqlalchemy import text

revision = '0022_school_directory'
down_revision = '0021_account_governance'
CAPABILITY = 'public.school_directory_request(text,text,text,jsonb,uuid,integer,timestamptz,uuid,text,text)'
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('School migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('School migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    INSERT INTO public.platform_permissions VALUES ('platform.schools.read'),('platform.schools.write');
    INSERT INTO public.platform_role_permissions VALUES ('security_admin','platform.schools.read'),
        ('security_admin','platform.schools.write'),('operator','platform.schools.read');
    ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check;
    ''')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},'schools.create','schools.revise'))''')
    op.execute(r'''
    CREATE FUNCTION public.school_metadata_valid(c jsonb) RETURNS boolean LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    DECLARE k text;
    BEGIN
        IF c IS NULL OR jsonb_typeof(c)<>'object' OR NOT c ?& ARRAY['name','region','academic_year','status']
            OR c - ARRAY['name','region','academic_year','status'] <> '{}'::jsonb THEN RETURN false; END IF;
        FOREACH k IN ARRAY ARRAY['name','region','academic_year','status'] LOOP
            IF jsonb_typeof(c->k) IS DISTINCT FROM 'string' OR c->>k<>btrim(c->>k)
                OR c->>k ~ '[[:cntrl:]]' THEN RETURN false; END IF;
        END LOOP;
        IF length(c->>'name') NOT BETWEEN 1 AND 120 OR length(c->>'region') NOT BETWEEN 1 AND 120
            OR c->>'status' NOT IN ('active','disabled') OR c->>'academic_year' !~ '^[0-9]{4}-[0-9]{4}$'
            THEN RETURN false; END IF;
        RETURN left(c->>'academic_year',4)::integer BETWEEN 2000 AND 9998
            AND right(c->>'academic_year',4)::integer=left(c->>'academic_year',4)::integer+1;
    END $$;
    CREATE TABLE public.school_directory (
        id uuid PRIMARY KEY, code text NOT NULL UNIQUE CHECK(code ~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'),
        current_revision integer NOT NULL CHECK(current_revision BETWEEN 1 AND 999999999),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.school_directory_revisions (
        id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school_directory(id),
        revision integer NOT NULL CHECK(revision BETWEEN 1 AND 999999999),
        config jsonb NOT NULL CHECK(public.school_metadata_valid(config)),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        request_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(school_id,revision), UNIQUE(actor_id,operation_id)
    );
    ALTER TABLE public.school_directory ADD CONSTRAINT school_directory_current_revision
        FOREIGN KEY(id,current_revision) REFERENCES public.school_directory_revisions(school_id,revision)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX school_revision_page ON public.school_directory_revisions(school_id,created_at DESC,id DESC);
    CREATE FUNCTION public.school_revision_immutable() RETURNS trigger LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    BEGIN RAISE EXCEPTION 'School history is immutable'; END $$;
    CREATE TRIGGER school_revision_immutable BEFORE UPDATE OR DELETE ON public.school_directory_revisions
        FOR EACH ROW EXECUTE FUNCTION public.school_revision_immutable();
    CREATE TRIGGER school_revision_no_truncate BEFORE TRUNCATE ON public.school_directory_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION public.school_revision_immutable();
    CREATE FUNCTION public.school_directory_view(p_id uuid,p_revision integer) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT r.config || jsonb_build_object('id',s.id,'code',s.code,'revision',r.revision,'revision_id',r.id,
            'reason',r.reason,'actor_id',r.actor_id,'created_at',r.created_at,'registered_at',s.created_at)
        FROM public.school_directory s JOIN public.school_directory_revisions r ON r.school_id=s.id AND r.revision=p_revision
        WHERE s.id=p_id
    $$;
    CREATE FUNCTION public.school_directory_request(p_hash text,p_op text,p_request text,p_payload jsonb,p_school uuid,
        p_limit integer,p_before timestamptz,p_before_id uuid,p_q text,p_status text) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        school public.school_directory%ROWTYPE; prior public.school_directory_revisions%ROWTYPE;
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
            OR (p_status IS NOT NULL AND p_status NOT IN ('active','disabled'))
            OR (p_op IN ('detail','history','revise') AND p_school IS NULL)
            OR (p_op='create' AND p_school IS NOT NULL)
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op IN ('create','revise') THEN 'platform.schools.write' ELSE 'platform.schools.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT d.id,d.created_at,public.school_directory_view(d.id,d.current_revision) AS v FROM public.school_directory d
                JOIN public.school_directory_revisions r ON r.school_id=d.id AND r.revision=d.current_revision
                WHERE (p_before IS NULL OR (d.created_at,d.id)<(p_before,p_before_id))
                    AND (p_status IS NULL OR r.config->>'status'=p_status)
                    AND (p_q='' OR position(lower(p_q) IN lower(d.code||' '||(r.config->>'name')||' '||(r.config->>'region')))>0)
                ORDER BY d.created_at DESC,d.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('detail','history') THEN
            SELECT * INTO school FROM public.school_directory WHERE id=p_school;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN value := public.school_directory_view(school.id,school.current_revision);
            ELSE
                SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                    SELECT r.id,r.created_at,public.school_directory_view(school.id,r.revision) AS v FROM public.school_directory_revisions r
                    WHERE r.school_id=school.id AND (p_before IS NULL OR (r.created_at,r.id)<(p_before,p_before_id))
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
        snapshot := jsonb_build_object('operation',p_op,'school_id',p_school,'payload',p_payload);
        SELECT * INTO prior FROM public.school_directory_revisions WHERE actor_id=a.id AND operation_id=operation;
        IF FOUND THEN
            IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',public.school_directory_view(prior.school_id,prior.revision));
        END IF;
        IF p_op='create' THEN
            IF p_payload - ARRAY['operation_id','reason','code','name','region','academic_year','status']<>'{}'::jsonb
                OR jsonb_typeof(p_payload->'code') IS DISTINCT FROM 'string'
                OR p_payload->>'code' !~ '^[A-Z0-9][A-Z0-9_-]{1,39}$' THEN RETURN jsonb_build_object('status',422); END IF;
            c := p_payload - ARRAY['operation_id','reason','code']; new_id := gen_random_uuid(); n := 1;
        ELSE
            IF jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
                OR p_payload->>'expected_revision' !~ '^[0-9]{1,9}$'
                THEN RETURN jsonb_build_object('status',422); END IF;
            expected := (p_payload->>'expected_revision')::integer;
            IF expected NOT BETWEEN 1 AND 999999998 THEN RETURN jsonb_build_object('status',422); END IF;
            SELECT * INTO school FROM public.school_directory WHERE id=p_school FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF school.current_revision<>expected THEN RETURN jsonb_build_object('status',409,'code','school_revision_conflict'); END IF;
            c := p_payload - ARRAY['operation_id','reason','expected_revision']; new_id := school.id; n := school.current_revision+1;
        END IF;
        IF NOT public.school_metadata_valid(c) THEN RETURN jsonb_build_object('status',422); END IF;
        IF p_op='create' THEN
            INSERT INTO public.school_directory(id,code,current_revision) VALUES(new_id,p_payload->>'code',n)
                ON CONFLICT(code) DO NOTHING;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409,'code','school_code_conflict'); END IF;
        END IF;
        INSERT INTO public.school_directory_revisions(id,school_id,revision,config,reason,actor_id,operation_id,request_snapshot)
            VALUES(gen_random_uuid(),new_id,n,c,p_payload->>'reason',a.id,operation,snapshot);
        IF p_op='revise' THEN UPDATE public.school_directory SET current_revision=n WHERE id=new_id; END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'schools.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.school_directory_view(new_id,n));
    END $$;
    ''')
    for table in ('school_directory', 'school_directory_revisions'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
    for function in (CAPABILITY, 'public.school_metadata_valid(jsonb)', 'public.school_directory_view(uuid,integer)', 'public.school_revision_immutable()'):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.school_directory)
        OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action IN ('schools.create','schools.revise'))''')).scalar_one():
        raise RuntimeError('Cannot discard school directory or change history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute('DROP FUNCTION public.school_directory_view(uuid,integer)')
    op.execute('ALTER TABLE public.school_directory DROP CONSTRAINT school_directory_current_revision')
    op.execute('DROP TABLE public.school_directory_revisions')
    op.execute('DROP TABLE public.school_directory')
    op.execute('DROP FUNCTION public.school_revision_immutable()')
    op.execute('DROP FUNCTION public.school_metadata_valid(jsonb)')
    op.execute("DELETE FROM public.platform_role_permissions WHERE permission IN ('platform.schools.read','platform.schools.write')")
    op.execute("DELETE FROM public.platform_permissions WHERE name IN ('platform.schools.read','platform.schools.write')")
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
