"""Conservative internal result contract, not a calibrated grading decision.

Keep the authoritative SQL validator in sync. Migration 0007 created it, and
0043 raises the page cap to 10. The worker has no direct table access;
validating here alone is not an authorization boundary.
"""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

Text = Annotated[str, Field(max_length=8000, pattern=r"^[^\x00]*$")]
Version = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")]
Coordinate = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Box = Annotated[list[Coordinate], Field(min_length=4, max_length=4)]
Position = Annotated[int, Field(ge=1, le=10)]
Reason = Literal["UNCALIBRATED_GATE", "UNSUPPORTED_TYPE", "UNCLEAR_INPUT", "MISSING_CONTEXT", "CONFLICTING_EVIDENCE"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("bbox", mode="before", check_fields=False)
    @classmethod
    def no_boolean_number(cls, value, info):
        # Pydantic's strict float accepts ints, but bool must never be a coordinate.
        if info.field_name == "bbox" and isinstance(value, list):
            if any(type(item) not in (int, float) for item in value):
                raise ValueError("Invalid coordinates")
        return value

    @field_validator("bbox", check_fields=False)
    @classmethod
    def ordered_box(cls, value, info):
        if info.field_name == "bbox" and not (value[0] < value[2] and value[1] < value[3]):
            raise ValueError("Invalid coordinates")
        return value


class Evidence(Contract):
    kind: Literal["answer_region", "question_region"]
    page: Position
    bbox: Box


class Question(Contract):
    ordinal: Annotated[int, Field(ge=1, le=100)]
    question_type: Literal["integer_arithmetic", "numeric_fill", "comparison", "unsupported"]
    stem: Annotated[Text, Field(min_length=1)]
    student_answer_raw: Text
    bbox: Box
    ocr_confidence: Coordinate | None
    verdict: Literal["pending_review", "unsupported"]
    reason_codes: Annotated[list[Reason], Field(min_length=1, max_length=5)]
    needs_guardian_review: StrictBool
    evidence: Annotated[list[Evidence], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def conservative(self):
        if not self.needs_guardian_review or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("Review required")
        if self.verdict == "unsupported" and "UNSUPPORTED_TYPE" not in self.reason_codes:
            raise ValueError("Unsupported reason required")
        return self


class SuccessfulPage(Contract):
    position: Position
    status: Literal["succeeded"]
    model_run_id: Annotated[str, Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")]
    questions: Annotated[list[Question], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def source_evidence(self):
        if sorted(q.ordinal for q in self.questions) != list(range(1, len(self.questions) + 1)):
            raise ValueError("Invalid question ordinals")
        if any(e.page != self.position for q in self.questions for e in q.evidence):
            raise ValueError("Evidence must refer to its source page")
        return self


class FailedPage(Contract):
    position: Position
    status: Literal["failed"]
    error_code: Literal["invalid_model_output", "unsupported_input", "provider_failed"]


class ConservativeResult(Contract):
    schema_version: Literal["1.0-conservative"]
    prompt_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    rule_version: Version
    eval_set_version: Version
    image_pipeline_version: Version
    pages: Annotated[list[Annotated[SuccessfulPage | FailedPage, Field(discriminator="status")]], Field(min_length=1, max_length=10)]

    @model_validator(mode="after")
    def source_pages(self):
        if sorted(page.position for page in self.pages) != list(range(1, len(self.pages) + 1)):
            raise ValueError("Invalid page positions")
        return self
