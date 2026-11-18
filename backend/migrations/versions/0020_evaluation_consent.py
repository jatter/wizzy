"""Separate, immutable guardian decisions for optional offline model evaluation."""
import os

from alembic import op
from sqlalchemy import text

revision = '0020_evaluation_consent'
down_revision = '0019_derivation_claim_guard'

CAPABILITY = 'public.evaluation_consent_request(text,uuid,text,jsonb,text)'
HELPERS = ('public.evaluation_authorization_active(uuid)', 'public.evaluation_consent_view(uuid)',
           'public.evaluation_consent_immutable()')
TABLES = ('evaluation_consent_records', 'evaluation_consent_states')


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Evaluation consent migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('Evaluation consent migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    ALTER TABLE public.auth_sessions ADD CONSTRAINT evaluation_session_identity UNIQUE(tenant_id,user_id,id);
    CREATE TABLE public.evaluation_consent_records (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        guardian_id uuid NOT NULL, session_id uuid NOT NULL,
        operation_id uuid NOT NULL, revision integer NOT NULL CHECK(revision BETWEEN 1 AND 1000000000),
        granted boolean NOT NULL, policy_version text NOT NULL CHECK(policy_version='evaluation-2026-10-26-v1'),
        source_epoch bigint NOT NULL CHECK(source_epoch>=0), tenant_epoch bigint NOT NULL CHECK(tenant_epoch>=0),
        request_snapshot jsonb NOT NULL CHECK(jsonb_typeof(request_snapshot)='object'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id),
        FOREIGN KEY(tenant_id,guardian_id) REFERENCES public.memberships(tenant_id,user_id),
        FOREIGN KEY(tenant_id,guardian_id,session_id) REFERENCES public.auth_sessions(tenant_id,user_id,id),
        UNIQUE(student_id,revision), UNIQUE(student_id,guardian_id,operation_id),
        UNIQUE(tenant_id,student_id,revision,id)
    );
    CREATE TABLE public.evaluation_consent_states (
        student_id uuid PRIMARY KEY, tenant_id uuid NOT NULL, revision integer NOT NULL,
        current_decision_id uuid NOT NULL,
        FOREIGN KEY(tenant_id,student_id,revision,current_decision_id)
            REFERENCES public.evaluation_consent_records(tenant_id,student_id,revision,id)
    );
    CREATE FUNCTION public.evaluation_consent_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN RAISE EXCEPTION 'Evaluation consent history is immutable' USING ERRCODE='55000'; END $$;
    CREATE TRIGGER evaluation_consent_immutable BEFORE UPDATE OR DELETE ON public.evaluation_consent_records
        FOR EACH ROW EXECUTE FUNCTION public.evaluation_consent_immutable();
    CREATE TRIGGER evaluation_consent_no_truncate BEFORE TRUNCATE ON public.evaluation_consent_records
        FOR EACH STATEMENT EXECUTE FUNCTION public.evaluation_consent_immutable();

    CREATE FUNCTION public.evaluation_authorization_active(p_id uuid) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT EXISTS(SELECT 1 FROM public.evaluation_consent_records e
            JOIN public.evaluation_consent_states c ON c.current_decision_id=e.id AND c.student_id=e.student_id
            JOIN public.students s ON s.id=e.student_id AND s.tenant_id=e.tenant_id
            JOIN public.tenants t ON t.id=e.tenant_id
            JOIN public.memberships m ON m.tenant_id=e.tenant_id AND m.user_id=e.guardian_id
            JOIN public.users u ON u.id=e.guardian_id
            WHERE e.id=p_id AND e.granted AND s.status='active' AND s.consent_active AND t.status='active'
                AND e.source_epoch=s.deletion_epoch AND e.tenant_epoch=t.deletion_epoch
                AND m.status='active' AND u.status='active'
                AND (m.role='owner' OR EXISTS(SELECT 1 FROM public.student_access a
                    WHERE a.tenant_id=e.tenant_id AND a.student_id=e.student_id AND a.user_id=e.guardian_id)))
    $$;
    CREATE FUNCTION public.evaluation_consent_view(p_student uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE e public.evaluation_consent_records%ROWTYPE; s public.students%ROWTYPE;
        t public.tenants%ROWTYPE; active boolean; reason text; last_decision jsonb;
    BEGIN
        SELECT * INTO s FROM public.students WHERE id=p_student;
        SELECT * INTO t FROM public.tenants WHERE id=s.tenant_id;
        SELECT r.* INTO e FROM public.evaluation_consent_records r
            JOIN public.evaluation_consent_states c ON c.current_decision_id=r.id WHERE c.student_id=p_student;
        IF NOT FOUND THEN
            RETURN jsonb_build_object('revision',0,'granted',false,'active',false,'authorization_id',NULL,
                'last_decision',NULL,'reason','not_granted','policy_version','evaluation-2026-10-26-v1');
        END IF;
        active := public.evaluation_authorization_active(e.id);
        reason := CASE WHEN NOT e.granted THEN 'withdrawn'
            WHEN s.status<>'active' OR t.status<>'active' THEN 'source_inactive'
            WHEN NOT s.consent_active THEN 'service_consent_required'
            WHEN s.deletion_epoch<>e.source_epoch OR t.deletion_epoch<>e.tenant_epoch THEN 'source_epoch_changed'
            WHEN NOT active THEN 'guardian_unavailable' ELSE 'active' END;
        last_decision := jsonb_build_object('id',e.id,'revision',e.revision,'granted',e.granted,
            'policy_version',e.policy_version,'created_at',e.created_at);
        RETURN jsonb_build_object('revision',e.revision,'granted',e.granted,'active',active,
            'authorization_id',CASE WHEN active THEN e.id ELSE NULL END,'last_decision',last_decision,
            'reason',reason,'policy_version','evaluation-2026-10-26-v1');
    END $$;

    CREATE FUNCTION public.evaluation_consent_request(p_hash text,p_student uuid,p_action text,p_payload jsonb,p_request text)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.auth_sessions%ROWTYPE; original public.auth_sessions%ROWTYPE;
        s public.students%ROWTYPE; t public.tenants%ROWTYPE; m public.memberships%ROWTYPE;
        old public.evaluation_consent_records%ROWTYPE; e public.evaluation_consent_records%ROWTYPE;
        operation uuid; expected integer; current_revision integer; new_id uuid; value jsonb;
    BEGIN
        SELECT * INTO original FROM public.auth_sessions WHERE token_hash=p_hash;
        IF NOT FOUND OR original.revoked_at IS NOT NULL OR original.expires_at<=clock_timestamp()
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF original.audience<>'guardian' OR original.tenant_id IS NULL THEN RETURN jsonb_build_object('status',403); END IF;
        -- Match family mutation lock order; student lock serializes CAS, service withdrawal and decision changes.
        SELECT * INTO t FROM public.tenants WHERE id=original.tenant_id AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        PERFORM id FROM public.users WHERE id=original.user_id AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO m FROM public.memberships WHERE tenant_id=t.id AND user_id=original.user_id AND status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.students WHERE id=p_student AND tenant_id=t.id AND status='active' FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        IF m.role<>'owner' THEN
            PERFORM student_id FROM public.student_access
                WHERE tenant_id=t.id AND student_id=s.id AND user_id=m.user_id FOR SHARE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        END IF;
        SELECT * INTO a FROM public.auth_sessions WHERE id=original.id FOR SHARE;
        IF NOT FOUND OR a.token_hash IS DISTINCT FROM p_hash OR a.revoked_at IS NOT NULL
            OR a.expires_at<=clock_timestamp() OR a.tenant_id IS DISTINCT FROM t.id
            OR a.user_id IS DISTINCT FROM m.user_id OR a.audience<>'guardian'
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_action IS NULL OR p_action NOT IN ('read','decide') OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            THEN RETURN jsonb_build_object('status',422); END IF;
        IF p_action='read' THEN
            IF p_payload<>'{}'::jsonb THEN RETURN jsonb_build_object('status',422); END IF;
            RETURN jsonb_build_object('status',200,'value',public.evaluation_consent_view(s.id));
        END IF;
        -- Validate here as well: API role may call the capability directly.
        IF NOT p_payload ?& ARRAY['operation_id','expected_revision','granted','policy_version']
            OR p_payload - ARRAY['operation_id','expected_revision','granted','policy_version']<>'{}'::jsonb
            OR jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
            OR p_payload->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
            OR jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
            OR p_payload->>'expected_revision' !~ '^(0|[1-9][0-9]{0,8})$'
            OR jsonb_typeof(p_payload->'granted') IS DISTINCT FROM 'boolean'
            OR jsonb_typeof(p_payload->'policy_version') IS DISTINCT FROM 'string'
            OR p_payload->>'policy_version'<>'evaluation-2026-10-26-v1'
            THEN RETURN jsonb_build_object('status',422); END IF;
        operation := (p_payload->>'operation_id')::uuid;
        expected := (p_payload->>'expected_revision')::integer;
        SELECT * INTO old FROM public.evaluation_consent_records
            WHERE student_id=s.id AND guardian_id=m.user_id AND operation_id=operation;
        IF FOUND THEN
            IF old.request_snapshot<>p_payload THEN RETURN jsonb_build_object('status',409); END IF;
            value := jsonb_build_object('id',old.id,'revision',old.revision,'granted',old.granted,
                'policy_version',old.policy_version,'created_at',old.created_at);
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('decision',value,
                'state',public.evaluation_consent_view(s.id)));
        END IF;
        SELECT revision INTO current_revision FROM public.evaluation_consent_states WHERE student_id=s.id;
        IF coalesce(current_revision,0)<>expected THEN RETURN jsonb_build_object('status',409); END IF;
        IF (p_payload->>'granted')::boolean AND NOT s.consent_active THEN RETURN jsonb_build_object('status',403); END IF;
        new_id := gen_random_uuid();
        INSERT INTO public.evaluation_consent_records(id,tenant_id,student_id,guardian_id,session_id,
            operation_id,revision,granted,policy_version,source_epoch,tenant_epoch,request_snapshot)
            VALUES(new_id,t.id,s.id,m.user_id,a.id,operation,expected+1,(p_payload->>'granted')::boolean,
                'evaluation-2026-10-26-v1',s.deletion_epoch,t.deletion_epoch,p_payload) RETURNING * INTO e;
        INSERT INTO public.evaluation_consent_states(student_id,tenant_id,revision,current_decision_id)
            VALUES(s.id,t.id,e.revision,e.id) ON CONFLICT(student_id)
            DO UPDATE SET revision=EXCLUDED.revision,current_decision_id=EXCLUDED.current_decision_id;
        INSERT INTO public.audit_events(id,tenant_id,actor_id,action,resource_id,request_id)
            VALUES(gen_random_uuid(),t.id,m.user_id,
                CASE WHEN e.granted THEN 'evaluation.consent.granted' ELSE 'evaluation.consent.revoked' END,s.id,p_request);
        value := jsonb_build_object('id',e.id,'revision',e.revision,'granted',e.granted,
            'policy_version',e.policy_version,'created_at',e.created_at);
        RETURN jsonb_build_object('status',201,'value',jsonb_build_object('decision',value,
            'state',public.evaluation_consent_view(s.id)));
    END $$;
    ''')
    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE public.{table} FROM PUBLIC, {role}')
    for function in (CAPABILITY, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC, {role}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text('''SELECT EXISTS(SELECT 1 FROM public.evaluation_consent_records)
        OR EXISTS(SELECT 1 FROM public.evaluation_consent_states)
        OR EXISTS(SELECT 1 FROM public.audit_events WHERE action LIKE 'evaluation.consent.%')''')).scalar_one():
        raise RuntimeError('Cannot discard evaluation consent or audit history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute('DROP FUNCTION public.evaluation_consent_view(uuid)')
    op.execute('DROP FUNCTION public.evaluation_authorization_active(uuid)')
    op.execute('DROP TABLE public.evaluation_consent_states, public.evaluation_consent_records')
    op.execute('DROP FUNCTION public.evaluation_consent_immutable()')
    op.execute('ALTER TABLE public.auth_sessions DROP CONSTRAINT evaluation_session_identity')
