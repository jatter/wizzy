import re
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Register(Input):
    email: str = Field(min_length=3, max_length=254, validation_alias=AliasChoices('username', 'email'))
    password: str = Field(min_length=8, max_length=128)
    privacy_version: Literal["2026-10-25"]
    accepted_privacy: Literal[True]

    @field_validator("email")
    @classmethod
    def account_format(cls, value):
        value = value.strip().lower()
        if '@' in value:
            if any(char.isspace() for char in value):
                raise ValueError('账号格式无效')
        elif not re.fullmatch(r'[a-z][a-z0-9_.-]{2,39}', value):
            raise ValueError('用户名需为3–40位，以拼音字母开头，可包含数字、下划线、点或短横线')
        return value


class Login(Input):
    email: str = Field(min_length=3, max_length=254, validation_alias=AliasChoices('username', 'email'))
    password: str = Field(min_length=1, max_length=128)


class TenantCreate(Input):
    name: str = Field(min_length=1, max_length=80)


class ActivateTenant(Input):
    tenant_id: UUID


class StartLearning(Input):
    student_id: UUID


class StudentCreate(Input):
    nickname: str = Field(min_length=1, max_length=40)
    grade: int = Field(ge=1, le=12)


class StudentPatch(Input):
    expected_revision: int = Field(ge=1)
    nickname: str | None = Field(default=None, min_length=1, max_length=40)
    grade: int | None = Field(default=None, ge=1, le=12)


class Consent(Input):
    student_id: UUID
    policy_version: Literal["2026-10-25"]
    child_rules_version: Literal["2026-10-25"]
    granted: bool


class ChildSession(Input):
    code: str = Field(min_length=8, max_length=128)
    device_name: str = Field(min_length=1, max_length=80)


class Invitation(Input):
    email: str = Field(min_length=3, max_length=254)
    student_ids: list[UUID] = Field(min_length=1, max_length=20)


class AcceptInvitation(Input):
    code: str = Field(min_length=8, max_length=128)


class UploadIntent(Input):
    student_id: UUID
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    size_bytes: int = Field(ge=1)


class SubmissionCreate(Input):
    student_id: UUID
    subject: Literal["math", "english", "chinese", "auto"] = "auto"
    activity_type: Literal['auto', 'exercise', 'dictation'] = 'auto'
    reference_text: str = Field(default='', max_length=8000, pattern=r'^[^\x00]*$')
    completed_attempt: Literal[True]
    media_ids: list[UUID] = Field(min_length=1, max_length=10)
    client_request_id: UUID

    @field_validator("media_ids")
    @classmethod
    def unique_pages(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("同一次提交不能包含重复图片")
        return value


class OcrCorrection(Input):
    expected_revision: int = Field(ge=1, le=2147483647, strict=True)
    stem: str = Field(min_length=1, max_length=8000, pattern=r"^[^\x00]*$")
    student_answer_raw: str = Field(max_length=8000, pattern=r"^[^\x00]*$")
    reason: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]*$")

    @field_validator("stem", "reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("内容不能为空白")
        return value


class VerdictOverride(Input):
    expected_revision: int = Field(ge=1, le=2147483647, strict=True)
    expected_verdict_revision: int = Field(ge=0, le=2147483647, strict=True)
    verdict: Literal['correct', 'incorrect', 'pending_review', 'unanswered', 'unsupported']
    reason: str = Field(min_length=1, max_length=1000, pattern=r'^[^\x00]*$')

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('原因不能为空白')
        return value


class CorrectionAttempt(Input):
    expected_effective_revision: int = Field(ge=1, le=9223372036854775807, strict=True)
    student_answer: str | None = Field(default=None, min_length=1, max_length=8000, pattern=r'^[^\x00]*$')
    media_id: UUID | None = None

    @model_validator(mode='after')
    def content(self):
        if (self.student_answer is None) == (self.media_id is None):
            raise ValueError('订正文字与图片必须二选一')
        if self.student_answer is not None and not self.student_answer.strip():
            raise ValueError('订正答案不能为空白')
        return self


class CorrectionJudgment(Input):
    expected_judgment_revision: int = Field(ge=0, le=2147483646, strict=True)
    verdict: Literal['correct', 'incorrect', 'pending_review']
    reason: str = Field(min_length=1, max_length=1000, pattern=r'^[^\x00]*$')

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('核对原因不能为空白')
        return value


class ReviewEnrollment(Input):
    expected_effective_revision: int = Field(ge=1, le=9223372036854775807, strict=True)


class ReviewDraft(Input):
    expected_draft_revision: int = Field(ge=0, le=2147483646, strict=True)
    student_answer: str = Field(max_length=8000, pattern=r'^[^\x00]*$')


class ReviewAttempt(Input):
    expected_draft_revision: int = Field(ge=0, le=2147483646, strict=True)
    outcome: Literal['submitted', 'needs_help', 'skipped']


class ReviewReschedule(Input):
    expected_schedule_revision: int = Field(ge=0, le=2147483646, strict=True)
    due_date: date
    reason: str = Field(min_length=1, max_length=1000, pattern=r'^[^\x00]*$')

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('改期原因不能为空白')
        return value


class TermCreate(Input):
    name: str = Field(min_length=1, max_length=80, pattern=r'^[^\x00]*$')
    start_date: date
    end_date: date
    week_starts_on: int = Field(default=1, ge=1, le=7, strict=True)

    @field_validator('name')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('学期名称不能为空白')
        return value

    @model_validator(mode='after')
    def bounds(self):
        if not 0 <= (self.end_date - self.start_date).days < 370:
            raise ValueError('学期起止必须有序且不超过370天')
        return self


class LessonFields(Input):
    period: int = Field(ge=1, le=20, strict=True)
    subject: str = Field(min_length=1, max_length=80, pattern=r'^[^\x00]*$')
    start_time: str | None = Field(default=None, pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    end_time: str | None = Field(default=None, pattern=r'^(?:[01][0-9]|2[0-3]):[0-5][0-9]$')
    room: str | None = Field(default=None, max_length=100, pattern=r'^[^\x00]*$')

    @field_validator('subject')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('科目不能为空白')
        return value

    @model_validator(mode='after')
    def times(self):
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError('开始和结束时间必须成对填写')
        if self.start_time is not None and self.start_time >= self.end_time:
            raise ValueError('开始时间必须早于结束时间')
        return self


class TimetableSlot(LessonFields):
    id: UUID
    weekday: int = Field(ge=1, le=7, strict=True)
    weeks: Literal['all', 'odd', 'even'] = 'all'


class TimetablePut(Input):
    expected_revision: int = Field(ge=0, le=2147483645, strict=True)
    slots: list[TimetableSlot] = Field(max_length=100)
    confirm_conflicts: bool = Field(default=False, strict=True)

    @field_validator('slots')
    @classmethod
    def unique_slots(cls, value):
        if len({slot.id for slot in value}) != len(value):
            raise ValueError('课程ID不能重复')
        return value


class LessonOverride(Input):
    term_id: UUID
    expected_revision: int = Field(ge=0, le=2147483645, strict=True)
    slot_id: UUID
    source_date: date
    action: Literal['cancel', 'move', 'restore']
    reason: str = Field(min_length=1, max_length=1000, pattern=r'^[^\x00]*$')
    target_date: date | None = None
    replacement: LessonFields | None = None
    confirm_conflicts: bool = Field(default=False, strict=True)

    @field_validator('reason')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('调整原因不能为空白')
        return value

    @model_validator(mode='after')
    def replacement_required(self):
        if self.action == 'move':
            if self.target_date is None or self.replacement is None:
                raise ValueError('调课必须填写目标日期和完整课程')
        elif self.target_date is not None or self.replacement is not None:
            raise ValueError('停课和恢复不能填写替换课程')
        return self


class LearningReport(Input):
    student_id: UUID
    type: Literal['weekly', 'monthly']
    period_start: date

    @model_validator(mode='after')
    def aligned_period(self):
        if not 2000 <= self.period_start.year <= 9998:
            raise ValueError('报告日期范围为2000至9998年')
        if self.type == 'weekly' and self.period_start.weekday() != 0:
            raise ValueError('周报必须从周一开始')
        if self.type == 'monthly' and self.period_start.day != 1:
            raise ValueError('月报必须从每月第一天开始')
        return self


class ReviewPlan(LearningReport):
    max_questions: int = Field(default=20, ge=1, le=100, strict=True)
    max_minutes: int = Field(default=20, ge=1, le=120, strict=True)
    minutes_per_question: int = Field(default=2, ge=1, le=30, strict=True)
    report_id: UUID | None = None
