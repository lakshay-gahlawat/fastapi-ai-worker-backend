"""Async LLM client with retries for summarize-and-extract jobs."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.schema import SummarizeResult

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-20b"
# Retired on Groq for free/developer tiers on 2026-08-16.
_DEPRECATED_GROQ_MODELS = {
    "llama-3.1-8b-instant": GROQ_DEFAULT_MODEL,
    "llama-3.3-70b-versatile": "openai/gpt-oss-120b",
}
_OPENAI_HOSTED_PREFIXES = ("gpt-4", "gpt-3.5", "gpt-4o", "o1-", "o3-", "o4-")

SYSTEM_PROMPT = """You are an operations analyst.
Summarize the document and extract concrete action items.
Respond with a single JSON object matching this schema:
{
  "summary": "2-4 sentence executive summary",
  "key_topics": ["short topic labels"],
  "action_items": [
    {"description": "what to do", "owner": "name or null", "priority": "low|medium|high"}
  ]
}
Do not include markdown. Infer owners only when they are named in the source text.
"""


class AIProcessingError(Exception):
    """Raised when the model call fails after retries or returns invalid JSON."""


def _provider_name() -> str:
    return (os.getenv("AI_PROVIDER") or settings.AI_PROVIDER or "openai").lower().strip()


def _api_key() -> str | None:
    key = os.getenv("OPENAI_API_KEY") or settings.OPENAI_API_KEY
    return key or None


def _model_name() -> str:
    """Resolve chat model; Groq defaults to openai/gpt-oss-20b (Llama 3.1 Instant was retired)."""
    provider = _provider_name()
    model = (os.getenv("OPENAI_MODEL") or settings.OPENAI_MODEL or "").strip()
    if provider == "groq":
        if model in _DEPRECATED_GROQ_MODELS:
            replacement = _DEPRECATED_GROQ_MODELS[model]
            logger.warning("Groq model %s is retired; using %s", model, replacement)
            return replacement
        if not model or model.startswith(_OPENAI_HOSTED_PREFIXES):
            return GROQ_DEFAULT_MODEL
        return model
    return model or "gpt-4o-mini"


def _build_client() -> AsyncOpenAI:
    """Create an OpenAI-compatible client; Groq requires an explicit base_url."""
    provider = _provider_name()
    api_key = _api_key()
    if provider == "groq":
        logger.info("AI client: provider=groq base_url=%s model=%s", GROQ_BASE_URL, _model_name())
        return AsyncOpenAI(
            api_key=api_key,
            base_url=GROQ_BASE_URL,
            timeout=settings.AI_TIMEOUT_SECONDS,
        )
    logger.info("AI client: provider=openai model=%s", _model_name())
    return AsyncOpenAI(
        api_key=api_key,
        timeout=settings.AI_TIMEOUT_SECONDS,
    )


client = _build_client()


def _error_code(exc: BaseException) -> str | None:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str):
                return code
    return None


def _is_quota_error(exc: BaseException) -> bool:
    """Billing/quota 429s are not transient and must not be retried."""
    if _error_code(exc) == "insufficient_quota":
        return True
    return "insufficient_quota" in str(exc)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError, TimeoutError)):
        return True
    if isinstance(exc, RateLimitError):
        return not _is_quota_error(exc)
    return False


@retry(
    reraise=True,
    stop=stop_after_attempt(settings.AI_MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=16),
    retry=retry_if_exception(_is_retryable),
)
async def _chat_completion(text: str, title: str | None) -> str:
    """Call OpenAI-compatible chat completions in JSON mode (retried on transient errors)."""
    global client
    client = _build_client()
    heading = f"Title: {title}\n\n" if title else ""
    response = await client.chat.completions.create(
        model=_model_name(),
        response_format={"type": "json_object"},
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"{heading}Document:\n{text}"},
        ],
    )
    content = response.choices[0].message.content
    if not content:
        raise AIProcessingError("Model returned an empty response")
    return content


async def _mock_summarize(text: str, title: str | None, *, reason: str) -> SummarizeResult:
    """Deterministic offline result used when live OpenAI is unavailable."""
    await asyncio.sleep(1.5)
    words = text.split()
    preview = " ".join(words[:40])
    prefix = f"Regarding {title}: " if title else ""
    return SummarizeResult(
        title=title,
        summary=(
            f"{prefix}This document discusses the following: {preview}"
            f"{'…' if len(words) > 40 else ''} "
            f"(Mock provider: {reason})"
        ),
        key_topics=["operations", "follow-up", "summary", "mock"],
        action_items=[
            {
                "description": "Review the source document and confirm owners",
                "owner": None,
                "priority": "medium",
            },
            {
                "description": (
                    "Add billing/quota on the OpenAI account, then re-run with AI_PROVIDER=openai"
                ),
                "owner": None,
                "priority": "low",
            },
        ],
    )


async def summarize_and_extract(text: str, title: str | None = None) -> tuple[SummarizeResult, str]:
    """Produce a structured summary and the provider name that produced it."""
    provider = _provider_name()
    if provider == "mock" or not _api_key():
        logger.info("Using mock AI provider")
        result = await _mock_summarize(text, title, reason="no live API key configured")
        return result, "mock"

    live_provider = "groq" if provider == "groq" else "openai"
    try:
        raw = await _chat_completion(text, title)
        payload: dict[str, Any] = json.loads(raw)
        payload.setdefault("title", title)
        return SummarizeResult.model_validate(payload), live_provider
    except json.JSONDecodeError as exc:
        raise AIProcessingError(f"Model returned invalid JSON: {exc}") from exc
    except RateLimitError as exc:
        if _is_quota_error(exc):
            logger.warning("Provider quota exceeded; falling back to mock provider")
            result = await _mock_summarize(
                text,
                title,
                reason="insufficient_quota — add billing or use a funded key",
            )
            return result, "mock"
        raise AIProcessingError(f"AI provider unavailable after retries: {exc}") from exc
    except (APITimeoutError, APIConnectionError) as exc:
        raise AIProcessingError(f"AI provider unavailable after retries: {exc}") from exc
    except Exception as exc:
        raise AIProcessingError(f"Unexpected AI failure: {exc}") from exc
