"""Revocable native device credentials and child-only WebAuthn public keys."""
import os

from alembic import op

revision = '0040_learning_device_login'
down_revision = '0039_homework_regrade'


def upgrade():
    op.execute('''
        ALTER TABLE auth_sessions ADD COLUMN source_session_id uuid REFERENCES auth_sessions(id);
        ALTER TABLE auth_sessions ADD COLUMN device_revoked_at timestamptz;
        CREATE INDEX sessions_source ON auth_sessions(source_session_id);
        CREATE TABLE learning_devices (
            session_id uuid PRIMARY KEY REFERENCES auth_sessions(id),
            token_hash text NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz
        );
        CREATE TABLE learning_passkeys (
            id uuid PRIMARY KEY,
            session_id uuid NOT NULL REFERENCES auth_sessions(id),
            credential_id text NOT NULL UNIQUE,
            public_key bytea NOT NULL,
            sign_count bigint NOT NULL DEFAULT 0 CHECK(sign_count>=0),
            name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_used_at timestamptz,
            revoked_at timestamptz
        );
        CREATE INDEX passkeys_session ON learning_passkeys(session_id);
        CREATE TABLE passkey_ceremonies (
            token_hash text PRIMARY KEY,
            challenge bytea NOT NULL,
            purpose text NOT NULL CHECK(purpose IN ('register','authenticate')),
            session_id uuid REFERENCES auth_sessions(id),
            expires_at timestamptz NOT NULL
        );
        CREATE INDEX ceremonies_expiry ON passkey_ceremonies(expires_at);
    ''')
    role = op.get_context().config.attributes.get('app_role') or os.getenv('WIZZY_APP_ROLE', 'wizzy_app')
    quoted = op.get_bind().dialect.identifier_preparer.quote(role)
    for table in ('learning_devices', 'learning_passkeys', 'passkey_ceremonies'):
        op.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON {table} TO {quoted}')


def downgrade():
    op.execute('DROP TABLE passkey_ceremonies,learning_passkeys,learning_devices')
    op.execute('ALTER TABLE auth_sessions DROP COLUMN source_session_id')
    op.execute('ALTER TABLE auth_sessions DROP COLUMN device_revoked_at')
