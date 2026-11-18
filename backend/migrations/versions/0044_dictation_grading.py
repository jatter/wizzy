"""Persist dictation context and update untouched starter prompts without replacing custom prompts."""
from alembic import op
from sqlalchemy import text

revision = '0044_dictation_grading'
down_revision = '0043_homework_page_limit'

OLD_PROMPTS = (('6f0e3a10-1111-4111-8111-000000000001', '批改这几张图片的作业，按以下格式指出错误题，以及分析错误原因：\n第X张的第X题错误，分析错误原因\n\n最后汇总给出正确答案，格式：\n第X张的第X题错误，正确答案为'), ('6f0e3a10-1111-4111-8111-000000000002', '请批改这几张图片中的学生作业。按图片顺序和题号逐题判断，只依据图片里看得见的题目和学生写下的内容。\n\n每题一行，结论只能是：正确、错误、未作答、看不清。\n第X张第X题：正确\n第X张第X题：错误。错在：指出具体的一步或结果\n第X张第X题：未作答\n第X张第X题：看不清。不要猜测题目或答案\n\n不要编造图片中没有的题，也不要把旁边的印刷答案当成学生的作答。'), ('6f0e3a10-1111-4111-8111-000000000003', '请批改这几张图片里的作业，并给每道错题归类。错因只使用这些词，可以多选：计算失误、概念不清、审题遗漏、步骤不完整、单位或符号错误、看不清。\n\n输出格式：\n第X张第X题：错误。错因：……。依据：学生写下的关键步骤\n第X张第X题：正确\n第X张第X题：未作答\n\n最后用不超过三句话说明这几页最需要先订正的错因。看不清的题不要归进其他错因。'), ('6f0e3a10-1111-4111-8111-000000000004', '请批改这几张图片的作业。先指出错误题，再给可以继续订正的提示，提示里不要直接写出最终答案。\n\n格式：\n第X张第X题：错误。错在：……\n订正提示：指出需要复查的条件、公式或步骤，停在学生能够自己改对的那一步。\n\n核对结束后，另起一段列出未作答和看不清的题。看不清时写明需要重拍，不要补全题目。'))

COMMON = ('兼容普通作业和单词/短语听写。无题号词表按列和位置逐项检查，不能以没有题目为由跳过。'
          '无参考材料时仅检查拼写，不推断听写目标、漏写或顺序；有明确参考时逐项对照。'
          '字迹或对应关系不明确时标待核对，保留原作答。分析内容按系统JSON契约逐页逐项返回。')
NEW_PROMPTS = (
    '批改图片中的作业，分析错误原因并给出正确答案或参考拼写。' + COMMON,
    '逐题或逐项核对图片中可见的学生作答，分别判断正确、错误、未作答和待核对，并说明依据。' + COMMON,
    '分析图片中作业的错因；普通题目归类为计算、概念、审题、步骤或单位符号问题，听写归类为拼写或参考不符。' + COMMON,
    '指出错误并给出适合学生继续订正的提示；不要编造题目、目标词或不清楚的字。' + COMMON,
)
SUBJECT = "'subject',(SELECT subject FROM public.submissions WHERE id=c.submission_id)"
CONTEXT = (SUBJECT + ",'activity_type',(SELECT activity_type FROM public.submissions WHERE id=c.submission_id)"
           + ",'reference_text',(SELECT reference_text FROM public.submissions WHERE id=c.submission_id)")


def _claim(old, new):
    bind = op.get_bind()
    definition = bind.execute(text("SELECT pg_get_functiondef('public.claim_homework_grading()'::regprocedure)")).scalar_one()
    if definition.count(old) != 2:
        raise RuntimeError('dictation context markers missing from claim_homework_grading')
    op.execute(definition.replace(old, new))


def _prompts(forward):
    for (identifier, old), new in zip(OLD_PROMPTS, NEW_PROMPTS, strict=True):
        before, after = (old, new) if forward else (new, old)
        op.get_bind().execute(text("""UPDATE public.grading_prompts SET content=:after,updated_at=clock_timestamp()
            WHERE id=CAST(:id AS uuid) AND content=:before AND updated_by IS NULL"""),
            {'id': identifier, 'before': before, 'after': after})


def upgrade():
    op.execute(r"""
    ALTER TABLE public.submissions
        ADD COLUMN activity_type text NOT NULL DEFAULT 'auto' CHECK(activity_type IN ('auto','exercise','dictation')),
        ADD COLUMN reference_text text NOT NULL DEFAULT '' CHECK(length(reference_text)<=8000);
    """)
    _claim(SUBJECT, CONTEXT)
    _prompts(True)


def downgrade():
    if op.get_bind().execute(text("""SELECT EXISTS(SELECT 1 FROM public.submissions
        WHERE activity_type<>'auto' OR reference_text<>'')""")).scalar_one():
        raise RuntimeError('Cannot discard dictation context')
    _claim(CONTEXT, SUBJECT)
    _prompts(False)
    op.execute('ALTER TABLE public.submissions DROP COLUMN activity_type,DROP COLUMN reference_text')
