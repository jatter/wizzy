"""Expose only the authorized, current conservative result projection."""
import os

from alembic import op
from sqlalchemy import text

revision = "0008_result_reader"
down_revision = "0007_atomic_results"


def upgrade():
    bind = op.get_bind()
    config = op.get_context().config
    app = config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    if not bind.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")).scalar_one():
        raise RuntimeError("Result reader requires an offline RLS-bypassing migration owner")
    safe = bind.execute(text("""SELECT rolcanlogin AND NOT(rolsuper OR rolbypassrls OR
        rolcreatedb OR rolcreaterole OR rolinherit OR rolreplication)
        FROM pg_roles WHERE rolname=:role"""), {"role": app}).scalar_one_or_none()
    if not safe:
        raise RuntimeError("A restricted API role is required")
    app = bind.dialect.identifier_preparer.quote(app)
    op.execute("""
    -- No runtime role gains direct access to grading_results. Trusted request
    -- context is set only after session authentication, never from request IDs.
    CREATE FUNCTION public.read_grading_result(target uuid) RETURNS jsonb
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
        SELECT jsonb_build_object(
            'submission_id',s.id,'student_id',s.student_id,'source_revision',s.source_revision,
            'status',s.status,'created_at',r.created_at,'schema_version',r.payload->'schema_version',
            'successful_pages',r.successful_pages,'failed_pages',r.failed_pages,
            'needs_guardian_review',true,
            'review_notice','当前结果仅供监护人核对识别内容，不能作为题目对错结论或模型批改正确率的依据。',
            'pages',(SELECT jsonb_agg(
                jsonb_build_object('position',sp.position,'media_id',sp.media_id,'status',p.value->'status')
                || CASE WHEN p.value->>'status'='failed'
                    THEN jsonb_build_object('error_code',p.value->'error_code')
                    ELSE jsonb_build_object('questions',(
                        SELECT jsonb_agg(jsonb_build_object(
                            'ordinal',q.value->'ordinal','question_type',q.value->'question_type',
                            'stem',q.value->'stem','student_answer_raw',q.value->'student_answer_raw',
                            'bbox',q.value->'bbox','ocr_confidence',q.value->'ocr_confidence',
                            'verdict',q.value->'verdict','reason_codes',q.value->'reason_codes',
                            'needs_guardian_review',true,'evidence',q.value->'evidence'
                        ) ORDER BY (q.value->>'ordinal')::integer)
                        FROM jsonb_array_elements(p.value->'questions') q
                    )) END ORDER BY sp.position)
                FROM jsonb_array_elements(r.payload->'pages') p
                JOIN public.submission_pages sp ON sp.tenant_id=r.tenant_id AND sp.student_id=r.student_id
                    AND sp.submission_id=r.submission_id AND sp.position=(p.value->>'position')::integer)
        )
        FROM public.grading_results r
        JOIN public.submissions s ON s.id=r.submission_id AND s.tenant_id=r.tenant_id
            AND s.student_id=r.student_id AND s.source_revision=r.source_revision
            AND s.deletion_epoch=r.deletion_epoch
        JOIN public.jobs j ON j.id=r.job_id AND j.tenant_id=r.tenant_id AND j.student_id=r.student_id
            AND j.submission_id=s.id AND j.deletion_epoch=r.deletion_epoch
        JOIN public.execution_receipts e ON e.event_id=r.event_id AND e.job_id=r.job_id
            AND e.tenant_id=r.tenant_id AND e.student_id=r.student_id AND e.submission_id=s.id
            AND e.source_revision=r.source_revision AND e.deletion_epoch=r.deletion_epoch AND e.state='settled'
        JOIN public.students st ON st.id=r.student_id AND st.tenant_id=r.tenant_id
            AND st.status='active' AND st.consent_active AND st.deletion_epoch=r.deletion_epoch
        JOIN public.tenants t ON t.id=r.tenant_id AND t.status='active'
        WHERE s.id=target AND t.id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
            AND ((s.status IN ('completed','partial') AND j.status='succeeded' AND r.successful_pages>0)
                OR (s.status='failed' AND j.status='failed' AND r.successful_pages=0))
            AND (
                (NULLIF(current_setting('app.user_id',true),'') IS NULL
                    AND st.id=NULLIF(current_setting('app.student_id',true),'')::uuid)
                OR (NULLIF(current_setting('app.student_id',true),'') IS NULL AND EXISTS(
                    SELECT 1 FROM public.memberships m JOIN public.users u ON u.id=m.user_id AND u.status='active'
                    WHERE m.tenant_id=t.id AND m.user_id=NULLIF(current_setting('app.user_id',true),'')::uuid
                        AND m.status='active' AND (m.role='owner' OR EXISTS(
                            SELECT 1 FROM public.student_access a WHERE a.tenant_id=t.id
                                AND a.student_id=st.id AND a.user_id=m.user_id))
                ))
            )
    $$;
    REVOKE ALL ON FUNCTION public.read_grading_result(uuid) FROM PUBLIC;
    """)
    op.execute(f"GRANT EXECUTE ON FUNCTION public.read_grading_result(uuid) TO {app}")


def downgrade():
    op.execute("DROP FUNCTION public.read_grading_result(uuid)")
