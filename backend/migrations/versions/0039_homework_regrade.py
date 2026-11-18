"""Regrade a failed homework batch, and move to the second-priority model after three access failures."""
import os

from alembic import op
from sqlalchemy import text

revision = '0039_homework_regrade'
down_revision = '0038_auto_homework'


def upgrade():
    config = op.get_context().config
    app = config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    worker = config.attributes.get('worker_role') or os.getenv('WIZZY_WORKER_ROLE', app + '_worker')
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute("""
    ALTER TABLE public.homework_grading_batches
        ADD COLUMN primary_call_failures integer NOT NULL DEFAULT 0 CHECK(primary_call_failures>=0);
    ALTER TABLE public.homework_grading_batches DROP CONSTRAINT homework_grading_batches_state_check;
    ALTER TABLE public.homework_grading_batches ADD CONSTRAINT homework_grading_batches_state_check
        CHECK(state IN ('pending','running','settled','failed'));
    UPDATE public.homework_grading_batches b SET primary_call_failures=(
        SELECT count(*) FROM public.homework_model_outputs o
        WHERE o.submission_id=b.submission_id AND o.status='failed'
            AND (o.error_message LIKE '模型调用失败（HTTP %' OR o.error_message LIKE '模型连接中断或超时%')
            AND o.model_id=(SELECT m.id FROM public.model_configs m ORDER BY m.priority,m.created_at,m.id LIMIT 1));

    CREATE FUNCTION public.homework_model_for_failures(p_failures integer) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT CASE WHEN p_failures>=3 AND jsonb_array_length(models)>=2 THEN models->1 ELSE models->0 END
        FROM (SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.priority,m.created_at,m.id),'[]'::jsonb) AS models
            FROM (SELECT * FROM public.model_configs ORDER BY priority,created_at,id LIMIT 2) m) chosen
    $$;

    CREATE OR REPLACE FUNCTION public.claim_homework_grading() RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE e record; c record; b public.homework_grading_batches%ROWTYPE;
        pending record; chosen_model jsonb; chosen_prompts jsonb; pages jsonb; next_token uuid;
    BEGIN
        FOR b IN SELECT * FROM public.homework_grading_batches
            WHERE state='running' AND lease_until<clock_timestamp() ORDER BY lease_until LIMIT 10 LOOP
            PERFORM public.settle_homework_grading(b.submission_id,b.token,'处理服务中断，请重新提交作业。');
        END LOOP;
        FOR pending IN SELECT hb.submission_id,hb.event_id FROM public.homework_grading_batches hb
            WHERE hb.state='pending' ORDER BY hb.created_at,hb.submission_id LIMIT 20 LOOP
            SELECT * INTO c FROM public.lock_execution_context(pending.event_id);
            IF NOT FOUND THEN CONTINUE; END IF;
            IF c.job_status='cancelled' OR c.submission_status='cancelled' THEN
                UPDATE public.homework_grading_batches SET state='failed',finished_at=clock_timestamp()
                    WHERE submission_id=c.submission_id AND state='pending';
                CONTINUE;
            END IF;
            IF c.job_status<>'queued' OR c.submission_status<>'queued' THEN CONTINUE; END IF;
            SELECT * INTO b FROM public.homework_grading_batches WHERE submission_id=c.submission_id FOR UPDATE;
            IF b.state<>'pending' THEN CONTINUE; END IF;
            chosen_model:=coalesce(public.homework_model_for_failures(b.primary_call_failures),'{}'::jsonb);
            SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.priority,p.created_at,p.id),'[]'::jsonb)
                INTO chosen_prompts FROM (SELECT * FROM public.grading_prompts WHERE enabled ORDER BY priority,created_at,id LIMIT 2) p;
            next_token:=gen_random_uuid();
            UPDATE public.homework_grading_batches AS hb SET token=next_token,
                lease_until=clock_timestamp()+interval '90 seconds',state='running',
                model_config=chosen_model,prompts=chosen_prompts,finished_at=NULL
                WHERE hb.submission_id=b.submission_id AND hb.state='pending';
            UPDATE public.jobs SET status='running',error_code=NULL,finished_at=NULL WHERE id=c.job_id;
            UPDATE public.submissions SET status='processing' WHERE id=c.submission_id;
            SELECT jsonb_agg(jsonb_build_object('position',p.position,'clean_key',m.clean_key) ORDER BY p.position)
                INTO pages FROM public.submission_pages p JOIN public.media_assets m ON m.id=p.media_id
                WHERE p.submission_id=c.submission_id AND m.status='ready' AND m.deletion_epoch=c.deletion_epoch;
            RETURN jsonb_build_object('submission_id',c.submission_id,'token',next_token,'model',chosen_model,
                'prompts',chosen_prompts,'pages',pages,'subject',(SELECT subject FROM public.submissions WHERE id=c.submission_id));
        END LOOP;
        FOR e IN SELECT x.id FROM public.outbox_events x JOIN public.jobs j ON j.id=x.aggregate_id
            WHERE x.event_type='submission.queued' AND j.status='queued'
            AND NOT EXISTS(SELECT 1 FROM public.homework_grading_batches hb WHERE hb.submission_id=j.submission_id)
            AND NOT EXISTS(SELECT 1 FROM public.execution_receipts r WHERE r.event_id=x.id)
            ORDER BY x.created_at,x.id LIMIT 20 LOOP
            SELECT * INTO c FROM public.lock_execution_context(e.id);
            IF NOT FOUND OR c.job_status<>'queued' OR c.submission_status<>'queued' THEN CONTINUE; END IF;
            IF EXISTS(SELECT 1 FROM public.homework_grading_batches WHERE submission_id=c.submission_id) THEN CONTINUE; END IF;
            chosen_model:=coalesce(public.homework_model_for_failures(0),'{}'::jsonb);
            SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.priority,p.created_at,p.id),'[]'::jsonb)
                INTO chosen_prompts FROM (SELECT * FROM public.grading_prompts WHERE enabled ORDER BY priority,created_at,id LIMIT 2) p;
            next_token:=gen_random_uuid();
            INSERT INTO public.homework_grading_batches(submission_id,event_id,token,lease_until,state,model_config,prompts)
                VALUES(c.submission_id,e.id,next_token,clock_timestamp()+interval '90 seconds','running',chosen_model,chosen_prompts);
            UPDATE public.jobs SET status='running' WHERE id=c.job_id;
            UPDATE public.submissions SET status='processing' WHERE id=c.submission_id;
            SELECT jsonb_agg(jsonb_build_object('position',p.position,'clean_key',m.clean_key) ORDER BY p.position)
                INTO pages FROM public.submission_pages p JOIN public.media_assets m ON m.id=p.media_id
                WHERE p.submission_id=c.submission_id AND m.status='ready' AND m.deletion_epoch=c.deletion_epoch;
            RETURN jsonb_build_object('submission_id',c.submission_id,'token',next_token,'model',chosen_model,
                'prompts',chosen_prompts,'pages',pages,'subject',(SELECT subject FROM public.submissions WHERE id=c.submission_id));
        END LOOP;
        RETURN NULL;
    END;
    $$;

    CREATE OR REPLACE FUNCTION public.finish_homework_call(p_id uuid,p_token uuid,p_sequence integer,p_content text,p_response jsonb,p_error text)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE changed integer; used uuid;
    BEGIN
        IF NOT public.heartbeat_homework_grading(p_id,p_token) THEN RETURN false; END IF;
        IF p_error IS NULL AND (p_content IS NULL OR length(btrim(p_content))=0) THEN RETURN false; END IF;
        UPDATE public.homework_model_outputs SET status=CASE WHEN p_error IS NULL THEN 'succeeded' ELSE 'failed' END,
            content=coalesce(p_content,''),response=p_response,error_message=p_error,finished_at=clock_timestamp()
            WHERE submission_id=p_id AND sequence=p_sequence AND status='running'
            RETURNING model_id INTO used;
        GET DIAGNOSTICS changed=ROW_COUNT;
        IF changed=1 AND p_error IS NOT NULL
            AND (p_error LIKE '模型调用失败（HTTP %' OR p_error LIKE '模型连接中断或超时%')
            AND used=(SELECT m.id FROM public.model_configs m ORDER BY m.priority,m.created_at,m.id LIMIT 1) THEN
            UPDATE public.homework_grading_batches SET primary_call_failures=primary_call_failures+1
                WHERE submission_id=p_id;
        END IF;
        RETURN changed=1;
    END;
    $$;

    CREATE FUNCTION public.advance_homework_model(p_id uuid,p_token uuid,p_sequence integer) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE b public.homework_grading_batches%ROWTYPE; nxt jsonb; removed integer;
    BEGIN
        IF NOT public.heartbeat_homework_grading(p_id,p_token) OR p_sequence NOT BETWEEN 1 AND 2 THEN RETURN NULL; END IF;
        SELECT * INTO b FROM public.homework_grading_batches WHERE submission_id=p_id FOR UPDATE;
        IF b.state<>'running' OR b.token<>p_token OR b.primary_call_failures<3 THEN RETURN NULL; END IF;
        nxt:=public.homework_model_for_failures(b.primary_call_failures);
        IF nxt IS NULL OR nxt->>'id' IS NOT DISTINCT FROM b.model_config->>'id' THEN RETURN NULL; END IF;
        DELETE FROM public.homework_model_outputs WHERE submission_id=p_id AND sequence=p_sequence AND status='failed';
        GET DIAGNOSTICS removed=ROW_COUNT;
        IF removed<>1 THEN RETURN NULL; END IF;
        UPDATE public.homework_grading_batches SET model_config=nxt WHERE submission_id=p_id;
        RETURN nxt;
    END;
    $$;

    DROP FUNCTION public.start_homework_call(uuid,uuid,integer);
    CREATE FUNCTION public.start_homework_call(p_id uuid,p_token uuid,p_sequence integer) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE b public.homework_grading_batches%ROWTYPE; p jsonb; changed integer;
    BEGIN
        IF NOT public.heartbeat_homework_grading(p_id,p_token) OR p_sequence NOT BETWEEN 1 AND 2 THEN RETURN 0; END IF;
        SELECT * INTO b FROM public.homework_grading_batches WHERE submission_id=p_id FOR UPDATE;
        p:=b.prompts->(p_sequence-1);
        IF p IS NULL OR b.model_config='{}'::jsonb THEN RETURN 0; END IF;
        IF EXISTS(SELECT 1 FROM public.homework_model_outputs
            WHERE submission_id=p_id AND sequence=p_sequence AND status='succeeded') THEN RETURN 2; END IF;
        IF p_sequence=2 AND NOT EXISTS(SELECT 1 FROM public.homework_model_outputs
            WHERE submission_id=p_id AND sequence=1 AND status='succeeded') THEN RETURN 0; END IF;
        INSERT INTO public.homework_model_outputs(submission_id,tenant_id,student_id,sequence,
            prompt_id,prompt_name,prompt_content,prompt_priority,model_id,model_name,model_priority,status)
            SELECT p_id,s.tenant_id,s.student_id,p_sequence,(p->>'id')::uuid,p->>'name',p->>'content',
                (p->>'priority')::integer,(b.model_config->>'id')::uuid,b.model_config->>'name',
                (b.model_config->>'priority')::integer,'running' FROM public.submissions s WHERE s.id=p_id
            ON CONFLICT(submission_id,sequence) DO NOTHING;
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN CASE WHEN changed=1 THEN 1 ELSE 0 END;
    END;
    $$;

    CREATE FUNCTION public.request_homework_regrade(p_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE tenant uuid:=NULLIF(current_setting('app.tenant_id',true),'')::uuid;
        student_setting uuid:=NULLIF(current_setting('app.student_id',true),'')::uuid;
        user_setting uuid:=NULLIF(current_setting('app.user_id',true),'')::uuid;
        s public.submissions%ROWTYPE; j public.jobs%ROWTYPE; b public.homework_grading_batches%ROWTYPE;
        st public.students%ROWTYPE; q public.quota_reservations%ROWTYPE;
    BEGIN
        IF tenant IS NULL OR p_id IS NULL THEN RETURN jsonb_build_object('status',404); END IF;
        PERFORM 1 FROM public.tenants t WHERE t.id=tenant AND t.status='active' FOR SHARE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT * INTO s FROM public.submissions WHERE id=p_id AND tenant_id=tenant;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT * INTO st FROM public.students WHERE id=s.student_id AND tenant_id=tenant FOR SHARE;
        IF NOT FOUND OR st.status<>'active' OR NOT st.consent_active OR st.deletion_epoch<>s.deletion_epoch
            THEN RETURN jsonb_build_object('status',404); END IF;
        IF NOT ((user_setting IS NULL AND st.id=student_setting) OR (student_setting IS NULL AND EXISTS(
            SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id AND u.status='active'
            WHERE m.tenant_id=tenant AND m.user_id=user_setting AND m.status='active'
                AND (m.role='owner' OR EXISTS(SELECT 1 FROM public.student_access a
                    WHERE a.tenant_id=tenant AND a.student_id=st.id AND a.user_id=m.user_id)))))
            THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT * INTO j FROM public.jobs WHERE submission_id=p_id AND tenant_id=tenant FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        SELECT * INTO s FROM public.submissions WHERE id=p_id AND tenant_id=tenant FOR UPDATE;
        IF NOT FOUND OR s.deletion_epoch<>st.deletion_epoch OR j.deletion_epoch<>st.deletion_epoch
            THEN RETURN jsonb_build_object('status',404); END IF;
        IF j.status IN ('queued','running','retry_wait') OR s.status IN ('queued','processing','cancelled','completed')
            OR s.status NOT IN ('failed','partial') OR j.error_code IS NULL
            THEN RETURN jsonb_build_object('status',409,'code','job_state_conflict'); END IF;
        SELECT * INTO b FROM public.homework_grading_batches WHERE submission_id=p_id FOR UPDATE;
        IF NOT FOUND OR b.state NOT IN ('failed','settled') THEN
            RETURN jsonb_build_object('status',409,'code','job_state_conflict'); END IF;
        SELECT * INTO q FROM public.quota_reservations WHERE submission_id=p_id AND tenant_id=tenant FOR UPDATE;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',409,'code','job_state_conflict'); END IF;
        IF q.status='released' THEN
            UPDATE public.quota_accounts SET reserved_pages=reserved_pages+q.pages
                WHERE tenant_id=q.tenant_id AND period_start=q.period_start
                    AND reserved_pages+used_pages+q.pages<=page_limit;
            IF NOT FOUND THEN RETURN jsonb_build_object('status',409,'code','quota_exceeded'); END IF;
            UPDATE public.quota_reservations SET status='reserved',settled_at=NULL WHERE id=q.id AND status='released';
        ELSIF q.status<>'consumed' THEN
            RETURN jsonb_build_object('status',409,'code','job_state_conflict');
        END IF;
        DELETE FROM public.homework_model_outputs WHERE submission_id=p_id AND status='failed';
        UPDATE public.homework_grading_batches SET state='pending',finished_at=NULL WHERE submission_id=p_id;
        UPDATE public.jobs SET status='queued',error_code=NULL,finished_at=NULL WHERE id=j.id;
        UPDATE public.submissions SET status='queued' WHERE id=p_id;
        RETURN jsonb_build_object('status',202,'submission_id',p_id,'job_id',j.id);
    END;
    $$;
    """)
    op.execute('REVOKE ALL ON FUNCTION public.homework_model_for_failures(integer) FROM PUBLIC')
    op.execute('REVOKE ALL ON FUNCTION public.advance_homework_model(uuid,uuid,integer) FROM PUBLIC')
    op.execute('REVOKE ALL ON FUNCTION public.request_homework_regrade(uuid) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.start_homework_call(uuid,uuid,integer) TO {quote(worker)}')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.advance_homework_model(uuid,uuid,integer) TO {quote(worker)}')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.request_homework_regrade(uuid) TO {quote(app)}')


def downgrade():
    if op.get_bind().execute(text("SELECT EXISTS(SELECT 1 FROM public.homework_grading_batches WHERE state='pending')")).scalar_one():
        raise RuntimeError('Cannot discard a homework regrade that is still waiting')
    op.execute("""
    CREATE OR REPLACE FUNCTION public.claim_homework_grading() RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE e record; c record; b public.homework_grading_batches%ROWTYPE;
        model jsonb; prompts jsonb; pages jsonb; token uuid;
    BEGIN
        FOR b IN SELECT * FROM public.homework_grading_batches
            WHERE state='running' AND lease_until<clock_timestamp() ORDER BY lease_until LIMIT 10 LOOP
            PERFORM public.settle_homework_grading(b.submission_id,b.token,'处理服务中断，请重新提交作业。');
        END LOOP;
        FOR e IN SELECT x.id FROM public.outbox_events x JOIN public.jobs j ON j.id=x.aggregate_id
            WHERE x.event_type='submission.queued' AND j.status='queued'
            AND NOT EXISTS(SELECT 1 FROM public.homework_grading_batches hb WHERE hb.submission_id=j.submission_id)
            AND NOT EXISTS(SELECT 1 FROM public.execution_receipts r WHERE r.event_id=x.id)
            ORDER BY x.created_at,x.id LIMIT 20 LOOP
            SELECT * INTO c FROM public.lock_execution_context(e.id);
            IF NOT FOUND OR c.job_status<>'queued' OR c.submission_status<>'queued' THEN CONTINUE; END IF;
            IF EXISTS(SELECT 1 FROM public.homework_grading_batches WHERE submission_id=c.submission_id) THEN CONTINUE; END IF;
            SELECT to_jsonb(m) INTO model FROM public.model_configs m ORDER BY priority,created_at,id LIMIT 1;
            SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.priority,p.created_at,p.id),'[]'::jsonb)
                INTO prompts FROM (SELECT * FROM public.grading_prompts WHERE enabled ORDER BY priority,created_at,id LIMIT 2) p;
            token:=gen_random_uuid();
            INSERT INTO public.homework_grading_batches(submission_id,event_id,token,lease_until,state,model_config,prompts)
                VALUES(c.submission_id,e.id,token,clock_timestamp()+interval '90 seconds','running',coalesce(model,'{}'::jsonb),prompts);
            UPDATE public.jobs SET status='running' WHERE id=c.job_id;
            UPDATE public.submissions SET status='processing' WHERE id=c.submission_id;
            SELECT jsonb_agg(jsonb_build_object('position',p.position,'clean_key',m.clean_key) ORDER BY p.position)
                INTO pages FROM public.submission_pages p JOIN public.media_assets m ON m.id=p.media_id
                WHERE p.submission_id=c.submission_id AND m.status='ready' AND m.deletion_epoch=c.deletion_epoch;
            RETURN jsonb_build_object('submission_id',c.submission_id,'token',token,'model',model,
                'prompts',prompts,'pages',pages,'subject',(SELECT subject FROM public.submissions WHERE id=c.submission_id));
        END LOOP;
        RETURN NULL;
    END;
    $$;
    CREATE OR REPLACE FUNCTION public.finish_homework_call(p_id uuid,p_token uuid,p_sequence integer,p_content text,p_response jsonb,p_error text)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE changed integer;
    BEGIN
        IF NOT public.heartbeat_homework_grading(p_id,p_token) THEN RETURN false; END IF;
        IF p_error IS NULL AND (p_content IS NULL OR length(btrim(p_content))=0) THEN RETURN false; END IF;
        UPDATE public.homework_model_outputs SET status=CASE WHEN p_error IS NULL THEN 'succeeded' ELSE 'failed' END,
            content=coalesce(p_content,''),response=p_response,error_message=p_error,finished_at=clock_timestamp()
            WHERE submission_id=p_id AND sequence=p_sequence AND status='running';
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;
    DROP FUNCTION public.start_homework_call(uuid,uuid,integer);
    CREATE FUNCTION public.start_homework_call(p_id uuid,p_token uuid,p_sequence integer) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE b public.homework_grading_batches%ROWTYPE; p jsonb; changed integer;
    BEGIN
        IF NOT public.heartbeat_homework_grading(p_id,p_token) OR p_sequence NOT BETWEEN 1 AND 2 THEN RETURN false; END IF;
        SELECT * INTO b FROM public.homework_grading_batches WHERE submission_id=p_id FOR UPDATE;
        p:=b.prompts->(p_sequence-1);
        IF p IS NULL OR b.model_config='{}'::jsonb THEN RETURN false; END IF;
        IF p_sequence=2 AND NOT EXISTS(SELECT 1 FROM public.homework_model_outputs
            WHERE submission_id=p_id AND sequence=1 AND status='succeeded') THEN RETURN false; END IF;
        INSERT INTO public.homework_model_outputs(submission_id,tenant_id,student_id,sequence,
            prompt_id,prompt_name,prompt_content,prompt_priority,model_id,model_name,model_priority,status)
            SELECT p_id,s.tenant_id,s.student_id,p_sequence,(p->>'id')::uuid,p->>'name',p->>'content',
                (p->>'priority')::integer,(b.model_config->>'id')::uuid,b.model_config->>'name',
                (b.model_config->>'priority')::integer,'running' FROM public.submissions s WHERE s.id=p_id
            ON CONFLICT(submission_id,sequence) DO NOTHING;
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;
    DROP FUNCTION public.request_homework_regrade(uuid);
    DROP FUNCTION public.advance_homework_model(uuid,uuid,integer);
    DROP FUNCTION public.homework_model_for_failures(integer);
    ALTER TABLE public.homework_grading_batches DROP CONSTRAINT homework_grading_batches_state_check;
    ALTER TABLE public.homework_grading_batches DROP COLUMN primary_call_failures;
    ALTER TABLE public.homework_grading_batches ADD CONSTRAINT homework_grading_batches_state_check
        CHECK(state IN ('running','settled','failed'));
    """)
    worker = op.get_context().config.attributes.get('worker_role') or os.getenv('WIZZY_WORKER_ROLE', 'wizzy_app_worker')
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute('REVOKE ALL ON FUNCTION public.start_homework_call(uuid,uuid,integer) FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.start_homework_call(uuid,uuid,integer) TO {quote(worker)}')
