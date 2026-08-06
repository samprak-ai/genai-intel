"""
Behavioral A/B: same prompts through Anthropic vs DeepSeek (playbook §5).

Runs representative PROD prompts (same strings the app uses) through BOTH
providers at temperature=0 and reports:
  - correctness (JSON parses, expected fields present, band usage for scores)
  - determinism (two runs identical at temp=0)
  - voice/quality (generative fields, eyeball)

Usage:
    python scripts/ab_providers.py            # both providers
    python scripts/ab_providers.py deepseek   # just deepseek
    python scripts/ab_providers.py anthropic  # just anthropic
Needs AI_PROVIDER + keys on both sides in .env. Adds ~cents per run.
"""

import json
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from app.services import ai_client as client  # noqa: E402

COMPANY = "Suno"
DOMAIN = "suno.com"
DESC = "AI music generation platform; users create full songs from text hooks and styles."
INVESTORS = "Khosla Ventures, Matrix Partners, SV Angel"
TAXONOMY = (
    "AI Applications & Tooling / Vertical-specific AI apps\n"
    "AI Applications & Tooling / Horizontal AI apps\n"
    "Data & Analytics / Infrastructure\n"
    "Hardware / Semiconductors\n"
    "Dev Tools / Developer infrastructure"
)


CLASSIFY_USER = f"""You are classifying a startup into a vertical and sub-vertical taxonomy for cloud provider intelligence.

Company: {COMPANY}
Domain: {DOMAIN}
Description: {DESC}
Investors: {INVESTORS}
Founder background: (none)

Choose the single best-fit vertical and sub-vertical from this taxonomy:

{TAXONOMY}

Rules:
- Choose the most specific sub-vertical that fits
- If the company could fit multiple, choose the one most relevant to their PRIMARY product
- If genuinely unclear, choose the closest match and set confidence to "low"

Respond with JSON only, no other text:
{{
  "vertical": "<exact vertical name>",
  "sub_vertical": "<exact sub-vertical name>",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence>"
}}"""

FUND_USER = f"""Extract funding information from this press release or article.

Title: Sunset AI raises $30M Series A to scale music tools
Content: {COMPANY} raised $30 million in Series A funding led by Khosla Ventures,
to expand its AI songwriting platform.

Return JSON in this exact format:
{{
  "company_name": "Official company name",
  "funding_amount_usd": 30,
  "funding_round": "Series A",
  "lead_investors": [],
  "website": null,
  "industry": "AI/ML",
  "description": "One sentence: what the company does"
}}

Rules:
- funding_amount_usd: number in millions (30 for $30M). Required.
- If NOT a startup/company raising VC or institutional funding, return: {{"not_funding": true}}

Return ONLY valid JSON, no explanation."""

OUTREACH_USER = f"""You are generating a concise engagement intelligence briefing for a cloud provider GTM team.

Company: {COMPANY}
Vertical: AI Applications & Tooling → Vertical-specific AI apps
Cloud Propensity: High (81% confidence, medium entrenchment)
Current Cloud Stack: AWS
Funding: Series A of $30M raised 12 days ago
Engagement Tier: Engage Now

Recent signals:
- hiring_surge: 4 infrastructure/engineering roles posted
- product_launch: released multi-model streaming endpoint

Generate a concise engagement intelligence briefing with:
1. A recommended engagement angle (2-3 sentences, specific)
2. 3-4 key signals that support this (short bullets)

Be direct, specific, grounded in the signals. No generic sales language.

Respond with JSON only:
{{
  "recommended_angle": "<2-3 sentence rationale>",
  "key_signals": ["<signal 1>", "<signal 2>", "<signal 3>"]
}}"""

CASES = [
    ("classify", CLASSIFY_USER, 200),
    ("funding-extract", FUND_USER, 300),
    ("outreach-brief", OUTREACH_USER, 700),
]


def _parse_json(text):
    """Strip code fences then parse; return (clean_text, obj_or_None)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        t = t.rsplit("```", 1)[0]
    try:
        return t, json.loads(t)
    except Exception:
        return t, None


def run_case(user, max_tokens):
    """Run twice at temp=0 to measure determinism."""
    runs = []
    for _ in range(2):
        text, usage = client.complete_with_usage("claude-sonnet-4-6", None, user, max_tokens)
        runs.append((text, usage))
    return runs


def report(case_name, runs):
    raw0, u0 = runs[0]
    raw1, u1 = runs[1]
    _, obj0 = _parse_json(raw0)

    # correctness
    if case_name == "classify":
        ok = bool(obj0 and obj0.get("vertical") and obj0.get("sub_vertical"))
        print(f"  JSON+fields: {'OK' if ok else 'FAIL'}")
        if obj0:
            print(f"  choice: {obj0.get('vertical')} / {obj0.get('sub_vertical')} (conf={obj0.get('confidence')})")
            print(f"  reasoning: {obj0.get('reasoning','')[:120]}")
        else:
            print(f"  RAW: {raw0[:200]}")
    elif case_name == "funding-extract":
        print(f"  JSON parse: {'OK' if obj0 else 'FAIL'}")
        if obj0:
            print(f"  amount=${obj0.get('funding_amount_usd')}M  round={obj0.get('funding_round')}  "
                  f"lead={obj0.get('lead_investors')}")
        else:
            print(f"  RAW: {raw0[:200]}")
    else:  # outreach
        print(f"  JSON parse: {'OK' if obj0 else 'FAIL'}")
        if obj0:
            print(f"  angle: ...{obj0.get('recommended_angle','')[:200]}")
            print(f"  signals: {obj0.get('key_signals')}")
        else:
            print(f"  RAW: {raw0[:200]}")

    # determinism
    det = "IDENTICAL" if raw0 == raw1 else "DIFFERS"
    print(f"  determinism (temp=0 x2): {det}")

    # usage
    inc = u0["input_tokens"] + u1["input_tokens"]
    out = u0["output_tokens"] + u1["output_tokens"]
    print(f"  tokens (2 runs): in={inc} out={out}")


def run_provider():
    label = os.getenv("AI_PROVIDER", "?")
    print(f"\n{'='*66}")
    print(f"PROVIDER = {label}")
    print('='*66)
    for case_name, user, max_tokens in CASES:
        print(f"\n--- {case_name} ---")
        try:
            runs = run_case(user, max_tokens)
            report(case_name, runs)
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")


def main():
    which = sys.argv[1].lower() if len(sys.argv) > 1 else "both"
    if which in ("both", "anthropic", "claude"):
        os.environ["AI_PROVIDER"] = "anthropic"
        run_provider()
    if which in ("both", "deepseek"):
        os.environ["AI_PROVIDER"] = "deepseek"
        run_provider()


if __name__ == "__main__":
    main()