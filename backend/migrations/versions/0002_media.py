"""Private, student-scoped upload intents and sanitized media."""
import os

from alembic import op

revision = "0002_media"
down_revision = "0001_identity"


def upgrade():
    op.execute("""
    CREATE TABLE media_assets (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL,
        status text NOT NULL DEFAULT 'intent' CHECK(status IN ('intent','uploaded','ready')),
        content_type text NOT NULL CHECK(content_type IN ('image/jpeg','image/png','image/webp')),
        size_bytes integer NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
        raw_key text NOT NULL, clean_key text,
        width integer, height integer, sha256 text,
        expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id),
        CHECK(status!='ready' OR (clean_key IS NOT NULL AND width>0 AND height>0 AND sha256 IS NOT NULL))
    );
    CREATE INDEX media_student_created ON media_assets(tenant_id,student_id,created_at,id);
    ALTER TABLE media_assets ENABLE ROW LEVEL SECURITY;
    ALTER TABLE media_assets FORCE ROW LEVEL SECURITY;
    CREATE POLICY media_isolation ON media_assets USING (
        tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
        AND (NULLIF(current_setting('app.student_id',true),'') IS NULL
             OR student_id=NULLIF(current_setting('app.student_id',true),'')::uuid)
    ) WITH CHECK (
        tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid
        AND (NULLIF(current_setting('app.student_id',true),'') IS NULL
             OR student_id=NULLIF(current_setting('app.student_id',true),'')::uuid)
    );
    """)
    role = op.get_bind().dialect.identifier_preparer.quote(op.get_context().config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app"))
    op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON media_assets TO {role}")


def downgrade():
    op.execute("DROP TABLE media_assets")
