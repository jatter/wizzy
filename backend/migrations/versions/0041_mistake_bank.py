"""Homework mistake bank, attempt history and printable review sheets."""
import os

from alembic import op
from sqlalchemy import text

revision = '0041_mistake_bank'
down_revision = '0040_learning_device_login'


def upgrade():
    config = op.get_context().config
    app = config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    worker = config.attributes.get('worker_role') or os.getenv('WIZZY_WORKER_ROLE', app + '_worker')
    quote = op.get_bind().dialect.identifier_preparer.quote
    op.execute('''
    CREATE TABLE public.mistake_items (
        id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL,
        student_id uuid NOT NULL,
        subject text NOT NULL CHECK(subject IN ('math','english','chinese','mixed','unknown')),
        stem_key text NOT NULL,
        stem text NOT NULL,
        reference_answer text NOT NULL DEFAULT '',
        dismissed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(tenant_id, student_id, subject, stem_key),
        FOREIGN KEY(tenant_id, student_id) REFERENCES public.students(tenant_id, id)
    );
    CREATE TABLE public.mistake_attempts (
        id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL,
        student_id uuid NOT NULL,
        item_id uuid NOT NULL REFERENCES public.mistake_items(id),
        submission_id uuid NOT NULL,
        output_id uuid NOT NULL,
        page_position integer NOT NULL CHECK(page_position BETWEEN 1 AND 9),
        question_number text NOT NULL,
        submitted_at timestamptz NOT NULL,
        student_answer text NOT NULL,
        verdict text NOT NULL CHECK(verdict IN ('correct','incorrect')),
        reasoning text NOT NULL DEFAULT '',
        FOREIGN KEY(tenant_id, student_id, submission_id) REFERENCES public.submissions(tenant_id, student_id, id)
    );
    CREATE INDEX mistake_attempts_item ON public.mistake_attempts(item_id, submitted_at, id);
    CREATE TABLE public.mistake_sync (
        submission_id uuid PRIMARY KEY REFERENCES public.submissions(id),
        output_id uuid NOT NULL,
        synced_at timestamptz NOT NULL DEFAULT clock_timestamp()
    );
    CREATE TABLE public.mistake_sheets (
        id uuid PRIMARY KEY,
        tenant_id uuid NOT NULL,
        student_id uuid NOT NULL,
        due_date date NOT NULL,
        item_ids uuid[] NOT NULL CHECK(cardinality(item_ids) BETWEEN 1 AND 40),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        FOREIGN KEY(tenant_id, student_id) REFERENCES public.students(tenant_id, id)
    );
    ''')
    for table in ('mistake_items', 'mistake_attempts', 'mistake_sync', 'mistake_sheets'):
        scope = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
        if table != 'mistake_sync':
            scope += " AND (NULLIF(current_setting('app.student_id',true),'') IS NULL OR student_id=NULLIF(current_setting('app.student_id',true),'')::uuid)"
        else:
            scope = '''EXISTS(SELECT 1 FROM public.submissions s WHERE s.id=submission_id
                AND s.tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
                AND (NULLIF(current_setting('app.student_id',true),'') IS NULL
                    OR s.student_id=NULLIF(current_setting('app.student_id',true),'')::uuid))'''
        op.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY {table}_isolation ON public.{table} USING ({scope})')
        op.execute(f'REVOKE ALL ON public.{table} FROM PUBLIC')
        op.execute(f'GRANT SELECT ON public.{table} TO {quote(app)}')
    op.execute('''
    CREATE FUNCTION public.latest_homework_output(p_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE output_row public.homework_model_outputs%ROWTYPE; pages integer; synced uuid;
    BEGIN
        SELECT * INTO output_row FROM public.homework_model_outputs
            WHERE submission_id=p_id AND status='succeeded' ORDER BY sequence DESC LIMIT 1;
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT output_id INTO synced FROM public.mistake_sync WHERE submission_id=p_id;
        IF synced IS NOT DISTINCT FROM output_row.id THEN RETURN NULL; END IF;
        SELECT count(*) INTO pages FROM public.submission_pages WHERE submission_id=p_id;
        RETURN jsonb_build_object('output_id', output_row.id, 'content', output_row.content,
            'page_count', pages, 'submitted_at', (SELECT created_at FROM public.submissions WHERE id=p_id));
    END;
    $$;

    CREATE FUNCTION public.apply_mistake_bank(p_id uuid, p_output uuid, p_questions jsonb) RETURNS integer
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sub public.submissions%ROWTYPE; q jsonb; item uuid; question_subject text; written integer := 0;
    BEGIN
        SELECT * INTO sub FROM public.submissions WHERE id=p_id;
        IF NOT FOUND THEN RETURN 0; END IF;
        IF NOT EXISTS(SELECT 1 FROM public.homework_model_outputs
            WHERE id=p_output AND submission_id=p_id AND status='succeeded') THEN RETURN 0; END IF;
        DELETE FROM public.mistake_attempts WHERE submission_id=p_id;
        FOR q IN SELECT value FROM jsonb_array_elements(coalesce(p_questions,'[]'::jsonb)) LOOP
            question_subject := q->>'subject';
            IF question_subject NOT IN ('math','english','chinese','mixed','unknown') THEN CONTINUE; END IF;
            IF (q->>'verdict') NOT IN ('correct','incorrect') THEN CONTINUE; END IF;
            IF coalesce(q->>'stem_key','')='' OR coalesce(q->>'number','')='' THEN CONTINUE; END IF;
            IF (q->>'position') !~ '^[1-9]$' THEN CONTINUE; END IF;
            INSERT INTO public.mistake_items(id,tenant_id,student_id,subject,stem_key,stem,reference_answer)
                VALUES(gen_random_uuid(), sub.tenant_id, sub.student_id, question_subject, left(q->>'stem_key', 400),
                    left(coalesce(q->>'stem', q->>'number'), 500), left(coalesce(q->>'answer',''), 2000))
                ON CONFLICT(tenant_id, student_id, subject, stem_key) DO UPDATE
                    SET reference_answer=CASE WHEN excluded.reference_answer<>'' THEN excluded.reference_answer
                        ELSE public.mistake_items.reference_answer END
                RETURNING id INTO item;
            INSERT INTO public.mistake_attempts(id,tenant_id,student_id,item_id,submission_id,output_id,
                page_position,question_number,submitted_at,student_answer,verdict,reasoning)
                VALUES(gen_random_uuid(), sub.tenant_id, sub.student_id, item, p_id, p_output,
                    (q->>'position')::integer, left(q->>'number', 100), sub.created_at,
                    left(coalesce(q->>'student_answer',''), 2000), q->>'verdict', left(coalesce(q->>'reasoning',''), 4000));
            written := written + 1;
        END LOOP;
        INSERT INTO public.mistake_sync(submission_id, output_id) VALUES(p_id, p_output)
            ON CONFLICT(submission_id) DO UPDATE SET output_id=excluded.output_id, synced_at=clock_timestamp();
        DELETE FROM public.mistake_items i WHERE i.student_id=sub.student_id
            AND NOT EXISTS(SELECT 1 FROM public.mistake_attempts a WHERE a.item_id=i.id)
            AND NOT EXISTS(SELECT 1 FROM public.mistake_sheets s WHERE i.id = ANY(s.item_ids));
        RETURN written;
    END;
    $$;

    CREATE FUNCTION public.pending_mistake_submissions(p_student uuid) RETURNS SETOF uuid
    LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT s.id FROM public.submissions s
        WHERE s.student_id=p_student
            AND s.tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid
            AND (NULLIF(current_setting('app.student_id', true), '') IS NULL
                OR s.student_id=NULLIF(current_setting('app.student_id', true), '')::uuid)
            AND EXISTS(SELECT 1 FROM public.homework_model_outputs o
                WHERE o.submission_id=s.id AND o.status='succeeded'
                    AND o.id IS DISTINCT FROM (SELECT m.output_id FROM public.mistake_sync m WHERE m.submission_id=s.id))
        ORDER BY s.created_at LIMIT 30
    $$;

    CREATE FUNCTION public.dismiss_mistake_item(p_id uuid) RETURNS boolean
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE changed integer;
    BEGIN
        UPDATE public.mistake_items SET dismissed_at=clock_timestamp()
            WHERE id=p_id AND dismissed_at IS NULL
                AND tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND (NULLIF(current_setting('app.student_id', true), '') IS NULL
                    OR student_id=NULLIF(current_setting('app.student_id', true), '')::uuid);
        GET DIAGNOSTICS changed=ROW_COUNT;
        RETURN changed=1;
    END;
    $$;

    CREATE FUNCTION public.create_mistake_sheet(p_student uuid, p_items uuid[], p_due date) RETURNS uuid
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE sheet uuid;
    BEGIN
        IF p_due IS NULL OR cardinality(p_items) NOT BETWEEN 1 AND 40 THEN RETURN NULL; END IF;
        IF p_student IS NULL OR NULLIF(current_setting('app.tenant_id', true), '') IS NULL THEN RETURN NULL; END IF;
        IF NULLIF(current_setting('app.student_id', true), '') IS NOT NULL
            AND NULLIF(current_setting('app.student_id', true), '')::uuid IS DISTINCT FROM p_student THEN RETURN NULL; END IF;
        IF (SELECT count(*) FROM public.mistake_items WHERE id = ANY(p_items) AND student_id=p_student
            AND tenant_id=NULLIF(current_setting('app.tenant_id', true), '')::uuid AND dismissed_at IS NULL)
            <> cardinality(p_items) THEN RETURN NULL; END IF;
        sheet := gen_random_uuid();
        INSERT INTO public.mistake_sheets(id, tenant_id, student_id, due_date, item_ids)
            VALUES(sheet, NULLIF(current_setting('app.tenant_id', true), '')::uuid, p_student, p_due, p_items);
        RETURN sheet;
    END;
    $$;
    ''')
    for function in (
        'latest_homework_output(uuid)', 'apply_mistake_bank(uuid,uuid,jsonb)', 'pending_mistake_submissions(uuid)',
    ):
        op.execute(f'REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{function} TO {quote(app)}, {quote(worker)}')
    for function in ('dismiss_mistake_item(uuid)', 'create_mistake_sheet(uuid,uuid[],date)'):
        op.execute(f'REVOKE ALL ON FUNCTION public.{function} FROM PUBLIC')
        op.execute(f'GRANT EXECUTE ON FUNCTION public.{function} TO {quote(app)}')


def downgrade():
    if op.get_bind().execute(text('''SELECT EXISTS(SELECT 1 FROM public.mistake_items)
        OR EXISTS(SELECT 1 FROM public.mistake_sheets)''')).scalar_one():
        raise RuntimeError('Cannot discard mistake bank history')
    op.execute('''
    DROP FUNCTION public.create_mistake_sheet(uuid,uuid[],date);
    DROP FUNCTION public.dismiss_mistake_item(uuid);
    DROP FUNCTION public.pending_mistake_submissions(uuid);
    DROP FUNCTION public.apply_mistake_bank(uuid,uuid,jsonb);
    DROP FUNCTION public.latest_homework_output(uuid);
    DROP TABLE public.mistake_sheets;
    DROP TABLE public.mistake_sync;
    DROP TABLE public.mistake_attempts;
    DROP TABLE public.mistake_items;
    ''')
