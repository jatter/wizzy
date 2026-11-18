"""Three subjects and durable, configured two-prompt homework grading."""
import os

from alembic import op
from sqlalchemy import text

revision = '0037_homework_grading'
down_revision = '0036_grading_prompts'


def upgrade():
    config = op.get_context().config
    app = config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    worker = config.attributes.get('worker_role') or os.getenv('WIZZY_WORKER_ROLE', app + '_worker')
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute("""
    ALTER TABLE public.submissions DROP CONSTRAINT submissions_subject_check;
    ALTER TABLE public.submissions ADD CONSTRAINT submissions_subject_check
        CHECK(subject IN ('math','english','chinese'));

    CREATE TABLE public.homework_grading_batches (
        submission_id uuid PRIMARY KEY REFERENCES public.submissions(id),
        event_id uuid NOT NULL REFERENCES public.outbox_events(id),
        token uuid NOT NULL, lease_until timestamptz NOT NULL,
        state text NOT NULL CHECK(state IN ('running','settled','failed')),
        model_config jsonb NOT NULL, prompts jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz
    );
    ALTER TABLE public.homework_grading_batches ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.homework_grading_batches FORCE ROW LEVEL SECURITY;
    CREATE TABLE public.homework_model_outputs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        submission_id uuid NOT NULL REFERENCES public.homework_grading_batches(submission_id),
        tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        sequence integer NOT NULL CHECK(sequence BETWEEN 1 AND 2),
        prompt_id uuid NOT NULL, prompt_name text NOT NULL, prompt_content text NOT NULL,
        prompt_priority integer NOT NULL, model_id uuid NOT NULL, model_name text NOT NULL,
        model_priority integer NOT NULL,
        status text NOT NULL CHECK(status IN ('running','succeeded','failed')),
        content text NOT NULL DEFAULT '', response jsonb, error_message text,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
        UNIQUE(submission_id,sequence),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES public.submissions(tenant_id,student_id,id)
    );
    ALTER TABLE public.homework_model_outputs ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.homework_model_outputs FORCE ROW LEVEL SECURITY;
    CREATE POLICY homework_outputs_isolation ON public.homework_model_outputs USING (
        tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
        AND (NULLIF(current_setting('app.student_id',true),'') IS NULL
            OR student_id=NULLIF(current_setting('app.student_id',true),'')::uuid));
    REVOKE ALL ON public.homework_grading_batches,public.homework_model_outputs FROM PUBLIC;

    CREATE OR REPLACE FUNCTION public.claim_execution(event uuid,job uuid,version integer,epoch bigint,request text)
    RETURNS TABLE(event_id uuid,job_id uuid,tenant_id uuid,student_id uuid,submission_id uuid,
                  source_revision integer,deletion_epoch bigint,request_id text,lease_token uuid,attempt integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        PERFORM 1 FROM public.lock_execution_context(event);
        IF NOT FOUND OR EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=event)
            OR EXISTS(SELECT 1 FROM public.homework_grading_batches b WHERE b.event_id=event) THEN RETURN; END IF;
        RETURN QUERY SELECT * FROM public.claim_execution_unfenced(event,job,version,epoch,request);
    END;
    $$;

    CREATE FUNCTION public.settle_homework_grading(p_id uuid,p_token uuid,p_error text)
    RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE b public.homework_grading_batches%ROWTYPE; c record;
        q public.quota_reservations%ROWTYPE; successes integer; target text;
    BEGIN
        SELECT event_id INTO b.event_id FROM public.homework_grading_batches WHERE submission_id=p_id;
        IF NOT FOUND THEN RETURN false; END IF;
        SELECT * INTO c FROM public.lock_execution_context(b.event_id);
        IF NOT FOUND THEN RETURN false; END IF;
        SELECT * INTO b FROM public.homework_grading_batches WHERE submission_id=p_id FOR UPDATE;
        IF b.token IS DISTINCT FROM p_token OR b.state<>'running' THEN RETURN false; END IF;
        IF c.job_status<>'running' OR c.submission_status<>'processing' THEN RETURN false; END IF;
        IF p_error IS NULL AND EXISTS(SELECT 1 FROM public.homework_model_outputs
            WHERE submission_id=p_id AND status='running') THEN RETURN false; END IF;
        UPDATE public.homework_model_outputs SET status='failed',error_message=coalesce(p_error,'处理未完成'),
            finished_at=clock_timestamp() WHERE submission_id=p_id AND status='running';
        SELECT count(*) INTO successes FROM public.homework_model_outputs WHERE submission_id=p_id AND status='succeeded';
        target:=CASE WHEN successes=2 THEN 'completed' WHEN successes>0 THEN 'partial' ELSE 'failed' END;
        UPDATE public.homework_grading_batches SET state=CASE WHEN successes>0 THEN 'settled' ELSE 'failed' END,
            finished_at=clock_timestamp() WHERE submission_id=p_id;
        UPDATE public.submissions SET status=target WHERE id=p_id;
        UPDATE public.jobs SET status=CASE WHEN successes>0 THEN 'succeeded' ELSE 'failed' END,
            error_code=CASE WHEN target='completed' THEN NULL ELSE 'homework_grading_failed' END,
            finished_at=clock_timestamp() WHERE id=c.job_id;
        UPDATE public.quota_reservations SET status=CASE WHEN successes>0 THEN 'consumed' ELSE 'released' END,
            consumed_pages=CASE WHEN successes>0 THEN pages ELSE 0 END,settled_at=clock_timestamp()
            WHERE submission_id=p_id AND status='reserved' RETURNING * INTO q;
        IF FOUND THEN
            UPDATE public.quota_accounts SET reserved_pages=reserved_pages-q.pages,
                used_pages=used_pages+CASE WHEN successes>0 THEN q.pages ELSE 0 END
                WHERE tenant_id=q.tenant_id AND period_start=q.period_start;
        END IF;
        RETURN true;
    END;
    $$;

    CREATE FUNCTION public.claim_homework_grading() RETURNS jsonb
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

    CREATE FUNCTION public.heartbeat_homework_grading(p_id uuid,p_token uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE event uuid; c record; changed integer;
    BEGIN
        SELECT event_id INTO event FROM public.homework_grading_batches WHERE submission_id=p_id;
        SELECT * INTO c FROM public.lock_execution_context(event);
        IF NOT FOUND OR c.job_status<>'running' OR c.submission_status<>'processing' THEN RETURN false; END IF;
        UPDATE public.homework_grading_batches SET lease_until=clock_timestamp()+interval '90 seconds'
            WHERE submission_id=p_id AND token=p_token AND state='running' AND lease_until>clock_timestamp();
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;

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

    CREATE FUNCTION public.finish_homework_call(p_id uuid,p_token uuid,p_sequence integer,p_content text,p_response jsonb,p_error text)
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
    """)
    functions = (
        'claim_homework_grading()', 'heartbeat_homework_grading(uuid,uuid)',
        'start_homework_call(uuid,uuid,integer)',
        'finish_homework_call(uuid,uuid,integer,text,jsonb,text)',
        'settle_homework_grading(uuid,uuid,text)',
    )
    for function in functions:
        op.execute(f'REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{function} TO {quote(worker)}')
    op.execute(f'GRANT SELECT ON public.homework_model_outputs TO {quote(app)}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.homework_grading_batches)
        OR EXISTS(SELECT 1 FROM public.submissions WHERE subject<>'math')''')).scalar_one():
        raise RuntimeError('Cannot discard homework responses, call fences or subject history')
    op.execute('''
    CREATE OR REPLACE FUNCTION public.claim_execution(event uuid,job uuid,version integer,epoch bigint,request text)
    RETURNS TABLE(event_id uuid,job_id uuid,tenant_id uuid,student_id uuid,submission_id uuid,
                  source_revision integer,deletion_epoch bigint,request_id text,lease_token uuid,attempt integer)
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    BEGIN
        PERFORM 1 FROM public.lock_execution_context(event);
        IF NOT FOUND OR EXISTS(SELECT 1 FROM public.model_runs m WHERE m.event_id=event) THEN RETURN; END IF;
        RETURN QUERY SELECT * FROM public.claim_execution_unfenced(event,job,version,epoch,request);
    END;
    $$;
    DROP FUNCTION public.finish_homework_call(uuid,uuid,integer,text,jsonb,text);
    DROP FUNCTION public.start_homework_call(uuid,uuid,integer);
    DROP FUNCTION public.heartbeat_homework_grading(uuid,uuid);
    DROP FUNCTION public.claim_homework_grading();
    DROP FUNCTION public.settle_homework_grading(uuid,uuid,text);
    DROP TABLE public.homework_model_outputs;
    DROP TABLE public.homework_grading_batches;
    ALTER TABLE public.submissions DROP CONSTRAINT submissions_subject_check;
    ALTER TABLE public.submissions ADD CONSTRAINT submissions_subject_check CHECK(subject='math');
    ''')
