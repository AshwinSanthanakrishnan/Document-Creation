"""Tests for the Gemini retry-then-fallback policy and output cleanup (no network calls)."""

import pytest

from src.ai_drafter import DraftingError, GeminiDrafter, clean_ai_text

GOOD = "Our firm will review your eligibility, prepare your forms, gather evidence, and keep you informed at every step of the process."


def _drafter(responses):
    """A drafter whose 'model' returns (or raises) each response in turn."""
    calls = iter(responses)

    def generate(prompt):
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    return GeminiDrafter(api_key=None, model="fake-model", retry_delay=0, generate=generate)


def test_first_attempt_success():
    result = _drafter([GOOD]).draft("scope_of_services", case_type="Asylum")
    assert (result.source, result.attempts, result.used_fallback) == ("gemini", 1, False)


def test_retries_once_then_succeeds():
    result = _drafter([TimeoutError("slow"), GOOD]).draft("next_steps", case_type="Asylum")
    assert (result.source, result.attempts) == ("gemini", 2)


def test_two_failures_fall_back_and_are_flagged():
    result = _drafter([ConnectionError("down"), ConnectionError("still down")]).draft(
        "request_intro", case_type="Asylum", missing_count=3
    )
    assert result.used_fallback
    assert result.attempts == 2
    assert "still down" in result.error
    assert "Asylum" in result.text


class _ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.status, self.message = code, "UNAUTHENTICATED", message


def test_rejected_key_stops_further_calls_for_the_run():
    calls = []

    def generate(prompt):
        calls.append(prompt)
        raise _ApiError(401, "API key not valid.")

    drafter = GeminiDrafter(api_key=None, model="fake-model", retry_delay=0, generate=generate)
    first = drafter.draft("scope_of_services", case_type="Asylum")
    second = drafter.draft("next_steps", case_type="Asylum")

    assert len(calls) == 2  # the first section's call + one retry, then no more
    assert first.used_fallback and first.attempts == 2
    assert first.error == "_ApiError: 401 UNAUTHENTICATED: API key not valid."
    assert second.used_fallback and second.attempts == 0
    assert not drafter.enabled


def test_transient_errors_do_not_disable_the_drafter():
    drafter = _drafter([ConnectionError("blip"), ConnectionError("blip"), GOOD])
    assert drafter.draft("scope_of_services", case_type="Asylum").used_fallback
    assert drafter.enabled
    assert not drafter.draft("next_steps", case_type="Asylum").used_fallback


def test_rate_limit_retry_waits_as_long_as_the_api_asks(monkeypatch):
    from src import ai_drafter

    class RateLimited(Exception):
        code = 429
        details = {"error": {"details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "33s"}]}}

    sleeps = []
    monkeypatch.setattr(ai_drafter.time, "sleep", sleeps.append)
    result = _drafter([RateLimited("quota"), GOOD]).draft("next_steps", case_type="Asylum")

    assert sleeps == [34.0]
    assert (result.source, result.attempts) == ("gemini", 2)


def test_unusable_output_counts_as_a_failure():
    result = _drafter(["", "Too short."]).draft("scope_of_services", case_type="Asylum")
    assert result.used_fallback


def test_missing_api_key_uses_fallback_without_calling_the_model():
    drafter = GeminiDrafter(api_key=None, model="fake-model")
    result = drafter.draft("scope_of_services", case_type="Naturalization")
    assert not drafter.enabled
    assert result.used_fallback and result.attempts == 0
    assert "GEMINI_API_KEY" in result.error


def test_clean_ai_text_strips_markdown_and_preamble():
    raw = "Here is the paragraph:\n\n**" + GOOD + "**"
    assert clean_ai_text(raw) == GOOD


def test_clean_ai_text_rejects_bracketed_placeholders():
    with pytest.raises(DraftingError, match="bracketed"):
        clean_ai_text(GOOD + " Contact [Attorney Name] with questions.")


def test_daily_quota_stops_further_calls_without_long_waits(monkeypatch):
    from src import ai_drafter

    class DailyQuota(Exception):
        code = 429
        details = {"error": {"details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "1869s"},
        ]}}

    sleeps = []
    monkeypatch.setattr(ai_drafter.time, "sleep", sleeps.append)
    drafter = _drafter([DailyQuota("quota"), DailyQuota("quota")])
    drafter.retry_delay = 3.0

    assert drafter.draft("scope_of_services", case_type="Asylum").used_fallback
    assert sleeps == [3.0]  # normal short retry, not a 31-minute (or 60s) wait
    assert not drafter.enabled
    assert "daily quota" in drafter.disabled_reason
