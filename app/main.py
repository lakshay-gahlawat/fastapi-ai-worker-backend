from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

from uuid import uuid4

from arq import create_pool
from arq.jobs import Job, JobStatus
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi_async_ratelimit.middleware import RateLimiter

from app.config import settings
from app.job_store import load_job, save_job
from app.schema import JobStatusName, JobStatusResponse, SummarizeAccepted, SummarizeRequest, SummarizeResult

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

ARQ_TO_PUBLIC: dict[JobStatus, JobStatusName] = {
    JobStatus.queued: "queued",
    JobStatus.deferred: "queued",
    JobStatus.in_progress: "in_progress",
    JobStatus.complete: "completed",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Hold an ARQ Redis pool for enqueue + job-record reads."""
    app.state.redis = await create_pool(settings.arq_redis_settings)
    yield
    await app.state.redis.close()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Async summarize-and-extract pipeline backed by Redis and ARQ.",
    lifespan=lifespan,
)

app.add_middleware(
    RateLimiter,
    redis_url=settings.redis_url,
    requests_limit=settings.RATE_LIMIT_REQUESTS,
    time_window=settings.RATE_LIMIT_WINDOW_SECONDS,
    methods=("POST",),
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for Docker and load balancers."""
    return {"status": "ok"}


@app.post("/api/v1/summarize", response_model=SummarizeAccepted, status_code=202)
async def submit_summarize_job(request: Request, body: SummarizeRequest) -> SummarizeAccepted:
    """
    Enqueue an AI summarize-and-extract job.

    Rate-limited (POST only). Returns immediately with a job id for polling.
    """
    redis_pool = request.app.state.redis
    job_id = uuid4().hex
    await save_job(redis_pool, job_id=job_id, status="queued", provider=settings.AI_PROVIDER)
    job = await redis_pool.enqueue_job(
        "summarize_text",
        body.text,
        body.title,
        _job_id=job_id,
    )
    if job is None:
        raise HTTPException(status_code=503, detail="Unable to enqueue job")
    return SummarizeAccepted(
        job_id=job.job_id,
        message="Summarize job queued.",
        status_url=f"/api/v1/jobs/{job.job_id}",
    )


@app.post("/api/v1/ai/generate", response_model=SummarizeAccepted, status_code=202)
async def create_ai_task(request: Request, body: SummarizeRequest) -> SummarizeAccepted:
    """Legacy enqueue path; same pipeline as `/api/v1/summarize`."""
    return await submit_summarize_job(request, body)


@app.get("/api/v1/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(request: Request, job_id: str) -> JobStatusResponse:
    """
    Return detailed job state: queued, in_progress, completed, or failed.

    Prefers the structured Redis record written by the worker; falls back to ARQ.
    """
    redis_pool = request.app.state.redis
    record = await load_job(redis_pool, job_id)
    if record is not None:
        return JobStatusResponse.model_validate(record.model_dump())

    job = Job(job_id, redis_pool)
    status = await job.status()
    if status == JobStatus.not_found:
        raise HTTPException(status_code=404, detail="Job not found")

    public_status: JobStatusName = ARQ_TO_PUBLIC.get(status, "queued")
    error: Optional[str] = None
    result: Optional[SummarizeResult] = None
    if status == JobStatus.complete:
        try:
            raw = await job.result()
            if isinstance(raw, dict) and raw.get("status") == "failed":
                public_status = "failed"
                error = raw.get("error")
            elif isinstance(raw, dict) and raw.get("result"):
                result = SummarizeResult.model_validate(raw["result"])
        except Exception as exc:
            public_status = "failed"
            error = str(exc)

    return JobStatusResponse(
        job_id=job_id,
        status=public_status,
        result=result,
        error=error,
        timestamp=datetime.now(timezone.utc),
        execution_duration_seconds=None,
        provider=None,
    )


@app.get("/api/v1/ai/tasks/{job_id}", response_model=JobStatusResponse)
async def get_task_status(request: Request, job_id: str) -> JobStatusResponse:
    """Legacy status path."""
    return await get_job_status(request, job_id)


@app.get("/")
async def dashboard() -> FileResponse:
    """Serve the single-page demo dashboard."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return FileResponse(index)


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
