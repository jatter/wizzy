"""Raise one homework batch from 9 pages to 10."""
from alembic import op
from sqlalchemy import text

revision = '0043_homework_page_limit'
down_revision = '0042_daily_homework'

CHECKS = (
    ('public.submission_pages', 'submission_pages_position_check', 'position BETWEEN 1 AND {limit}'),
    ('public.quota_reservations', 'quota_reservations_pages_check', 'pages BETWEEN 1 AND {limit}'),
    ('public.grading_results', 'grading_results_successful_pages_check', 'successful_pages BETWEEN 0 AND {limit}'),
    ('public.grading_results', 'grading_results_failed_pages_check', 'failed_pages BETWEEN 0 AND {limit}'),
    ('public.grading_results', 'grading_results_check', 'successful_pages+failed_pages BETWEEN 1 AND {limit}'),
    ('public.questions', 'questions_page_position_check', 'page_position BETWEEN 1 AND {limit}'),
    ('public.mistake_attempts', 'mistake_attempts_page_position_check', 'page_position BETWEEN 1 AND {limit}'),
    ('public.homework_import_images', 'homework_import_images_position_check', 'position BETWEEN 1 AND {limit}'),
)


def _checks(limit):
    statements = []
    for table, name, rule in CHECKS:
        statements.append(f'ALTER TABLE {table} DROP CONSTRAINT {name}')
        statements.append(f'ALTER TABLE {table} ADD CONSTRAINT {name} CHECK({rule.format(limit=limit)})')
    op.execute(';\n'.join(statements))


def _once(src, marker):
    return f"(length({src}) - length(replace({src}, $m${marker}$m$, ''))) = length($m${marker}$m$)"


def _functions(old_total, new_total, old_pattern, new_pattern):
    page = old_pattern.format(field="p->>'position'")
    evidence = old_pattern.format(field="e->>'page'")
    mistake = old_pattern.format(field="q->>'position'")
    new_page = new_pattern.format(field="p->>'position'")
    new_evidence = new_pattern.format(field="e->>'page'")
    new_mistake = new_pattern.format(field="q->>'position'")
    op.execute(f'''
    DO $migrate$
    DECLARE src text;
    BEGIN
      SELECT pg_get_functiondef('public.valid_conservative_result(jsonb)'::regprocedure) INTO src;
      IF NOT ({_once('src', old_total)} AND {_once('src', page)} AND {_once('src', evidence)}) THEN
        RAISE EXCEPTION 'page-limit markers missing from valid_conservative_result';
      END IF;
      src := replace(src, $m${old_total}$m$, $m${new_total}$m$);
      src := replace(src, $m${page}$m$, $m${new_page}$m$);
      src := replace(src, $m${evidence}$m$, $m${new_evidence}$m$);
      EXECUTE src;

      SELECT pg_get_functiondef('public.apply_mistake_bank(uuid,uuid,jsonb)'::regprocedure) INTO src;
      IF NOT ({_once('src', mistake)}) THEN
        RAISE EXCEPTION 'page-limit marker missing from apply_mistake_bank';
      END IF;
      src := replace(src, $m${mistake}$m$, $m${new_mistake}$m$);
      EXECUTE src;
    END
    $migrate$;
    ''')


def upgrade():
    _functions(
        'IF total NOT BETWEEN 1 AND 9 THEN RETURN false; END IF;',
        'IF total NOT BETWEEN 1 AND 10 THEN RETURN false; END IF;',
        "({field}) !~ '^[1-9]$'",
        "({field}) !~ '^(10|[1-9])$'",
    )
    _checks(10)


def downgrade():
    if op.get_bind().execute(text('''
        SELECT EXISTS(SELECT 1 FROM public.submission_pages WHERE position > 9)
            OR EXISTS(SELECT 1 FROM public.quota_reservations WHERE pages > 9)
            OR EXISTS(SELECT 1 FROM public.grading_results WHERE successful_pages > 9 OR failed_pages > 9
                OR successful_pages + failed_pages > 9)
            OR EXISTS(SELECT 1 FROM public.questions WHERE page_position > 9)
            OR EXISTS(SELECT 1 FROM public.mistake_attempts WHERE page_position > 9)
            OR EXISTS(SELECT 1 FROM public.homework_import_images WHERE position > 9)
    ''')).scalar_one():
        raise RuntimeError('Cannot restore the 9-page limit while a larger batch exists')
    _functions(
        'IF total NOT BETWEEN 1 AND 10 THEN RETURN false; END IF;',
        'IF total NOT BETWEEN 1 AND 9 THEN RETURN false; END IF;',
        "({field}) !~ '^(10|[1-9])$'",
        "({field}) !~ '^[1-9]$'",
    )
    _checks(9)
