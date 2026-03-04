"""
Vertical Classification & Cloud Propensity

LLM-based classifier that maps a company to the closest vertical + sub-vertical
from the taxonomy, then derives a Cloud Propensity tag (High / Medium / Low)
from the sub-vertical lookup table.

Classification is independent of attribution — it does not influence signal
gathering and should not be added to search queries.

Uses Claude Haiku for cost efficiency (~$0.0002 per classification).
"""

import os
import re
import json
from dataclasses import dataclass
from typing import Optional

import anthropic

from app.taxonomy import (
    TAXONOMY,
    SUB_VERTICAL_PROPENSITY,
    VALID_VERTICALS,
)


@dataclass
class ClassificationResult:
    vertical: Optional[str]
    sub_vertical: Optional[str]
    cloud_propensity: Optional[str]           # "High" / "Medium" / "Low"
    classification_confidence: Optional[str]  # "high" / "medium" / "low"
    classification_source: str                # "llm_classification" or "manual"
    reasoning: str                            # LLM's one-line rationale (debug only)


def _format_taxonomy_for_prompt() -> str:
    """Format the taxonomy as a readable list for the LLM prompt."""
    lines = []
    for vertical, data in TAXONOMY.items():
        lines.append(f"\n{vertical}:")
        for sv in data["sub_verticals"]:
            lines.append(f"  - {sv}")
    return "\n".join(lines)


_TAXONOMY_PROMPT_LIST = _format_taxonomy_for_prompt()


CLASSIFICATION_PROMPT = """You are classifying a startup into a vertical and sub-vertical taxonomy for cloud provider intelligence.

Company: {company_name}
Domain: {domain}
Description: {description}
Investors: {investors}
Founder background: {founder_background}
{article_context}
Choose the single best-fit vertical and sub-vertical from this taxonomy:

{taxonomy_list}

Rules:
- Choose the most specific sub-vertical that fits
- If the company could fit multiple, choose the one most relevant to their PRIMARY product
- If genuinely unclear, choose the closest match and set confidence to "low"

Respond with JSON only, no other text:
{{
  "vertical": "<exact vertical name>",
  "sub_vertical": "<exact sub-vertical name>",
  "confidence": "high" | "medium" | "low",
  "reasoning": "<one sentence explaining the classification>"
}}"""


def classify_company(
    company_name: str,
    domain: str,
    description: str = "",
    investors: Optional[list[str]] = None,
    founder_background: Optional[str] = None,
    article_text: Optional[str] = None,
) -> ClassificationResult:
    """
    Classify a company into the vertical taxonomy using Claude Haiku.

    Stateless and independently testable — works with just company_name + domain
    if no other context is available.

    Returns ClassificationResult with vertical, sub_vertical, cloud_propensity.
    On failure (missing API key, LLM error, invalid response), returns a result
    with None fields and reasoning explaining the failure.
    """
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        return ClassificationResult(
            vertical=None,
            sub_vertical=None,
            cloud_propensity=None,
            classification_confidence=None,
            classification_source="llm_classification",
            reasoning="No ANTHROPIC_API_KEY available",
        )

    # Build prompt
    article_context = ""
    if article_text:
        # Truncate to 1500 chars to keep prompt cost low while providing rich context
        trimmed = article_text[:1500]
        article_context = f"Funding article excerpt: {trimmed}\n"

    prompt = CLASSIFICATION_PROMPT.format(
        company_name=company_name,
        domain=domain or "unknown",
        description=description or "No description available",
        investors=", ".join(investors) if investors else "Unknown",
        founder_background=founder_background or "Unknown",
        article_context=article_context,
        taxonomy_list=_TAXONOMY_PROMPT_LIST,
    )

    # Call Claude Haiku
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=300,
            messages=[{'role': 'user', 'content': prompt}],
        )
        raw = response.content[0].text.strip()
    except Exception as e:
        return ClassificationResult(
            vertical=None,
            sub_vertical=None,
            cloud_propensity=None,
            classification_confidence=None,
            classification_source="llm_classification",
            reasoning=f"LLM call failed: {e}",
        )

    # Parse JSON response
    try:
        # Strip markdown fences if present
        if raw.startswith('```'):
            raw = re.sub(r'^```[a-z]*\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)

        result = json.loads(raw)
    except json.JSONDecodeError as e:
        return ClassificationResult(
            vertical=None,
            sub_vertical=None,
            cloud_propensity=None,
            classification_confidence=None,
            classification_source="llm_classification",
            reasoning=f"JSON parse error: {e} | raw: {raw[:100]}",
        )

    vertical = str(result.get("vertical", "")).strip()
    sub_vertical = str(result.get("sub_vertical", "")).strip()
    confidence = str(result.get("confidence", "")).strip().lower()
    reasoning = str(result.get("reasoning", "")).strip()

    # Validate confidence
    if confidence not in ("high", "medium", "low"):
        confidence = "low"

    # Validate vertical
    if vertical not in VALID_VERTICALS:
        # Try case-insensitive match
        matched = _fuzzy_match_vertical(vertical)
        if matched:
            vertical = matched
        else:
            return ClassificationResult(
                vertical=None,
                sub_vertical=None,
                cloud_propensity=None,
                classification_confidence=confidence,
                classification_source="llm_classification",
                reasoning=f"Invalid vertical: '{vertical}' | {reasoning}",
            )

    # Validate sub_vertical within the matched vertical
    valid_subs = TAXONOMY[vertical]["sub_verticals"]
    if sub_vertical not in valid_subs:
        # Try fuzzy match within this vertical's sub-verticals
        matched_sv = _fuzzy_match_sub_vertical(sub_vertical, valid_subs)
        if matched_sv:
            sub_vertical = matched_sv
        else:
            # Accept the vertical but null out the sub-vertical
            return ClassificationResult(
                vertical=vertical,
                sub_vertical=None,
                cloud_propensity=TAXONOMY[vertical]["propensity"],  # fall back to vertical-level
                classification_confidence=confidence,
                classification_source="llm_classification",
                reasoning=f"Invalid sub_vertical: '{sub_vertical}' in {vertical} | {reasoning}",
            )

    # Derive propensity from taxonomy
    cloud_propensity = SUB_VERTICAL_PROPENSITY.get(
        sub_vertical,
        TAXONOMY[vertical]["propensity"],  # fallback to vertical-level default
    )

    return ClassificationResult(
        vertical=vertical,
        sub_vertical=sub_vertical,
        cloud_propensity=cloud_propensity,
        classification_confidence=confidence,
        classification_source="llm_classification",
        reasoning=reasoning,
    )


def _fuzzy_match_vertical(candidate: str) -> Optional[str]:
    """Try to match a vertical name case-insensitively or by substring."""
    candidate_lower = candidate.lower().strip()
    for v in VALID_VERTICALS:
        if v.lower() == candidate_lower:
            return v
    # Substring match (e.g. "Cybersecurity" matches "Cybersecurity")
    for v in VALID_VERTICALS:
        if candidate_lower in v.lower() or v.lower() in candidate_lower:
            return v
    return None


def _fuzzy_match_sub_vertical(candidate: str, valid_subs: dict) -> Optional[str]:
    """Try to match a sub-vertical name case-insensitively or by close substring."""
    candidate_lower = candidate.lower().strip()
    for sv in valid_subs:
        if sv.lower() == candidate_lower:
            return sv
    for sv in valid_subs:
        if candidate_lower in sv.lower() or sv.lower() in candidate_lower:
            return sv
    return None
