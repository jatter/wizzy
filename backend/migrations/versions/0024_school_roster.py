"""Explicit school student identities and immutable annual enrollment revisions."""
import os

from alembic import op
from sqlalchemy import text

revision = '0024_school_roster'
down_revision = '0023_school_structure'
CAPABILITY = 'public.school_roster_request(text,text,text,jsonb,uuid,uuid,text,text,text,integer,timestamptz,uuid)'
HELPERS = ('public.school_roster_metadata_valid(jsonb)', 'public.school_roster_view(uuid,integer)')
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject','schools.create','schools.revise','schools.structure.create','schools.structure.revise'"
TABLES = ('school_roster_students', 'school_enrollments', 'school_enrollment_revisions')


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('School roster migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('School roster migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},'schools.roster.create','schools.roster.revise'))''')
    op.execute(r'''
    INSERT INTO public.platform_permissions VALUES ('platform.students.read'),('platform.students.write');
    INSERT INTO public.platform_role_permissions VALUES ('security_admin','platform.students.read'),
        ('security_admin','platform.students.write'),('operator','platform.students.read');
    CREATE FUNCTION public.school_roster_metadata_valid(c jsonb) RETURNS boolean LANGUAGE plpgsql
        SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF c IS NULL OR jsonb_typeof(c)<>'object' OR NOT c ?& ARRAY['display_name','status','class_id']
            OR c - ARRAY['display_name','status','class_id'] <> '{}'::jsonb THEN RETURN false; END IF;
        RETURN jsonb_typeof(c->'display_name')='string' AND length(c->>'display_name') BETWEEN 1 AND 80
            AND c->>'display_name'=btrim(c->>'display_name') AND c->>'display_name' !~ '[[:cntrl:]]'
            AND jsonb_typeof(c->'status')='string' AND c->>'status' IN ('enrolled','paused','left','graduated')
            AND (c->'class_id'='null'::jsonb OR (jsonb_typeof(c->'class_id')='string'
                AND c->>'class_id' ~ '^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$'));
    END $$;
    CREATE TABLE public.school_roster_students (
        id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school_directory(id),
        code text NOT NULL CHECK(code ~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(school_id,code), UNIQUE(id,school_id)
    );
    CREATE TABLE public.school_enrollments (
        id uuid PRIMARY KEY, school_id uuid NOT NULL REFERENCES public.school_directory(id), student_id uuid NOT NULL,
        academic_year text NOT NULL CHECK(academic_year ~ '^[0-9]{4}-[0-9]{4}$'
            AND left(academic_year,4)::integer BETWEEN 2000 AND 9998
            AND right(academic_year,4)::integer=left(academic_year,4)::integer+1),
        current_revision integer NOT NULL CHECK(current_revision BETWEEN 1 AND 999999999),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(student_id,academic_year),
        FOREIGN KEY(student_id,school_id) REFERENCES public.school_roster_students(id,school_id)
    );
    CREATE TABLE public.school_enrollment_revisions (
        id uuid PRIMARY KEY, enrollment_id uuid NOT NULL REFERENCES public.school_enrollments(id),
        revision integer NOT NULL CHECK(revision BETWEEN 1 AND 999999999),
        config jsonb NOT NULL CHECK(public.school_roster_metadata_valid(config)),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        request_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(enrollment_id,revision), UNIQUE(actor_id,operation_id)
    );
    ALTER TABLE public.school_enrollments ADD CONSTRAINT school_enrollment_current_revision
        FOREIGN KEY(id,current_revision) REFERENCES public.school_enrollment_revisions(enrollment_id,revision)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE INDEX school_enrollment_page ON public.school_enrollments(school_id,academic_year,created_at DESC,id DESC);
    CREATE INDEX school_enrollment_revision_page ON public.school_enrollment_revisions(enrollment_id,created_at DESC,id DESC);
    CREATE TRIGGER school_enrollment_immutable BEFORE UPDATE OR DELETE ON public.school_enrollment_revisions
        FOR EACH ROW EXECUTE FUNCTION public.school_revision_immutable();
    CREATE TRIGGER school_enrollment_no_truncate BEFORE TRUNCATE ON public.school_enrollment_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION public.school_revision_immutable();
    CREATE FUNCTION public.school_roster_view(p_id uuid,p_revision integer) RETURNS jsonb LANGUAGE sql
        SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT r.config || jsonb_build_object('id',d.id,'student_id',d.student_id,'school_id',d.school_id,'code',s.code,
            'academic_year',d.academic_year,'revision',r.revision,'revision_id',r.id,'reason',r.reason,
            'actor_id',r.actor_id,'created_at',r.created_at,'registered_at',d.created_at)
        FROM public.school_enrollments d JOIN public.school_roster_students s ON s.id=d.student_id
            JOIN public.school_enrollment_revisions r ON r.enrollment_id=d.id
        WHERE d.id=p_id AND r.revision=p_revision;
    $$;
    CREATE FUNCTION public.school_roster_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_school uuid,p_enrollment uuid,p_year text,p_query text,p_status text,p_limit integer,
        p_before timestamptz,p_before_id uuid) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        school public.school_directory%ROWTYPE; school_config jsonb; enrollment public.school_enrollments%ROWTYPE;
        prior public.school_enrollment_revisions%ROWTYPE; class_node public.school_structure_nodes%ROWTYPE;
        needed text; c jsonb; snapshot jsonb; operation uuid; expected integer; value jsonb; new_id uuid;
        student uuid; class_id uuid; cursor_id uuid; parent_config jsonb; n integer; node_year text; depth integer;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','history','create','revise') OR p_school IS NULL
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            OR (p_op IN ('detail','history','revise') AND p_enrollment IS NULL)
            OR (p_op IN ('list','create') AND p_enrollment IS NOT NULL)
            OR (p_query IS NOT NULL AND (length(p_query)>80 OR p_query ~ '[[:cntrl:]]'))
            OR (p_status IS NOT NULL AND p_status NOT IN ('enrolled','paused','left','graduated'))
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op IN ('create','revise') THEN 'platform.students.write' ELSE 'platform.students.read' END;
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
            snapshot := jsonb_build_object('operation',p_op,'school_id',p_school,'enrollment_id',p_enrollment,'payload',p_payload);
            SELECT * INTO prior FROM public.school_enrollment_revisions WHERE actor_id=a.id AND operation_id=operation;
            IF FOUND THEN
                IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
                RETURN jsonb_build_object('status',200,'value',public.school_roster_view(prior.enrollment_id,prior.revision));
            END IF;
        END IF;
        -- Shared lock order with school/structure writes prevents assignment during moves or disables.
        SELECT * INTO school FROM public.school_directory WHERE id=p_school FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT config INTO school_config FROM public.school_directory_revisions
            WHERE school_id=school.id AND revision=school.current_revision;
        IF p_op='list' THEN
            IF p_year IS NULL OR p_year !~ '^[0-9]{4}-[0-9]{4}$' THEN RETURN jsonb_build_object('status',422); END IF;
            IF left(p_year,4)::integer NOT BETWEEN 2000 AND 9998
                OR right(p_year,4)::integer<>left(p_year,4)::integer+1 THEN RETURN jsonb_build_object('status',422); END IF;
            SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT d.id,d.created_at,public.school_roster_view(d.id,d.current_revision) AS v
                FROM public.school_enrollments d JOIN public.school_roster_students st ON st.id=d.student_id
                    JOIN public.school_enrollment_revisions r ON r.enrollment_id=d.id AND r.revision=d.current_revision
                WHERE d.school_id=p_school AND d.academic_year=p_year
                    AND (p_status IS NULL OR r.config->>'status'=p_status)
                    AND (p_query IS NULL OR strpos(lower(st.code),lower(p_query))>0
                        OR strpos(lower(r.config->>'display_name'),lower(p_query))>0)
                    AND (p_before IS NULL OR (d.created_at,d.id)<(p_before,p_before_id))
                ORDER BY d.created_at DESC,d.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('detail','history','revise') THEN
            SELECT * INTO enrollment FROM public.school_enrollments WHERE id=p_enrollment AND school_id=p_school;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN
                RETURN jsonb_build_object('status',200,'value',public.school_roster_view(enrollment.id,enrollment.current_revision));
            ELSIF p_op='history' THEN
                SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                    SELECT r.id,r.created_at,public.school_roster_view(enrollment.id,r.revision) AS v
                    FROM public.school_enrollment_revisions r WHERE r.enrollment_id=enrollment.id
                        AND (p_before IS NULL OR (r.created_at,r.id)<(p_before,p_before_id))
                    ORDER BY r.created_at DESC,r.id DESC LIMIT p_limit+1) t;
                RETURN jsonb_build_object('status',200,'value',value);
            END IF;
        END IF;
        IF school_config->>'status'<>'active' THEN RETURN jsonb_build_object('status',409,'code','school_disabled'); END IF;
        IF p_op='create' THEN
            IF jsonb_typeof(p_payload->'code') IS DISTINCT FROM 'string' OR p_payload->>'code' !~ '^[A-Z0-9][A-Z0-9_-]{1,39}$'
                OR jsonb_typeof(p_payload->'academic_year') IS DISTINCT FROM 'string'
                OR p_payload->>'academic_year' !~ '^[0-9]{4}-[0-9]{4}$' THEN RETURN jsonb_build_object('status',422); END IF;
            node_year := p_payload->>'academic_year';
            IF left(node_year,4)::integer NOT BETWEEN 2000 AND 9998
                OR right(node_year,4)::integer<>left(node_year,4)::integer+1 THEN RETURN jsonb_build_object('status',422); END IF;
            c := p_payload - ARRAY['operation_id','reason','code','academic_year']; new_id := gen_random_uuid(); n := 1;
        ELSE
            IF jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
                OR p_payload->>'expected_revision' !~ '^[0-9]{1,9}$' THEN RETURN jsonb_build_object('status',422); END IF;
            expected := (p_payload->>'expected_revision')::integer;
            IF expected NOT BETWEEN 1 AND 999999998 THEN RETURN jsonb_build_object('status',422); END IF;
            IF enrollment.current_revision<>expected THEN RETURN jsonb_build_object('status',409,'code','enrollment_revision_conflict'); END IF;
            c := p_payload - ARRAY['operation_id','reason','expected_revision'];
            new_id := enrollment.id; n := enrollment.current_revision+1; node_year := enrollment.academic_year;
        END IF;
        IF public.school_roster_metadata_valid(c) IS DISTINCT FROM true THEN RETURN jsonb_build_object('status',422); END IF;
        class_id := (c->>'class_id')::uuid;
        IF c->>'status'='enrolled' AND class_id IS NULL THEN RETURN jsonb_build_object('status',422,'code','invalid_enrollment_class'); END IF;
        IF class_id IS NOT NULL THEN
            SELECT * INTO class_node FROM public.school_structure_nodes
                WHERE id=class_id AND school_id=p_school AND academic_year=node_year AND kind='class';
            IF NOT FOUND THEN RETURN jsonb_build_object('status',422,'code','invalid_enrollment_class'); END IF;
            IF c->>'status'='enrolled' THEN
                cursor_id := class_id; depth := 0;
                WHILE cursor_id IS NOT NULL LOOP
                    depth := depth+1;
                    IF depth>8 THEN RETURN jsonb_build_object('status',422,'code','invalid_enrollment_class'); END IF;
                    SELECT r.config INTO parent_config FROM public.school_structure_nodes d
                        JOIN public.school_structure_revisions r ON r.node_id=d.id AND r.revision=d.current_revision
                        WHERE d.id=cursor_id AND d.school_id=p_school AND d.academic_year=node_year;
                    IF NOT FOUND OR parent_config->>'status'<>'active'
                        THEN RETURN jsonb_build_object('status',422,'code','invalid_enrollment_class'); END IF;
                    cursor_id := (parent_config->>'parent_id')::uuid;
                END LOOP;
            END IF;
        END IF;
        IF p_op='create' THEN
            SELECT id INTO student FROM public.school_roster_students WHERE school_id=p_school AND code=p_payload->>'code';
            IF FOUND THEN
                IF EXISTS(SELECT 1 FROM public.school_enrollments WHERE student_id=student AND academic_year=node_year)
                    THEN RETURN jsonb_build_object('status',409,'code','enrollment_exists'); END IF;
            ELSE
                student := gen_random_uuid();
                INSERT INTO public.school_roster_students(id,school_id,code) VALUES(student,p_school,p_payload->>'code');
            END IF;
            INSERT INTO public.school_enrollments(id,student_id,school_id,academic_year,current_revision)
                VALUES(new_id,student,p_school,node_year,n);
        END IF;
        INSERT INTO public.school_enrollment_revisions(id,enrollment_id,revision,config,reason,actor_id,operation_id,request_snapshot)
            VALUES(gen_random_uuid(),new_id,n,c,p_payload->>'reason',a.id,operation,snapshot);
        IF p_op='revise' THEN UPDATE public.school_enrollments SET current_revision=n WHERE id=new_id; END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'schools.roster.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.school_roster_view(new_id,n));
    END $$;
    ''')
    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
    for function in (CAPABILITY, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.school_roster_students)
        OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action IN ('schools.roster.create','schools.roster.revise'))''')).scalar_one():
        raise RuntimeError('Cannot discard school roster or change history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute('DROP FUNCTION public.school_roster_view(uuid,integer)')
    op.execute('ALTER TABLE public.school_enrollments DROP CONSTRAINT school_enrollment_current_revision')
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE public.{table}')
    op.execute('DROP FUNCTION public.school_roster_metadata_valid(jsonb)')
    op.execute("DELETE FROM public.platform_role_permissions WHERE permission IN ('platform.students.read','platform.students.write')")
    op.execute("DELETE FROM public.platform_permissions WHERE name IN ('platform.students.read','platform.students.write')")
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
