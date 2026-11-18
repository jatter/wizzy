"""Authorized source revisions, blind independent gold labels and immutable corpus lock."""
import os
import re

from alembic import op
from sqlalchemy import text

revision = '0029_evaluation_datasets'
down_revision = '0028_textbook_content'
REQUEST = 'public.evaluation_dataset_request(text,text,text,jsonb,uuid,uuid,integer,integer)'
HELPERS = ('public.evaluation_dataset_valid(text,jsonb)', 'public.evaluation_gold_valid(jsonb)',
           'public.evaluation_source_active(uuid,uuid,integer,text)',
           'public.evaluation_dataset_view(uuid,uuid,integer,integer)',
           'public.evaluation_dataset_immutable()')
TABLES = ('evaluation_datasets', 'evaluation_samples', 'evaluation_annotations',
          'evaluation_adjudications', 'evaluation_dataset_operations')
GOVERNANCE = 'public.platform_account_governance(text,text,text,uuid,jsonb,integer,timestamptz,uuid)'
OLD_ROLES = "p_payload->>'role' NOT IN ('security_admin','operator')"
NEW_ROLES = "p_payload->>'role' NOT IN ('security_admin','operator','evaluation_annotator','evaluation_adjudicator')"
ACTIONS = "'evaluation.datasets.create','evaluation.datasets.intake','evaluation.datasets.annotate','evaluation.datasets.adjudicate','evaluation.datasets.lock'"


def governance_roles(bind, previous, replacement):
    definition = bind.execute(text('SELECT pg_get_functiondef(to_regprocedure(:name))'),
                              {'name': GOVERNANCE}).scalar_one()
    if definition.count(previous) != 1:
        raise RuntimeError('Unexpected account governance role validation')
    op.execute(definition.replace(previous, replacement))


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Evaluation migration requires an offline RLS-bypassing owner')
    name = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb
        OR rolinherit OR rolreplication) FROM pg_roles WHERE rolname=:name'''), {'name': name}).scalar_one_or_none():
        raise RuntimeError('Evaluation migration requires a restricted application role')
    role = bind.dialect.identifier_preparer.quote(name)
    governance_roles(bind, OLD_ROLES, NEW_ROLES)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action IN ({ACTIONS}))''')
    op.execute(r'''
    INSERT INTO public.platform_permissions VALUES ('platform.evaluation.read'),('platform.evaluation.curate'),
        ('platform.evaluation.annotate'),('platform.evaluation.adjudicate');
    INSERT INTO public.platform_role_permissions SELECT 'security_admin',name FROM public.platform_permissions
        WHERE name LIKE 'platform.evaluation.%';
    INSERT INTO public.platform_roles VALUES ('evaluation_annotator'),('evaluation_adjudicator');
    INSERT INTO public.platform_role_permissions VALUES
        ('evaluation_annotator','platform.evaluation.read'),('evaluation_annotator','platform.evaluation.annotate'),
        ('evaluation_adjudicator','platform.evaluation.read'),('evaluation_adjudicator','platform.evaluation.adjudicate');

    CREATE FUNCTION public.evaluation_gold_valid(v jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE k text; b jsonb; n jsonb;
    BEGIN
        IF v IS NULL OR jsonb_typeof(v)<>'object' OR NOT v ?& ARRAY['stem','student_answer','correct_answer',
            'verdict','question_type','difficulty','requires_abstention','redaction_safe','box']
            OR v-ARRAY['stem','student_answer','correct_answer','verdict','question_type','difficulty',
                'requires_abstention','redaction_safe','box']<>'{}' THEN RETURN false; END IF;
        FOREACH k IN ARRAY ARRAY['stem','student_answer','correct_answer'] LOOP
            IF jsonb_typeof(v->k) IS DISTINCT FROM 'string' OR length(v->>k)>8000
                THEN RETURN false; END IF;
        END LOOP;
        IF length(btrim(v->>'stem'))=0 OR jsonb_typeof(v->'verdict') IS DISTINCT FROM 'string'
            OR v->>'verdict' NOT IN ('correct','incorrect','unanswered','pending_review','unsupported')
            OR jsonb_typeof(v->'difficulty') IS DISTINCT FROM 'string' OR v->>'difficulty' NOT IN ('easy','medium','hard')
            OR jsonb_typeof(v->'question_type') IS DISTINCT FROM 'string'
            OR v->>'question_type' !~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'
            OR jsonb_typeof(v->'requires_abstention') IS DISTINCT FROM 'boolean'
            OR jsonb_typeof(v->'redaction_safe') IS DISTINCT FROM 'boolean'
            OR (v->>'verdict' IN ('pending_review','unsupported') AND v->'requires_abstention'<>'true')
            OR (v->>'verdict'='unanswered' AND length(btrim(v->>'student_answer'))>0)
            OR (v->>'verdict' IN ('correct','incorrect') AND length(btrim(v->>'correct_answer'))=0)
            THEN RETURN false; END IF;
        b:=v->'box';
        IF jsonb_typeof(b) IS DISTINCT FROM 'array' OR jsonb_array_length(b)<>4 THEN RETURN false; END IF;
        FOR n IN SELECT * FROM jsonb_array_elements(b) LOOP
            IF jsonb_typeof(n) IS DISTINCT FROM 'number' OR n::text !~ '^(0|[1-9][0-9]{0,4})$'
                OR n::text::integer>10000 THEN RETURN false; END IF;
        END LOOP;
        RETURN (b->>0)::integer<(b->>2)::integer AND (b->>1)::integer<(b->>3)::integer;
    END $$;
    CREATE FUNCTION public.evaluation_dataset_valid(p_op text,v jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE keys text[]; k text;
    BEGIN
        keys:=ARRAY['operation_id','expected_revision'] || CASE p_op
            WHEN 'create' THEN ARRAY['name','split','handbook_version']
            WHEN 'intake' THEN ARRAY['question_id','source_revision','authorization_id','near_duplicate_group',
                'redacted_stem','redacted_answer','redaction_note']
            WHEN 'annotate' THEN ARRAY['label'] WHEN 'adjudicate' THEN ARRAY['label','reason']
            WHEN 'lock' THEN ARRAY[]::text[] ELSE NULL END;
        IF keys IS NULL OR v IS NULL OR jsonb_typeof(v)<>'object' OR NOT v ?& keys OR v-keys<>'{}'
            OR jsonb_typeof(v->'operation_id') IS DISTINCT FROM 'string'
            OR v->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
            OR jsonb_typeof(v->'expected_revision') IS DISTINCT FROM 'number'
            OR v->>'expected_revision' !~ '^(0|[1-9][0-9]{0,8})$' THEN RETURN false; END IF;
        IF p_op='create' THEN
            RETURN jsonb_typeof(v->'name')='string' AND length(v->>'name') BETWEEN 1 AND 120
                AND v->>'name'=btrim(v->>'name') AND v->>'name' !~ '[[:cntrl:]]'
                AND jsonb_typeof(v->'split')='string' AND v->>'split' IN ('train','validation','test')
                AND jsonb_typeof(v->'handbook_version')='string'
                AND v->>'handbook_version' ~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$';
        ELSIF p_op='intake' THEN
            FOREACH k IN ARRAY ARRAY['question_id','authorization_id'] LOOP
                IF jsonb_typeof(v->k) IS DISTINCT FROM 'string'
                    OR v->>k !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
                    THEN RETURN false; END IF;
            END LOOP;
            RETURN jsonb_typeof(v->'source_revision')='number' AND v->>'source_revision' ~ '^[1-9][0-9]{0,8}$'
                AND jsonb_typeof(v->'near_duplicate_group')='string'
                AND v->>'near_duplicate_group' ~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'
                AND jsonb_typeof(v->'redacted_stem')='string' AND length(btrim(v->>'redacted_stem')) BETWEEN 1 AND 8000
                AND jsonb_typeof(v->'redacted_answer')='string' AND length(v->>'redacted_answer')<=8000
                AND jsonb_typeof(v->'redaction_note')='string' AND length(btrim(v->>'redaction_note')) BETWEEN 1 AND 1000;
        ELSIF p_op IN ('annotate','adjudicate') THEN
            RETURN public.evaluation_gold_valid(v->'label') AND (p_op='annotate' OR (
                jsonb_typeof(v->'reason')='string' AND length(btrim(v->>'reason')) BETWEEN 1 AND 1000));
        END IF;
        RETURN true;
    END $$;

    CREATE TABLE public.evaluation_datasets (
        id uuid PRIMARY KEY, name text NOT NULL, split text NOT NULL CHECK(split IN ('train','validation','test')),
        handbook_version text NOT NULL, revision integer NOT NULL CHECK(revision>0),
        state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','locked')),
        created_by uuid NOT NULL REFERENCES public.platform_accounts(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        locked_at timestamptz, locked_by uuid REFERENCES public.platform_accounts(id), locked_digest text,
        CHECK ((state='draft' AND locked_at IS NULL AND locked_by IS NULL AND locked_digest IS NULL)
            OR (state='locked' AND locked_at IS NOT NULL AND locked_by IS NOT NULL AND locked_digest ~ '^[a-f0-9]{64}$'))
    );
    CREATE TABLE public.evaluation_samples (
        id uuid PRIMARY KEY, dataset_id uuid NOT NULL REFERENCES public.evaluation_datasets(id),
        question_id uuid NOT NULL, source_revision integer NOT NULL,
        authorization_id uuid NOT NULL REFERENCES public.evaluation_consent_records(id),
        source_digest text NOT NULL CHECK(source_digest ~ '^[a-f0-9]{64}$'),
        student_id uuid NOT NULL REFERENCES public.students(id), submission_id uuid NOT NULL REFERENCES public.submissions(id),
        page_position integer NOT NULL, grade integer NOT NULL CHECK(grade BETWEEN 1 AND 6),
        near_duplicate_group text NOT NULL, content_fingerprint text NOT NULL,
        redacted_stem text NOT NULL, redacted_answer text NOT NULL, redaction_note text NOT NULL,
        created_by uuid NOT NULL REFERENCES public.platform_accounts(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(question_id,source_revision) REFERENCES public.question_revisions(question_id,revision),
        UNIQUE(dataset_id,question_id)
    );
    CREATE INDEX evaluation_samples_group_student ON public.evaluation_samples(student_id);
    CREATE INDEX evaluation_samples_group_duplicate ON public.evaluation_samples(near_duplicate_group);
    CREATE INDEX evaluation_samples_group_content ON public.evaluation_samples(content_fingerprint);
    CREATE INDEX evaluation_samples_page ON public.evaluation_samples(dataset_id,created_at,id);
    CREATE TABLE public.evaluation_annotations (
        sample_id uuid NOT NULL REFERENCES public.evaluation_samples(id), actor_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        label jsonb NOT NULL CHECK(public.evaluation_gold_valid(label)), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(sample_id,actor_id)
    );
    CREATE TABLE public.evaluation_adjudications (
        sample_id uuid PRIMARY KEY REFERENCES public.evaluation_samples(id), actor_id uuid NOT NULL REFERENCES public.platform_accounts(id),
        label jsonb NOT NULL CHECK(public.evaluation_gold_valid(label)), reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.evaluation_dataset_operations (
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        dataset_id uuid NOT NULL REFERENCES public.evaluation_datasets(id), snapshot jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(actor_id,operation_id)
    );
    CREATE FUNCTION public.evaluation_dataset_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF TG_TABLE_NAME='evaluation_datasets' AND TG_OP='UPDATE' THEN
            IF OLD.state='draft' THEN RETURN NEW; END IF;
        END IF;
        RAISE EXCEPTION 'Evaluation history and locked corpora are immutable' USING ERRCODE='55000';
    END $$;

    -- Match consent/lifecycle lock order. Locks cover the full transaction, including body projection.
    CREATE FUNCTION public.evaluation_source_active(p_authorization uuid,p_question uuid,p_revision integer,p_digest text)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE e public.evaluation_consent_records%ROWTYPE; q public.questions%ROWTYPE; r public.question_revisions%ROWTYPE;
    BEGIN
        SELECT * INTO e FROM public.evaluation_consent_records WHERE id=p_authorization;
        IF NOT FOUND THEN RETURN false; END IF;
        PERFORM id FROM public.tenants WHERE id=e.tenant_id FOR SHARE;
        PERFORM id FROM public.users WHERE id=e.guardian_id FOR SHARE;
        PERFORM user_id FROM public.memberships WHERE tenant_id=e.tenant_id AND user_id=e.guardian_id FOR SHARE;
        PERFORM id FROM public.students WHERE id=e.student_id FOR SHARE;
        PERFORM student_id FROM public.student_access WHERE tenant_id=e.tenant_id AND student_id=e.student_id AND user_id=e.guardian_id FOR SHARE;
        IF NOT public.evaluation_authorization_active(e.id) THEN RETURN false; END IF;
        SELECT * INTO q FROM public.questions WHERE id=p_question AND student_id=e.student_id AND tenant_id=e.tenant_id FOR SHARE;
        IF NOT FOUND OR q.deletion_epoch<>e.source_epoch THEN RETURN false; END IF;
        SELECT * INTO r FROM public.question_revisions WHERE question_id=q.id AND revision=p_revision FOR SHARE;
        IF NOT FOUND THEN RETURN false; END IF;
        RETURN p_digest IS NULL OR p_digest=encode(sha256(convert_to(jsonb_build_array(r.stem,r.student_answer_raw)::text,'UTF8')),'hex');
    END $$;

    CREATE FUNCTION public.evaluation_dataset_view(p_id uuid,p_actor uuid,p_limit integer,p_offset integer) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE d public.evaluation_datasets%ROWTYPE; v public.evaluation_samples%ROWTYPE; labels jsonb; gold jsonb;
        own jsonb; judge jsonb; item jsonb; items jsonb:='[]'; active boolean; usable boolean:=true;
        total integer:=0; paired integer:=0; agreed integer:=0; conflicts integer:=0; resolved integer:=0;
        state text; may_judge boolean; n integer;
    BEGIN
        SELECT * INTO d FROM public.evaluation_datasets WHERE id=p_id;
        SELECT EXISTS(SELECT 1 FROM public.platform_role_permissions rp JOIN public.platform_accounts a ON a.role=rp.role
            WHERE a.id=p_actor AND rp.permission='platform.evaluation.adjudicate') INTO may_judge;
        FOR v IN SELECT * FROM public.evaluation_samples WHERE dataset_id=d.id ORDER BY student_id,authorization_id,id LOOP
            total:=total+1;
            active:=public.evaluation_source_active(v.authorization_id,v.question_id,v.source_revision,v.source_digest);
            usable:=usable AND active;
        END LOOP;
        SELECT count(*),count(*) FILTER(WHERE x.n=2 AND x.distinct_labels=1),count(*) FILTER(WHERE x.n=2 AND x.distinct_labels>1)
            INTO paired,agreed,conflicts FROM (
                SELECT count(*) n,count(DISTINCT a.label) distinct_labels FROM public.evaluation_samples s
                JOIN public.evaluation_annotations a ON a.sample_id=s.id WHERE s.dataset_id=d.id GROUP BY s.id
            ) x WHERE x.n=2;
        SELECT count(*) INTO resolved FROM public.evaluation_adjudications a JOIN public.evaluation_samples s ON s.id=a.sample_id WHERE s.dataset_id=d.id;
        FOR v IN SELECT * FROM public.evaluation_samples WHERE dataset_id=d.id ORDER BY created_at,id LIMIT p_limit OFFSET p_offset LOOP
            active:=public.evaluation_source_active(v.authorization_id,v.question_id,v.source_revision,v.source_digest);
            SELECT coalesce(jsonb_agg(a.label ORDER BY a.actor_id),'[]') INTO labels FROM public.evaluation_annotations a WHERE sample_id=v.id;
            SELECT a.label INTO own FROM public.evaluation_annotations a WHERE sample_id=v.id AND actor_id=p_actor;
            SELECT a.label INTO judge FROM public.evaluation_adjudications a WHERE sample_id=v.id;
            n:=jsonb_array_length(labels);
            state:=CASE WHEN NOT active THEN 'authorization_inactive' WHEN judge IS NOT NULL THEN 'adjudicated'
                WHEN n=2 AND labels->0=labels->1 THEN 'agreed' WHEN n=2 THEN 'disputed' ELSE 'pending' END;
            gold:=CASE WHEN active AND d.state='locked' THEN coalesce(judge,labels->0) ELSE NULL END;
            item:=jsonb_build_object('id',v.id,'grade',v.grade,'state',state,'label_count',n,
                'student_key',md5('evaluation-student/'||v.student_id::text),
                'page_key',md5('evaluation-page/'||v.submission_id::text||'/'||v.page_position::text),
                'redacted_stem',CASE WHEN active THEN v.redacted_stem END,
                'redacted_answer',CASE WHEN active THEN v.redacted_answer END,
                'own_label',CASE WHEN active THEN own END,'gold',gold,
                'disagreement_labels',CASE WHEN active AND state='disputed' AND may_judge AND own IS NULL THEN labels ELSE NULL END);
            items:=items||jsonb_build_array(item);
        END LOOP;
        RETURN jsonb_build_object('id',d.id,'name',d.name,'split',d.split,'handbook_version',d.handbook_version,
            'revision',d.revision,'state',d.state,'created_at',d.created_at,'locked_at',d.locked_at,'locked_digest',d.locked_digest,
            'usable',usable AND total>0,'sample_count',total,'samples',items,'offset',p_offset,
            'next_offset',CASE WHEN p_offset+p_limit<total THEN p_offset+p_limit END,
            'agreement',jsonb_build_object('double_labeled',paired,'agreed',agreed,'disputed',conflicts,'adjudicated',resolved,
                'rate',CASE WHEN paired>0 THEN agreed::numeric/paired ELSE NULL END),
            'production_eligible',false);
    END $$;

    CREATE FUNCTION public.evaluation_dataset_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_dataset uuid,p_sample uuid,p_limit integer,p_offset integer) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; se public.platform_sessions%ROWTYPE; account uuid;
        d public.evaluation_datasets%ROWTYPE; v public.evaluation_samples%ROWTYPE; q public.questions%ROWTYPE;
        r public.question_revisions%ROWTYPE; old public.evaluation_dataset_operations%ROWTYPE;
        needed text; snapshot jsonb; value jsonb; labels jsonb; gold jsonb; new_id uuid; fingerprint text; hash text; count integer;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO se FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR se.revoked_at IS NOT NULL OR se.expires_at<=clock_timestamp() OR se.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','create','intake','annotate','adjudicate','lock')
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR p_offset IS NULL OR p_offset NOT BETWEEN 0 AND 1000000 OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 80
            OR (p_op NOT IN ('create','list') AND p_dataset IS NULL)
            OR (p_op IN ('annotate','adjudicate') AND p_sample IS NULL)
            OR (p_op NOT IN ('annotate','adjudicate') AND p_sample IS NOT NULL)
            OR (p_op IN ('create','list') AND p_dataset IS NOT NULL)
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed:=CASE p_op WHEN 'annotate' THEN 'platform.evaluation.annotate' WHEN 'adjudicate' THEN 'platform.evaluation.adjudicate'
            WHEN 'create' THEN 'platform.evaluation.curate' WHEN 'intake' THEN 'platform.evaluation.curate'
            WHEN 'lock' THEN 'platform.evaluation.curate' ELSE 'platform.evaluation.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op IN ('list','detail') THEN
            IF p_payload<>'{}' THEN RETURN jsonb_build_object('status',422); END IF;
            IF p_op='list' THEN
                SELECT coalesce(jsonb_agg(t.v ORDER BY t.created_at DESC,t.id DESC),'[]') INTO value FROM (
                    SELECT id,created_at,jsonb_build_object('id',id,'name',name,'split',split,'revision',revision,'state',state,
                        'handbook_version',handbook_version,'created_at',created_at,'production_eligible',false) v
                    FROM public.evaluation_datasets ORDER BY created_at DESC,id DESC LIMIT p_limit+1 OFFSET p_offset) t;
                RETURN jsonb_build_object('status',200,'value',jsonb_build_object('items',value- p_limit,
                    'next_offset',CASE WHEN jsonb_array_length(value)>p_limit THEN p_offset+p_limit END));
            END IF;
            PERFORM id FROM public.evaluation_datasets WHERE id=p_dataset FOR SHARE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            RETURN jsonb_build_object('status',200,'value',public.evaluation_dataset_view(p_dataset,a.id,p_limit,p_offset));
        END IF;
        IF NOT coalesce(public.evaluation_dataset_valid(p_op,p_payload),false) THEN RETURN jsonb_build_object('status',422); END IF;
        snapshot:=jsonb_build_object('operation',p_op,'dataset',p_dataset,'sample',p_sample,'body',p_payload);
        SELECT * INTO old FROM public.evaluation_dataset_operations WHERE actor_id=a.id AND operation_id=(p_payload->>'operation_id')::uuid;
        IF FOUND THEN
            IF old.snapshot<>snapshot THEN RETURN jsonb_build_object('status',409); END IF;
            PERFORM id FROM public.evaluation_datasets WHERE id=old.dataset_id FOR SHARE;
            value:=public.evaluation_dataset_view(old.dataset_id,a.id,p_limit,p_offset);
            IF value->'sample_count'<>'0' AND value->'usable'<>'true' THEN RETURN jsonb_build_object('status',409); END IF;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op='create' THEN
            IF p_payload->'expected_revision'<>'0' THEN RETURN jsonb_build_object('status',409); END IF;
            new_id:=gen_random_uuid();
            INSERT INTO public.evaluation_datasets(id,name,split,handbook_version,revision,created_by)
                VALUES(new_id,p_payload->>'name',p_payload->>'split',p_payload->>'handbook_version',1,a.id);
        ELSE
            SELECT * INTO d FROM public.evaluation_datasets WHERE id=p_dataset FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF d.state='locked' OR d.revision<>(p_payload->>'expected_revision')::integer OR d.revision>=999999999
                THEN RETURN jsonb_build_object('status',409); END IF;
            new_id:=d.id;
            -- Recheck every retained authorization before writing or locking a corpus.
            FOR v IN SELECT * FROM public.evaluation_samples WHERE dataset_id=d.id ORDER BY student_id,authorization_id,id LOOP
                IF NOT public.evaluation_source_active(v.authorization_id,v.question_id,v.source_revision,v.source_digest)
                    THEN RETURN jsonb_build_object('status',409); END IF;
            END LOOP;
            IF p_op='intake' THEN
                IF (SELECT count(*) FROM public.evaluation_samples WHERE dataset_id=d.id)>=10000 THEN RETURN jsonb_build_object('status',409); END IF;
                IF NOT public.evaluation_source_active((p_payload->>'authorization_id')::uuid,(p_payload->>'question_id')::uuid,
                    (p_payload->>'source_revision')::integer,NULL) THEN RETURN jsonb_build_object('status',404); END IF;
                SELECT * INTO q FROM public.questions WHERE id=(p_payload->>'question_id')::uuid;
                SELECT * INTO r FROM public.question_revisions WHERE question_id=q.id AND revision=(p_payload->>'source_revision')::integer;
                fingerprint:=encode(sha256(convert_to(lower(regexp_replace(r.stem,'[[:space:]]','','g')),'UTF8')),'hex');
                hash:=encode(sha256(convert_to(jsonb_build_array(r.stem,r.student_answer_raw)::text,'UTF8')),'hex');
                -- Serialize admission across different corpora: a concurrent train/test import cannot pass both checks.
                PERFORM pg_advisory_xact_lock(20261026,29);
                IF EXISTS(SELECT 1 FROM public.evaluation_samples s JOIN public.evaluation_datasets e ON e.id=s.dataset_id
                    WHERE (s.dataset_id=d.id AND s.question_id=q.id) OR (e.split<>d.split AND (
                        s.student_id=q.student_id OR (s.submission_id=q.submission_id AND s.page_position=q.page_position)
                        OR s.near_duplicate_group=p_payload->>'near_duplicate_group' OR s.content_fingerprint=fingerprint)))
                    THEN RETURN jsonb_build_object('status',409); END IF;
                INSERT INTO public.evaluation_samples(id,dataset_id,question_id,source_revision,authorization_id,source_digest,
                    student_id,submission_id,page_position,grade,near_duplicate_group,content_fingerprint,
                    redacted_stem,redacted_answer,redaction_note,created_by)
                    VALUES(gen_random_uuid(),d.id,q.id,r.revision,(p_payload->>'authorization_id')::uuid,hash,
                        q.student_id,q.submission_id,q.page_position,(SELECT grade FROM public.students WHERE id=q.student_id),
                        p_payload->>'near_duplicate_group',fingerprint,p_payload->>'redacted_stem',p_payload->>'redacted_answer',
                        p_payload->>'redaction_note',a.id);
            ELSIF p_op IN ('annotate','adjudicate') THEN
                SELECT * INTO v FROM public.evaluation_samples WHERE id=p_sample AND dataset_id=d.id;
                IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
                SELECT coalesce(jsonb_agg(label ORDER BY actor_id),'[]') INTO labels FROM public.evaluation_annotations WHERE sample_id=v.id;
                count:=jsonb_array_length(labels);
                IF EXISTS(SELECT 1 FROM public.evaluation_annotations WHERE sample_id=v.id AND actor_id=a.id)
                    THEN RETURN jsonb_build_object('status',409); END IF;
                IF p_op='annotate' THEN
                    IF count>=2 THEN RETURN jsonb_build_object('status',409); END IF;
                    INSERT INTO public.evaluation_annotations(sample_id,actor_id,label) VALUES(v.id,a.id,p_payload->'label');
                ELSE
                    IF count<>2 OR labels->0=labels->1 OR EXISTS(SELECT 1 FROM public.evaluation_adjudications WHERE sample_id=v.id)
                        THEN RETURN jsonb_build_object('status',409); END IF;
                    INSERT INTO public.evaluation_adjudications(sample_id,actor_id,label,reason)
                        VALUES(v.id,a.id,p_payload->'label',p_payload->>'reason');
                END IF;
            ELSE
                count:=0;
                FOR v IN SELECT * FROM public.evaluation_samples WHERE dataset_id=d.id ORDER BY id LOOP
                    count:=count+1;
                    SELECT coalesce(jsonb_agg(label ORDER BY actor_id),'[]') INTO labels FROM public.evaluation_annotations WHERE sample_id=v.id;
                    IF jsonb_array_length(labels)<>2 OR labels->0->'redaction_safe'<>'true' OR labels->1->'redaction_safe'<>'true'
                        THEN RETURN jsonb_build_object('status',409); END IF;
                    SELECT label INTO gold FROM public.evaluation_adjudications WHERE sample_id=v.id;
                    IF gold IS NULL THEN
                        IF labels->0<>labels->1 THEN RETURN jsonb_build_object('status',409); END IF;
                        gold:=labels->0;
                    END IF;
                    IF gold->'redaction_safe'<>'true' THEN RETURN jsonb_build_object('status',409); END IF;
                END LOOP;
                IF count=0 THEN RETURN jsonb_build_object('status',409); END IF;
                SELECT encode(sha256(convert_to(jsonb_build_object('handbook',d.handbook_version,'split',d.split,
                    'samples',jsonb_agg(jsonb_build_object('id',s.id,'authorization',s.authorization_id,'source_digest',s.source_digest,
                        'source_revision',s.source_revision,'grade',s.grade,'group',s.near_duplicate_group,
                        'redacted_stem',s.redacted_stem,'redacted_answer',s.redacted_answer,
                        'labels',(SELECT jsonb_agg(jsonb_build_object('actor',x.actor_id,'label',x.label) ORDER BY x.actor_id)
                            FROM public.evaluation_annotations x WHERE x.sample_id=s.id),
                        'adjudication',(SELECT jsonb_build_object('actor',x.actor_id,'label',x.label,'reason',x.reason)
                            FROM public.evaluation_adjudications x WHERE x.sample_id=s.id)) ORDER BY s.id))::text,'UTF8')),'hex')
                    INTO hash FROM public.evaluation_samples s WHERE s.dataset_id=d.id;
                UPDATE public.evaluation_datasets SET revision=revision+1,state='locked',locked_at=clock_timestamp(),locked_by=a.id,locked_digest=hash WHERE id=d.id;
            END IF;
            IF p_op<>'lock' THEN
                UPDATE public.evaluation_datasets SET revision=revision+1 WHERE id=d.id;
            END IF;
        END IF;
        INSERT INTO public.evaluation_dataset_operations(actor_id,operation_id,dataset_id,snapshot)
            VALUES(a.id,(p_payload->>'operation_id')::uuid,new_id,snapshot);
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'evaluation.datasets.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',public.evaluation_dataset_view(new_id,a.id,p_limit,p_offset));
    END $$;
    ''')
    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
        op.execute(f'''CREATE TRIGGER evaluation_history_immutable BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.evaluation_dataset_immutable()''')
        op.execute(f'''CREATE TRIGGER evaluation_history_no_truncate BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.evaluation_dataset_immutable()''')
    for function in (REQUEST, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {REQUEST} TO {role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT EXISTS(SELECT 1 FROM public.evaluation_datasets) OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action LIKE 'evaluation.datasets.%') OR EXISTS(SELECT 1 FROM public.platform_accounts WHERE role IN ('evaluation_annotator','evaluation_adjudicator')) OR EXISTS(SELECT 1 FROM public.platform_account_changes WHERE role IN ('evaluation_annotator','evaluation_adjudicator') OR previous_role IN ('evaluation_annotator','evaluation_adjudicator'))")).scalar_one():
        raise RuntimeError('Cannot discard evaluation history or evaluator accounts')
    governance_roles(bind, NEW_ROLES, OLD_ROLES)
    op.execute(f'DROP FUNCTION {REQUEST}')
    for helper in HELPERS[2:4]:
        op.execute(f'DROP FUNCTION {helper}')
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE public.{table}')
    for helper in (HELPERS[0], HELPERS[1], HELPERS[4]):
        op.execute(f'DROP FUNCTION {helper}')
    op.execute("DELETE FROM public.platform_role_permissions WHERE permission LIKE 'platform.evaluation.%'")
    op.execute("DELETE FROM public.platform_permissions WHERE name LIKE 'platform.evaluation.%'")
    op.execute("DELETE FROM public.platform_roles WHERE name IN ('evaluation_annotator','evaluation_adjudicator')")
    # Remove this revision's actions without hardcoding the preceding migrations' complete audit vocabulary.
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    actions = [action for action in re.findall(r"'([^']+)'::text", previous) if not action.startswith('evaluation.datasets.')]
    quoted = ','.join("'" + action.replace("'", "''") + "'" for action in actions)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({quoted}))')
