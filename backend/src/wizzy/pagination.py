import base64
import binascii
from datetime import datetime
from uuid import UUID

from wizzy.errors import DomainError


def decode_cursor(cursor: str | None):
    if cursor is None:
        return None, None
    try:
        if len(cursor) > 256:
            raise ValueError
        date, identifier = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        timestamp = datetime.fromisoformat(date)
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp, UUID(identifier)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise DomainError(422, "invalid_cursor", "分页游标无效") from exc


def page(rows, limit, *, id_key="id", timestamp_key="created_at"):
    visible = rows[:limit]
    cursor = None
    if len(rows) > limit:
        last = visible[-1]
        cursor = base64.urlsafe_b64encode(f"{last[timestamp_key].isoformat()}|{last[id_key]}".encode()).decode()
    return {"items": visible, "next_cursor": cursor}
