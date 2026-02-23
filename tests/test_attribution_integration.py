"""
Integration tests — require live network and/or ANTHROPIC_API_KEY.

These tests are excluded from the default `pytest` run.
Run them explicitly with:  pytest -m integration

Migrated from:
  - tests/test_attribution.py (test_subprocessors_parser, test_full_attribution)
  - tests/test_discovery.py   (test_discovery)
"""

import os
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]


# ============================================================================
# Subprocessors parser — hits harvey.ai live
# ============================================================================

def test_subprocessors_parser_harvey():
    """
    Tests the SubprocessorsParser against Harvey's live trust/legal page.
    Verifies structural correctness of the parser output, not specific values
    (the page content may change over time).
    """
    from app.attribution.subprocessors_parser import SubprocessorsParser

    parser = SubprocessorsParser()
    result = parser.parse("harvey.ai")

    if not result.found:
        pytest.skip("Harvey subprocessors page not reachable — URL may have changed")

    assert len(result.signals) > 0, "Expected at least one signal from subprocessors page"
    for signal in result.signals:
        assert signal.confidence_weight in [1.0, 0.6, 0.3], (
            f"Unexpected weight {signal.confidence_weight} for signal {signal}"
        )
        assert signal.signal_source == "subprocessors_page"


# ============================================================================
# Full attribution engine — hits live DNS, HTTP, and news searches
# ============================================================================

def test_full_attribution_harvey():
    """
    Full attribution on Harvey AI using live signal gathering.
    Expected: Azure cloud (from DNS/subprocessors), multi-AI (OpenAI + others).
    We only assert structure, not specific providers — infra can change.
    """
    from app.attribution.attribution_engine import AttributionEngine

    engine = AttributionEngine()
    cloud_attr, ai_attr = engine.attribute_startup("Harvey", "harvey.ai")

    # At least one attribution should be found
    assert cloud_attr is not None or ai_attr is not None, (
        "Expected at least one attribution for Harvey AI"
    )

    if cloud_attr:
        assert cloud_attr.confidence >= 0.0
        assert cloud_attr.evidence_count >= 0
        assert cloud_attr.display_name  # non-empty string


def test_full_attribution_notion():
    """
    Full attribution on Notion — expected to be on AWS.
    Structural checks only.
    """
    from app.attribution.attribution_engine import AttributionEngine

    engine = AttributionEngine()
    cloud_attr, ai_attr = engine.attribute_startup("Notion", "notion.so")

    if cloud_attr:
        assert cloud_attr.confidence <= 1.0
        assert len(cloud_attr.providers) >= 1


# ============================================================================
# Discovery — hits RSS feeds and Anthropic API
# ============================================================================

def test_discover_recent_funding():
    """
    Tests the full funding discovery pipeline against live RSS feeds.
    Requires ANTHROPIC_API_KEY to be set.

    Verifies structural correctness of returned events — specific companies
    vary week to week.
    """
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set — skipping live discovery test")

    from app.discovery.funding_discovery import FundingDiscovery

    discovery = FundingDiscovery()
    events = discovery.discover_recent_funding(days_back=7, limit=5)

    assert isinstance(events, list), "Expected a list of FundingEvents"

    for event in events:
        assert event.funding_amount_usd >= 0
        assert event.company_name, "Expected non-empty company name"
        assert event.announcement_date, "Expected announcement date"
        assert event.funding_round, "Expected funding round"
