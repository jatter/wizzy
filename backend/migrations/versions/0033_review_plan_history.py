"""Read-only bounded history of shared review plans for authorized families."""
import os

from alembic import op
from sqlalchemy import text

revision = '0033_review_plan_history'
down_revision = '0032_platform_password_captcha'
CAPABILITY = 'read_review_plans(uuid,text,integer,timestamptz,uuid)'


def upgrade():
    bind = op.get_bind()
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    if not bind.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one():
        raise RuntimeError('Review plan history migration requires an offline RLS-bypassing owner')
    safe = bind.execute(text('''SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role'''), {'role': role}).scalar_one_or_none()
    if not safe:
        raise RuntimeError('A restricted API role is required')
    role = bind.dialect.identifier_preparer.quote(role)
    op.execute('''
    CREATE FUNCTION public.read_review_plans(target uuid,kind text,maximum integer,
        after_date timestamptz,after_id uuid) RETURNS jsonb
    LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
    DECLARE epoch bigint; entries jsonb;
    BEGIN
        epoch=public.lock_plan_student(target);
        IF epoch IS NULL THEN RETURN jsonb_build_object('error','not_found'); END IF;
        IF maximum IS NULL OR maximum NOT BETWEEN 1 AND 100 OR (after_date IS NULL)<>(after_id IS NULL)
            OR (kind IS NOT NULL AND kind NOT IN ('weekly','monthly')) THEN
            RETURN jsonb_build_object('error','invalid_plan'); END IF;
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
            'id',p.id,'student_id',p.student_id,'type',p.type,'period_start',p.period_start,
            'period_end',p.period_end,'timezone',p.timezone,'report_id',p.report_id,
            'created_at',p.created_at,'question_count',c.total,
            'estimated_minutes',c.total*p.minutes_per_question,'duration_basis','planning_estimate'
        ) ORDER BY p.created_at DESC,p.id DESC),'[]'::jsonb) INTO entries
        FROM (SELECT * FROM public.review_plans WHERE student_id=target AND deletion_epoch=epoch
            AND (kind IS NULL OR type=kind)
            AND (after_date IS NULL OR (created_at,id)<(after_date,after_id))
            ORDER BY created_at DESC,id DESC LIMIT maximum+1) p
        CROSS JOIN LATERAL (SELECT count(*) AS total FROM public.review_plan_entries WHERE plan_id=p.id) c;
        RETURN jsonb_build_object('items',entries);
    END $$;
    ''')
    op.execute(f'REVOKE ALL ON FUNCTION public.{CAPABILITY} FROM PUBLIC')
    op.execute(f'GRANT EXECUTE ON FUNCTION public.{CAPABILITY} TO {role}')


def downgrade():
    # Only the listing capability is removed; plans and their audit history survive.
    op.execute(f'DROP FUNCTION public.{CAPABILITY}')
