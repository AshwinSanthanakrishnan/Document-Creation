"""Gemini drafting for the few letter sections that need real prose.

Only non-identifying context is sent to the model: the case type and, for the
document request letter, how many documents are outstanding. Names, addresses,
case IDs and document lists are filled from data by the templates, never generated.

Every call is tried twice (one retry). If both attempts fail, a neutral template
paragraph is used instead and the result is marked as a fallback so the run
report can flag it for the reviewing attorney.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Callable

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # the first call plus one retry

SYSTEM_INSTRUCTION = (
    "You draft short passages for letters that a U.S. immigration law firm sends to its clients. "
    "Write in plain, warm, professional English that a non-lawyer can easily follow. "
    "Return only the paragraph text: no preamble, no title, no markdown, no bullet points, "
    "no greeting or sign-off, and no placeholders or square brackets. "
    "Do not promise outcomes, quote fees, give processing times, or state legal conclusions. "
    "An attorney will review everything you write before it is sent."
)

PROMPTS = {
    "scope_of_services": (
        "Write one paragraph of 90 to 130 words describing the scope of services our firm will "
        "provide for a client's {case_type} case. Describe the work in general terms that fit this "
        "case type, such as reviewing eligibility, preparing and filing the required forms, "
        "assembling supporting evidence, and corresponding with the government on the client's "
        "behalf. Address the client as \"you\" and refer to the firm as \"our firm\" or \"we\"."
    ),
    "next_steps": (
        "Write one paragraph of 70 to 110 words explaining in plain language what typically "
        "happens next in a {case_type} case once the client signs and returns our engagement letter. "
        "The letter has not been signed yet, so describe these steps as future events (\"once you sign\"). "
        "Address the client as \"you\". Keep it general and note that timelines vary."
    ),
    "request_intro": (
        "Write a brief, friendly opening paragraph of 40 to 70 words for a letter asking a client "
        "to send {missing_count} remaining document(s) for their {case_type} case. Thank them for "
        "what they have sent so far and explain that the remaining documents help us keep their "
        "case moving. Do not list or name the documents; they appear in a list below the paragraph."
    ),
}

# Neutral text used when Gemini is unavailable. Deliberately generic: it is always
# flagged in the run report so an attorney knows to review or rewrite it.
FALLBACKS = {
    "scope_of_services": (
        "Our firm will represent you in connection with your {case_type} matter. Our work includes "
        "reviewing your eligibility and options, advising you on the process, preparing and filing "
        "the required forms and supporting documents, and corresponding with the relevant government "
        "agencies on your behalf. We will keep you informed of important developments and will let "
        "you know promptly if we need additional information or documents from you."
    ),
    "next_steps": (
        "Once we receive your signed engagement letter, we will review the information and documents "
        "you have provided and let you know if anything else is needed. We will then prepare your "
        "filing for your review before it is submitted. After filing, the government may send notices, "
        "schedule appointments, or request more information, and we will guide you through each step. "
        "Timelines vary from case to case, and we will keep you updated along the way."
    ),
    "request_intro": (
        "Thank you for the documents you have sent so far for your {case_type} case. To keep your case "
        "moving forward, we need a few more items from you. Please review the list below and send "
        "these documents at your earliest convenience."
    ),
}

# Characters the PDF's built-in serif font cannot draw, mapped to safe equivalents.
_CHAR_REPLACEMENTS = {
    "‑": "-",  # non-breaking hyphen
    "‐": "-",  # hyphen
    " ": " ",  # no-break space
    " ": " ",  # narrow no-break space
    " ": " ",  # thin space
    "​": "",  # zero-width space
}


class DraftingError(Exception):
    """Gemini returned nothing usable."""


@dataclass
class DraftResult:
    section: str
    text: str
    source: str  # "gemini" or "fallback"
    model: str | None
    attempts: int
    error: str | None = None
    reused: bool = False  # same prompt already answered earlier in this run

    @property
    def used_fallback(self) -> bool:
        return self.source == "fallback"

    def to_dict(self) -> dict:
        return {
            "section": self.section,
            "source": self.source,
            "model": self.model,
            "attempts": self.attempts,
            "error": self.error,
            "reused": self.reused,
        }


def clean_ai_text(raw: str | None) -> str:
    """Strip the formatting a model sometimes adds despite instructions, and reject bad output."""
    if not raw or not raw.strip():
        raise DraftingError("empty response")

    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)  # code fences
    text = re.sub(r"^(?:here is|here's|sure)[^\n]*:\s*\n+", "", text, flags=re.IGNORECASE)  # preamble line
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)  # markdown headings
    text = re.sub(r"[*`]|__", "", text)  # bold/italic/code markers
    for bad, good in _CHAR_REPLACEMENTS.items():
        text = text.replace(bad, good)

    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", text) if p.strip()]
    text = "\n\n".join(paragraphs).strip().strip('"').strip()

    if len(text.split()) < 15:
        raise DraftingError(f"response too short ({len(text.split())} words)")
    if re.search(r"\[[^\]]*\]", text):
        raise DraftingError("response contains a bracketed placeholder")
    return text


def _gemini_generate_fn(api_key: str, model: str, timeout_seconds: int) -> Callable[[str], str]:
    """Build a function that sends one prompt to Gemini and returns the raw response text."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        # attempts=1 turns off the SDK's own retries so the retry policy lives in one place (draft()).
        http_options=types.HttpOptions(
            timeout=timeout_seconds * 1000,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    config = types.GenerateContentConfig(system_instruction=SYSTEM_INSTRUCTION, temperature=0.4)

    def generate(prompt: str) -> str:
        response = client.models.generate_content(model=model, contents=prompt, config=config)
        return response.text or ""

    return generate


class GeminiDrafter:
    """Drafts prose sections with Gemini, falling back to neutral templates on failure.

    `generate` can be injected (tests do this); otherwise a real Gemini client is
    built from `api_key`. With no key, or with offline=True, every section uses the
    fallback text and is flagged.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str,
        *,
        offline: bool = False,
        retry_delay: float = 3.0,
        timeout_seconds: int = 30,
        generate: Callable[[str], str] | None = None,
    ) -> None:
        self.model = model
        self.retry_delay = retry_delay
        self.disabled_reason: str | None = None
        self._generate = generate
        # Paragraphs depend only on the case type (and document count), so clients with the
        # same case type reuse one Gemini draft: fewer API calls and consistent wording.
        self._cache: dict[str, DraftResult] = {}

        if self._generate is None:
            if offline:
                self.disabled_reason = "--offline flag was passed"
            elif not api_key:
                self.disabled_reason = "GEMINI_API_KEY is not set"
            else:
                self._generate = _gemini_generate_fn(api_key, model, timeout_seconds)

    @property
    def enabled(self) -> bool:
        return self._generate is not None

    def draft(self, section: str, **context: object) -> DraftResult:
        if section not in PROMPTS:
            raise KeyError(f"Unknown AI section '{section}'")

        if self._generate is None:
            return self._fallback(section, context, attempts=0, error=f"AI drafting disabled: {self.disabled_reason}")

        prompt = PROMPTS[section].format(**context)
        if prompt in self._cache:
            log.info("    Reused Gemini draft for '%s' (same case type earlier in this run)", section)
            return replace(self._cache[prompt], attempts=0, reused=True)

        last_exc: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                text = clean_ai_text(self._generate(prompt))
                log.info("    Gemini drafted '%s' (%d words, attempt %d)", section, len(text.split()), attempt)
                result = DraftResult(section, text, "gemini", self.model, attempt)
                self._cache[prompt] = result  # only successful drafts; failures get a fresh try next time
                return result
            except Exception as exc:  # network, quota, safety block, or unusable text
                last_exc = exc
                log.warning("    Gemini call for '%s' failed (attempt %d/%d): %s", section, attempt, MAX_ATTEMPTS, _short_error(exc))
                if attempt < MAX_ATTEMPTS:
                    # On a rate limit, Gemini says how long to wait; honour it (capped) for the one retry.
                    delay = _server_retry_delay(exc) or self.retry_delay
                    if delay > self.retry_delay:
                        log.info("    Rate limited; waiting %.0fs before retrying, as the API requested", delay)
                    time.sleep(delay)

        # A rejected key or unknown model will fail every later call too, so stop calling for this run.
        config_problem = _configuration_problem(last_exc)
        if config_problem:
            self._generate = None
            self.disabled_reason = config_problem
            log.error("    %s. Gemini is disabled for the rest of this run; remaining sections use fallback text.", config_problem)
        return self._fallback(section, context, attempts=MAX_ATTEMPTS, error=_short_error(last_exc))

    def _fallback(self, section: str, context: dict, *, attempts: int, error: str) -> DraftResult:
        if attempts:
            log.warning("    Using fallback template for '%s' — flagged for attorney review", section)
        else:
            log.info("    Using fallback template for '%s' (%s)", section, self.disabled_reason)
        return DraftResult(section, FALLBACKS[section].format(**context), "fallback", None, attempts, error)


def _short_error(exc: Exception | None, limit: int = 200) -> str:
    if exc is None:
        return "unknown error"
    # google-genai API errors carry code/status/message; prefer those over the raw JSON body.
    code, status, api_message = (getattr(exc, a, None) for a in ("code", "status", "message"))
    if api_message and (code or status):
        message = f"{code or ''} {status or ''}: {api_message}".strip()
    else:
        message = str(exc)
    message = " ".join(message.split()) or "no details"
    if len(message) > limit:
        message = message[: limit - 1] + "…"
    return f"{type(exc).__name__}: {message}"


MAX_SERVER_RETRY_DELAY = 60.0


def _server_retry_delay(exc: Exception) -> float | None:
    """Read the RetryInfo delay (e.g. "33s") from a Gemini rate-limit error, capped at 60s."""
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return None
    for item in details.get("error", {}).get("details", []) or []:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
            match = re.fullmatch(r"([\d.]+)s", str(item.get("retryDelay", "")))
            if match:
                delay = float(match.group(1)) + 1
                # A longer wait means a daily quota, not a per-minute one: don't stall the run for it.
                return delay if delay <= MAX_SERVER_RETRY_DELAY else None
    return None


def _configuration_problem(exc: Exception | None) -> str | None:
    """Return a reason if the error means no later call can succeed (bad key or model)."""
    code = getattr(exc, "code", None)
    text = str(exc).lower()
    if code in (401, 403) or "api key not valid" in text or "api_key_invalid" in text:
        return "Gemini rejected the API key (check GEMINI_API_KEY)"
    if code == 404:
        return "Gemini model not found (check GEMINI_MODEL)"
    if code == 429 and "perday" in json.dumps(getattr(exc, "details", None) or {}).lower():
        return "Gemini daily quota for this model is used up (try later, or set GEMINI_MODEL)"
    return None
