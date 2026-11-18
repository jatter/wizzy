"""Current, live-authorized mistake projections, not grading gold labels."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from wizzy.db import get_db, run
from wizzy.errors import data
from wizzy.media import consent_student
from wizzy.pagination import decode_cursor, page
from wizzy.security import Principal, current

router = APIRouter(prefix='/api/v1', tags=['mistakes'])


@router.get('/students/{student_id}/mistakes')
def mistakes(student_id: UUID, request: Request, limit: int = Query(20, ge=1, le=100),
             cursor: str | None = None, actor: Principal = Depends(current),
             db: Session = Depends(get_db, scope='function')):
    after_date, after_id = decode_cursor(cursor)
    consent_student(db, actor, student_id)
    view = run(db, 'SELECT public.read_student_mistakes(:id,:limit,:date,:after)',
               id=student_id, limit=limit, date=after_date, after=after_id).scalar_one()
    for item in view['items']:
        item['created_at'] = datetime.fromisoformat(item['created_at'])
    return data(request, page(view['items'], limit, id_key='question_id') |
                {'pending_updates': view['pending_updates']})
