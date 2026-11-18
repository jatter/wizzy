import os
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import URL, create_engine

config = context.config


def migrate(connection):
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Online migrations required to verify the target runtime role")
connection = config.attributes.get("connection")
if connection is not None:
    migrate(connection)
else:
    values = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = os.getenv("WIZZY_MIGRATION_URL")
    if not url:
        password = values.get("POSTGRES_PASSWORD")
        if not password:
            raise RuntimeError("POSTGRES_PASSWORD or WIZZY_MIGRATION_URL required")
        url = URL.create("postgresql+psycopg", username="wizzy", password=password,
                         host="127.0.0.1", port=5432, database="wizzy")
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.connect() as connection:
            migrate(connection)
    finally:
        engine.dispose()
