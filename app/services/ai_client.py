"""
Unified AI provider client (DeepSeek migration playbook).

Every LLM call routes through `complete()` / `complete_with_usage()`, which
dispatches on AI_PROVIDER ("anthropic" default | "deepseek").  Call sites pass a
SEMANTIC model label (e.g. "claude-sonnet-4-6") and each provider maps it to its
own model string.

Three traps (all hit in the source migration, documented in the playbook):
  1. DeepSeek v4-pro is a thinking model by default: reasoning goes to
     `reasoning_content` and `content` comes back EMPTY.  We always send
     `thinking: {"type": "disabled"}` or the JSON parser breaks.
  2. Model strings are provider-specific — map every `claude-*` label to the
     configured DeepSeek model, else DeepSeek rejects the id with a 400.
  3. Anthropic `system` blocks (list w/ cache_control) vs DeepSeek single
     string — flatten for DeepSeek, pass verbatim for Claude (keeps caching).

Provider SDKs are imported lazily so the other provider's SDK is never required.
"""

import os
import random
import time

import httpx

_DEFAULT_CLAUDE = "claude-sonnet-4-6"


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
    try:
        import anthropic

        if isinstance(e, (anthropic.APIConnectionError,
                          anthropic.APITimeoutError,
                          anthropic.RateLimitError,
                          anthropic.InternalServerError)):
            return True
    except Exception:
        pass

    if isinstance(e, httpx.RequestError):
        return True  # connection reset, DNS, timeout, etc.
    if isinstance(e, httpx.HTTPStatusError):
        return 429 <= status <= 599

    # Non-SDK status errors with a retryable status code.
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

def provider() -> str:
    return (os.getenv("AI_PROVIDER") or "anthropic").lower().replace(" ", "")


def is_configured() -> bool:
    """True if an API key exists for the active provider."""
    if provider() == "deepseek":
        return bool(os.getenv("DEEPSEEK_API_KEY"))
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def complete(model: str, system, user: str, max_tokens: int = 1024) -> str:
    """Complete a single-turn call. `system` may be a string or an Anthropic
    blocks list; the client normalizes it per provider."""
    if provider() == "deepseek":
        return complete_deepseek(model, _flatten_system(system), user, max_tokens)
    return _call_anthropic(model, system, user, max_tokens)


def complete_with_usage(model: str, system, user: str, max_tokens: int = 1024):
    """Same as complete(), but returns (text, usage_dict) for cost reporting."""
    if provider() == "deepseek":
        text, usage = _call_deepseek(model, _flatten_system(system), user, max_tokens)
        return text, usage
    return _call_anthropic_usage(model, system, user, max_tokens)


# --------------------------------------------------------------------------- #
# Anthropic backend
# --------------------------------------------------------------------------- #

def _call_anthropic(model: str, system, user: str, max_tokens: int) -> str:
    text, _ = _call_anthropic_usage(model, system, user, max_tokens)
    return text


def _anthropic_client():
    import anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=key)


def _call_anthropic_usage(model: str, system, user: str, max_tokens: int):
    if not is_configured():
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = _anthropic_client()

    message = _with_retries(_anthropic_create, client, model, system, user, max_tokens)
    text = message.content[0].text.strip() if message.content else ""
    usage = {
        "input_tokens": getattr(message.usage, "input_tokens", 0),
        "output_tokens": getattr(message.usage, "output_tokens", 0),
    }
    return text, usage


def _anthropic_create(client, model: str, system, user: str, max_tokens: int):
    """Single Anthropic request — isolated so _with_retries can re-invoke it."""
    kwargs = {}
    if system:
        kwargs["system"] = system  # pass blocks verbatim to preserve prompt caching
    return client.messages.create(
        model=_anthropic_model(model),
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": user}],
        **kwargs,
    )


def _anthropic_model(model: str) -> str:
    """Anthropic model labels are already valid ids; pass through."""
    if model and not model.startswith("claude"):
        return model
    return model or _DEFAULT_CLAUDE


# --------------------------------------------------------------------------- #
# DeepSeek backend (OpenAI-compatible)
# --------------------------------------------------------------------------- #

def complete_deepseek(model: str, system: str, user: str, max_tokens: int) -> str:
    text, _ = _call_deepseek(model, system, user, max_tokens)
    return text


def _deepseek_model(model: str) -> str:
    """DeepSeek rejects `claude-*` ids. Any non-claude label is passed through;
    claude labels (and empty) map to the configured DeepSeek model."""
    if model and not model.startswith("claude"):
        return model
    return os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")


def _flatten_system(system) -> str:
    """Normalize an Anthropic system blocks list (or a plain string) to a single
    string for the OpenAI-compatible endpoint."""
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
        raise RuntimeError("DEEPSEEK_API_KEY not configured for AI_PROVIDER=deepseek")
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