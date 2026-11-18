"""Atomic submissions, page quotas and a transactional task outbox."""
import os

from alembic import op

revision = "0003_submissions"
down_revision = "0002_media"


def upgrade():
    op.execute("""
    CREATE TABLE submissions (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        deletion_epoch bigint NOT NULL, source_revision integer NOT NULL DEFAULT 1 CHECK(source_revision=1),
        subject text NOT NULL CHECK(subject='math'),
        status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','processing','completed','partial','failed','cancelled')),
        idempotency_key uuid NOT NULL, body_hash text NOT NULL, client_request_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,student_id,id), UNIQUE(tenant_id,idempotency_key),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id)
    );
    CREATE INDEX submissions_student_created ON submissions(tenant_id,student_id,created_at,id);
    CREATE TABLE submission_pages (
        tenant_id uuid NOT NULL, student_id uuid NOT NULL, submission_id uuid NOT NULL,
        position integer NOT NULL CHECK(position BETWEEN 1 AND 9), media_id uuid NOT NULL,
        PRIMARY KEY(tenant_id,submission_id,position), UNIQUE(tenant_id,submission_id,media_id),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES submissions(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,student_id,media_id) REFERENCES media_assets(tenant_id,student_id,id)
    );
    CREATE TABLE jobs (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        submission_id uuid NOT NULL, deletion_epoch bigint NOT NULL,
        status text NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','retry_wait','failed','cancelled')),
        error_code text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
        UNIQUE(tenant_id,id), UNIQUE(tenant_id,submission_id),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES submissions(tenant_id,student_id,id)
    );
    CREATE TABLE quota_accounts (
        tenant_id uuid NOT NULL REFERENCES tenants(id), period_start date NOT NULL,
        page_limit integer NOT NULL CHECK(page_limit>0),
        reserved_pages integer NOT NULL DEFAULT 0 CHECK(reserved_pages>=0),
        used_pages integer NOT NULL DEFAULT 0 CHECK(used_pages>=0),
        CHECK(reserved_pages+used_pages<=page_limit), PRIMARY KEY(tenant_id,period_start)
    );
    CREATE TABLE quota_reservations (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        submission_id uuid NOT NULL, period_start date NOT NULL,
        pages integer NOT NULL CHECK(pages BETWEEN 1 AND 9),
        status text NOT NULL DEFAULT 'reserved' CHECK(status IN ('reserved','consumed','released')),
        created_at timestamptz NOT NULL DEFAULT now(), settled_at timestamptz,
        UNIQUE(tenant_id,submission_id),
        FOREIGN KEY(tenant_id,student_id,submission_id) REFERENCES submissions(tenant_id,student_id,id),
        FOREIGN KEY(tenant_id,period_start) REFERENCES quota_accounts(tenant_id,period_start)
    );
    CREATE TABLE outbox_events (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        aggregate_id uuid NOT NULL, event_type text NOT NULL CHECK(event_type='submission.queued'),
        payload jsonb NOT NULL, request_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
        UNIQUE(tenant_id,aggregate_id,event_type),
        FOREIGN KEY(tenant_id,aggregate_id) REFERENCES jobs(tenant_id,id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id)
    );
    CREATE INDEX outbox_pending ON outbox_events(created_at,id) WHERE published_at IS NULL;
    """)
    role = op.get_bind().dialect.identifier_preparer.quote(op.get_context().config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app"))
    for table in ("submissions", "submission_pages", "jobs", "quota_reservations", "outbox_events", "quota_accounts"):
        scope = "tenant_id=NULLIF(current_setting('app.tenant_id',true),'')::uuid"
        if table != "quota_accounts":
            scope += " AND (NULLIF(current_setting('app.student_id',true),'') IS NULL OR student_id=NULLIF(current_setting('app.student_id',true),'')::uuid)"
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY; ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING ({scope}) WITH CHECK ({scope})")
        op.execute(f"GRANT SELECT,INSERT ON {table} TO {role}")
    op.execute(f"GRANT UPDATE(status) ON submissions TO {role}")
    op.execute(f"GRANT UPDATE(status,error_code,finished_at) ON jobs TO {role}")
    op.execute(f"GRANT UPDATE(reserved_pages,used_pages) ON quota_accounts TO {role}")
    op.execute(f"GRANT UPDATE(status,settled_at) ON quota_reservations TO {role}")


def downgrade():
    for table in ("outbox_events", "quota_reservations", "quota_accounts", "jobs", "submission_pages", "submissions"):
        op.execute(f"DROP TABLE {table}")
