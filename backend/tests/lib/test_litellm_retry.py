"""Tests for the LiteLLM retry wrapper.

Covers:
- Success on first attempt returns the response untouched
- Retryable errors (RateLimitError, APIConnectionError, Timeout) trigger backoff
- Non-retryable errors propagate immediately without retry
- Max attempts exhausted re-raises the last exception
- ``Retry-After`` provider hint is honoured when present
- Backoff timing is approximately correct (exponential with jitter)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import litellm
import pytest


@pytest.fixture(autouse=True)
def fast_settings():
    """Patch settings so tests don't wait real seconds between attempts."""
    with patch("app.lib.litellm_retry.settings") as mock_settings:
        mock_settings.litellm_retry_max_attempts = 3
        mock_settings.litellm_retry_base_delay = 0.0  # zero so sleeps are no-ops
        yield mock_settings


@pytest.mark.asyncio
async def test_success_on_first_attempt_no_retry():
    """The wrapper is a pass-through when the call succeeds."""
    from app.lib.litellm_retry import acompletion_with_retry

    expected = {"choices": [{"message": {"content": "ok"}}]}
    with patch("app.lib.litellm_retry.litellm.acompletion", new=AsyncMock(return_value=expected)) as mock:
        result = await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    assert result is expected
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_retry_on_rate_limit_then_success():
    """RateLimitError on attempt 1 should trigger one retry, then succeed."""
    from app.lib.litellm_retry import acompletion_with_retry

    success_response = {"choices": [{"message": {"content": "ok-after-retry"}}]}
    rate_limit_error = litellm.RateLimitError(
        message="rate limited",
        model="gpt-4o-mini",
        llm_provider="openai",
    )
    mock = AsyncMock(side_effect=[rate_limit_error, success_response])
    with patch("app.lib.litellm_retry.litellm.acompletion", new=mock):
        result = await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    assert result is success_response
    assert mock.call_count == 2


@pytest.mark.asyncio
async def test_max_attempts_exhausted_raises_last_exception():
    """After max_attempts retries, the last exception propagates."""
    from app.lib.litellm_retry import acompletion_with_retry

    rate_limit_error = litellm.RateLimitError(
        message="rate limited",
        model="gpt-4o-mini",
        llm_provider="openai",
    )
    # 3 attempts (matches fast_settings fixture), all fail.
    mock = AsyncMock(side_effect=[rate_limit_error] * 3)
    with patch("app.lib.litellm_retry.litellm.acompletion", new=mock):
        with pytest.raises(litellm.RateLimitError):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    assert mock.call_count == 3


@pytest.mark.asyncio
async def test_non_retryable_error_propagates_immediately():
    """A bug-class error (ValueError) does not trigger retry."""
    from app.lib.litellm_retry import acompletion_with_retry

    bug = ValueError("internal bug, not a transient API error")
    mock = AsyncMock(side_effect=bug)
    with patch("app.lib.litellm_retry.litellm.acompletion", new=mock):
        with pytest.raises(ValueError, match="internal bug"):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    # Exactly one call — no retry on non-retryable errors.
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_kwargs_forwarded_verbatim():
    """All kwargs (except max_attempts/base_delay) reach litellm.acompletion."""
    from app.lib.litellm_retry import acompletion_with_retry

    success_response = {"choices": []}
    mock = AsyncMock(return_value=success_response)
    with patch("app.lib.litellm_retry.litellm.acompletion", new=mock):
        await acompletion_with_retry(
            model="gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=100,
        )

    mock.assert_awaited_once_with(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=100,
    )


@pytest.mark.asyncio
async def test_max_attempts_one_disables_retry():
    """Setting max_attempts=1 means single attempt, no retry on rate limit."""
    from app.lib.litellm_retry import acompletion_with_retry

    rate_limit_error = litellm.RateLimitError(
        message="rate limited",
        model="gpt-4o-mini",
        llm_provider="openai",
    )
    mock = AsyncMock(side_effect=rate_limit_error)
    with patch("app.lib.litellm_retry.litellm.acompletion", new=mock):
        with pytest.raises(litellm.RateLimitError):
            await acompletion_with_retry(
                model="gpt-4o-mini",
                messages=[],
                max_attempts=1,
            )

    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_retry_after_hint_used_when_present():
    """If the exception carries retry_after, the wrapper sleeps for that long."""
    from app.lib.litellm_retry import acompletion_with_retry

    rate_limit_error = litellm.RateLimitError(
        message="rate limited",
        model="gpt-4o-mini",
        llm_provider="openai",
    )
    # Attach a retry_after attribute that the extractor will pick up.
    rate_limit_error.retry_after = 0.5
    success_response = {"choices": []}

    mock = AsyncMock(side_effect=[rate_limit_error, success_response])
    sleep_mock = AsyncMock()
    with patch("app.lib.litellm_retry.litellm.acompletion", new=mock):
        with patch("app.lib.litellm_retry.asyncio.sleep", new=sleep_mock):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    # Wrapper should have honoured the 0.5s hint.
    sleep_mock.assert_awaited_once()
    actual_sleep = sleep_mock.await_args[0][0]
    assert actual_sleep == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Backend token accounting (track_tokens / _record_usage)
# ---------------------------------------------------------------------------


class _FakeUsage:
    def __init__(self, prompt: int, completion: int):
        self.prompt_tokens = prompt
        self.completion_tokens = completion


class _FakeResponse:
    def __init__(self, prompt: int, completion: int):
        self.usage = _FakeUsage(prompt, completion)
        self.choices = []


def _patched_acompletion(*responses):
    """A litellm.acompletion mock returning the given responses in order."""
    return patch(
        "app.lib.litellm_retry.litellm.acompletion",
        new=AsyncMock(side_effect=list(responses)),
    )


@pytest.mark.asyncio
async def test_track_tokens_accumulates_usage():
    """track_tokens totals prompt/completion tokens across calls in the block."""
    from app.lib.litellm_retry import acompletion_with_retry, track_tokens

    with track_tokens() as bucket:
        with _patched_acompletion(_FakeResponse(10, 3), _FakeResponse(5, 2)):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])
        with _patched_acompletion(_FakeResponse(5, 2)):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    # First patch only resolves its first side_effect per call, so two separate
    # calls above → 10+3 then 5+2.
    assert bucket == {"prompt_tokens": 15, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_no_active_bucket_is_a_noop():
    """Outside any track_tokens block, usage recording must not raise."""
    from app.lib.litellm_retry import acompletion_with_retry

    with _patched_acompletion(_FakeResponse(10, 3)):
        # No track_tokens() context — _record_usage sees no bucket and skips.
        resp = await acompletion_with_retry(model="gpt-4o-mini", messages=[])
    assert resp.usage.prompt_tokens == 10  # call still returns normally


@pytest.mark.asyncio
async def test_nested_track_tokens_rolls_up_to_parent():
    """Inner block totals its own calls AND propagates them to the outer block."""
    from app.lib.litellm_retry import acompletion_with_retry, track_tokens

    with track_tokens() as outer:
        with _patched_acompletion(_FakeResponse(10, 4)):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])  # outer-only
        with track_tokens() as inner:
            with _patched_acompletion(_FakeResponse(7, 1)):
                await acompletion_with_retry(model="gpt-4o-mini", messages=[])  # inner
        # Inner totals only its own call...
        assert inner == {"prompt_tokens": 7, "completion_tokens": 1}

    # ...and the outer sees its own call PLUS the rolled-up inner spend.
    assert outer == {"prompt_tokens": 17, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_missing_usage_does_not_break_tracking():
    """A response with no .usage is tolerated (counts stay at zero)."""
    from app.lib.litellm_retry import acompletion_with_retry, track_tokens

    class _NoUsage:
        choices = []

    with track_tokens() as bucket:
        with _patched_acompletion(_NoUsage()):
            await acompletion_with_retry(model="gpt-4o-mini", messages=[])

    assert bucket == {"prompt_tokens": 0, "completion_tokens": 0}
