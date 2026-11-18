"""Local database initialization; administrative credentials never reach the API."""
import os
import secrets
import sys
import tempfile
from pathlib import Path

from alembic import command
from dotenv import dotenv_values, set_key
from psycopg import sql
from sqlalchemy import URL, create_engine, text

from wizzy.config import ROOT, migration_config


class BootstrapError(Exception):
    pass


def save_credentials(path: Path, credentials: dict[str, str]):
    original = path.read_text() if path.exists() else ""
    values = dotenv_values(path) if path.exists() else {}
    if all(values.get(key) == value for key, value in credentials.items()):
        path.chmod(0o600)
        return
    fd, temporary = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(original)
        for key, value in credentials.items():
            set_key(temporary, key, value)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restricted_login(connection, admin_url, role, password):
    existing = connection.execute(text("""SELECT oid,rolcanlogin,rolsuper,rolbypassrls,
        rolcreatedb,rolcreaterole,rolinherit,rolreplication FROM pg_roles WHERE rolname=:role"""),
        {"role": role}).mappings().one_or_none()
    if existing:
        unsafe = not existing["rolcanlogin"] or any(existing[key] for key in (
            "rolsuper", "rolbypassrls", "rolcreatedb", "rolcreaterole", "rolinherit", "rolreplication"))
        owns_or_inherits = connection.execute(text("""SELECT
            EXISTS(SELECT 1 FROM pg_class WHERE relowner=:oid) OR
            EXISTS(SELECT 1 FROM pg_namespace WHERE nspowner=:oid) OR
            EXISTS(SELECT 1 FROM pg_database WHERE datdba=:oid) OR
            EXISTS(SELECT 1 FROM pg_auth_members WHERE member=:oid)"""), {"oid": existing["oid"]}).scalar_one()
        if unsafe or owns_or_inherits:
            raise BootstrapError("Runtime role must be restricted and must not own database objects")
        if not password:
            raise BootstrapError("Cannot reuse existing role without its matching local secret; password was not reset")
        runtime = create_engine(admin_url.set(username=role, password=password), hide_parameters=True)
        try:
            with runtime.connect() as check:
                check.execute(text("SELECT 1"))
        finally:
            runtime.dispose()
    else:
        password = password or secrets.token_urlsafe(32)
        connection.connection.driver_connection.execute(sql.SQL(
            "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS "
            "NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION"
        ).format(sql.Identifier(role), sql.Literal(password)))
    return password


def initialize(admin_url: URL, env_path: Path, role: str, dispatcher_role: str | None = None,
               worker_role: str | None = None):
    dispatcher_role = dispatcher_role or role + "_dispatch"
    worker_role = worker_role or role + "_worker"
    roles = (role, dispatcher_role, worker_role)
    if len(set(roles)) != 3 or any(not name or len(name.encode()) > 63 for name in roles):
        raise BootstrapError("Valid separate restricted runtime roles are required")
    engine = create_engine(admin_url, hide_parameters=True)
    try:
        with engine.begin() as connection:
            # Lock the shared file as well as cluster-wide roles in a stable order.
            for key in sorted((str(env_path.resolve()), *roles)):
                connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"), {"key": key})
            values = dotenv_values(env_path) if env_path.exists() else {}
            credentials = {}
            for prefix, name in (("WIZZY_APP", role), ("WIZZY_DISPATCHER", dispatcher_role), ("WIZZY_WORKER", worker_role)):
                password = values.get(prefix + "_PASSWORD") if values.get(prefix + "_ROLE") == name else None
                credentials[prefix + "_ROLE"] = name
                credentials[prefix + "_PASSWORD"] = restricted_login(connection, admin_url, name, password)
            config = migration_config()
            config.attributes.update(connection=connection, app_role=role, dispatcher_role=dispatcher_role, worker_role=worker_role)
            command.upgrade(config, "head")
            for name in (dispatcher_role, worker_role):
                grants = connection.execute(text("""SELECT EXISTS(SELECT 1 FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
                    AND c.relkind IN ('r','p','v','m','f') AND (
                        has_any_column_privilege(:role,c.oid,'SELECT,INSERT,UPDATE,REFERENCES') OR
                        has_table_privilege(:role,c.oid,'DELETE,TRUNCATE,TRIGGER')))"""), {"role": name}).scalar_one()
                if grants:
                    raise BootstrapError("Restricted dispatcher/worker must not have business-table privileges")
            # One atomic file replacement for all roles. SQL rolls back on file failure.
            # Retain secrets on a later commit failure so initialization can be retried.
            save_credentials(env_path, credentials)
    finally:
        engine.dispose()


def main():
    try:
        path = ROOT / ".env"
        values = dotenv_values(path)
        url = os.getenv("WIZZY_MIGRATION_URL")
        if url:
            from sqlalchemy.engine import make_url

            url = make_url(url)
        else:
            password = values.get("POSTGRES_PASSWORD")
            if not password:
                raise BootstrapError("Local PostgreSQL administrative secret is missing")
            url = URL.create("postgresql+psycopg", username="wizzy", password=password,
                             host="127.0.0.1", port=5432, database="wizzy")
        initialize(url, path, os.getenv("WIZZY_APP_ROLE") or values.get("WIZZY_APP_ROLE") or "wizzy_app",
                   os.getenv("WIZZY_DISPATCHER_ROLE") or values.get("WIZZY_DISPATCHER_ROLE"),
                   os.getenv("WIZZY_WORKER_ROLE") or values.get("WIZZY_WORKER_ROLE"))
    except Exception:
        # Driver exceptions can contain credentials or SQL; never print their repr.
        print("Database initialization failed. Check local configuration, role permissions and database availability.", file=sys.stderr)
        return 1
    print("Database initialized; restricted runtime credentials saved locally (mode 600).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
