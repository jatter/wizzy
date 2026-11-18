"""Immutable fixed-version offline model runs and complete prediction imports."""
import os
import re

from alembic import op
from sqlalchemy import text

revision = '0030_evaluation_runs'
down_revision = '0029_evaluation_datasets'
REQUEST = 'public.evaluation_run_request(text,text,text,jsonb,uuid,integer,integer)'
HELPERS = ('public.evaluation_run_valid(text,jsonb)', 'public.evaluation_run_usable(uuid)',
           'public.evaluation_run_view(uuid)', 'public.evaluation_run_immutable()')
TABLES = ('evaluation_runs', 'evaluation_run_predictions', 'evaluation_run_operations')


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Evaluation migration requires an offline RLS-bypassing owner')
    name = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb
        OR rolinherit OR rolreplication) FROM pg_roles WHERE rolname=:name'''), {'name': name}).scalar_one_or_none():
        raise RuntimeError('Evaluation migration requires a restricted application role')
    role = bind.dialect.identifier_preparer.quote(name)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action IN ('evaluation.runs.create','evaluation.runs.import'))''')
    op.execute(r'''
    INSERT INTO public.platform_permissions VALUES ('platform.evaluation.run');
    INSERT INTO public.platform_role_permissions VALUES ('security_admin','platform.evaluation.run');

    CREATE FUNCTION public.evaluation_run_valid(p_op text,v jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $$
    DECLARE fields text[]; k text; x jsonb;
    BEGIN
        fields:=CASE p_op WHEN 'create' THEN ARRAY['operation_id','expected_revision','dataset_id','dataset_revision',
            'model_id','model_revision','prompt_hash','schema_version','rule_version','image_pipeline_version','required_strata']
            WHEN 'import' THEN ARRAY['operation_id','expected_revision','manifest_digest','source_reference','predictions'] END;
        IF fields IS NULL OR v IS NULL OR jsonb_typeof(v)<>'object' OR NOT v ?& fields OR v-fields<>'{}'
            OR jsonb_typeof(v->'operation_id') IS DISTINCT FROM 'string'
            OR v->>'operation_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
            OR jsonb_typeof(v->'expected_revision') IS DISTINCT FROM 'number'
            OR v->>'expected_revision' !~ '^(0|[1-9][0-9]{0,8})$' THEN RETURN false; END IF;
        k:=CASE WHEN p_op='create' THEN 'prompt_hash' ELSE 'manifest_digest' END;
        IF jsonb_typeof(v->k) IS DISTINCT FROM 'string' OR v->>k !~ '^[a-f0-9]{64}$' THEN RETURN false; END IF;
        IF p_op='create' THEN
            FOREACH k IN ARRAY ARRAY['dataset_id','model_id'] LOOP
                IF jsonb_typeof(v->k) IS DISTINCT FROM 'string'
                    OR v->>k !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
                    THEN RETURN false; END IF;
            END LOOP;
            FOREACH k IN ARRAY ARRAY['dataset_revision','model_revision'] LOOP
                IF jsonb_typeof(v->k) IS DISTINCT FROM 'number' OR v->>k !~ '^[1-9][0-9]{0,8}$'
                    THEN RETURN false; END IF;
            END LOOP;
            FOREACH k IN ARRAY ARRAY['schema_version','rule_version','image_pipeline_version'] LOOP
                IF jsonb_typeof(v->k) IS DISTINCT FROM 'string' OR v->>k !~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$'
                    THEN RETURN false; END IF;
            END LOOP;
            IF jsonb_typeof(v->'required_strata') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
            IF jsonb_array_length(v->'required_strata') NOT BETWEEN 1 AND 100 THEN RETURN false; END IF;
            FOR x IN SELECT value FROM jsonb_array_elements(v->'required_strata') LOOP
                IF jsonb_typeof(x)<>'object' OR NOT x ?& ARRAY['grade','question_type'] OR x-ARRAY['grade','question_type']<>'{}'
                    OR jsonb_typeof(x->'grade') IS DISTINCT FROM 'number' OR x->>'grade' !~ '^[1-6]$'
                    OR jsonb_typeof(x->'question_type') IS DISTINCT FROM 'string'
                    OR x->>'question_type' !~ '^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,119}$' THEN RETURN false; END IF;
            END LOOP;
            RETURN (SELECT count(*)=count(DISTINCT value) FROM jsonb_array_elements(v->'required_strata'));
        END IF;
        IF jsonb_typeof(v->'source_reference') IS DISTINCT FROM 'string'
            OR length(btrim(v->>'source_reference')) NOT BETWEEN 1 AND 200
            OR v->>'source_reference'<>btrim(v->>'source_reference') OR v->>'source_reference' ~ '[[:cntrl:]]'
            OR jsonb_typeof(v->'predictions') IS DISTINCT FROM 'array' THEN RETURN false; END IF;
        IF jsonb_array_length(v->'predictions')>50000 THEN RETURN false; END IF;
        FOR x IN SELECT value FROM jsonb_array_elements(v->'predictions') LOOP
            IF jsonb_typeof(x)<>'object' OR NOT x ?& ARRAY['question_id','verdict','automatic_release']
                OR x-ARRAY['question_id','verdict','automatic_release']<>'{}'
                OR jsonb_typeof(x->'question_id') IS DISTINCT FROM 'string'
                OR x->>'question_id' !~ '^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
                OR jsonb_typeof(x->'verdict') IS DISTINCT FROM 'string'
                OR x->>'verdict' NOT IN ('correct','incorrect','unanswered','pending_review','unsupported')
                OR jsonb_typeof(x->'automatic_release') IS DISTINCT FROM 'boolean'
                OR (x->>'verdict' IN ('pending_review','unsupported') AND x->'automatic_release'='true') THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
    END $$;

    CREATE TABLE public.evaluation_runs (
        id uuid PRIMARY KEY, dataset_id uuid NOT NULL REFERENCES public.evaluation_datasets(id),
        model_id uuid NOT NULL REFERENCES public.model_catalog(id), model_revision integer NOT NULL,
        manifest jsonb NOT NULL, manifest_digest text NOT NULL CHECK(manifest_digest ~ '^[a-f0-9]{64}$'),
        revision integer NOT NULL DEFAULT 1 CHECK(revision IN (1,2)),
        state text NOT NULL DEFAULT 'awaiting_predictions' CHECK(state IN ('awaiting_predictions','completed')),
        source_reference text, created_by uuid NOT NULL REFERENCES public.platform_accounts(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), completed_at timestamptz,
        FOREIGN KEY(model_id,model_revision) REFERENCES public.model_catalog_revisions(model_id,revision),
        CHECK ((state='awaiting_predictions' AND revision=1 AND source_reference IS NULL AND completed_at IS NULL)
            OR (state='completed' AND revision=2 AND source_reference IS NOT NULL AND completed_at IS NOT NULL))
    );
    CREATE INDEX evaluation_runs_page ON public.evaluation_runs(created_at DESC,id DESC);
    CREATE TABLE public.evaluation_run_predictions (
        run_id uuid NOT NULL REFERENCES public.evaluation_runs(id), sample_id uuid NOT NULL REFERENCES public.evaluation_samples(id),
        verdict text NOT NULL CHECK(verdict IN ('correct','incorrect','unanswered','pending_review','unsupported')),
        automatic_release boolean NOT NULL, PRIMARY KEY(run_id,sample_id),
        CHECK (NOT automatic_release OR verdict NOT IN ('pending_review','unsupported'))
    );
    CREATE TABLE public.evaluation_run_operations (
        actor_id uuid NOT NULL REFERENCES public.platform_accounts(id), operation_id uuid NOT NULL,
        run_id uuid NOT NULL REFERENCES public.evaluation_runs(id), snapshot jsonb NOT NULL, result jsonb NOT NULL,
        PRIMARY KEY(actor_id,operation_id)
    );
    CREATE FUNCTION public.evaluation_run_immutable() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        IF TG_OP='UPDATE' AND TG_TABLE_NAME='evaluation_runs' THEN
        IF OLD.state='awaiting_predictions'
            AND NEW.state='completed' AND NEW.revision=2
            AND (to_jsonb(NEW)-ARRAY['state','revision','source_reference','completed_at'])=
                (to_jsonb(OLD)-ARRAY['state','revision','source_reference','completed_at']) THEN RETURN NEW; END IF;
        END IF;
        RAISE EXCEPTION 'Evaluation run history is immutable' USING ERRCODE='55000';
    END $$;
    CREATE FUNCTION public.evaluation_run_usable(p_dataset uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE s public.evaluation_samples%ROWTYPE; usable boolean:=true; n integer:=0;
    BEGIN
        PERFORM id FROM public.evaluation_datasets WHERE id=p_dataset AND state='locked' AND split IN ('validation','test') FOR SHARE;
        IF NOT FOUND THEN RETURN false; END IF;
        FOR s IN SELECT * FROM public.evaluation_samples WHERE dataset_id=p_dataset ORDER BY student_id,authorization_id,id LOOP
            n:=n+1;
            usable:=public.evaluation_source_active(s.authorization_id,s.question_id,s.source_revision,s.source_digest) AND usable;
        END LOOP;
        RETURN usable AND n>0;
    END $$;
    CREATE FUNCTION public.evaluation_run_view(p_id uuid) RETURNS jsonb
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT r.manifest || jsonb_build_object('id',r.id,'manifest_digest',r.manifest_digest,'revision',r.revision,'state',r.state,
            'source_reference',r.source_reference,'created_at',r.created_at,'completed_at',r.completed_at,
            'usable',public.evaluation_run_usable(r.dataset_id),'production_eligible',false,
            'prediction_count',(SELECT count(*) FROM public.evaluation_run_predictions p WHERE p.run_id=r.id))
        FROM public.evaluation_runs r WHERE r.id=p_id
    $$;
    CREATE FUNCTION public.evaluation_run_request(p_hash text,p_op text,p_request text,p_payload jsonb,
        p_run uuid,p_limit integer,p_offset integer) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; se public.platform_sessions%ROWTYPE; account uuid;
        r public.evaluation_runs%ROWTYPE; d public.evaluation_datasets%ROWTYPE; m public.model_catalog%ROWTYPE;
        old public.evaluation_run_operations%ROWTYPE; config jsonb; value jsonb; manifest jsonb; snapshot jsonb;
        golds jsonb; predictions jsonb; n integer; new_id uuid; needed text;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR UPDATE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO se FROM public.platform_sessions WHERE token_hash=p_hash FOR UPDATE;
        IF NOT FOUND OR se.revoked_at IS NOT NULL OR se.expires_at<=clock_timestamp() OR se.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','detail','report','create','import')
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object' OR p_limit IS NULL OR p_limit NOT BETWEEN 1 AND 100
            OR p_offset IS NULL OR p_offset NOT BETWEEN 0 AND 1000000 OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 80
            OR ((p_op IN ('list','create'))<>(p_run IS NULL)) THEN RETURN jsonb_build_object('status',422); END IF;
        needed:=CASE WHEN p_op IN ('create','import') THEN 'platform.evaluation.run' ELSE 'platform.evaluation.read' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id) VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op IN ('list','detail','report') THEN
            IF p_payload<>'{}' THEN RETURN jsonb_build_object('status',422); END IF;
            IF p_op='list' THEN
                SELECT coalesce(jsonb_agg(public.evaluation_run_view(t.id) ORDER BY t.created_at DESC,t.id DESC),'[]') INTO value
                    FROM (SELECT id,created_at FROM public.evaluation_runs ORDER BY created_at DESC,id DESC LIMIT p_limit+1 OFFSET p_offset) t;
                RETURN jsonb_build_object('status',200,'value',jsonb_build_object('items',value-p_limit,
                    'next_offset',CASE WHEN jsonb_array_length(value)>p_limit THEN p_offset+p_limit END));
            END IF;
            SELECT * INTO r FROM public.evaluation_runs WHERE id=p_run FOR SHARE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF p_op='detail' THEN RETURN jsonb_build_object('status',200,'value',public.evaluation_run_view(r.id)); END IF;
            IF r.state<>'completed' OR NOT public.evaluation_run_usable(r.dataset_id) THEN RETURN jsonb_build_object('status',409); END IF;
            SELECT jsonb_agg(jsonb_build_object('question_id',s.id,'student_key',md5('evaluation-student/'||s.student_id::text),
                'page_key',md5('evaluation-page/'||s.submission_id::text||'/'||s.page_position::text),'grade',s.grade,
                'question_type',label->'question_type','difficulty',label->'difficulty','verdict',label->'verdict',
                'requires_abstention',label->'requires_abstention') ORDER BY s.id) INTO golds
                FROM public.evaluation_samples s CROSS JOIN LATERAL (
                    SELECT coalesce((SELECT x.label FROM public.evaluation_adjudications x WHERE x.sample_id=s.id),
                        (SELECT x.label FROM public.evaluation_annotations x WHERE x.sample_id=s.id ORDER BY x.actor_id LIMIT 1)) label
                ) g WHERE s.dataset_id=r.dataset_id;
            SELECT jsonb_agg(jsonb_build_object('question_id',sample_id,'verdict',verdict,'automatic_release',automatic_release)
                ORDER BY sample_id) INTO predictions FROM public.evaluation_run_predictions WHERE run_id=r.id;
            RETURN jsonb_build_object('status',200,'value',jsonb_build_object('run_id',r.id,'manifest_digest',r.manifest_digest,
                'golds',golds,'predictions',predictions,'required_strata',r.manifest->'required_strata'));
        END IF;
        IF NOT coalesce(public.evaluation_run_valid(p_op,p_payload),false) THEN RETURN jsonb_build_object('status',422); END IF;
        snapshot:=jsonb_build_object('operation',p_op,'run',p_run,'body',p_payload);
        SELECT * INTO old FROM public.evaluation_run_operations WHERE actor_id=a.id AND operation_id=(p_payload->>'operation_id')::uuid;
        IF FOUND THEN
            IF old.snapshot<>snapshot THEN RETURN jsonb_build_object('status',409); END IF;
            SELECT * INTO r FROM public.evaluation_runs WHERE id=old.run_id FOR SHARE;
            IF NOT public.evaluation_run_usable(r.dataset_id) THEN RETURN jsonb_build_object('status',409); END IF;
            RETURN jsonb_build_object('status',200,'value',old.result);
        END IF;
        IF p_op='create' THEN
            IF p_payload->'expected_revision'<>'0' THEN RETURN jsonb_build_object('status',409); END IF;
            SELECT * INTO d FROM public.evaluation_datasets WHERE id=(p_payload->>'dataset_id')::uuid FOR SHARE;
            IF NOT FOUND OR d.state<>'locked' OR d.split NOT IN ('test','validation')
                OR d.revision<>(p_payload->>'dataset_revision')::integer OR NOT public.evaluation_run_usable(d.id)
                THEN RETURN jsonb_build_object('status',409); END IF;
            SELECT * INTO m FROM public.model_catalog WHERE id=(p_payload->>'model_id')::uuid FOR SHARE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409); END IF;
            SELECT x.config INTO config FROM public.model_catalog_revisions x WHERE x.model_id=m.id
                AND x.revision=(p_payload->>'model_revision')::integer FOR SHARE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409); END IF;
            manifest:=jsonb_build_object('dataset',jsonb_build_object('id',d.id,'name',d.name,'revision',d.revision,
                    'split',d.split,'locked_digest',d.locked_digest),
                'model',jsonb_build_object('id',m.id,'revision',(p_payload->>'model_revision')::integer,
                    'provider',m.provider,'model_key',m.model_key,'fixed_version',m.fixed_version,'region',m.region,
                    'display_name',config->'display_name'),
                'prompt_hash',p_payload->'prompt_hash','schema_version',p_payload->'schema_version',
                'rule_version',p_payload->'rule_version','image_pipeline_version',p_payload->'image_pipeline_version',
                'required_strata',p_payload->'required_strata','algorithm_version','grading-statistics-v1',
                'execution_mode','offline_import');
            new_id:=gen_random_uuid();
            INSERT INTO public.evaluation_runs(id,dataset_id,model_id,model_revision,manifest,manifest_digest,created_by)
                VALUES(new_id,d.id,m.id,(p_payload->>'model_revision')::integer,manifest,
                    encode(sha256(convert_to(manifest::text,'UTF8')),'hex'),a.id);
        ELSE
            SELECT * INTO r FROM public.evaluation_runs WHERE id=p_run FOR UPDATE;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
            IF r.state<>'awaiting_predictions' OR r.revision<>(p_payload->>'expected_revision')::integer
                OR r.manifest_digest<>p_payload->>'manifest_digest' OR NOT public.evaluation_run_usable(r.dataset_id)
                THEN RETURN jsonb_build_object('status',409); END IF;
            SELECT count(*) INTO n FROM public.evaluation_samples WHERE dataset_id=r.dataset_id;
            IF jsonb_array_length(p_payload->'predictions')<>n OR
                (SELECT count(DISTINCT (x.value->>'question_id')::uuid) FROM jsonb_array_elements(p_payload->'predictions') x)<>n OR
                EXISTS(SELECT 1 FROM jsonb_array_elements(p_payload->'predictions') x
                    WHERE NOT EXISTS(SELECT 1 FROM public.evaluation_samples s
                        WHERE s.id=(x.value->>'question_id')::uuid AND s.dataset_id=r.dataset_id))
                THEN RETURN jsonb_build_object('status',409); END IF;
            INSERT INTO public.evaluation_run_predictions(run_id,sample_id,verdict,automatic_release)
                SELECT r.id,(x.value->>'question_id')::uuid,x.value->>'verdict',(x.value->>'automatic_release')::boolean
                FROM jsonb_array_elements(p_payload->'predictions') x;
            UPDATE public.evaluation_runs SET revision=2,state='completed',source_reference=p_payload->>'source_reference',
                completed_at=clock_timestamp() WHERE id=r.id;
            new_id:=r.id;
        END IF;
        value:=public.evaluation_run_view(new_id);
        INSERT INTO public.evaluation_run_operations(actor_id,operation_id,run_id,snapshot,result)
            VALUES(a.id,(p_payload->>'operation_id')::uuid,new_id,snapshot,value);
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'evaluation.runs.'||p_op,p_request);
        RETURN jsonb_build_object('status',201,'value',value);
    END $$;
    ''')
    for table in TABLES:
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC, {role}')
        op.execute(f'''CREATE TRIGGER evaluation_run_history_immutable BEFORE UPDATE OR DELETE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.evaluation_run_immutable()''')
        op.execute(f'''CREATE TRIGGER evaluation_run_history_no_truncate BEFORE TRUNCATE ON public.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION public.evaluation_run_immutable()''')
    for function in (REQUEST, *HELPERS):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {REQUEST} TO {role}')


def downgrade():
    bind = op.get_bind()
    if bind.execute(text("SELECT EXISTS(SELECT 1 FROM public.evaluation_runs) OR EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action LIKE 'evaluation.runs.%')")).scalar_one():
        raise RuntimeError('Cannot discard evaluation run history')
    op.execute(f'DROP FUNCTION {REQUEST}')
    op.execute(f'DROP FUNCTION {HELPERS[2]}')
    op.execute(f'DROP FUNCTION {HELPERS[1]}')
    for table in reversed(TABLES):
        op.execute(f'DROP TABLE public.{table}')
    for function in (HELPERS[0], HELPERS[3]):
        op.execute(f'DROP FUNCTION {function}')
    op.execute("DELETE FROM public.platform_role_permissions WHERE permission='platform.evaluation.run'")
    op.execute("DELETE FROM public.platform_permissions WHERE name='platform.evaluation.run'")
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    actions = [a for a in re.findall(r"'([^']+)'::text", previous) if not a.startswith('evaluation.runs.')]
    quoted = ','.join("'" + a.replace("'", "''") + "'" for a in actions)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({quoted}))')
