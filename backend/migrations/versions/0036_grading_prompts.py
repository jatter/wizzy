"""Grading prompts: name, body, call priority and enabled flag, with starter rows."""
import os
import re

from alembic import op
from sqlalchemy import text

revision = '0036_grading_prompts'
down_revision = '0035_model_configs'
CAPABILITY = 'public.grading_prompt_request(text,text,text,jsonb,uuid)'
VIEW = 'public.grading_prompt_view(public.grading_prompts)'
ACTIONS = ('grading_prompts.create', 'grading_prompts.update', 'grading_prompts.delete')

ANSWER_PROMPT = (
    '批改这几张图片的作业，按以下格式指出错误题，以及分析错误原因：\n'
    '第X张的第X题错误，分析错误原因\n'
    '\n'
    '最后汇总给出正确答案，格式：\n'
    '第X张的第X题错误，正确答案为'
)
ITEM_PROMPT = (
    '请批改这几张图片中的学生作业。按图片顺序和题号逐题判断，只依据图片里看得见的题目和学生写下的内容。\n'
    '\n'
    '每题一行，结论只能是：正确、错误、未作答、看不清。\n'
    '第X张第X题：正确\n'
    '第X张第X题：错误。错在：指出具体的一步或结果\n'
    '第X张第X题：未作答\n'
    '第X张第X题：看不清。不要猜测题目或答案\n'
    '\n'
    '不要编造图片中没有的题，也不要把旁边的印刷答案当成学生的作答。'
)
CAUSE_PROMPT = (
    '请批改这几张图片里的作业，并给每道错题归类。错因只使用这些词，可以多选：'
    '计算失误、概念不清、审题遗漏、步骤不完整、单位或符号错误、看不清。\n'
    '\n'
    '输出格式：\n'
    '第X张第X题：错误。错因：……。依据：学生写下的关键步骤\n'
    '第X张第X题：正确\n'
    '第X张第X题：未作答\n'
    '\n'
    '最后用不超过三句话说明这几页最需要先订正的错因。看不清的题不要归进其他错因。'
)
HINT_PROMPT = (
    '请批改这几张图片的作业。先指出错误题，再给可以继续订正的提示，提示里不要直接写出最终答案。\n'
    '\n'
    '格式：\n'
    '第X张第X题：错误。错在：……\n'
    '订正提示：指出需要复查的条件、公式或步骤，停在学生能够自己改对的那一步。\n'
    '\n'
    '核对结束后，另起一段列出未作答和看不清的题。看不清时写明需要重拍，不要补全题目。'
)
SEEDS = (
    {'id': '6f0e3a10-1111-4111-8111-000000000001', 'name': '错题原因与正确答案',
     'content': ANSWER_PROMPT, 'priority': 10, 'enabled': True},
    {'id': '6f0e3a10-1111-4111-8111-000000000002', 'name': '逐题对错判定',
     'content': ITEM_PROMPT, 'priority': 20, 'enabled': True},
    {'id': '6f0e3a10-1111-4111-8111-000000000003', 'name': '错因归类',
     'content': CAUSE_PROMPT, 'priority': 30, 'enabled': True},
    {'id': '6f0e3a10-1111-4111-8111-000000000004', 'name': '订正提示',
     'content': HINT_PROMPT, 'priority': 40, 'enabled': True},
)


def upgrade():
    bind = op.get_bind()
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Grading prompt migration requires an offline RLS-bypassing owner')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole)
        FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none():
        raise RuntimeError('Grading prompt migration requires a restricted API role')
    quoted_role = bind.dialect.identifier_preparer.quote(role)
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    added = ','.join(f"'{action}'" for action in ACTIONS)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'''ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check
        CHECK (({previous.removeprefix('CHECK (').removesuffix(')')}) OR action IN ({added}))''')
    op.execute(r'''
    CREATE TABLE public.grading_prompts (
        id uuid PRIMARY KEY,
        name text NOT NULL CHECK(length(name) BETWEEN 1 AND 80 AND name=btrim(name) AND name !~ '[[:cntrl:]]'),
        content text NOT NULL CHECK(length(content) BETWEEN 1 AND 4000
            AND content=btrim(content, E' \n\t')
            AND regexp_replace(content, E'[\n\t]', '', 'g') !~ '[[:cntrl:]]'),
        priority integer NOT NULL CHECK(priority BETWEEN 1 AND 9999),
        enabled boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        updated_by uuid REFERENCES public.platform_accounts(id),
        UNIQUE(name)
    );
    CREATE INDEX grading_prompts_priority ON public.grading_prompts(priority, created_at, id);

    CREATE FUNCTION public.grading_prompt_view(m public.grading_prompts) RETURNS jsonb
    LANGUAGE sql SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object('id',m.id,'name',m.name,'content',m.content,'priority',m.priority,
            'enabled',m.enabled,'created_at',m.created_at,'updated_at',m.updated_at)
    $$;

    CREATE FUNCTION public.grading_prompt_request(p_hash text,p_op text,p_request text,p_payload jsonb,p_id uuid)
    RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE a public.platform_accounts%ROWTYPE; s public.platform_sessions%ROWTYPE; account uuid;
        m public.grading_prompts%ROWTYPE; needed text; value jsonb;
    BEGIN
        SELECT account_id INTO account FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO a FROM public.platform_accounts WHERE id=account FOR SHARE;
        IF NOT FOUND OR a.status<>'active' THEN RETURN jsonb_build_object('status',401); END IF;
        SELECT * INTO s FROM public.platform_sessions WHERE token_hash=p_hash;
        IF NOT FOUND OR s.revoked_at IS NOT NULL OR s.expires_at<=clock_timestamp() OR s.credential_version<>a.credential_version
            THEN RETURN jsonb_build_object('status',401); END IF;
        IF p_op IS NULL OR p_op NOT IN ('list','create','update','delete')
            OR p_payload IS NULL OR jsonb_typeof(p_payload)<>'object'
            OR (p_op IN ('update','delete') AND p_id IS NULL)
            OR p_request IS NULL OR length(p_request) NOT BETWEEN 1 AND 100
            THEN RETURN jsonb_build_object('status',422); END IF;
        needed := CASE WHEN p_op='list' THEN 'platform.models.read' ELSE 'platform.models.write' END;
        IF NOT EXISTS(SELECT 1 FROM public.platform_role_permissions WHERE role=a.role AND permission=needed) THEN
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'access.denied',p_request);
            RETURN jsonb_build_object('status',403);
        END IF;
        IF p_op='list' THEN
            SELECT coalesce(jsonb_agg(public.grading_prompt_view(c) ORDER BY c.priority,c.created_at,c.id),'[]'::jsonb)
                INTO value FROM public.grading_prompts c;
            RETURN jsonb_build_object('status',200,'value',value);
        END IF;
        IF p_op IN ('create','update') THEN
            IF p_payload - ARRAY['name','content','priority','enabled']<>'{}'::jsonb
                OR jsonb_typeof(p_payload->'name') IS DISTINCT FROM 'string'
                OR jsonb_typeof(p_payload->'content') IS DISTINCT FROM 'string'
                OR jsonb_typeof(p_payload->'priority') IS DISTINCT FROM 'number'
                OR jsonb_typeof(p_payload->'enabled') IS DISTINCT FROM 'boolean'
                OR p_payload->>'priority' !~ '^[0-9]{1,4}$'
                OR length(p_payload->>'name') NOT BETWEEN 1 AND 80
                OR btrim(p_payload->>'name') IS DISTINCT FROM p_payload->>'name'
                OR p_payload->>'name' ~ '[[:cntrl:]]'
                OR length(p_payload->>'content') NOT BETWEEN 1 AND 4000
                OR btrim(p_payload->>'content', E' \n\t') IS DISTINCT FROM p_payload->>'content'
                OR regexp_replace(p_payload->>'content', E'[\n\t]', '', 'g') ~ '[[:cntrl:]]'
                THEN RETURN jsonb_build_object('status',422); END IF;
            BEGIN
                IF p_op='create' THEN
                    INSERT INTO public.grading_prompts(id,name,content,priority,enabled,updated_by)
                        VALUES(gen_random_uuid(),p_payload->>'name',p_payload->>'content',
                            (p_payload->>'priority')::integer,(p_payload->>'enabled')::boolean,a.id)
                        RETURNING * INTO m;
                ELSE
                    UPDATE public.grading_prompts SET name=p_payload->>'name',content=p_payload->>'content',
                        priority=(p_payload->>'priority')::integer,enabled=(p_payload->>'enabled')::boolean,
                        updated_at=clock_timestamp(),updated_by=a.id
                        WHERE id=p_id RETURNING * INTO m;
                    IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
                END IF;
            EXCEPTION
                WHEN unique_violation THEN RETURN jsonb_build_object('status',409);
                WHEN check_violation OR numeric_value_out_of_range THEN RETURN jsonb_build_object('status',422);
            END;
            INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
                VALUES(gen_random_uuid(),a.id,'grading_prompts.'||p_op,p_request);
            RETURN jsonb_build_object('status',CASE WHEN p_op='create' THEN 201 ELSE 200 END,
                'value',public.grading_prompt_view(m));
        END IF;
        DELETE FROM public.grading_prompts WHERE id=p_id RETURNING * INTO m;
        IF NOT FOUND THEN RETURN jsonb_build_object('status',404); END IF;
        INSERT INTO public.platform_audit_events(id,actor_id,action,request_id)
            VALUES(gen_random_uuid(),a.id,'grading_prompts.delete',p_request);
        RETURN jsonb_build_object('status',200,'value',jsonb_build_object('id',m.id));
    END $$;
    ''')
    for seed in SEEDS:
        bind.execute(text('''INSERT INTO public.grading_prompts(id,name,content,priority,enabled)
            VALUES(CAST(:id AS uuid),:name,:content,:priority,:enabled)'''), seed)
    op.execute('ALTER TABLE public.grading_prompts ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE public.grading_prompts FORCE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON public.grading_prompts FROM PUBLIC')
    for function in (VIEW, CAPABILITY):
        op.execute(f'REVOKE ALL ON FUNCTION {function} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION {CAPABILITY} TO {quoted_role}')


def _unmodified_seeds(bind):
    rows = bind.execute(text('''SELECT id::text AS id, name, content, priority, enabled, updated_by
        FROM public.grading_prompts''')).mappings().all()
    actual = {row['id']: (row['name'], row['content'], row['priority'], row['enabled'], row['updated_by'] is None)
              for row in rows}
    expected = {seed['id']: (seed['name'], seed['content'], seed['priority'], seed['enabled'], True) for seed in SEEDS}
    return actual == expected


def downgrade():
    bind = op.get_bind()
    if not _unmodified_seeds(bind):
        raise RuntimeError('Cannot discard configured prompts')
    if bind.execute(text('SELECT EXISTS(SELECT 1 FROM public.platform_audit_events WHERE action = ANY(:actions))'),
                    {'actions': list(ACTIONS)}).scalar_one():
        raise RuntimeError('Cannot discard grading prompt audit history')
    op.execute(f'DROP FUNCTION {CAPABILITY}')
    op.execute(f'DROP FUNCTION {VIEW}')
    op.execute('DROP TABLE public.grading_prompts')
    previous = bind.execute(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='public.platform_audit_events'::regclass AND conname='platform_audit_events_action_check'")).scalar_one()
    actions = [a for a in re.findall(r"'([^']+)'::text", previous) if a not in ACTIONS]
    quoted = ','.join("'" + a.replace("'", "''") + "'" for a in actions)
    op.execute('ALTER TABLE public.platform_audit_events DROP CONSTRAINT platform_audit_events_action_check')
    op.execute(f'ALTER TABLE public.platform_audit_events ADD CONSTRAINT platform_audit_events_action_check CHECK(action IN ({quoted}))')
