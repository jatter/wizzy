"""Daily homework lists, source evidence and separately fenced OCR imports."""
import os

from alembic import op
from sqlalchemy import text

revision = '0042_daily_homework'
down_revision = '0041_mistake_bank'


def upgrade():
    config = op.get_context().config
    app = config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    worker = config.attributes.get('worker_role') or os.getenv('WIZZY_WORKER_ROLE', app + '_worker')
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute('''
    CREATE TABLE public.homework_imports (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL, created_by uuid NOT NULL REFERENCES public.users(id),
        source_kind text NOT NULL CHECK(source_kind='manual_image'), source_label text NOT NULL,
        target_date date, status text NOT NULL DEFAULT 'queued', revision integer NOT NULL DEFAULT 1,
        draft jsonb NOT NULL DEFAULT '{"items":[],"no_homework":[],"notes":[]}',
        applied_ids jsonb NOT NULL DEFAULT '{}',
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id)
    );
    CREATE INDEX homework_import_queue ON public.homework_imports(status,created_at);
    CREATE TABLE public.homework_import_images (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        import_id uuid NOT NULL, media_id uuid NOT NULL, position integer NOT NULL CHECK(position BETWEEN 1 AND 9),
        crop_rect jsonb, status text NOT NULL DEFAULT 'queued', error_message text,
        UNIQUE(import_id,position), UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,import_id) REFERENCES public.homework_imports(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,media_id) REFERENCES public.media_assets(tenant_id,student_id,id)
    );
    CREATE TABLE public.homework_import_runs (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        import_id uuid NOT NULL, image_id uuid NOT NULL, attempt integer NOT NULL,
        state text NOT NULL, token uuid NOT NULL, lease_until timestamptz NOT NULL,
        call_started_at timestamptz, model_config jsonb NOT NULL, prompt_content text NOT NULL,
        prompt_version text NOT NULL, content text NOT NULL DEFAULT '', response jsonb,
        result jsonb, usage jsonb, cost_state text NOT NULL DEFAULT 'unknown', error_message text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
        UNIQUE(image_id,attempt), UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,import_id) REFERENCES public.homework_imports(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,image_id) REFERENCES public.homework_import_images(tenant_id,student_id,id)
    );
    CREATE TABLE public.homework_days (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        assigned_date date NOT NULL, revision integer NOT NULL DEFAULT 1,
        scope_state text NOT NULL DEFAULT 'incomplete' CHECK(scope_state IN ('incomplete','complete')),
        subjects jsonb NOT NULL DEFAULT '{"chinese":"unknown","math":"unknown","english":"unknown","other":"unknown"}',
        confirmed_by uuid, confirmed_at timestamptz,
        UNIQUE(tenant_id,student_id,assigned_date,deletion_epoch), UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id)
    );
    CREATE TABLE public.homework_items (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        day_id uuid NOT NULL, subject text NOT NULL CHECK(subject IN ('chinese','math','english','other')),
        item_type text NOT NULL CHECK(item_type IN ('written','recitation','reading','preview','preparation','other')),
        title text NOT NULL, raw_text text NOT NULL DEFAULT '', locator jsonb NOT NULL DEFAULT '{}', due_at timestamptz,
        work_state text NOT NULL DEFAULT 'pending' CHECK(work_state IN ('pending','in_progress','submitted','completed','exempted')),
        verification_state text NOT NULL DEFAULT 'unverified' CHECK(verification_state IN ('unverified','guardian_confirmed')),
        completion_actor text CHECK(completion_actor IN ('child','guardian')),
        revision integer NOT NULL DEFAULT 1, sort_order integer NOT NULL DEFAULT 0,
        deleted_at timestamptz, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,day_id) REFERENCES public.homework_days(tenant_id,student_id,id)
    );
    CREATE INDEX homework_items_day ON public.homework_items(day_id,subject,sort_order,id);
    CREATE TABLE public.homework_item_sources (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        item_id uuid NOT NULL, image_id uuid NOT NULL, run_id uuid, candidate_id text NOT NULL,
        raw_text text NOT NULL, bbox jsonb, source_mark text,
        UNIQUE(image_id,candidate_id,item_id),
        FOREIGN KEY(tenant_id,student_id,item_id) REFERENCES public.homework_items(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,image_id) REFERENCES public.homework_import_images(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,run_id) REFERENCES public.homework_import_runs(tenant_id,student_id,id)
    );
    CREATE TABLE public.homework_item_events (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        item_id uuid, actor_id uuid, actor_audience text NOT NULL, action text NOT NULL,
        client_request_id uuid NOT NULL, request_hash text NOT NULL, before_value jsonb, after_value jsonb,
        receipt jsonb NOT NULL, reason text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id,client_request_id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES public.students(tenant_id,id),
        FOREIGN KEY(tenant_id,student_id,item_id) REFERENCES public.homework_items(tenant_id,student_id,id)
    );
    CREATE INDEX homework_events_item ON public.homework_item_events(item_id,created_at,id);
    ''')
    tables = ('homework_imports', 'homework_import_images', 'homework_import_runs', 'homework_days',
              'homework_items', 'homework_item_sources', 'homework_item_events')
    for table in tables:
        scope = """tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND (NULLIF(current_setting('app.student_id',true),'') IS NULL
                OR student_id=NULLIF(current_setting('app.student_id',true),'')::uuid)"""
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {table}_isolation ON public.{table} USING ({scope}) WITH CHECK ({scope})')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC')
        # The worker can only use bounded functions below; model secrets never reach API table grants.
        if table == 'homework_item_events':
            op.execute(f'GRANT SELECT, INSERT ON public.{table} TO {quote(app)}')
            op.execute(f'GRANT UPDATE(receipt) ON public.{table} TO {quote(app)}')
        elif table != 'homework_import_runs':
            op.execute(f'GRANT SELECT, INSERT, UPDATE ON public.{table} TO {quote(app)}')
    op.execute(f'''GRANT SELECT (id,tenant_id,student_id,deletion_epoch,import_id,image_id,attempt,state,
        prompt_version,result,usage,cost_state,error_message,created_at,finished_at) ON public.homework_import_runs TO {quote(app)}''')
    op.execute('''
    CREATE FUNCTION public.homework_import_allowed(p_id uuid) RETURNS boolean
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT EXISTS(SELECT 1 FROM public.homework_imports i
            JOIN public.students s ON s.id=i.student_id AND s.tenant_id=i.tenant_id
            JOIN public.tenants t ON t.id=i.tenant_id
            JOIN public.users u ON u.id=i.created_by
            JOIN public.memberships m ON m.tenant_id=i.tenant_id AND m.user_id=i.created_by
            WHERE i.id=p_id AND i.status<>'cancelled' AND s.status='active' AND t.status='active'
                AND s.consent_active AND s.deletion_epoch=i.deletion_epoch AND u.status='active' AND m.status='active'
                AND (m.role='owner' OR EXISTS(SELECT 1 FROM public.student_access a
                    WHERE a.tenant_id=i.tenant_id AND a.student_id=i.student_id AND a.user_id=i.created_by)))
    $$;
    REVOKE ALL ON FUNCTION public.homework_import_allowed(uuid) FROM PUBLIC;

    CREATE FUNCTION public.settle_homework_import(p_id uuid) RETURNS void
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE c integer; f integer; q integer;
    BEGIN
        SELECT count(*) FILTER(WHERE status='succeeded'), count(*) FILTER(WHERE status='failed'),
            count(*) FILTER(WHERE status IN ('queued','processing')) INTO c,f,q
            FROM public.homework_import_images WHERE import_id=p_id;
        UPDATE public.homework_imports SET status=CASE WHEN q>0 THEN 'processing' WHEN c>0 AND f>0 THEN 'partial'
            WHEN c>0 THEN 'review_ready' ELSE 'failed' END, revision=revision+1 WHERE id=p_id AND status<>'cancelled';
    END;
    $$;
    REVOKE ALL ON FUNCTION public.settle_homework_import(uuid) FROM PUBLIC;

    CREATE FUNCTION public.claim_homework_import(p_model uuid,p_prompt text,p_version text) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE image public.homework_import_images%ROWTYPE; imp public.homework_imports%ROWTYPE;
        r public.homework_import_runs%ROWTYPE; model jsonb; token uuid; rid uuid; attempt integer; key text;
    BEGIN
        FOR r IN SELECT * FROM public.homework_import_runs WHERE state='running'
            AND lease_until<clock_timestamp() ORDER BY lease_until LIMIT 20 LOOP
            PERFORM 1 FROM public.students WHERE id=r.student_id FOR SHARE;
            PERFORM 1 FROM public.homework_imports WHERE id=r.import_id FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN CONTINUE; END IF;
            PERFORM 1 FROM public.homework_import_images WHERE id=r.image_id FOR UPDATE;
            SELECT * INTO r FROM public.homework_import_runs WHERE id=r.id AND state='running'
                AND lease_until<clock_timestamp() FOR UPDATE;
            IF NOT FOUND THEN CONTINUE; END IF;
            UPDATE public.homework_import_runs SET state='failed',error_message=CASE WHEN call_started_at IS NULL
                THEN '处理服务中断，可重试' ELSE 'call_outcome_unknown：调用结果未知，可能已计费，请确认后重试' END,
                finished_at=clock_timestamp() WHERE id=r.id;
            UPDATE public.homework_import_images SET status='failed',error_message=CASE WHEN r.call_started_at IS NULL
                THEN '处理服务中断，可重试' ELSE 'call_outcome_unknown：调用结果未知，可能已计费，请确认后重试' END WHERE id=r.image_id;
            PERFORM public.settle_homework_import(r.import_id);
        END LOOP;
        FOR image IN SELECT x.* FROM public.homework_import_images x
            JOIN public.homework_imports i ON i.id=x.import_id
            WHERE x.status='queued' AND i.status IN ('queued','processing')
            ORDER BY i.created_at,x.position LIMIT 20 LOOP
            PERFORM 1 FROM public.students WHERE id=image.student_id FOR SHARE;
            SELECT * INTO imp FROM public.homework_imports WHERE id=image.import_id
                AND status IN ('queued','processing') FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN CONTINUE; END IF;
            SELECT * INTO image FROM public.homework_import_images WHERE id=image.id AND status='queued'
                FOR UPDATE SKIP LOCKED;
            IF NOT FOUND THEN CONTINUE; END IF;
            IF NOT public.homework_import_allowed(image.import_id) THEN
                UPDATE public.homework_imports SET status='cancelled' WHERE id=image.import_id;
                CONTINUE;
            END IF;
            IF NOT public.homework_import_allowed(image.import_id) THEN CONTINUE; END IF;
            SELECT to_jsonb(m) INTO model FROM public.model_configs m WHERE p_model IS NULL OR m.id=p_model
                ORDER BY priority,created_at,id LIMIT 1;
            SELECT clean_key INTO key FROM public.media_assets WHERE id=image.media_id AND status='ready'
                AND deletion_epoch=image.deletion_epoch;
            SELECT coalesce(max(x.attempt),0)+1 INTO attempt FROM public.homework_import_runs x WHERE x.image_id=image.id;
            token:=gen_random_uuid(); rid:=gen_random_uuid();
            INSERT INTO public.homework_import_runs(id,tenant_id,student_id,deletion_epoch,import_id,image_id,attempt,
                state,token,lease_until,model_config,prompt_content,prompt_version)
                VALUES(rid,image.tenant_id,image.student_id,image.deletion_epoch,image.import_id,image.id,attempt,
                    'running',token,clock_timestamp()+interval '90 seconds',coalesce(model,'{}'),p_prompt,p_version);
            UPDATE public.homework_import_images SET status='processing',error_message=NULL WHERE id=image.id;
            UPDATE public.homework_imports SET status='processing' WHERE id=image.import_id;
            RETURN jsonb_build_object('id',rid,'token',token,'import_id',image.import_id,'image_id',image.id,
                'model',model,'prompt_content',p_prompt,'clean_key',key,'crop_rect',image.crop_rect,
                'target_date',imp.target_date);
        END LOOP;
        RETURN NULL;
    END;
    $$;

    CREATE FUNCTION public.heartbeat_homework_import(p_id uuid,p_token uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r public.homework_import_runs%ROWTYPE; changed integer;
    BEGIN
        SELECT * INTO r FROM public.homework_import_runs WHERE id=p_id;
        IF NOT FOUND THEN RETURN false; END IF;
        PERFORM 1 FROM public.students WHERE id=r.student_id FOR SHARE;
        PERFORM 1 FROM public.homework_imports WHERE id=r.import_id FOR UPDATE;
        PERFORM 1 FROM public.homework_import_images WHERE id=r.image_id FOR UPDATE;
        IF NOT public.homework_import_allowed(r.import_id) THEN RETURN false; END IF;
        UPDATE public.homework_import_runs SET lease_until=clock_timestamp()+interval '90 seconds'
            WHERE id=p_id AND token=p_token AND state='running' AND lease_until>clock_timestamp();
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;

    CREATE FUNCTION public.start_homework_import_call(p_id uuid,p_token uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE changed integer;
    BEGIN
        IF NOT public.heartbeat_homework_import(p_id,p_token) THEN RETURN false; END IF;
        UPDATE public.homework_import_runs SET call_started_at=clock_timestamp()
            WHERE id=p_id AND token=p_token AND state='running' AND call_started_at IS NULL;
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;

    CREATE FUNCTION public.finish_homework_import_call(p_id uuid,p_token uuid,p_content text,p_response jsonb,p_result jsonb,p_error text)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE r public.homework_import_runs%ROWTYPE; merged jsonb;
    BEGIN
        IF NOT public.heartbeat_homework_import(p_id,p_token) THEN RETURN false; END IF;
        SELECT * INTO r FROM public.homework_import_runs WHERE id=p_id AND token=p_token AND state='running' FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        UPDATE public.homework_import_runs SET state=CASE WHEN p_error IS NULL THEN 'succeeded' ELSE 'failed' END,
            content=coalesce(p_content,''),response=p_response,result=p_result,error_message=p_error,
            usage=p_response->'usage',finished_at=clock_timestamp() WHERE id=p_id;
        UPDATE public.homework_import_images SET status=CASE WHEN p_error IS NULL THEN 'succeeded' ELSE 'failed' END,
            error_message=p_error WHERE id=r.image_id;
        IF p_error IS NULL AND p_result IS NOT NULL THEN
            SELECT jsonb_build_object('items',coalesce(draft->'items','[]')||coalesce(p_result->'items','[]'),
                'no_homework',coalesce(draft->'no_homework','[]')||coalesce(p_result->'no_homework','[]'),
                'notes',coalesce(draft->'notes','[]')||coalesce(p_result->'notes','[]')) INTO merged
                FROM public.homework_imports WHERE id=r.import_id FOR UPDATE;
            UPDATE public.homework_imports SET draft=merged WHERE id=r.import_id;
        END IF;
        PERFORM public.settle_homework_import(r.import_id);
        RETURN true;
    END;
    $$;
    ''')
    for function in ('claim_homework_import(uuid,text,text)', 'heartbeat_homework_import(uuid,uuid)',
                     'start_homework_import_call(uuid,uuid)',
                     'finish_homework_import_call(uuid,uuid,text,jsonb,jsonb,text)'):
        op.execute(f'REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{function} TO {quote(worker)}')


def downgrade():
    if op.get_bind().execute(text('SELECT EXISTS(SELECT 1 FROM public.homework_days) OR EXISTS(SELECT 1 FROM public.homework_imports)')).scalar_one():
        raise RuntimeError('Cannot discard daily homework and source history')
    op.execute('''
    DROP FUNCTION public.finish_homework_import_call(uuid,uuid,text,jsonb,jsonb,text);
    DROP FUNCTION public.start_homework_import_call(uuid,uuid);
    DROP FUNCTION public.heartbeat_homework_import(uuid,uuid);
    DROP FUNCTION public.claim_homework_import(uuid,text,text);
    DROP FUNCTION public.settle_homework_import(uuid);
    DROP FUNCTION public.homework_import_allowed(uuid);
    DROP TABLE public.homework_item_events,public.homework_item_sources,public.homework_items,
        public.homework_days,public.homework_import_runs,public.homework_import_images,public.homework_imports;
    ''')
