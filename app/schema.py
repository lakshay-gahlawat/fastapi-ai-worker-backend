from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

JobStatusName = Literal["queued", "in_progress", "completed", "failed"]


class SummarizeRequest(BaseModel):
    """Payload for submitting a summarize-and-extract job."""

    text: str = Field(
        ...,
        min_length=20,
        max_length=20_000,
        description="Source document or notes to summarize.",
    )
    title: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional document title for context.",
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_prompt(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("text") and data.get("prompt"):
            data = {**data, "text": data["prompt"]}
        return data

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 20:
            raise ValueError("text must contain at least 20 non-whitespace characters")
        return cleaned


class SummarizeAccepted(BaseModel):
    job_id: str
    status: Literal["queued"] = "queued"
    message: str
    status_url: str


class ActionItem(BaseModel):
    description: str
    owner: Optional[str] = None
    priority: Literal["low", "medium", "high"] = "medium"


class SummarizeResult(BaseModel):
    summary: str
    action_items: list[ActionItem] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    title: Optional[str] = None


class JobRecord(BaseModel):
    """Structured job record persisted in Redis after each state change."""

    job_id: str
    status: JobStatusName
    result: Optional[SummarizeResult] = None
    error: Optional[str] = None
    timestamp: datetime
    execution_duration_seconds: Optional[float] = None
    provider: Optional[str] = None


class JobStatusResponse(JobRecord):
    """Public job status payload returned by GET /api/v1/jobs/{job_id}."""


# Backward-compatible aliases
AITaskRequest = SummarizeRequest
AITaskResponse = SummarizeAccepted
