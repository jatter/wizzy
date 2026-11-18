"""Private PDF intake; quarantine is neither a clean scan nor published content."""
import os

from alembic import op
from sqlalchemy import text

revision = '0026_textbook_documents'
down_revision = '0025_textbook_rights'
CAPABILITY = 'public.textbook_document_request(text,text,text,jsonb,uuid,uuid,integer,timestamptz,uuid)'

OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject','schools.create','schools.revise','schools.structure.create','schools.structure.revise','schools.roster.create','schools.roster.revise','textbooks.create','textbooks.revise','textbooks.submit','textbooks.approve','textbooks.reject','textbooks.withdraw'"
ACTIONS = "'textbooks.documents.intent','textbooks.documents.received'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Document migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
        AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('Document migration requires a restricted app role')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},{ACTIONS}))''')
    op.execute(r'''
    CREATE TABLE public.textbook_documents (
        id uuid PRIMARY KEY, edition_id uuid NOT NULL REFERENCES public.textbook_editions(id),
        edition_revision integer NOT NULL,
        filename text NOT NULL CHECK(length(filename) BETWEEN 5 AND 160 AND filename=btrim(filename)
            AND filename !~ '[[:cntrl:]/\\]' AND filename ~* '\.pdf$'),
        size_bytes integer NOT NULL CHECK(size_bytes BETWEEN 1 AND 52428800),
        sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
        status text NOT NULL DEFAULT 'awaiting_upload' CHECK(status IN ('awaiting_upload','quarantined')),
        created_by uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500 AND reason=btrim(reason) AND reason !~ '[[:cntrl:]]'),
        request_snapshot jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), expires_at timestamptz NOT NULL,
        received_at timestamptz,
        UNIQUE(created_by,operation_id),
        FOREIGN KEY(edition_id,edition_revision) REFERENCES public.textbook_edition_revisions(edition_id,revision),
        CHECK((status='quarantined')=(received_at IS NOT NULL)), CHECK(expires_at>created_at)
    );
    CREATE INDEX textbook_documents_page ON public.textbook_documents(edition_id,created_at DESC,id DESC);
    CREATE FUNCTION public.textbook_document_view(d public.textbook_documents) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT to_jsonb(d)-'request_snapshot' || jsonb_build_object('content_available',false,'scan_status','not_scanned')
    $$;
    CREATE FUNCTION public.textbook_document_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_edition uuid,p_document uuid,p_limit integer,p_before timestamptz,p_before_id uuid) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        edition public.textbook_editions%ROWTYPE; current public.textbook_edition_revisions%ROWTYPE;
        document public.textbook_documents%ROWTYPE; needed text; value jsonb; snapshot jsonb; operation uuid;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','intent','upload','complete')
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
            OR (p_op<>'intent' AND p_payload<>'{}'::jsonb)
            OR p_edition IS NULL OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100 OR (p_before IS NULL)<>(p_before_id IS NULL)
            OR (p_op IN ('detail','upload','complete') AND p_document IS NULL)
            OR (p_op IN ('intent','list') AND p_document IS NOT NULL)
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op IN ('list','detail') THEN 'platform.textbooks.read' ELSE 'platform.textbooks.write' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        SELECT * INTO edition FROM public.textbook_editions WHERE id=p_edition FOR SHARE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]'::jsonb) INTO value FROM (
                SELECT d.id,d.created_at,public.textbook_document_view(d) AS v FROM public.textbook_documents d
                WHERE d.edition_id=p_edition AND (p_before IS NULL OR (d.created_at,d.id)<(p_before,p_before_id))
                ORDER BY d.created_at DESC,d.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('detail','upload','complete') THEN
            SELECT * INTO document FROM public.textbook_documents WHERE id=p_document AND edition_id=p_edition FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_payload<>'{}'::jsonb THEN RETURN jsonb_build_object('status',422); END IF;
            IF p_op='detail' THEN RETURN jsonb_build_object('status',200,'value',public.textbook_document_view(document)); END IF;
            IF document.created_by<>a.id THEN
                INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                    VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
                RETURN jsonb_build_object('status',403); END IF;
        ELSE
            IF NOT p_payload ?& ARRAY['operation_id','expected_revision','filename','size_bytes','sha256','reason']
                OR p_payload - ARRAY['operation_id','expected_revision','filename','size_bytes','sha256','reason']<>'{}'::jsonb
                OR jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
                OR p_payload->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
                OR jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number'
                OR p_payload->>'expected_revision' !~ '^[1-9][0-9]{0,8}$'
                OR jsonb_typeof(p_payload->'size_bytes') IS DISTINCT FROM 'number'
                OR p_payload->>'size_bytes' !~ '^[1-9][0-9]{0,7}$'
                OR (p_payload->>'size_bytes')::integer>52428800
                OR jsonb_typeof(p_payload->'filename') IS DISTINCT FROM 'string'
                OR length(p_payload->>'filename') NOT BETWEEN 5 AND 160
                OR p_payload->>'filename'<>btrim(p_payload->>'filename')
                OR p_payload->>'filename' ~ '[[:cntrl:]/\\]' OR p_payload->>'filename' !~* '\.pdf$'
                OR jsonb_typeof(p_payload->'sha256') IS DISTINCT FROM 'string' OR p_payload->>'sha256' !~ '^[0-9a-f]{64}$'
                OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string'
                OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500 OR p_payload->>'reason'<>btrim(p_payload->>'reason')
                OR p_payload->>'reason' ~ '[[:cntrl:]]' THEN RETURN jsonb_build_object('status',422); END IF;
            operation := (p_payload->>'operation_id')::uuid;
            snapshot := jsonb_build_object('edition_id',p_edition,'payload',p_payload);
            SELECT * INTO document FROM public.textbook_documents WHERE created_by=a.id AND operation_id=operation;
            IF FOUND THEN
                IF document.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
                RETURN jsonb_build_object('status',200,'value',public.textbook_document_view(document));
            END IF;
        END IF;
        SELECT * INTO current FROM public.textbook_edition_revisions WHERE edition_id=edition.id AND revision=edition.current_revision;
        IF public.textbook_availability(current.config,current.state,clock_timestamp())<>'rights_approved'
            OR NOT (current.config->'rights'->'scopes' ? 'storage')
            THEN RETURN jsonb_build_object('status',409,'code','document_rights_unavailable'); END IF;
        IF edition.current_revision<>(CASE WHEN p_op='intent' THEN (p_payload->>'expected_revision')::integer ELSE document.edition_revision END)
            THEN RETURN jsonb_build_object('status',409,'code','textbook_revision_conflict'); END IF;
        IF p_op='intent' THEN
            INSERT INTO public.textbook_documents(id,edition_id,edition_revision,filename,size_bytes,sha256,created_by,
                operation_id,reason,request_snapshot,expires_at)
                VALUES(gen_random_uuid(),p_edition,edition.current_revision,p_payload->>'filename',(p_payload->>'size_bytes')::integer,
                p_payload->>'sha256',a.id,operation,p_payload->>'reason',snapshot,clock_timestamp()+interval '15 minutes')
                RETURNING * INTO document;
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'textbooks.documents.intent',p_request);
            RETURN jsonb_build_object('status',201,'value',public.textbook_document_view(document));
        END IF;
        IF document.status='awaiting_upload' AND document.expires_at<=clock_timestamp()
            THEN RETURN jsonb_build_object('status',409,'code','upload_expired'); END IF;
        IF p_op='complete' AND document.status='awaiting_upload' THEN
            UPDATE public.textbook_documents SET status='quarantined',received_at=clock_timestamp() WHERE id=document.id RETURNING * INTO document;
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'textbooks.documents.received',p_request);
            RETURN jsonb_build_object('status',201,'value',public.textbook_document_view(document));
        END IF;
        RETURN jsonb_build_object('status',200,'value',public.textbook_document_view(document));
    END $$;
    ALTER TABLE public.textbook_documents ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.textbook_documents FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.textbook_documents FROM PUBLIC;
    ''')
    op.execute(f'REVOKE ALL ON public.textbook_documents FROM {role}')
    op.execute(f'REVOKE ALL ON FUNCTION {CAPABILITY} FROM PUBLIC')
    op.execute('REVOKE ALL ON FUNCTION public.textbook_document_view(public.textbook_documents) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {role}')


def downgrade():
    if op.get_bind().execute(text("SELECT EXISTS(SELECT 1 FROM public.textbook_documents) OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action LIKE 'textbooks.documents.%')")).scalar_one():
        raise RuntimeError('Cannot discard textbook document receipts')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute('DROP FUNCTION public.textbook_document_view(public.textbook_documents)')
    op.execute('DROP TABLE public.textbook_documents')
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS}))''')
