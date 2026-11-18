"""Human-corrected textbook content, chapter provenance and independent publication."""
import os

from alembic import op
from sqlalchemy import text

revision = '0028_textbook_content'
down_revision = '0027_textbook_processing'
REQUEST = 'public.textbook_content_request(text,text,text,jsonb,uuid,uuid,integer,integer,integer,integer,timestamptz,uuid)'
HELPERS = ('public.textbook_content_valid(jsonb,integer)', 'public.textbook_content_ready(jsonb)',
           'public.textbook_content_view(uuid,integer)', 'public.textbook_content_allowed(uuid,integer)')
ACTIONS = "'textbooks.content.create','textbooks.content.revise','textbooks.content.submit','textbooks.content.approve','textbooks.content.reject','textbooks.content.withdraw'"


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Content migration requires an offline RLS-bypassing owner')
    name = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
        AND NOT rolcreatedb FROM pg_roles WHERE rolname=:name'''), {'name': name}).scalar_one_or_none():
        raise RuntimeError('Content migration requires a restricted application role')
    role = bind.dialect.identifier_preparer.quote(name)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    # Keep the preceding action list exactly; the downgrade removes only this revision's actions.
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action IN ({ACTIONS}))''')
    op.execute(r'''
    CREATE FUNCTION public.textbook_content_valid(v jsonb, page_count integer) RETURNS boolean
        LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE p jsonb; c jsonb; parent jsonb; n integer:=0; total integer:=0; codes text[]:=ARRAY[]::text[];
    BEGIN
        IF v IS NULL OR jsonb_typeof(v)<>'object' OR NOT v ?& ARRAY['pages','chapters'] OR v-ARRAY['pages','chapters']<>'{}'
            OR jsonb_typeof(v->'pages') IS DISTINCT FROM 'array' OR jsonb_typeof(v->'chapters') IS DISTINCT FROM 'array'
            OR page_count IS NULL OR page_count NOT BETWEEN 1 AND 500 OR jsonb_array_length(v->'pages')<>page_count
            OR jsonb_array_length(v->'chapters')>500 THEN RETURN false; END IF;
        FOR p IN SELECT * FROM jsonb_array_elements(v->'pages') LOOP
            n:=n+1;
            IF jsonb_typeof(p) IS DISTINCT FROM 'object' OR NOT p ?& ARRAY['number','text','verified']
                OR p-ARRAY['number','text','verified']<>'{}' OR p->'number' IS DISTINCT FROM to_jsonb(n)
                OR jsonb_typeof(p->'text') IS DISTINCT FROM 'string' OR char_length(p->>'text')>100000
                OR jsonb_typeof(p->'verified') IS DISTINCT FROM 'boolean' THEN RETURN false; END IF;
            total:=total+char_length(p->>'text');
        END LOOP;
        IF total>1000000 THEN RETURN false; END IF;
        FOR c IN SELECT * FROM jsonb_array_elements(v->'chapters') LOOP
            IF jsonb_typeof(c) IS DISTINCT FROM 'object' OR NOT c ?& ARRAY['code','title','parent_code','first_page','last_page']
                OR c-ARRAY['code','title','parent_code','first_page','last_page']<>'{}'
                OR jsonb_typeof(c->'code') IS DISTINCT FROM 'string' OR c->>'code' !~ '^[A-Z0-9][A-Z0-9_-]{0,39}$'
                OR c->>'code'=ANY(codes) OR jsonb_typeof(c->'title') IS DISTINCT FROM 'string'
                OR length(c->>'title') NOT BETWEEN 1 AND 200 OR c->>'title'<>btrim(c->>'title') OR c->>'title' ~ '[[:cntrl:]]'
                OR jsonb_typeof(c->'first_page') IS DISTINCT FROM 'number' OR c->>'first_page' !~ '^[1-9][0-9]{0,2}$'
                OR jsonb_typeof(c->'last_page') IS DISTINCT FROM 'number' OR c->>'last_page' !~ '^[1-9][0-9]{0,2}$'
                OR (c->>'first_page')::integer>(c->>'last_page')::integer OR (c->>'last_page')::integer>page_count
                THEN RETURN false; END IF;
            IF c->'parent_code'<>'null'::jsonb THEN
                IF jsonb_typeof(c->'parent_code') IS DISTINCT FROM 'string' OR NOT (c->>'parent_code'=ANY(codes)) THEN RETURN false; END IF;
                SELECT x INTO parent FROM jsonb_array_elements(v->'chapters') x WHERE x->>'code'=c->>'parent_code';
                IF (c->>'first_page')::integer<(parent->>'first_page')::integer OR (c->>'last_page')::integer>(parent->>'last_page')::integer
                    THEN RETURN false; END IF;
            END IF;
            codes:=array_append(codes,c->>'code');
        END LOOP;
        RETURN true;
    EXCEPTION WHEN invalid_text_representation OR numeric_value_out_of_range THEN RETURN false;
    END $$;
    CREATE FUNCTION public.textbook_content_ready(v jsonb) RETURNS boolean
        LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_array_length(v->'chapters')>0
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v->'pages') p WHERE p->'verified'<>'true'::jsonb)
            AND EXISTS(SELECT 1 FROM jsonb_array_elements(v->'pages') p WHERE p->>'text' ~ '[^[:space:]   -     　]')
            AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v->'pages') p WHERE NOT EXISTS(
                SELECT 1 FROM jsonb_array_elements(v->'chapters') c
                WHERE (p->>'number')::integer BETWEEN (c->>'first_page')::integer AND (c->>'last_page')::integer))
    $$;
    CREATE TABLE public.textbook_contents (
        id uuid PRIMARY KEY, job_id uuid NOT NULL UNIQUE REFERENCES public.textbook_processing_jobs(id),
        edition_id uuid NOT NULL REFERENCES public.textbook_editions(id), edition_revision integer NOT NULL,
        created_by uuid NOT NULL REFERENCES public.platform_accounts(id), current_revision integer NOT NULL,
        state text NOT NULL CHECK(state IN ('draft','pending_review','published','withdrawn')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(edition_id,edition_revision) REFERENCES public.textbook_edition_revisions(edition_id,revision)
    );
    CREATE UNIQUE INDEX textbook_content_one_publication ON public.textbook_contents(edition_id) WHERE state='published';
    CREATE TABLE public.textbook_content_revisions (
        id uuid PRIMARY KEY, content_id uuid NOT NULL REFERENCES public.textbook_contents(id),
        revision integer NOT NULL CHECK(revision BETWEEN 1 AND 999999999),
        state text NOT NULL CHECK(state IN ('draft','pending_review','published','withdrawn')),
        body jsonb NOT NULL, action text NOT NULL CHECK(action IN ('create','revise','submit','approve','reject','withdraw')),
        submitted_by uuid REFERENCES public.platform_accounts(id), actor_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        operation_id uuid NOT NULL, request_snapshot jsonb NOT NULL, reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),
        sample_pages jsonb NOT NULL DEFAULT '[]'::jsonb, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(state NOT IN ('pending_review','published') OR submitted_by IS NOT NULL),
        UNIQUE(content_id,revision), UNIQUE(actor_id,operation_id), UNIQUE(content_id,revision,state)
    );
    ALTER TABLE public.textbook_contents ADD CONSTRAINT textbook_content_current_revision
        FOREIGN KEY(id,current_revision,state) REFERENCES public.textbook_content_revisions(content_id,revision,state)
        DEFERRABLE INITIALLY DEFERRED;
    CREATE TRIGGER textbook_content_history_immutable BEFORE UPDATE OR DELETE ON public.textbook_content_revisions
        FOR EACH ROW EXECUTE FUNCTION public.textbook_revision_immutable();
    CREATE TRIGGER textbook_content_history_no_truncate BEFORE TRUNCATE ON public.textbook_content_revisions
        FOR EACH STATEMENT EXECUTE FUNCTION public.textbook_revision_immutable();
    CREATE FUNCTION public.textbook_content_allowed(p_edition uuid,p_revision integer) RETURNS boolean
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT EXISTS(SELECT 1 FROM public.textbook_editions e JOIN public.textbook_edition_revisions r
            ON r.edition_id=e.id AND r.revision=e.current_revision WHERE e.id=p_edition AND e.current_revision=p_revision
            AND public.textbook_availability(r.config,r.state,clock_timestamp())='rights_approved'
            AND r.config->'rights'->'scopes' ?& ARRAY['storage','ocr','preview'])
    $$;
    CREATE FUNCTION public.textbook_content_view(p_id uuid,p_revision integer) RETURNS jsonb
        LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',h.id,'job_id',h.job_id,'edition_id',h.edition_id,'edition_revision',h.edition_revision,
            'document_id',j.document_id,'revision',r.revision,'revision_id',r.id,'state',r.state,'current_revision',h.current_revision,
            'created_by',h.created_by,'submitted_by',r.submitted_by,'actor_id',r.actor_id,'action',r.action,'reason',r.reason,
            'created_at',r.created_at,'source_created_at',h.created_at,'page_count',jsonb_array_length(r.body->'pages'),'chapter_count',jsonb_array_length(r.body->'chapters'),
            'verified_pages',(SELECT count(*) FROM jsonb_array_elements(r.body->'pages') p WHERE p->'verified'='true'::jsonb),
            'sample_pages',r.sample_pages,'ready',public.textbook_content_ready(r.body),'ocr_performed',false,
            'contributors',(SELECT jsonb_agg(DISTINCT x.actor_id) FROM public.textbook_content_revisions x
                WHERE x.content_id=h.id AND x.action IN ('create','revise','submit')),
            'content_available',r.state='published' AND h.state='published' AND h.current_revision=r.revision
                AND public.textbook_content_allowed(h.edition_id,h.edition_revision)
                AND EXISTS(SELECT 1 FROM public.textbook_edition_revisions er WHERE er.edition_id=h.edition_id
                    AND er.revision=h.edition_revision AND er.config->'rights'->'scopes' ? 'retrieval'),
            'required_sample_pages',(SELECT jsonb_agg(n ORDER BY n) FROM (
                SELECT (p->>'number')::integer n FROM jsonb_array_elements(r.body->'pages') p
                WHERE p->>'number'='1' OR (p->>'number')::integer=jsonb_array_length(r.body->'pages')
                    OR j.result->'draft'->'pages_without_text' @> jsonb_build_array((p->>'number')::integer)
                    OR p->>'text' IS DISTINCT FROM j.result->'draft'->'pages'->((p->>'number')::integer-1)->>'text') sample))
        FROM public.textbook_contents h JOIN public.textbook_content_revisions r ON r.content_id=h.id AND r.revision=p_revision
            JOIN public.textbook_processing_jobs j ON j.id=h.job_id WHERE h.id=p_id
    $$;
    CREATE FUNCTION public.textbook_content_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_edition uuid,p_target uuid,p_revision integer,p_page integer,p_before integer,
        p_limit integer,p_before_time timestamptz,p_before_id uuid) RETURNS jsonb
        LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        e public.textbook_editions%ROWTYPE; er public.textbook_edition_revisions%ROWTYPE;
        h public.textbook_contents%ROWTYPE; r public.textbook_content_revisions%ROWTYPE;
        prior public.textbook_content_revisions%ROWTYPE; j public.textbook_processing_jobs%ROWTYPE;
        needed text; snapshot jsonb; body jsonb; value jsonb; sample jsonb; n integer; new_state text; txt text; submitter uuid; new_id uuid;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','history','page','body','txt','create','revise','submit','approve','reject','withdraw')
            OR p_edition IS NULL OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 80
            OR (p_op<>'list' AND p_target IS NULL) OR (p_op='list' AND p_target IS NOT NULL)
            OR (p_op IN ('body','page') AND (p_revision IS NULL OR p_revision NOT BETWEEN 1 AND 999999999))
            OR (p_op='page' AND (p_page IS NULL OR p_page NOT BETWEEN 1 AND 500))
            OR (p_op='history' AND p_before IS NOT NULL AND p_before NOT BETWEEN 1 AND 999999999)
            OR (p_op<>'history' AND p_before IS NOT NULL)
            OR (p_op NOT IN ('body','page') AND p_revision IS NOT NULL)
            OR (p_op<>'page' AND p_page IS NOT NULL)
            OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR ((p_before_time IS NULL)<>(p_before_id IS NULL))
            OR (p_op<>'list' AND (p_before_time IS NOT NULL OR p_before_id IS NOT NULL))
            OR (p_op IN ('list','detail','history','page','body','txt') AND p_payload<>'{}')
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed:=CASE WHEN p_op IN ('approve','reject') THEN 'platform.textbooks.review'
            WHEN p_op IN ('create','revise','submit','withdraw') THEN 'platform.textbooks.write' ELSE 'platform.textbooks.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        -- Serialize rights changes and all publication decisions for one edition.
        SELECT * INTO e FROM public.textbook_editions WHERE id=p_edition FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT * INTO er FROM public.textbook_edition_revisions WHERE edition_id=e.id AND revision=e.current_revision;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(public.textbook_content_view(x.id,x.current_revision) ORDER BY x.created_at DESC,x.id DESC),'[]') INTO value
                FROM (SELECT * FROM public.textbook_contents WHERE edition_id=e.id
                    AND (p_before_time IS NULL OR (created_at,id)<(p_before_time,p_before_id))
                    ORDER BY created_at DESC,id DESC LIMIT p_limit+1) x;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op='create' THEN
            SELECT * INTO j FROM public.textbook_processing_jobs WHERE id=p_target AND edition_id=e.id;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        ELSE
            SELECT * INTO h FROM public.textbook_contents WHERE id=p_target AND edition_id=e.id FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            SELECT * INTO r FROM public.textbook_content_revisions WHERE content_id=h.id AND revision=h.current_revision;
            IF p_op='detail' THEN RETURN jsonb_build_object('status',200,'value',public.textbook_content_view(h.id,r.revision)); END IF;
            IF p_op='history' THEN
                SELECT coalesce(jsonb_agg(public.textbook_content_view(h.id,x.revision) ORDER BY x.revision DESC),'[]') INTO value FROM (
                    SELECT revision FROM public.textbook_content_revisions WHERE content_id=h.id AND (p_before IS NULL OR revision<p_before)
                    ORDER BY revision DESC LIMIT p_limit+1) x;
                RETURN jsonb_build_object('status',200,'value',value);
            END IF;
            SELECT * INTO j FROM public.textbook_processing_jobs WHERE id=h.job_id;
        END IF;
        IF p_op IN ('body','page','txt') THEN
            IF NOT public.textbook_content_allowed(e.id,h.edition_revision) THEN RETURN jsonb_build_object('status',409,'code','content_rights_unavailable'); END IF;
            IF p_op='txt' THEN
                IF NOT (public.textbook_content_view(h.id,r.revision)->>'content_available')::boolean
                    THEN RETURN jsonb_build_object('status',409,'code','content_not_published'); END IF;
                SELECT string_agg('[PDF page '||(p->>'number')||E']\n'||(p->>'text'), E'\n\n' ORDER BY (p->>'number')::integer)
                    INTO txt FROM jsonb_array_elements(r.body->'pages') p;
                RETURN jsonb_build_object('status',200,'value',jsonb_build_object('content_id',h.id,'revision',r.revision,'text',txt,'chapters',r.body->'chapters'));
            END IF;
            SELECT * INTO r FROM public.textbook_content_revisions WHERE content_id=h.id AND revision=p_revision;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='body' THEN RETURN jsonb_build_object('status',200,'value',jsonb_build_object('content_id',h.id,'revision',r.revision,'body',r.body)); END IF;
            IF p_page>jsonb_array_length(r.body->'pages') THEN RETURN jsonb_build_object('status',404); END IF;
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('content_id',h.id,'revision',r.revision,'page',r.body->'pages'->(p_page-1)));
        END IF;
        IF NOT p_payload ?& ARRAY['operation_id','expected_revision','reason']
            OR p_payload-ARRAY['operation_id','expected_revision','reason','body','sample_pages']<>'{}'
            OR jsonb_typeof(p_payload->'operation_id') IS DISTINCT FROM 'string'
            OR p_payload->>'operation_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
            OR jsonb_typeof(p_payload->'expected_revision') IS DISTINCT FROM 'number' OR p_payload->>'expected_revision' !~ '^[1-9][0-9]{0,8}$'
            OR jsonb_typeof(p_payload->'reason') IS DISTINCT FROM 'string' OR length(p_payload->>'reason') NOT BETWEEN 1 AND 500
            OR p_payload->>'reason'<>btrim(p_payload->>'reason') OR p_payload->>'reason' ~ '[[:cntrl:]]'
            OR (p_op='revise' AND NOT p_payload ? 'body') OR (p_op<>'revise' AND p_payload ? 'body')
            OR (p_op='approve' AND NOT p_payload ? 'sample_pages') OR (p_op<>'approve' AND p_payload ? 'sample_pages')
            THEN RETURN jsonb_build_object('status',422); END IF;
        snapshot:=jsonb_build_object('edition_id',e.id,'target',p_target,'action',p_op,'payload',p_payload);
        SELECT * INTO prior FROM public.textbook_content_revisions WHERE actor_id=a.id AND operation_id=(p_payload->>'operation_id')::uuid;
        IF FOUND THEN
            IF prior.request_snapshot<>snapshot THEN RETURN jsonb_build_object('status',409,'code','operation_conflict'); END IF;
            RETURN jsonb_build_object('status',200,'value',public.textbook_content_view(prior.content_id,prior.revision));
        END IF;
        IF p_op<>'withdraw' AND NOT public.textbook_content_allowed(e.id,j.edition_revision)
            THEN RETURN jsonb_build_object('status',409,'code','content_rights_unavailable'); END IF;
        IF p_op='create' THEN
            IF e.current_revision<>(p_payload->>'expected_revision')::integer THEN RETURN jsonb_build_object('status',409,'code','textbook_revision_conflict'); END IF;
            IF j.state<>'draft_ready' THEN RETURN jsonb_build_object('status',409,'code','content_source_unavailable'); END IF;
            IF EXISTS(SELECT 1 FROM public.textbook_contents WHERE job_id=j.id) THEN RETURN jsonb_build_object('status',409,'code','content_already_exists'); END IF;
            SELECT jsonb_build_object('pages',jsonb_agg(p||jsonb_build_object('verified',false) ORDER BY (p->>'number')::integer),'chapters','[]'::jsonb)
                INTO body FROM jsonb_array_elements(j.result->'draft'->'pages') p;
            new_id:=gen_random_uuid(); n:=1; new_state:='draft'; submitter:=NULL;
        ELSE
            IF h.current_revision<>(p_payload->>'expected_revision')::integer THEN RETURN jsonb_build_object('status',409,'code','content_revision_conflict'); END IF;
            IF h.current_revision=999999999 THEN RETURN jsonb_build_object('status',409,'code','content_revision_conflict'); END IF;
            new_id:=h.id; n:=h.current_revision+1; body:=r.body; new_state:=r.state; submitter:=r.submitted_by;
            IF p_op='revise' THEN
                IF r.state NOT IN ('draft','withdrawn') THEN RETURN jsonb_build_object('status',409,'code','content_state_conflict'); END IF;
                body:=p_payload->'body'; new_state:='draft'; submitter:=NULL;
            ELSIF p_op='submit' THEN
                IF r.state<>'draft' THEN RETURN jsonb_build_object('status',409,'code','content_state_conflict'); END IF;
                IF NOT public.textbook_content_ready(body) THEN RETURN jsonb_build_object('status',409,'code','content_review_incomplete'); END IF;
                new_state:='pending_review'; submitter:=a.id;
            ELSIF p_op IN ('approve','reject') THEN
                IF r.state<>'pending_review' THEN RETURN jsonb_build_object('status',409,'code','content_state_conflict'); END IF;
                IF EXISTS(SELECT 1 FROM public.textbook_content_revisions x WHERE x.content_id=h.id AND x.action IN ('create','revise','submit') AND x.actor_id=a.id)
                    THEN RETURN jsonb_build_object('status',409,'code','independent_reviewer_required'); END IF;
                IF p_op='approve' THEN
                    sample:=p_payload->'sample_pages';
                    IF jsonb_typeof(sample) IS DISTINCT FROM 'array' THEN RETURN jsonb_build_object('status',422); END IF;
                    IF jsonb_array_length(sample) NOT BETWEEN 1 AND 500 OR EXISTS(SELECT 1 FROM jsonb_array_elements(sample) x
                        WHERE jsonb_typeof(x)<>'number' OR x::text !~ '^[1-9][0-9]{0,2}$') THEN RETURN jsonb_build_object('status',422); END IF;
                    IF EXISTS(SELECT 1 FROM jsonb_array_elements(sample) x WHERE x::text::integer>jsonb_array_length(body->'pages'))
                        OR jsonb_array_length(sample)<>(SELECT count(DISTINCT x) FROM jsonb_array_elements(sample) x)
                        THEN RETURN jsonb_build_object('status',422); END IF;
                    IF NOT sample @> (public.textbook_content_view(h.id,r.revision)->'required_sample_pages')
                        THEN RETURN jsonb_build_object('status',409,'code','content_sample_incomplete'); END IF;
                    IF NOT (er.config->'rights'->'scopes' ? 'retrieval') THEN RETURN jsonb_build_object('status',409,'code','content_retrieval_unavailable'); END IF;
                    IF EXISTS(SELECT 1 FROM public.textbook_contents WHERE edition_id=e.id AND state='published')
                        THEN RETURN jsonb_build_object('status',409,'code','content_publication_exists'); END IF;
                    new_state:='published';
                ELSE new_state:='draft'; submitter:=NULL; END IF;
            ELSIF p_op='withdraw' THEN
                IF r.state NOT IN ('pending_review','published') THEN RETURN jsonb_build_object('status',409,'code','content_state_conflict'); END IF;
                new_state:='withdrawn';
            END IF;
        END IF;
        IF NOT public.textbook_content_valid(body,(j.result->'draft'->>'page_count')::integer) THEN RETURN jsonb_build_object('status',422); END IF;
        IF p_op='create' THEN
            INSERT INTO public.textbook_contents(id,job_id,edition_id,edition_revision,created_by,current_revision,state)
                VALUES(new_id,j.id,e.id,j.edition_revision,a.id,n,new_state);
        ELSE UPDATE public.textbook_contents SET current_revision=n,state=new_state WHERE id=h.id; END IF;
        INSERT INTO public.textbook_content_revisions(id,content_id,revision,state,body,action,submitted_by,actor_id,operation_id,request_snapshot,reason,sample_pages)
            VALUES(gen_random_uuid(),new_id,n,new_state,body,p_op,submitter,a.id,(p_payload->>'operation_id')::uuid,snapshot,p_payload->>'reason',coalesce(sample,'[]'::jsonb));
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'textbooks.content.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.textbook_content_view(new_id,n));
    END $$;
    ''')
    for table in ('textbook_contents', 'textbook_content_revisions'):
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
    for function in (REQUEST, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {REQUEST} TO {role}')


def downgrade():
    if op.get_bind().execute(text("SELECT EXISTS(SELECT 1 FROM public.textbook_contents) OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action LIKE 'textbooks.content.%')")).scalar_one():
        raise RuntimeError('Cannot discard textbook content or publication history')
    op.execute(f'DROP FUNCTION {REQUEST}')
    op.execute('DROP FUNCTION public.textbook_content_view(uuid,integer)')
    op.execute('DROP FUNCTION public.textbook_content_allowed(uuid,integer)')
    op.execute('ALTER TABLE public.textbook_contents DROP CONSTRAINT textbook_content_current_revision')
    op.execute('DROP TABLE public.textbook_content_revisions')
    op.execute('DROP TABLE public.textbook_contents')
    op.execute('DROP FUNCTION public.textbook_content_ready(jsonb)')
    op.execute('DROP FUNCTION public.textbook_content_valid(jsonb,integer)')
    old_actions = "'bootstrap','login','me','roles','accounts','audit','logout','logout_all','access.denied','models.create','models.revise','accounts.change','accounts.approve','accounts.reject','schools.create','schools.revise','schools.structure.create','schools.structure.revise','schools.roster.create','schools.roster.revise','textbooks.create','textbooks.revise','textbooks.submit','textbooks.approve','textbooks.reject','textbooks.withdraw','textbooks.documents.intent','textbooks.documents.received','textbooks.processing.request','textbooks.processing.finished'"
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({old_actions}))')
