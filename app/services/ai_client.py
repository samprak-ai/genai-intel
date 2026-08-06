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

import httpx

_DEFAULT_CLAUDE = "claude-sonnet-4-6"


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

    kwargs = {}
    if system:
        kwargs["system"] = system  # pass blocks verbatim to preserve prompt caching

    message = client.messages.create(
        model=_anthropic_model(model),
        max_tokens=max_tokens,
        temperature=0,
        messages=[{"role": "user", "content": user}],
        **kwargs,
    )
    text = message.content[0].text.strip() if message.content else ""
    usage = {
        "input_tokens": getattr(message.usage, "input_tokens", 0),
        "output_tokens": getattr(message.usage, "output_tokens", 0),
    }
    return text, usage


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

    resp = httpx.post(
        f"{base_url}/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    choice = data["choices"][0]
    content = choice["message"].get("content") or ""
    usage = data.get("usage", {})
    return content.strip(), {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
    }