"""
Deterministic guard for the provider switch (DeepSeek migration playbook §4).

Ensures every migrated LLM call site routes through app.services.ai_client, so
the DeepSeek client can never be silently bypassed. No service may call an LLM
SDK directly; there is no second provider anymore (Anthropic support was
removed after the migration completed).

Usage:
    python scripts/check_ai_client_wiring.py
Exit code 0 = wiring correct. Nonzero + problem list = a regression.

Mirrors L31 / L32 in the job-search-intel playbook.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

MIGRATED = [
    "app/discovery/funding_discovery.py",
    "app/attribution/attribution_engine.py",
    "app/intelligence/outreach_generator.py",
    "app/triggers/trigger_detector.py",
    "api/routers/ask.py",
    "app/classification/classifier.py",
    "app/resolution/domain_resolver.py",
]

# Exempt by design — must call an LLM SDK directly, must NOT be special-cased
# inside the ai_client.  Currently none; all services route through
# app.services.ai_client.
# (Historic: classifier was exempt as a cheap decision-loop classifier, and
# domain_resolver was exempt while it used the Anthropic `web_search` tool —
# that tool has no DeepSeek equivalent, so resolution was rewritten to search
# via Google News RSS out-of-band and feed the evidence into the prompt.)
EXEMPT = {}


def read(rel: str) -> str:
    return (ROOT / rel).read_text()


def collect_problems() -> list[str]:
    problems = []

    # -- L31-style: client must be a complete dispatcher -------------------
    client = read("app/services/ai_client.py")
    for label, needle, msg in [
        ("complete", "def complete(", "missing `def complete(`"),
        ("complete_with_usage", "def complete_with_usage(", "missing `def complete_with_usage(`"),
        ("deepseek backend", "deepseek" in client, "must reference 'deepseek'"),
        ("no anthropic backend", "anthropic.Anthropic(" not in client,
         "must NOT construct an Anthropic client (provider removed)"),
        ("thinking disabled", '"type": "disabled"', "missing DeepSeek thinking:disabled guard"),
        ("_deepseek_model", "def _deepseek_model(", "missing `_deepseek_model(` converter"),
        ("_flatten_system", "def _flatten_system(", "missing `_flatten_system(`"),
    ]:
        ok = needle if isinstance(needle, bool) else (needle in client)
        if not ok:
            problems.append(f"app/services/ai_client.py: {label}: {msg}")

    # -- L31-style: every migrated module routes through the client ---------
    for rel in MIGRATED:
        src = read(rel)
        if "app.services.ai_client" not in src:
            problems.append(f"{rel}: must import and route through app.services.ai_client")
        if "anthropic.Anthropic(" in src:
            problems.append(f"{rel}: direct Anthropic call present — must route through ai_client.complete")
        if "messages.create(" in src:
            problems.append(f"{rel}: calls messages.create directly — must route through ai_client.complete")

    # -- L32-style: exempt services bypass the client by design -------------
    for rel in EXEMPT:
        src = read(rel)
        if "app.services.ai_client" in src:
            problems.append(f"{rel}: exempt service must NOT route through ai_client")

    return problems


def main() -> int:
    problems = collect_problems()
    if problems:
        print("selfcheck FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("selfcheck passed: every migrated call site routes through ai_client; "
          "no direct SDK calls.")
    return 0


if __name__ == "__main__":
    sys.exit(main())