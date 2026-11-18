"""Allow automatic subject detection for mixed-image homework submissions."""
from alembic import op
from sqlalchemy import text

revision = '0038_auto_homework'
down_revision = '0037_homework_grading'


def upgrade():
    op.execute('ALTER TABLE public.submissions DROP CONSTRAINT submissions_subject_check')
    op.execute("ALTER TABLE public.submissions ADD CONSTRAINT submissions_subject_check CHECK(subject IN ('math','english','chinese','auto'))")


def downgrade():
    if op.get_bind().execute(text("SELECT EXISTS(SELECT 1 FROM public.submissions WHERE subject='auto')")).scalar_one():
        raise RuntimeError('Cannot discard automatically classified homework history')
    op.execute('ALTER TABLE public.submissions DROP CONSTRAINT submissions_subject_check')
    op.execute("ALTER TABLE public.submissions ADD CONSTRAINT submissions_subject_check CHECK(subject IN ('math','english','chinese'))")
