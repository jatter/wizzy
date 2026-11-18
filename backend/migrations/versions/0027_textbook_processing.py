"""Durable private textbook processing, fenced leases and review-only drafts."""
import os

from alembic import op
from sqlalchemy import text

revision = '0027_textbook_processing'
down_revision = '0026_textbook_documents'
REQUEST = 'public.textbook_processing_request(text,text,text,jsonb,uuid,uuid,uuid,integer,timestamptz,uuid,integer)'
WORKER = 'public.textbook_processing_worker(text,uuid,uuid,jsonb)'
HELPERS = ('public.textbook_processing_view(public.textbook_processing_jobs)',
           'public.textbook_processing_authorized(public.textbook_processing_jobs)',
           'public.textbook_processing_result_valid(jsonb)')
OLD_ACTIONS = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject','schools.create','schools.revise','schools.structure.create','schools.structure.revise','schools.roster.create','schools.roster.revise','textbooks.create','textbooks.revise','textbooks.submit','textbooks.approve','textbooks.reject','textbooks.withdraw','textbooks.documents.intent','textbooks.documents.received'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Processing migration requires an offline RLS-bypassing owner')
    roles = {}
    for kind, default in (('app', 'wizzy_app'), ('worker', 'wizzy_app_worker')):
        name = op.get_context().config.attributes.get(f'{kind}_role') or os.getenv(f'WIZZY_{kind.upper()}_ROLE', default)
        if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
            AND NOT rolcreaterole AND NOT rolcreatedb FROM pg_roles WHERE rolname=:name'''), {'name': name}).scalar_one_or_none():
            raise RuntimeError('Processing migration requires restricted runtime roles')
        roles[kind] = bind.dialect.identifier_preparer.quote(name)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK(action IN ({OLD_ACTIONS},'textbooks.processing.request','textbooks.processing.finished'))''')
    op.execute(r'''
    CREATE TABLE public.textbook_processing_jobs (
        id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES public.textbook_documents(id),
        edition_id uuid NOT NULL REFERENCES public.textbook_editions(id), edition_revision integer NOT NULL,
        created_by uuid NOT NULL REFERENCES public.platform_accounts(id), session_id uuid NOT NULL REFERENCES public.platform_sessions(id),
        operation_id uuid NOT NULL, reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500), request_snapshot jsonb NOT NULL,
        state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','draft_ready','no_text','rejected','unavailable','blocked')),
        attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
        lease_token uuid, lease_until timestamptz, result jsonb,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
        UNIQUE(created_by,operation_id),
        FOREIGN KEY(edition_id,edition_revision) REFERENCES public.textbook_edition_revisions(edition_id,revision),
        CHECK((state='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
            OR (state<>'running' AND lease_token IS NULL AND lease_until IS NULL)),
        CHECK((state IN ('queued','running'))=(finished_at IS NULL)),
        CHECK((state IN ('queued','running'))=(result IS NULL))
    );
    CREATE UNIQUE INDEX textbook_processing_active ON public.textbook_processing_jobs(document_id) WHERE state IN ('queued','running');
    CREATE INDEX textbook_processing_page ON public.textbook_processing_jobs(document_id,created_at DESC,id DESC);
    CREATE INDEX textbook_processing_queue ON public.textbook_processing_jobs(created_at,id) WHERE state IN ('queued','running');
    CREATE FUNCTION public.textbook_processing_view(j public.textbook_processing_jobs) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',j.id,'document_id',j.document_id,'edition_id',j.edition_id,
            'edition_revision',j.edition_revision,'created_by',j.created_by,'reason',j.reason,'state',j.state,
            'attempts',j.attempts,'created_at',j.created_at,'finished_at',j.finished_at,
            'code',j.result->'code','scan',j.result->'scan','page_count',j.result->'draft'->'page_count',
            'character_count',j.result->'draft'->'character_count','pages_without_text',j.result->'draft'->'pages_without_text',
            'requires_review',true,'ocr_performed',false,'content_available',false)
    $$;
    CREATE FUNCTION public.textbook_processing_authorized(j public.textbook_processing_jobs) RETURNS boolean
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT EXISTS(SELECT 1 FROM public.platform_accounts a JOIN public.platform_sessions s ON s.account_id=a.id
            JOIN public.textbook_editions e ON e.id=j.edition_id
            JOIN public.textbook_edition_revisions r ON r.edition_id=e.id AND r.revision=e.current_revision
            JOIN public.textbook_documents d ON d.id=j.document_id AND d.edition_id=e.id
            WHERE a.id=j.created_by AND a.status='active' AND s.id=j.session_id AND s.revoked_at IS NULL
            AND s.expires_at>clock_timestamp() AND s.credential_version=a.credential_version
            AND EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission='platform.textbooks.write')
            AND e.current_revision=j.edition_revision AND d.edition_revision=j.edition_revision AND d.status='quarantined'
            AND public.textbook_availability(r.config,r.state,clock_timestamp())='rights_approved'
            AND r.config->'rights'->'scopes' ?& ARRAY['storage','ocr'])
    $$;
    CREATE FUNCTION public.textbook_processing_result_valid(v jsonb) RETURNS boolean
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE d jsonb; p jsonb; scan jsonb; n integer:=0; total integer:=0; empty_pages jsonb:='[]';
        whitespace text; checked timestamptz;
    BEGIN
        IF v IS NULL OR jsonb_typeof(v) IS DISTINCT FROM 'object' OR octet_length(v::text)>12582912
            OR NOT v ?& ARRAY['status','code','scan','draft','content_available']
            OR v - ARRAY['status','code','scan','draft','content_available']<>'{}'
            OR v->'content_available' IS DISTINCT FROM 'false'::jsonb
            OR jsonb_typeof(v->'status') IS DISTINCT FROM 'string'
            OR v->>'status' NOT IN ('draft_ready','no_text','rejected','unavailable') THEN RETURN false; END IF;
        scan:=v->'scan';
        IF scan IS DISTINCT FROM 'null'::jsonb THEN
            IF jsonb_typeof(scan) IS DISTINCT FROM 'object'
                OR NOT scan ?& ARRAY['engine','database_revision','database_date','checked_at']
                OR scan - ARRAY['engine','database_revision','database_date','checked_at']<>'{}'
                OR jsonb_typeof(scan->'engine') IS DISTINCT FROM 'string' OR length(scan->>'engine') NOT BETWEEN 1 AND 100
                OR jsonb_typeof(scan->'database_revision') IS DISTINCT FROM 'number' OR scan->>'database_revision' !~ '^[1-9][0-9]{0,11}$'
                OR jsonb_typeof(scan->'database_date') IS DISTINCT FROM 'string' OR length(scan->>'database_date') NOT BETWEEN 1 AND 100
                OR jsonb_typeof(scan->'checked_at') IS DISTINCT FROM 'string' THEN RETURN false; END IF;
            checked:=(scan->>'checked_at')::timestamptz;
            IF NOT isfinite(checked) THEN RETURN false; END IF;
        END IF;
        IF v->>'status' IN ('rejected','unavailable') THEN
            IF v->'draft' IS DISTINCT FROM 'null'::jsonb OR jsonb_typeof(v->'code') IS DISTINCT FROM 'string' THEN RETURN false; END IF;
            IF v->>'code' IN ('source_mismatch','scanner_rejected') THEN
                RETURN v->>'status'='rejected' AND scan='null';
            ELSIF v->>'code' IN ('source_unavailable','scanner_unavailable','storage_unavailable') THEN
                RETURN v->>'status'='unavailable' AND scan='null';
            ELSIF v->>'code' IN ('extraction_failed','extraction_timeout','extraction_unavailable') THEN
                RETURN v->>'status'='unavailable' AND scan<>'null';
            ELSIF v->>'code' IN ('encrypted_pdf','page_limit_exceeded','unsupported_pdf_features','extraction_limit_exceeded','invalid_pdf') THEN
                RETURN v->>'status'='rejected' AND scan<>'null';
            END IF;
            RETURN false;
        END IF;
        IF v->'code' IS DISTINCT FROM 'null'::jsonb OR scan='null' THEN RETURN false; END IF;
        d:=v->'draft';
        IF jsonb_typeof(d) IS DISTINCT FROM 'object' OR NOT d ?& ARRAY['pages','page_count','pages_without_text','character_count','method','requires_review','ocr_performed','content_available']
            OR d - ARRAY['pages','page_count','pages_without_text','character_count','method','requires_review','ocr_performed','content_available']<>'{}'
            OR d->'method' IS DISTINCT FROM '"embedded_text"'::jsonb OR d->'requires_review' IS DISTINCT FROM 'true'::jsonb
            OR d->'ocr_performed' IS DISTINCT FROM 'false'::jsonb OR d->'content_available' IS DISTINCT FROM 'false'::jsonb
            OR jsonb_typeof(d->'pages') IS DISTINCT FROM 'array' OR jsonb_typeof(d->'pages_without_text') IS DISTINCT FROM 'array'
            OR jsonb_typeof(d->'page_count') IS DISTINCT FROM 'number' OR d->>'page_count' !~ '^[1-9][0-9]{0,2}$'
            OR (d->>'page_count')::int>500 OR jsonb_array_length(d->'pages')<>(d->>'page_count')::int
            OR jsonb_typeof(d->'character_count') IS DISTINCT FROM 'number' OR d->>'character_count' !~ '^(0|[1-9][0-9]{0,6})$'
            THEN RETURN false; END IF;
        -- Exactly Python str.strip() Unicode whitespace, independent of database locale.
        SELECT string_agg(chr(c),'') INTO whitespace FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,
            8192,8193,8194,8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,8287,12288]) c;
        FOR p IN SELECT * FROM jsonb_array_elements(d->'pages') LOOP
            n:=n+1;
            IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR p - ARRAY['number','text']<>'{}'
                OR p->'number' IS DISTINCT FROM to_jsonb(n) OR jsonb_typeof(p->'text') IS DISTINCT FROM 'string'
                OR char_length(p->>'text')>100000 THEN RETURN false; END IF;
            total:=total+char_length(p->>'text');
            IF btrim(p->>'text',whitespace)='' THEN empty_pages:=empty_pages||to_jsonb(n); END IF;
        END LOOP;
        RETURN total<=1000000 AND total=(d->>'character_count')::integer AND empty_pages=d->'pages_without_text'
            AND ((v->>'status'='no_text')=(jsonb_array_length(empty_pages)=n));
    EXCEPTION WHEN invalid_text_representation OR invalid_datetime_format OR datetime_field_overflow OR numeric_value_out_of_range THEN RETURN false;
    END $$;
    CREATE FUNCTION public.textbook_processing_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_edition uuid,p_document uuid,p_job uuid,p_limit integer,p_before timestamptz,p_before_id uuid,p_page integer) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        e public.textbook_editions%ROWTYPE; r public.textbook_edition_revisions%ROWTYPE;
        d public.textbook_documents%ROWTYPE; j public.textbook_processing_jobs%ROWTYPE;
        needed text; value jsonb; snapshot jsonb;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','create','page') OR p_edition IS NULL OR p_document IS NULL
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR (p_op<>'create' AND p_payload<>'{}')
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 80 OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR (p_before IS NULL)<>(p_before_id IS NULL) OR (p_op IN ('detail','page') AND p_job IS NULL)
            OR (p_op IN ('list','create') AND p_job IS NOT NULL)
            OR (p_op='page' AND (p_page IS NULL OR p_page NOT BETWEEN 1 AND 500))
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed:=CASE WHEN p_op='create' THEN 'platform.textbooks.write' ELSE 'platform.textbooks.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        SELECT * INTO e FROM public.textbook_editions WHERE id=p_edition FOR SHARE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT * INTO d FROM public.textbook_documents WHERE id=p_document AND edition_id=e.id FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]') INTO value FROM (
                SELECT x.id,x.created_at,public.textbook_processing_view(x) v FROM public.textbook_processing_jobs x
                WHERE x.document_id=d.id AND (p_before IS NULL OR (x.created_at,x.id)<(p_before,p_before_id))
                ORDER BY x.created_at DESC,x.id DESC LIMIT p_limit+1) t;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('detail','page') THEN
            SELECT * INTO j FROM public.textbook_processing_jobs WHERE id=p_job AND document_id=d.id;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN RETURN jsonb_build_object('status',200,'value',public.textbook_processing_view(j)); END IF;
            SELECT * INTO r FROM public.textbook_edition_revisions WHERE edition_id=e.id AND revision=e.current_revision;
            IF e.current_revision<>j.edition_revision OR public.textbook_availability(r.config,r.state,clock_timestamp())<>'rights_approved'
                OR NOT (r.config->'rights'->'scopes' ?& ARRAY['storage','ocr','preview'])
                THEN RETURN jsonb_build_object('status',409,'code','draft_rights_unavailable'); END IF;
            IF j.state NOT IN ('draft_ready','no_text') OR p_page>(j.result->'draft'->>'page_count')::integer
                THEN RETURN jsonb_build_object('status',404); END IF;
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('job_id',j.id,'page_count',j.result->'draft'->'page_count',
                'page',j.result->'draft'->'pages'->(p_page-1),'requires_review',true,'ocr_performed',false,'content_available',false));
        END IF;
        IF NOT p_payload ?& ARRAY['operation_id','expected_revision','reason'] OR p_payload-ARRAY['operation_id','expected_revision','reason']<>'{}'
            OR jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
            OR p_payload->>'operation_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            OR jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number' OR p_payload->>'expected_revision' !~ '^[1-9][0-9]{0,8}$'
            OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string' OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500
            OR p_payload->>'reason'<>btrim(p_payload->>'reason') OR p_payload->>'reason' ~ '[[:cntrl:]]'
            THEN RETURN jsonb_build_object('status',422); END IF;
        snapshot:=jsonb_build_object('edition_id',e.id,'document_id',d.id,'payload',p_payload);
        SELECT * INTO j FROM public.textbook_processing_jobs WHERE created_by=a.id AND operation_id=(p_payload->>'operation_id')::uuid;
        IF FOUND THEN
            IF j.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',public.textbook_processing_view(j));
        END IF;
        SELECT * INTO r FROM public.textbook_edition_revisions WHERE edition_id=e.id AND revision=e.current_revision;
        IF d.status<>'quarantined' THEN RETURN jsonb_build_object('status',409,'code','document_not_received'); END IF;
        IF e.current_revision<>(p_payload->>'expected_revision')::integer OR e.current_revision<>d.edition_revision
            THEN RETURN jsonb_build_object('status',409,'code','textbook_revision_conflict'); END IF;
        IF public.textbook_availability(r.config,r.state,clock_timestamp())<>'rights_approved'
            OR NOT (r.config->'rights'->'scopes' ?& ARRAY['storage','ocr'])
            THEN RETURN jsonb_build_object('status',409,'code','document_rights_unavailable'); END IF;
        IF EXISTS(SELECT 1 FROM public.textbook_processing_jobs WHERE document_id=d.id AND state IN ('queued','running'))
            THEN RETURN jsonb_build_object('status',409,'code','processing_active'); END IF;
        INSERT INTO public.textbook_processing_jobs(id,document_id,edition_id,edition_revision,created_by,session_id,operation_id,reason,request_snapshot)
            VALUES(gen_random_uuid(),d.id,e.id,e.current_revision,a.id,s.id,(p_payload->>'operation_id')::uuid,p_payload->>'reason',snapshot)
            RETURNING * INTO j;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'textbooks.processing.request',p_request);
        RETURN jsonb_build_object('status',201,'value',public.textbook_processing_view(j));
    END $$;
    CREATE FUNCTION public.textbook_processing_worker(p_op text,p_job uuid,p_token uuid,p_result jsonb) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE candidate public.textbook_processing_jobs%ROWTYPE; j public.textbook_processing_jobs%ROWTYPE;
        d public.textbook_documents%ROWTYPE; permitted boolean;
    BEGIN
        IF p_op IS NULL OR p_op NOT IN ('claim','finish') OR (p_op='finish' AND (p_job IS NULL OR p_token IS NULL))
            OR (p_op='claim' AND (p_token IS NOT NULL OR p_result IS NOT NULL)) THEN RETURN jsonb_build_object('status','invalid'); END IF;
        SELECT * INTO candidate FROM public.textbook_processing_jobs WHERE (p_job IS NULL OR id=p_job)
            AND (state='queued' OR (state='running' AND lease_until<=clock_timestamp()))
            ORDER BY created_at,id LIMIT 1;
        IF p_op='finish' THEN SELECT * INTO candidate FROM public.textbook_processing_jobs WHERE id=p_job; END IF;
        IF NOT FOUND THEN RETURN jsonb_build_object('status','idle'); END IF;
        -- Match API/rights lock ordering; never hold these locks during scanning.
        PERFORM 1 FROM public.platform_accounts WHERE id=candidate.created_by FOR SHARE;
        PERFORM 1 FROM public.platform_sessions WHERE id=candidate.session_id FOR SHARE;
        PERFORM 1 FROM public.textbook_editions WHERE id=candidate.edition_id FOR SHARE;
        SELECT * INTO d FROM public.textbook_documents WHERE id=candidate.document_id FOR SHARE;
        SELECT * INTO j FROM public.textbook_processing_jobs WHERE id=candidate.id FOR UPDATE SKIP LOCKED;
        IF NOT FOUND THEN RETURN jsonb_build_object('status','idle'); END IF;
        IF p_op='finish' THEN
            IF j.state<>'running' OR j.lease_token<>p_token OR j.lease_until<=clock_timestamp()
                THEN RETURN jsonb_build_object('status','stale'); END IF;
        ELSE
            IF j.state<>'queued' AND (j.state<>'running' OR j.lease_until>clock_timestamp())
                THEN RETURN jsonb_build_object('status','idle'); END IF;
        END IF;
        permitted:=public.textbook_processing_authorized(j);
        IF NOT permitted OR (p_op='claim' AND j.attempts>=3) THEN
            UPDATE public.textbook_processing_jobs SET state='blocked',lease_token=NULL,lease_until=NULL,finished_at=clock_timestamp(),
                result=jsonb_build_object('code',CASE WHEN permitted THEN 'attempts_exhausted' ELSE 'authorization_changed' END) WHERE id=j.id;
        ELSIF p_op='claim' THEN
            UPDATE public.textbook_processing_jobs SET state='running',attempts=attempts+1,lease_token=gen_random_uuid(),
                lease_until=clock_timestamp()+interval '180 seconds' WHERE id=j.id RETURNING * INTO j;
            RETURN jsonb_build_object('status','claimed','id',j.id,'document_id',d.id,'size_bytes',d.size_bytes,'sha256',d.sha256,'lease_token',j.lease_token);
        ELSE
            IF NOT public.textbook_processing_result_valid(p_result) THEN RETURN jsonb_build_object('status','invalid'); END IF;
            UPDATE public.textbook_processing_jobs SET state=p_result->>'status',result=p_result,lease_token=NULL,lease_until=NULL,
                finished_at=clock_timestamp() WHERE id=j.id;
        END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),j.created_by,'textbooks.processing.finished','processing-'||j.id::text);
        RETURN jsonb_build_object('status',CASE WHEN NOT permitted OR (p_op='claim' AND j.attempts>=3) THEN 'blocked' ELSE 'finished' END);
    END $$;
    ALTER TABLE public.textbook_processing_jobs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.textbook_processing_jobs FORCE ROW LEVEL SECURITY;
    REVOKE ALL ON public.textbook_processing_jobs FROM PUBLIC;
    ''')
    for role in roles.values():
        op.execute(f'REVOKE ALL ON public.textbook_processing_jobs FROM {role}')
    for function in (REQUEST, WORKER, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {REQUEST} TO {roles["app"]}')
    op.execute(f'GRANT EXECUTE ON FUNCTION {WORKER} TO {roles["worker"]}')


def downgrade():
    if op.get_bind().execute(text("SELECT EXISTS(SELECT 1 FROM public.textbook_processing_jobs) OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action LIKE 'textbooks.processing.%')")).scalar_one():
        raise RuntimeError('Cannot discard textbook processing history')
    for function in (REQUEST, WORKER, *HELPERS):
        op.execute(f'DROP FUNCTION {function}')
    op.execute('DROP TABLE public.textbook_processing_jobs')
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({OLD_ACTIONS}))')
