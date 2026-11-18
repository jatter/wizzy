"""Identity, consent and tenant/student isolation.

Revision ID: 0001_identity
"""
import os

from alembic import op
from sqlalchemy import text

revision = "0001_identity"
down_revision = None


def upgrade():
    op.execute("""
    CREATE TABLE users (
        id uuid PRIMARY KEY, email text NOT NULL UNIQUE,
        password_hash text NOT NULL, privacy_version text NOT NULL,
        status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE tenants (
        id uuid PRIMARY KEY, name text NOT NULL,
        status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','deleted')),
        deletion_epoch bigint NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE memberships (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        user_id uuid NOT NULL REFERENCES users(id),
        role text NOT NULL CHECK(role IN ('owner','guardian')),
        status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(tenant_id,user_id), UNIQUE(tenant_id,id)
    );
    CREATE UNIQUE INDEX one_owner ON memberships(tenant_id) WHERE role='owner' AND status='active';
    CREATE TABLE students (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id),
        nickname text NOT NULL, grade smallint NOT NULL CHECK(grade BETWEEN 1 AND 12),
        revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
        status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled','deleted')),
        consent_active boolean NOT NULL DEFAULT false,
        deletion_epoch bigint NOT NULL DEFAULT 0,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(tenant_id,id)
    );
    CREATE TABLE student_access (
        tenant_id uuid NOT NULL, student_id uuid NOT NULL, user_id uuid NOT NULL,
        PRIMARY KEY(tenant_id,student_id,user_id),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id),
        FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id)
    );
    CREATE TABLE consent_records (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        guardian_id uuid NOT NULL REFERENCES users(id),
        policy_version text NOT NULL, child_rules_version text NOT NULL, granted boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id)
    );
    CREATE TABLE auth_sessions (
        id uuid PRIMARY KEY, token_hash text NOT NULL UNIQUE,
        user_id uuid REFERENCES users(id), tenant_id uuid REFERENCES tenants(id), student_id uuid,
        audience text NOT NULL CHECK(audience IN ('guardian','child')),
        device_name text, expires_at timestamptz NOT NULL, revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id),
        CHECK((audience='guardian' AND user_id IS NOT NULL AND student_id IS NULL)
            OR (audience='child' AND user_id IS NULL AND tenant_id IS NOT NULL AND student_id IS NOT NULL))
    );
    CREATE INDEX sessions_user ON auth_sessions(user_id,tenant_id);
    CREATE TABLE pairing_codes (
        id uuid PRIMARY KEY, code_hash text NOT NULL UNIQUE,
        tenant_id uuid NOT NULL, student_id uuid NOT NULL,
        guardian_id uuid NOT NULL REFERENCES users(id),
        expires_at timestamptz NOT NULL, consumed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(tenant_id,student_id) REFERENCES students(tenant_id,id)
    );
    CREATE TABLE guardian_invitations (
        id uuid PRIMARY KEY, code_hash text NOT NULL UNIQUE,
        tenant_id uuid NOT NULL REFERENCES tenants(id), email text NOT NULL,
        student_ids uuid[] NOT NULL, invited_by uuid NOT NULL REFERENCES users(id),
        expires_at timestamptz NOT NULL, consumed_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE TABLE rate_limits (
        bucket text PRIMARY KEY, attempts integer NOT NULL, window_start timestamptz NOT NULL
    );
    CREATE TABLE audit_events (
        id uuid PRIMARY KEY, tenant_id uuid NOT NULL REFERENCES tenants(id), actor_id uuid,
        action text NOT NULL, resource_id uuid, request_id text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    );
    """)
    for table in ("tenants", "memberships", "students", "student_access", "consent_records", "audit_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        column = "id" if table == "tenants" else "tenant_id"
        rule = f"{column} = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
        if table == "students":
            rule += " AND (NULLIF(current_setting('app.student_id', true), '') IS NULL OR id = NULLIF(current_setting('app.student_id', true), '')::uuid)"
        if table in ("student_access", "consent_records"):
            rule += " AND (NULLIF(current_setting('app.student_id', true), '') IS NULL OR student_id = NULLIF(current_setting('app.student_id', true), '')::uuid)"
        read_rule = rule
        if table == "memberships":
            read_rule = f"({rule}) OR user_id=NULLIF(current_setting('app.user_id', true), '')::uuid"
        op.execute(f"CREATE POLICY {table}_isolation ON {table} USING ({read_rule}) WITH CHECK ({rule})")
    bind = op.get_bind()
    role = op.get_context().config.attributes.get("app_role") or os.getenv("WIZZY_APP_ROLE", "wizzy_app")
    info = bind.execute(text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=:role"), {"role": role}).first()
    if not info or info.rolsuper or info.rolbypassrls:
        raise RuntimeError("Create a non-superuser/non-BYPASSRLS runtime role before migrating")
    quoted = bind.dialect.identifier_preparer.quote(role)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {quoted}")
    op.execute(f"GRANT SELECT ON alembic_version TO {quoted}")
    for table in ("users", "tenants", "memberships", "students", "student_access", "auth_sessions", "pairing_codes", "guardian_invitations", "rate_limits"):
        op.execute(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO {quoted}")
    # Immutable evidence: runtime cannot UPDATE/DELETE consent or audit rows.
    for table in ("consent_records", "audit_events"):
        op.execute(f"GRANT SELECT,INSERT ON {table} TO {quoted}")


def downgrade():
    for table in ("audit_events", "rate_limits", "guardian_invitations", "pairing_codes", "auth_sessions", "consent_records", "student_access", "students", "memberships", "tenants", "users"):
        op.execute(f"DROP TABLE {table}")
