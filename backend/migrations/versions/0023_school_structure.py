"""School organization trees, independent of student or household authorization."""
import os

from alembic import op
from sqlalchemy import text

revision = '0023_school_structure'
down_revision = '0022_school_directory'
CAPABILITY = 'public.school_structure_request(text,text,text,jsonb,uuid,uuid,text,integer,timestamptz,uuid)'
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject','schools.create','schools.revise'"
HELPERS = ('public.school_structure_metadata_valid(jsonb)', 'public.school_structure_view(uuid,integer)')


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('School structure migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('School structure migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},'schools.structure.create','schools.structure.revise'))''')
    op.execute(r'''
    CREATE FUNCTION public.school_structure_metadata_valid(c jsonb) RETURNS boolean LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF c IS NULL OR jsonb_typeof(c)<>'object' OR NOT c ?& ARRAY['name','status','parent_id']
            OR c - ARRAY['name','status','parent_id'] <> '{}'::jsonb THEN RETURN false; END IF;
        RETURN jsonb_typeof(c->'name')='string' AND length(c->>'name') BETWEEN 1 AND 120
            AND c->>'name'=btrim(c->>'name') AND c->>'name' !~ '[[:cntrl:]]'
            AND jsonb_typeof(c->'status')='string' AND c->>'status' IN ('active','disabled')
            AND (c->'parent_id'='null'::jsonb OR (jsonb_typeof(c->'parent_id')='string'
                AND c->>'parent_id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'));
    END $$;
    CREATE TABLE public.school_structure_nodes (
        id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school_directory(id),
        academic_year text NOT NULL CHECK(academic_year ~ '^[0-9]{4}-[0-9]{4}$'
            AND left(academic_year,4)::integer BETWEEN 2000 AND 9998
            AND right(academic_year,4)::integer=left(academic_year,4)::integer+1),
        code text NOT NULL CHECK(code ~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'),
        kind text NOT NULL CHECK(kind IN ('campus','department','grade','class')),
        current_revision integer NOT NULL CHECK(current_revision BETWEEN 1 AND 999999999),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(school_id,academic_year,code)
    );
    CREATE TABLE public.school_structure_revisions (
        id uuid PRIMARY KEY, node_id uuid NOT NULL REFERENCES public.school_structure_nodes(id),
        revision integer NOT NULL CHECK(revision BETWEEN 1 AND 999999999),
        config jsonb NOT NULL CHECK(public.school_structure_metadata_valid(config)),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        request_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(node_id,revision), UNIQUE(actor_id,operation_id)
    );
    ALTER TABLE public.school_structure_nodes ADD CONSTRAINT school_structure_current_revision
        FOREIGN KEY(id,current_revision) REFERENCES public.school_structure_revisions(node_id,revision)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX school_structure_revision_page ON public.school_structure_revisions(node_id,created_at DESC,id DESC);
    CREATE TRIGGER school_structure_immutable BEFORE UPDATE OR DELETE ON public.school_structure_revisions
        FOR EACH ROW EXECUTE FUNCTION public.school_revision_immutable();
    CREATE TRIGGER school_structure_no_truncate BEFORE TRUNCATE ON public.school_structure_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION public.school_revision_immutable();
    CREATE FUNCTION public.school_structure_view(p_id uuid,p_revision integer) RETURNS jsonb LANGUAGE sql
        SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT r.config || jsonb_build_object('id',d.id,'school_id',d.school_id,'code',d.code,'kind',d.kind,
            'academic_year',d.academic_year,'revision',r.revision,'revision_id',r.id,'reason',r.reason,
            'actor_id',r.actor_id,'created_at',r.created_at,'registered_at',d.created_at)
        FROM public.school_structure_nodes d JOIN public.school_structure_revisions r ON r.node_id=d.id
        WHERE d.id=p_id AND r.revision=p_revision;
    $$;
    CREATE FUNCTION public.school_structure_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_school uuid,p_node uuid,p_year text,p_limit integer,p_before timestamptz,p_before_id uuid) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        school public.school_directory%ROWTYPE; school_config jsonb; node public.school_structure_nodes%ROWTYPE;
        parent public.school_structure_nodes%ROWTYPE; prior public.school_structure_revisions%ROWTYPE;
        needed text; c jsonb; snapshot jsonb; operation uuid; expected integer; value jsonb; new_id uuid;
        parent_id uuid; cursor_id uuid; parent_config jsonb; n integer; depth integer; subtree_depth integer;
        node_kind text; node_year text;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('tree','detail','history','create','revise') OR p_school IS NULL
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            OR (p_op IN ('detail','history','revise') AND p_node IS NULL)
            OR (p_op IN ('tree','create') AND p_node IS NOT NULL)
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op IN ('create','revise') THEN 'platform.schools.write' ELSE 'platform.schools.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op IN ('create','revise') THEN
            IF jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
                OR p_payload->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
                OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string'
                OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500 OR p_payload->>'reason'<>btrim(p_payload->>'reason')
                OR p_payload->>'reason' ~ '[[:cntrl:]]' THEN RETURN jsonb_build_object('status',422); END IF;
            operation := (p_payload->>'operation_id')::uuid;
            snapshot := jsonb_build_object('operation',p_op,'school_id',p_school,'node_id',p_node,'payload',p_payload);
            SELECT * INTO prior FROM public.school_structure_revisions WHERE actor_id=a.id AND operation_id=operation;
            IF FOUND THEN
                IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
                RETURN jsonb_build_object('status',200,'value',public.school_structure_view(prior.node_id,prior.revision));
            END IF;
        END IF;
        -- All graph mutations and school status changes serialize on the same school row.
        SELECT * INTO school FROM public.school_directory WHERE id=p_school FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT config INTO school_config FROM public.school_directory_revisions
            WHERE school_id=school.id AND revision=school.current_revision;
        IF p_op='tree' THEN
            IF p_year IS NULL OR p_year !~ '^[0-9]{4}-[0-9]{4}$' THEN RETURN jsonb_build_object('status',422); END IF;
            IF left(p_year,4)::integer NOT BETWEEN 2000 AND 9998
                OR right(p_year,4)::integer<>left(p_year,4)::integer+1 THEN RETURN jsonb_build_object('status',422); END IF;
            SELECT coalesce(jsonb_agg(public.school_structure_view(d.id,d.current_revision)
                ORDER BY d.created_at,d.id),'[]'::jsonb) INTO value FROM public.school_structure_nodes d
                WHERE d.school_id=p_school AND d.academic_year=p_year;
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('items',value,'academic_year',p_year,
                'school_status',school_config->>'status','school_revision',school.current_revision));
        END IF;
        IF p_op IN ('detail','history','revise') THEN
            SELECT * INTO node FROM public.school_structure_nodes WHERE id=p_node AND school_id=p_school;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN
                RETURN jsonb_build_object('status',200,'value',public.school_structure_view(node.id,node.current_revision));
            ELSIF p_op='history' THEN
                SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                    SELECT r.id,r.created_at,public.school_structure_view(node.id,r.revision) AS v
                    FROM public.school_structure_revisions r WHERE r.node_id=node.id
                        AND (p_before IS NULL OR (r.created_at,r.id)<(p_before,p_before_id))
                    ORDER BY r.created_at DESC,r.id DESC LIMIT p_limit+1) t;
                RETURN jsonb_build_object('status',200,'value',value);
            END IF;
        END IF;
        IF school_config->>'status'<>'active' THEN RETURN jsonb_build_object('status',409,'code','school_disabled'); END IF;
        IF p_op='create' THEN
            IF jsonb_typeof(p_payload->'code') IS DISTINCT FROM 'string' OR p_payload->>'code' !~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'
                OR jsonb_typeof(p_payload->'kind') IS DISTINCT FROM 'string'
                OR p_payload->>'kind' NOT IN ('campus','department','grade','class')
                OR jsonb_typeof(p_payload->'academic_year') IS DISTINCT FROM 'string'
                OR p_payload->>'academic_year' !~ '^[0-9]{4}-[0-9]{4}$' THEN RETURN jsonb_build_object('status',422); END IF;
            node_kind := p_payload->>'kind'; node_year := p_payload->>'academic_year';
            IF left(node_year,4)::integer NOT BETWEEN 2000 AND 9998
                OR right(node_year,4)::integer<>left(node_year,4)::integer+1 THEN RETURN jsonb_build_object('status',422); END IF;
            IF (SELECT count(*) FROM public.school_structure_nodes WHERE school_id=p_school AND academic_year=node_year)>=2000
                THEN RETURN jsonb_build_object('status',409,'code','structure_capacity'); END IF;
            c := p_payload - ARRAY['operation_id','reason','code','kind','academic_year']; new_id := gen_random_uuid(); n := 1;
        ELSE
            IF jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
                OR p_payload->>'expected_revision' !~ '^[0-9]{1,9}$' THEN RETURN jsonb_build_object('status',422); END IF;
            expected := (p_payload->>'expected_revision')::integer;
            IF expected NOT BETWEEN 1 AND 999999998 THEN RETURN jsonb_build_object('status',422); END IF;
            IF node.current_revision<>expected THEN RETURN jsonb_build_object('status',409,'code','structure_revision_conflict'); END IF;
            c := p_payload - ARRAY['operation_id','reason','expected_revision'];
            new_id := node.id; n := node.current_revision+1; node_kind := node.kind; node_year := node.academic_year;
        END IF;
        IF public.school_structure_metadata_valid(c) IS DISTINCT FROM true THEN RETURN jsonb_build_object('status',422); END IF;
        parent_id := (c->>'parent_id')::uuid;
        IF parent_id IS NOT NULL THEN
            SELECT * INTO parent FROM public.school_structure_nodes
                WHERE id=parent_id AND school_id=p_school AND academic_year=node_year;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',422,'code','invalid_structure_parent'); END IF;
        END IF;
        IF (node_kind='campus' AND parent_id IS NOT NULL)
            OR (node_kind='class' AND (parent_id IS NULL OR parent.kind<>'grade'))
            OR (node_kind IN ('department','grade') AND parent_id IS NOT NULL AND parent.kind NOT IN ('campus','department'))
            THEN RETURN jsonb_build_object('status',422,'code','invalid_structure_parent'); END IF;
        depth := 1; cursor_id := parent_id;
        WHILE cursor_id IS NOT NULL LOOP
            IF cursor_id=new_id THEN RETURN jsonb_build_object('status',422,'code','structure_cycle'); END IF;
            depth := depth+1;
            IF depth>8 THEN RETURN jsonb_build_object('status',422,'code','structure_depth'); END IF;
            SELECT r.config INTO parent_config FROM public.school_structure_nodes d
                JOIN public.school_structure_revisions r ON r.node_id=d.id AND r.revision=d.current_revision WHERE d.id=cursor_id;
            cursor_id := (parent_config->>'parent_id')::uuid;
        END LOOP;
        -- Moving a subtree must keep its deepest descendant within the same depth bound.
        WITH RECURSIVE descendants(id,level) AS (
            SELECT new_id,0 UNION ALL SELECT d.id,t.level+1 FROM descendants t
            JOIN public.school_structure_nodes d ON d.school_id=p_school AND d.academic_year=node_year
            JOIN public.school_structure_revisions r ON r.node_id=d.id AND r.revision=d.current_revision
            WHERE r.config->>'parent_id'=t.id::text AND t.level<8
        ) SELECT max(level) INTO subtree_depth FROM descendants;
        IF depth+subtree_depth>8 THEN RETURN jsonb_build_object('status',422,'code','structure_depth'); END IF;
        IF p_op='create' THEN
            INSERT INTO public.school_structure_nodes(id,school_id,academic_year,code,kind,current_revision)
                VALUES(new_id,p_school,node_year,p_payload->>'code',node_kind,n)
                ON CONFLICT(school_id,academic_year,code) DO NOTHING;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409,'code','structure_code_conflict'); END IF;
        END IF;
        INSERT INTO public.school_structure_revisions(id,node_id,revision,config,reason,actor_id,operation_id,request_snapshot)
            VALUES(gen_random_uuid(),new_id,n,c,p_payload->>'reason',a.id,operation,snapshot);
        IF p_op='revise' THEN UPDATE public.school_structure_nodes SET current_revision=n WHERE id=new_id; END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'schools.structure.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.school_structure_view(new_id,n));
    END $$;
    ''')
    for table in ('school_structure_nodes', 'school_structure_revisions'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
    for function in (CAPABILITY, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.school_structure_nodes)
        OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action IN ('schools.structure.create','schools.structure.revise'))''')).scalar_one():
        raise RuntimeError('Cannot discard school structure or change history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute('DROP FUNCTION public.school_structure_view(uuid,integer)')
    op.execute('ALTER TABLE public.school_structure_nodes DROP CONSTRAINT school_structure_current_revision')
    op.execute('DROP TABLE public.school_structure_revisions')
    op.execute('DROP TABLE public.school_structure_nodes')
    op.execute('DROP FUNCTION public.school_structure_metadata_valid(jsonb)')
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
