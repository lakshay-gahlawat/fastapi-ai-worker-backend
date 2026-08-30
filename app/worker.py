"""ARQ worker: dequeue summarize jobs, call the LLM, persist structured results."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from arq.connections import RedisSettings

from app.ai_client import AIProcessingError, summarize_and_extract
from app.config import settings
from app.job_store import save_job

logger = logging.getLogger(__name__)


async def summarize_text(ctx: dict[str, Any], text: str, title: Optional[str] = None) -> dict[str, Any]:
    """
    Worker entrypoint: summarize text and extract action items.

    Stores a structured JSON record in Redis covering status, payload,
    UTC timestamp, and wall-clock duration. Transient OpenAI failures are
    retried inside the AI client; remaining errors are recorded as `failed`.
    """
    redis = ctx["redis"]
    job_id = str(ctx["job_id"])
    started = time.perf_counter()
    provider = "openai" if settings.OPENAI_API_KEY and settings.AI_PROVIDER.lower() != "mock" else "mock"

    await save_job(
        redis,
        job_id=job_id,
        status="in_progress",
        provider=provider,
    )
    logger.info("Job %s in_progress", job_id)

    try:
        result, provider = await summarize_and_extract(text, title)
        duration = round(time.perf_counter() - started, 3)
        record = await save_job(
            redis,
            job_id=job_id,
            status="completed",
            result=result,
            execution_duration_seconds=duration,
            provider=provider,
        )
        logger.info("Job %s completed in %.3fs", job_id, duration)
        return record.model_dump(mode="json")
    except AIProcessingError as exc:
        duration = round(time.perf_counter() - started, 3)
        record = await save_job(
            redis,
            job_id=job_id,
            status="failed",
            error=str(exc),
            execution_duration_seconds=duration,
            provider=provider,
        )
        logger.exception("Job %s failed after %.3fs: %s", job_id, duration, exc)
        return record.model_dump(mode="json")
    except Exception as exc:
        duration = round(time.perf_counter() - started, 3)
        record = await save_job(
            redis,
            job_id=job_id,
            status="failed",
            error=f"Unexpected worker failure: {exc}",
            execution_duration_seconds=duration,
            provider=provider,
        )
        logger.exception("Job %s crashed after %.3fs", job_id, duration)
        return record.model_dump(mode="json")


async def process_ai_task(ctx: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Legacy worker function name; delegates to summarize_text."""
    return await summarize_text(ctx, prompt, None)


class WorkerSettings:
    """ARQ Redis worker configuration."""

    functions = [summarize_text, process_ai_task]
    redis_settings = RedisSettings(host=settings.REDIS_HOST, port=settings.REDIS_PORT)
    max_tries = 1
    job_timeout = 180
    keep_result = settings.JOB_TTL_SECONDS
