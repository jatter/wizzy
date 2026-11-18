"""Strict import data contracts. Image content is data, never system instructions."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

Subject = Literal['chinese', 'math', 'english', 'other']
ItemType = Literal['written', 'recitation', 'reading', 'preview', 'preparation', 'other']
WorkState = Literal['pending', 'in_progress', 'submitted', 'completed', 'exempted']
ScopeValue = Literal['unknown', 'has_items', 'confirmed_none']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Locator(Contract):
    book_alias: str | None = Field(default=None, max_length=150)
    book_id: UUID | None = None
    lesson: str | None = Field(default=None, max_length=150)
    pages: str | list[str] | None = None
    units: str | list[str] | None = None
    question_range: str | None = Field(default=None, max_length=200)
    repetition_raw: str | None = Field(default=None, max_length=100)

    @field_validator('pages', 'units')
    @classmethod
    def bounded(cls, value):
        if isinstance(value, str) and len(value) > 200:
            raise ValueError('范围过长')
        if isinstance(value, list) and (len(value) > 50 or any(len(x) > 100 for x in value)):
            raise ValueError('范围过长')
        return value


class Operation(Contract):
    client_request_id: UUID


class Versioned(Operation):
    expected_revision: StrictInt = Field(ge=0)


class ItemCreate(Operation):
    subject: Subject
    item_type: ItemType = 'written'
    title: str = Field(min_length=1, max_length=1000)
    raw_text: str = Field(default='', max_length=4000)
    locator: Locator = Field(default_factory=Locator)
    due_at: datetime | None = None

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('请输入作业内容')
        return value.strip()

    @field_validator('due_at')
    @classmethod
    def timezone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('截止时间必须包含时区')
        return value


class ItemUpdate(Versioned):
    subject: Subject | None = None
    item_type: ItemType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    raw_text: str | None = Field(default=None, max_length=4000)
    locator: Locator | None = None
    due_at: datetime | None = None
    deleted: StrictBool = False
    exempted: StrictBool | None = None
    reason: str = Field(default='', max_length=500)

    @field_validator('title')
    @classmethod
    def title_present(cls, value):
        if value is not None and not value.strip():
            raise ValueError('请输入作业内容')
        return value.strip() if value is not None else value

    @field_validator('due_at')
    @classmethod
    def timezone(cls, value):
        return ItemCreate.timezone(value)


class Progress(Versioned):
    state: WorkState
    reason: str = Field(default='', max_length=500)


class Verify(Versioned):
    completed: StrictBool
    reason: str = Field(default='', max_length=500)


class Subjects(Contract):
    chinese: ScopeValue = 'unknown'
    math: ScopeValue = 'unknown'
    english: ScopeValue = 'unknown'
    other: ScopeValue = 'unknown'


class Scope(Versioned):
    scope_state: Literal['incomplete', 'complete']
    subjects: Subjects


class ImageInput(Contract):
    media_id: UUID
    crop_rect: list[float] | None = None

    @field_validator('crop_rect')
    @classmethod
    def rect(cls, value):
        if value is not None and (len(value) != 4 or any(not 0 <= x <= 1 for x in value)
                                  or value[2] <= 0 or value[3] <= 0
                                  or value[0] + value[2] > 1.000001 or value[1] + value[3] > 1.000001):
            raise ValueError('选区必须是图片内的归一化x/y/宽/高')
        return value


class ImportCreate(Operation):
    student_id: UUID
    source_kind: Literal['manual_image'] = 'manual_image'
    source_label: str = Field(default='群作业截图', max_length=150)
    target_date: date | None = None
    images: list[ImageInput] = Field(min_length=1, max_length=10)


class Candidate(Contract):
    id: str = Field(min_length=1, max_length=100)
    image_id: UUID
    subject: Subject
    item_type: ItemType = 'written'
    title: str = Field(min_length=1, max_length=1000)
    raw_text: str = Field(default='', max_length=4000)
    locator: Locator = Field(default_factory=Locator)
    due_at: datetime | None = None
    assigned_date: date | None = None
    date_raw: str = Field(default='', max_length=150)
    date_candidates: list[str] = Field(default_factory=list, max_length=10)
    needs_review: list[str] = Field(default_factory=list, max_length=20)
    source_mark: str | None = Field(default=None, max_length=150)
    bbox: list[float] | None = None
    decision: Literal['include', 'exclude', 'merge', 'pending'] = 'pending'
    merge_item_id: UUID | None = None
    date_confirmed: StrictBool = False
    conflict_confirmed: StrictBool = False

    @field_validator('title')
    @classmethod
    def title_present(cls, value):
        return ItemCreate.nonblank(value)

    @field_validator('bbox')
    @classmethod
    def rect(cls, value):
        return ImageInput.rect(value)

    @field_validator('due_at')
    @classmethod
    def timezone(cls, value):
        return ItemCreate.timezone(value)


class NoHomework(Contract):
    id: str = Field(min_length=1, max_length=100)
    image_id: UUID
    subject: Subject
    assigned_date: date | None = None
    date_raw: str = Field(default='', max_length=150)
    confirmed: StrictBool = False
    excluded: StrictBool = False


class Draft(Contract):
    items: list[Candidate] = Field(default_factory=list, max_length=300)
    no_homework: list[NoHomework] = Field(default_factory=list, max_length=40)
    notes: list[str] = Field(default_factory=list, max_length=30)


class DraftSave(Versioned):
    draft: Draft


class Apply(Versioned):
    day_revisions: dict[date, StrictInt] = Field(default_factory=dict, max_length=31)


class Retry(Versioned):
    confirm_unknown_cost: StrictBool = False
    image_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=10)


class SourceEnvelope(Contract):
    """Future connector boundary; no external message ingestion is exposed yet."""
    source_kind: Literal['manual_image', 'manual_text', 'wechat_message']
    connector_id: str | None = None
    source_message_id: str | None = None
    conversation_ref: str | None = None
    sent_at: datetime | None = None
    received_at: datetime | None = None
    text: str = Field(default='', max_length=20000)
    media_refs: list[UUID] = Field(default_factory=list, max_length=10)
    source_revision: int = 1
    relation: Literal['new', 'correction', 'withdrawal'] = 'new'


class MatchCandidate(Contract):
    """Reserved evidence contract; matching is not enabled by these data types."""
    item_id: UUID
    item_revision: int
    submission_id: UUID
    media_ids: list[UUID]
    status: Literal['no_evidence', 'possible_match', 'partial_coverage', 'coverage_pending_confirmation', 'unclear']
    covered_ranges: list[str]
    missing_ranges: list[str]
    algorithm_version: str
    explanation: str


class CompletionProposal(Contract):
    item_id: UUID
    item_revision: int
    evidence: list[MatchCandidate]
    requires_guardian_confirmation: Literal[True] = True
    algorithm_version: str
