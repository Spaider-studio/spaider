"""Exponential-backoff retry wrapper for LiteLLM async completions.

Why this exists
---------------
LiteLLM's ``acompletion`` is a thin shim over the underlying provider API
(OpenAI, Anthropic, etc.). Under burst load these providers return 429
"rate limit exceeded" responses; without a retry layer those failures
propagate to the caller as ``litellm.RateLimitError``, which kills any
SpAIder workflow that calls the LLM (synthesis, decomposition, extraction).

The 2026-05-07 gpt-4o measurement run hit OpenAI per-minute limits on
~40% of calls. Switching synthesis defaults to gpt-4o requires this
retry layer to be in place first.

Design
------
- One drop-in async function ``acompletion_with_retry`` with the same
  signature as ``litellm.acompletion``.
- Exponential backoff with jitter: ``base_delay * 2**attempt + random[0, base_delay)``.
- Defaults read from ``settings.litellm_retry_max_attempts`` and
  ``settings.litellm_retry_base_delay``; can be overridden per-call.
- Retries on transient errors only: ``RateLimitError``, ``APIConnectionError``,
  ``Timeout``, generic ``APIError`` with 5xx status. Other errors propagate
  immediately so bugs don't get hidden behind 25 seconds of pointless retry.
- Respects the ``Retry-After`` HTTP header when the provider exposes it
  via the exception, falling back to the computed exponential delay
  otherwise.
- Each retry logs at WARNING with attempt number, delay, and the error
  class so operators can correlate retry storms with provider incidents.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import random
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import litellm

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend token accounting
# ---------------------------------------------------------------------------
# Every backend LLM call (decomposition, synthesis, verification, ensemble,
# extraction) funnels through acompletion_with_retry. A context-local bucket
# lets a single request total its own backend token spend without threading a
# counter through ~6 call sites. We record COUNTS ONLY — never prompt/answer
# text — because the prompt embeds the user's private graph data.
#
# The ContextVar is async-task-local: contextvars propagate into tasks created
# with asyncio.create_task within the same context, so spend inside a query is
# attributed to that query's bucket and not bled across concurrent requests.

# A bucket is a 2-key dict: {"prompt_tokens": int, "completion_tokens": int}.
_token_bucket: contextvars.ContextVar[Optional[dict[str, int]]] = contextvars.ContextVar(
    "spaider_token_bucket", default=None
)


@contextmanager
def track_tokens() -> Iterator[dict[str, int]]:
    """Accumulate backend LLM token usage for the duration of the block.

    Usage::

        with track_tokens() as bucket:
            ... await query work that calls acompletion_with_retry ...
        # bucket == {"prompt_tokens": <in>, "completion_tokens": <out>}

    Nesting is additive: an inner block totals only the calls made while it is
    active, and on exit rolls its totals up into the enclosing bucket. So an
    outer block (e.g. a swarm query) sees the sum of every nested block (e.g.
    each per-agent query_nl) plus its own direct calls — counted once, at the
    leaf, then propagated up. Yields the bucket dict, mutated as calls complete.
    """
    bucket: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    parent = _token_bucket.get()
    token = _token_bucket.set(bucket)
    try:
        yield bucket
    finally:
        _token_bucket.reset(token)
        if parent is not None:
            parent["prompt_tokens"] += bucket["prompt_tokens"]
            parent["completion_tokens"] += bucket["completion_tokens"]


def _record_usage(response: Any) -> None:
    """Add a completion's token usage to the active bucket, if any.

    Best-effort and silent: token accounting must never break an LLM call.
    """
    bucket = _token_bucket.get()
    if bucket is None:
        return
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        # litellm Usage is attribute-accessible; dict-accessible as a fallback.
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if prompt is None and isinstance(usage, dict):
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
        bucket["prompt_tokens"] += int(prompt or 0)
        bucket["completion_tokens"] += int(completion or 0)
    except Exception:  # pragma: no cover — accounting must not raise
        pass


# Exception classes that warrant a retry. Any of these may be raised by
# the underlying provider under transient conditions and should be
# retried with backoff rather than propagated immediately.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    litellm.RateLimitError,
    litellm.APIConnectionError,
    litellm.Timeout,
    litellm.InternalServerError,
    litellm.ServiceUnavailableError,
)


def _extract_retry_after_seconds(exc: Exception) -> Optional[float]:
    """Return the provider-suggested retry delay in seconds, if any.

    OpenAI surfaces ``Retry-After`` on 429s; LiteLLM passes the raw
    exception attributes through. Best-effort extraction — returns
    ``None`` when nothing usable is available.
    """
    for attr in ("retry_after", "response_headers"):
        val = getattr(exc, attr, None)
        if val is None:
            continue
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, dict):
            header = val.get("retry-after") or val.get("Retry-After")
            if header is not None:
                try:
                    return float(header)
                except (TypeError, ValueError):
                    continue
    return None


async def acompletion_with_retry(
    *,
    max_attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    **call_kwargs: Any,
) -> Any:
    """Call ``litellm.acompletion`` with exponential-backoff retry on transient errors.

    Parameters
    ----------
    max_attempts:
        Maximum number of attempts (including the first). Defaults to
        ``settings.litellm_retry_max_attempts``. Set to 1 to disable retry.
    base_delay:
        Initial backoff delay in seconds. Doubles each retry plus
        ±50% jitter. Defaults to ``settings.litellm_retry_base_delay``.
    **call_kwargs:
        Forwarded verbatim to ``litellm.acompletion``.

    Returns
    -------
    The ``litellm.acompletion`` response object on success.

    Raises
    ------
    The last exception raised by ``litellm.acompletion`` once
    ``max_attempts`` is exhausted, or any non-retryable exception
    immediately.
    """
    if max_attempts is None:
        max_attempts = settings.litellm_retry_max_attempts
    if base_delay is None:
        base_delay = settings.litellm_retry_base_delay

    # max_attempts must be at least 1 (the initial call).
    max_attempts = max(1, int(max_attempts))
    base_delay = max(0.0, float(base_delay))

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = await litellm.acompletion(**call_kwargs)
            _record_usage(response)
            return response
        except _RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            # No retry after the final attempt — re-raise below.
            if attempt + 1 >= max_attempts:
                break

            suggested = _extract_retry_after_seconds(exc)
            if suggested is not None:
                # Honour provider hint; cap at a sane ceiling.
                delay = min(suggested, 60.0)
            else:
                # Exponential backoff with up to ±50% jitter.
                expo = base_delay * (2 ** attempt)
                jitter = random.uniform(-0.5 * base_delay, 0.5 * base_delay)
                delay = max(0.0, expo + jitter)

            logger.warning(
                "litellm.acompletion %s on attempt %d/%d — sleeping %.2fs before retry",
                type(exc).__name__,
                attempt + 1,
                max_attempts,
                delay,
            )
            await asyncio.sleep(delay)
    # All attempts exhausted; re-raise the last exception.
    assert last_exc is not None  # the loop only exits via exception or return
    logger.error(
        "litellm.acompletion gave up after %d attempts: %s",
        max_attempts,
        type(last_exc).__name__,
    )
    raise last_exc
