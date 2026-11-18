"""Account changes, separated approval for administrator grants, immutable history."""
import os

from alembic import op
from sqlalchemy import text

revision = '0021_account_governance'
down_revision = '0020_evaluation_consent'
CAPABILITY = 'public.platform_account_governance(text,text,text,uuid,jsonb,integer,timestamptz,uuid)'
VIEW = 'public.platform_account_change_view(uuid)'
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Account migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('Account migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    INSERT INTO public.platform_permissions VALUES ('platform.accounts.write'),('platform.accounts.approve');
    INSERT INTO public.platform_role_permissions VALUES
        ('security_admin','platform.accounts.write'),('security_admin','platform.accounts.approve');
    ALTER TABLE public.platform_accounts ADD COLUMN governance_revision integer NOT NULL DEFAULT 0
        CHECK(governance_revision BETWEEN 0 AND 999999999);
    CREATE TABLE public.platform_account_changes(
        id uuid PRIMARY KEY, account_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        expected_revision integer NOT NULL CHECK(expected_revision BETWEEN 0 AND 999999998),
        previous_role text NOT NULL REFERENCES public.platform_roles(name),
        previous_status text NOT NULL CHECK(previous_status IN ('active','disabled')),
        role text NOT NULL REFERENCES public.platform_roles(name), status text NOT NULL CHECK(status IN ('active','disabled')),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        requires_approval boolean NOT NULL, request_snapshot jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), UNIQUE(actor_id,operation_id)
    );
    CREATE TABLE public.platform_account_decisions(
        id uuid PRIMARY KEY, change_id uuid NOT NULL UNIQUE REFERENCES public.platform_account_changes(id),
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        decision text NOT NULL CHECK(decision IN ('applied','approved','rejected')),
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        resulting_revision integer CHECK(resulting_revision BETWEEN 1 AND 999999999),
        request_snapshot jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((decision='rejected')=(resulting_revision IS NULL)), UNIQUE(actor_id,operation_id)
    );
    CREATE INDEX platform_account_change_page ON public.platform_account_changes(account_id,created_at DESC,id DESC);
    CREATE TRIGGER platform_account_change_immutable BEFORE UPDATE OR DELETE ON public.platform_account_changes
        FOR EACH ROW EXECUTE FUNCTION public.model_revision_immutable();
    CREATE TRIGGER platform_account_decision_immutable BEFORE UPDATE OR DELETE ON public.platform_account_decisions
        FOR EACH ROW EXECUTE FUNCTION public.model_revision_immutable();
    CREATE FUNCTION public.platform_account_change_view(p_id uuid) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',c.id,'account_id',c.account_id,'actor_id',c.actor_id,
            'expected_revision',c.expected_revision,'previous_role',c.previous_role,'previous_status',c.previous_status,
            'role',c.role,'status',c.status,'reason',c.reason,'requires_approval',c.requires_approval,
            'created_at',c.created_at,'state',coalesce(d.decision,'pending'),
            'decided_by',d.actor_id,'decision_reason',d.reason,'decided_at',d.created_at,
            'resulting_revision',d.resulting_revision,'stale',a.governance_revision<>c.expected_revision AND d.id IS NULL)
        FROM public.platform_account_changes c JOIN public.platform_accounts a ON a.id=c.account_id
        LEFT JOIN public.platform_account_decisions d ON d.change_id=c.id WHERE c.id=p_id
    $$;
    ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check;
    ''')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},'accounts.change','accounts.approve','accounts.reject'))''')
    op.execute(r'''
    CREATE FUNCTION public.platform_account_governance(p_hash text,p_op text,p_request text,p_id uuid,
        p_payload jsonb,p_limit integer,p_before timestamptz,p_before_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE;
        target public.platform_accounts%ROWTYPE; proposer public.platform_accounts%ROWTYPE;
        c public.platform_account_changes%ROWTYPE; prior public.platform_account_changes%ROWTYPE;
        d public.platform_account_decisions%ROWTYPE; account uuid; operation uuid; snapshot jsonb;
        expected integer; needed text; value jsonb; high_risk boolean;
    BEGIN
        -- Serialize governance before account/session locks; protects last-admin counts and idempotency.
        PERFORM pg_advisory_xact_lock(872109,21);
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('detail','history','change','approve','reject') OR p_id IS NULL
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 80
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op='change' THEN 'platform.accounts.write'
            WHEN p_op IN ('approve','reject') THEN 'platform.accounts.approve' ELSE 'platform.accounts.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op IN ('detail','history','change') THEN
            SELECT * INTO target FROM public.platform_accounts WHERE id=p_id FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        END IF;
        IF p_op='detail' THEN
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('id',target.id,'email',target.email,
                'role',target.role,'status',target.status,'revision',target.governance_revision,'created_at',target.created_at));
        ELSIF p_op='history' THEN
            SELECT coalesce(jsonb_agg(v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT h.id,h.created_at,public.platform_account_change_view(h.id) AS v FROM public.platform_account_changes h
                WHERE h.account_id=p_id AND (p_before IS NULL OR (h.created_at,h.id)<(p_before,p_before_id))
                ORDER BY h.created_at DESC,h.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF NOT p_payload ?& ARRAY['operation_id','reason']
            OR jsonb_typeof(p_payload->'operation_id')<>'string' OR length(p_payload->>'operation_id')<>36
            OR jsonb_typeof(p_payload->'reason')<>'string' OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500
            OR p_payload->>'reason'<>btrim(p_payload->>'reason') OR p_payload->>'reason' ~ '[[:cntrl:]]'
            THEN RETURN jsonb_build_object('status',422); END IF;
        BEGIN operation := (p_payload->>'operation_id')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN RETURN jsonb_build_object('status',422); END;
        snapshot := p_payload || jsonb_build_object('target',p_id,'operation',p_op);
        -- Operation keys cover both proposals and decisions, including different targets.
        SELECT * INTO prior FROM public.platform_account_changes WHERE actor_id=a.id AND operation_id=operation;
        IF FOUND THEN
            IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',public.platform_account_change_view(prior.id));
        END IF;
        SELECT * INTO d FROM public.platform_account_decisions WHERE actor_id=a.id AND operation_id=operation;
        IF FOUND THEN
            IF d.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',public.platform_account_change_view(d.change_id));
        END IF;
        IF p_op='change' THEN
            IF NOT p_payload ?& ARRAY['expected_revision','role','status']
                OR p_payload - ARRAY['operation_id','reason','expected_revision','role','status']<>'{}'::jsonb
                OR jsonb_typeof(p_payload->'expected_revision')<>'number'
                OR p_payload->>'expected_revision' !~ '^[0-9]{1,9}$'
                OR jsonb_typeof(p_payload->'role')<>'string' OR p_payload->>'role' NOT IN ('security_admin','operator')
                OR jsonb_typeof(p_payload->'status')<>'string' OR p_payload->>'status' NOT IN ('active','disabled')
                THEN RETURN jsonb_build_object('status',422); END IF;
            expected := (p_payload->>'expected_revision')::integer;
            IF expected<>target.governance_revision THEN RETURN jsonb_build_object('status',409,'code','revision_conflict'); END IF;
            IF target.governance_revision>=999999999 OR (target.role=p_payload->>'role' AND target.status=p_payload->>'status')
                THEN RETURN jsonb_build_object('status',422); END IF;
            high_risk := p_payload->>'role'='security_admin' AND p_payload->>'status'='active'
                AND (target.role<>'security_admin' OR target.status<>'active');
            c.id := gen_random_uuid(); c.account_id := target.id; c.role := p_payload->>'role'; c.status := p_payload->>'status';
            c.requires_approval := high_risk;
        ELSE
            IF p_payload - ARRAY['operation_id','reason']<>'{}'::jsonb THEN RETURN jsonb_build_object('status',422); END IF;
            SELECT * INTO c FROM public.platform_account_changes WHERE id=p_id;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF EXISTS(SELECT 1 FROM public.platform_account_decisions WHERE change_id=c.id)
                THEN RETURN jsonb_build_object('status',409,'code','already_decided'); END IF;
            IF c.actor_id=a.id THEN RETURN jsonb_build_object('status',409,'code','separate_approver_required'); END IF;
            SELECT * INTO target FROM public.platform_accounts WHERE id=c.account_id FOR UPDATE;
            IF p_op='approve' THEN
                SELECT * INTO proposer FROM public.platform_accounts WHERE id=c.actor_id FOR UPDATE;
                IF proposer.status<>'active' OR NOT EXISTS(SELECT 1 FROM public.platform_role_permissions
                    WHERE role=proposer.role AND permission='platform.accounts.write')
                    THEN RETURN jsonb_build_object('status',409,'code','proposer_ineligible'); END IF;
                IF c.expected_revision<>target.governance_revision THEN RETURN jsonb_build_object('status',409,'code','revision_conflict'); END IF;
            END IF;
        END IF;
        IF (p_op='change' AND NOT high_risk) OR p_op='approve' THEN
            IF target.role='security_admin' AND target.status='active' AND (c.role<>'security_admin' OR c.status<>'active')
                AND (SELECT count(*) FROM public.platform_accounts WHERE role='security_admin' AND status='active')<=1
                THEN RETURN jsonb_build_object('status',409,'code','last_security_admin'); END IF;
        END IF;
        IF p_op='change' THEN
            INSERT INTO public.platform_account_changes(id,account_id,actor_id,operation_id,expected_revision,
                previous_role,previous_status,role,status,reason,requires_approval,request_snapshot)
                VALUES(c.id,target.id,a.id,operation,expected,target.role,target.status,c.role,c.status,
                    p_payload->>'reason',high_risk,snapshot);
        END IF;
        IF (p_op='change' AND NOT high_risk) OR p_op IN ('approve','reject') THEN
            IF p_op<>'reject' THEN
                UPDATE public.platform_accounts SET role=c.role,status=c.status,governance_revision=governance_revision+1
                    WHERE id=target.id RETURNING * INTO target;
                UPDATE public.platform_sessions SET revoked_at=clock_timestamp() WHERE account_id=target.id AND revoked_at IS NULL;
            END IF;
            INSERT INTO public.platform_account_decisions(id,change_id,actor_id,operation_id,decision,reason,resulting_revision,request_snapshot)
                VALUES(gen_random_uuid(),c.id,a.id,operation,CASE WHEN p_op='change' THEN 'applied'
                    WHEN p_op='approve' THEN 'approved' ELSE 'rejected' END,p_payload->>'reason',
                    CASE WHEN p_op='reject' THEN NULL ELSE target.governance_revision END,snapshot);
        END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'accounts.'||p_op,p_request);
        RETURN jsonb_build_object('status',CASE WHEN p_op='change' THEN 201 ELSE 200 END,
            'value',public.platform_account_change_view(c.id));
    END $$;
    ''')
    for table in ('platform_account_changes', 'platform_account_decisions'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC')
    for function in (CAPABILITY, VIEW):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text('SELECT EXISTS(SELECT 1 FROM public.platform_account_changes)')).scalar_one():
        raise RuntimeError('Cannot discard account governance history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute(f'DROP FUNCTION {VIEW}')
    op.execute('''DROP TABLE public.platform_account_decisions;
        DROP TABLE public.platform_account_changes;
        ALTER TABLE public.platform_accounts DROP COLUMN governance_revision;
        DELETE FROM public.platform_role_permissions WHERE permission IN ('platform.accounts.write','platform.accounts.approve');
        DELETE FROM public.platform_permissions WHERE name IN ('platform.accounts.write','platform.accounts.approve');
        ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check;''')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
