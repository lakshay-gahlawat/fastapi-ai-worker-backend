"""Redis persistence for structured AI job records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from redis.asyncio import Redis

from app.config import settings
from app.schema import JobRecord, JobStatusName, SummarizeResult

KEY_PREFIX = "ai_job:"


def job_key(job_id: str) -> str:
    return f"{KEY_PREFIX}{job_id}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def save_job(
    redis: Redis,
    *,
    job_id: str,
    status: JobStatusName,
    result: Optional[SummarizeResult | dict[str, Any]] = None,
    error: Optional[str] = None,
    execution_duration_seconds: Optional[float] = None,
    provider: Optional[str] = None,
) -> JobRecord:
    """Upsert a job record and return the serialized model."""
    parsed_result: Optional[SummarizeResult] = None
    if isinstance(result, SummarizeResult):
        parsed_result = result
    elif isinstance(result, dict):
        parsed_result = SummarizeResult.model_validate(result)

    record = JobRecord(
        job_id=job_id,
        status=status,
        result=parsed_result,
        error=error,
        timestamp=utcnow(),
        execution_duration_seconds=execution_duration_seconds,
        provider=provider,
    )
    await redis.set(job_key(job_id), record.model_dump_json(), ex=settings.JOB_TTL_SECONDS)
    return record


async def load_job(redis: Redis, job_id: str) -> Optional[JobRecord]:
    """Fetch a job record, or None if it has expired / never existed."""
    raw = await redis.get(job_key(job_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return JobRecord.model_validate_json(raw)
