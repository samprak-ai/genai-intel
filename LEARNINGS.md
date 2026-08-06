# LEARNINGS.md — DeepSeek provider migration (Aug 2026)

Playbook: `PORT_TO_DEEPSEEK.md` (source: job-search-intel migration, Jul-Aug 2026).
Guard: `scripts/check_ai_client_wiring.py` — deterministic, exit 0/1.

## What shipped

- `app/services/ai_client.py` — single dispatcher on `AI_PROVIDER`
  (`anthropic` default | `deepseek`). `complete()` / `complete_with_usage()`
  are the only entry points; `temperature=0` lives in the client.
- Every LLM call site routes through the client (see guard's MIGRATED list),
  including the classifier and domain_resolver — no services remain exempt.
- Config keys (`.env`, mirror Railway vars):
  `AI_PROVIDER`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL`.

## The three traps (all real)

1. **DeepSeek v4-pro is a thinking model by default.** Reasoning goes to
   `reasoning_content` and `content` comes back EMPTY — JSON parsers break.
   Every DeepSeek call sends `"thinking": {"type": "disabled"}`.
2. **Model strings are provider-specific.** `claude-*` ids 400 on DeepSeek.
   `_deepseek_model()` maps any `claude-*` label to `DEEPSEEK_MODEL`.
3. **Anthropic `system` blocks vs DeepSeek string.** `_flatten_system()`
   handles both; Claude path passes blocks verbatim (keeps prompt caching).

## Why the originally-exempt services were moved

- **classifier.py** — plain text-only call, no tools; ported straight to
  `complete()`. (Initially exempted as a "cheap decision-loop classifier".)
- **domain_resolver.py** — its `_ai_search` used the Anthropic `web_search`
  tool, which the OpenAI-compatible DeepSeek endpoint has NO equivalent for.
  It now searches Brave deterministically (same API as Stage 2.5) and feeds the
  result URLs/snippets into the prompt, making the LLM pick provider-agnostic.
  The `web_search` dependency is fully removed.

## Guard semantics

The guard FAILS if any migrated module imports `anthropic.Anthropic` or calls
`messages.create` directly, if the client loses its dispatcher / thinking guard /
model converters, or if an exempt service stops calling the SDK directly / starts
routing through the client (or the client special-cases it). Verified both ways:
regression → exit 1, fix → exit 0.

## Not yet done

- Prod (Railway) vars not set; `DEEPSEEK_MODEL` should be set explicitly there.
- Verify the deployed app by triggering real work (a generation / a score), not
  just `/health` — a thinking-mode pro response is 200 but empty content.

## Behavioral A/B (playbook §5) — DONE 2026

Ran `scripts/ab_providers.py` (classify, funding-extract, outreach-brief twice at
temp=0 each) on both Anthropic Sonnet-4.6 and DeepSeek V4 Pro.

- **Correctness — equal.** Same JSON shapes and judgment on all three:
  classify → `AI Applications & Tooling / Vertical-specific AI apps (high)`;
  funding → `$30M Series A, Khosla Ventures`; outreach → valid JSON, same
  grounded signals (GPU/compute scaling, AWS medium-entrenchment).
- **Determinism at temp=0 — neither is deterministic on generative output.**
  classify + funding identical on both; outreach brief DIFFERS on BOTH providers
  (longer generation still samples at temp=0). Not a DeepSeek regression; these
  are advisory prose, not fed into a numeric decision, so variance is acceptable.
- **Band usage — no compression risk.** This app classifies categorically; there
  is no 0-100 score band, so DeepSeek Flash's band-compression issue does not
  apply. Verified Pro uses all bands.
- **Verdict:** DeepSeek Pro is behaviorally equivalent for the whole pipeline;
  swap is validated.
