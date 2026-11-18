from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session


def get_db(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as db, db.begin():
        yield db


def one(db: Session, query: str, **params):
    return db.execute(text(query), params).mappings().first()


def many(db: Session, query: str, **params):
    return [dict(row) for row in db.execute(text(query), params).mappings()]


def run(db: Session, query: str, **params):
    return db.execute(text(query), params)


def set_context(db: Session, tenant_id=None, student_id=None, user_id=None):
    for key, value in (("tenant_id", tenant_id), ("student_id", student_id), ("user_id", user_id)):
        db.execute(text("SELECT set_config(:key, :value, true)"),
                   {"key": f"app.{key}", "value": str(value) if value else ""})
