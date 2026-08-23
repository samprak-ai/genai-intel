"""
Unified AI client — DeepSeek (OpenAI-compatible endpoint).

Every LLM call in the codebase routes through `complete()` /
`complete_with_usage()` (enforced by scripts/check_ai_client_wiring.py).
Call sites pass a SEMANTIC model label (e.g. "claude-sonnet-4-6", a leftover
of the original Claude implementation); every label maps to the configured
DeepSeek model via _deepseek_model().

Two traps (hit during the DeepSeek migration, documented in the playbook):
  1. DeepSeek v4-pro is a thinking model by default: reasoning goes to
     `reasoning_content` and `content` comes back EMPTY.  We always send
     `thinking: {"type": "disabled"}` or the JSON parser breaks.
  2. Model strings are provider-specific — map every label to the configured
     DeepSeek model, else DeepSeek rejects unknown ids with a 400.
"""

import os
import random
import time

import httpx


# --------------------------------------------------------------------------- #
# Retry / backoff
# --------------------------------------------------------------------------- #

def _retry_config() -> tuple[int, float, float]:
    """(max_attempts, base_delay_sec, multiplier) — configurable via env for ops."""
    try:
        max_attempts = max(1, int(os.getenv("AI_RETRY_MAX", "3")))
    except ValueError:
        max_attempts = 3
    try:
        base_delay = max(0.0, float(os.getenv("AI_RETRY_BASE", "1.0")))
    except ValueError:
        base_delay = 1.0
    try:
        multiplier = max(1.0, float(os.getenv("AI_RETRY_MULT", "2.0")))
    except ValueError:
        multiplier = 2.0
    return max_attempts, base_delay, multiplier


def _is_retryable(e: Exception, status: int = 0) -> bool:
    """True if the exception represents a transient failure worth retrying."""
    if isinstance(e, httpx.RequestError):
        return True  # connection reset, DNS, timeout, etc.
    # HTTPStatusError and non-SDK errors: retry rate limits + server faults only.
    return 429 <= status <= 599


def _with_retries(fn, *args):
    """
    Run `fn(*args)` with exponential backoff + jitter on transient failures.

    Raises the final exception if all attempts fail. Returns normal result
    otherwise. Status codes 429 and 5xx are retried; errors retried only when
    they map to connectivity / rate-limit / server faults.
    """
    max_attempts, base_delay, multiplier = _retry_config()
    attempt = 0
    last_exc = None
    while attempt < max_attempts:
        attempt += 1
        try:
            return fn(*args)
        except Exception as e:
            status = getattr(e, "status_code", 0) or getattr(getattr(e, "response", None), "status_code", 0)
            if attempt >= max_attempts or not _is_retryable(e, status):
                raise
            last_exc = e
            # Exponential backoff with full jitter: delay grows but is randomized
            # to avoid a thundering herd when many workers retry together.
            jitter = random.uniform(0, base_delay)
            sleep_for = min((base_delay * (multiplier ** (attempt - 1))) + jitter, 30)
            time.sleep(sleep_for)
    raise last_exc


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """True if DEEPSEEK_API_KEY exists."""
    return bool(os.getenv("DEEPSEEK_API_KEY"))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def complete(model: str, system, user: str, max_tokens: int = 1024) -> str:
    """Complete a single-turn call. `system` may be a string or a blocks list;
    it is normalized to a single string for the OpenAI-compatible endpoint."""
    return complete_deepseek(model, _flatten_system(system), user, max_tokens)


def complete_with_usage(model: str, system, user: str, max_tokens: int = 1024):
    """Same as complete(), but returns (text, usage_dict) for cost reporting."""
    text, usage = _call_deepseek(model, _flatten_system(system), user, max_tokens)
    return text, usage


# --------------------------------------------------------------------------- #
# DeepSeek backend (OpenAI-compatible)
# --------------------------------------------------------------------------- #

def complete_deepseek(model: str, system: str, user: str, max_tokens: int) -> str:
    text, _ = _call_deepseek(model, system, user, max_tokens)
    return text


def _deepseek_model(model: str) -> str:
    """DeepSeek rejects unknown ids. Any explicitly non-claude label is passed
    through (allows pinning a specific deepseek id per call site); claude labels
    (and empty) map to the configured DeepSeek model."""
    if model and not model.startswith("claude"):
        return model
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")


def _flatten_system(system) -> str:
    """Normalize a legacy blocks list (or a plain string) to a single string."""
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts = []
        for block in system:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            else:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
        return "\n\n".join(parts)
    return str(system)


def _call_deepseek(model: str, system: str, user: str, max_tokens: int):
    key = os.getenv("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    payload = {
        "model": _deepseek_model(model),
        "temperature": 0,
        "max_tokens": max_tokens,
        # Mandatory for v4-pro: without this, content comes back EMPTY and the
        # reasoning goes to reasoning_content instead (breaks every JSON parser).
        "thinking": {"type": "disabled"},
        "messages": messages,
    }

    resp = _with_retries(_deepseek_post, base_url, key, payload)
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]
    content = choice["message"].get("content") or ""
    usage = data.get("usage", {})
    return content.strip(), {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }


def _deepseek_post(base_url: str, key: str, payload: dict):
    """Single DeepSeek request — isolated so _with_retries can re-invoke it."""
    return httpx.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=120,
    )
