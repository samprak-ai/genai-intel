"""
Unit tests for pipeline.py

Tests cover pipeline orchestration logic with attribution mocked out,
so no network calls or API keys are needed. The TEST_EVENTS fixture
preserves the spirit of the original test_pipeline.py test data.

Pipeline.__init__ creates FundingDiscovery, DomainResolver, and
AttributionEngine — all of which make network calls or need API keys.
We mock them at the class level to keep tests fast and deterministic.
"""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from app.models import (
    FundingEvent, Attribution, ProviderEntry, ProviderType, EntrenchmentLevel
)
from tests.conftest import make_funding_event


# ============================================================================
# Test data — mirrors original test_pipeline.py TEST_EVENTS
# ============================================================================

TEST_EVENTS = [
    FundingEvent(
        company_name="Harvey",
        funding_amount_usd=100,
        funding_round="Series C",
        announcement_date=date.today(),
        website="harvey.ai",
        industry="Legal AI",
        description="AI for lawyers",
        source_name="techcrunch",
        source_url="https://techcrunch.com/test",
    ),
    FundingEvent(
        company_name="Notion",
        funding_amount_usd=275,
        funding_round="Series C",
        announcement_date=date.today(),
        website="notion.so",
        industry="Productivity",
        description="All-in-one workspace",
        source_name="techcrunch",
        source_url="https://techcrunch.com/test",
    ),
    FundingEvent(
        company_name="Linear",
        funding_amount_usd=35,
        funding_round="Series B",
        announcement_date=date.today(),
        website="linear.app",
        industry="Developer Tools",
        description="Issue tracking for software teams",
        source_name="vcnewsdaily",
        source_url="https://vcnewsdaily.com/test",
    ),
]


def _make_single_attribution(provider_name: str, provider_type: ProviderType) -> Attribution:
    entry = ProviderEntry(
        provider_name=provider_name,
        role="Cloud infrastructure" if provider_type == ProviderType.CLOUD else "AI service provider",
        confidence=1.0,
        entrenchment=EntrenchmentLevel.STRONG,
        raw_score=2.0,
    )
    return Attribution(
        provider_type=provider_type,
        is_multi=False,
        primary_provider=provider_name,
        providers=[entry],
        confidence=1.0,
        evidence_count=1,
    )


# ============================================================================
# Fixture: Pipeline with all external dependencies mocked
# ============================================================================

@pytest.fixture
def pipeline(mocker):
    """
    Pipeline(dry_run=True) with FundingDiscovery, DomainResolver, and
    AttributionEngine all mocked — no network, no API keys needed.
    """
    mocker.patch("app.discovery.funding_discovery.FundingDiscovery.__init__", return_value=None)
    mocker.patch("app.resolution.domain_resolver.DomainResolver.__init__", return_value=None)
    mocker.patch("app.attribution.attribution_engine.AttributionEngine.__init__", return_value=None)
    mocker.patch("app.core.database.DatabaseClient.__init__", return_value=None)

    from pipeline import Pipeline
    p = Pipeline(dry_run=True)
    # Attach mock sub-components
    p.discovery = MagicMock()
    p.resolver = MagicMock()
    p.attribution = MagicMock()
    p.db = MagicMock()
    # Pipeline needs a run object for _stage_attribute to update attributed count
    from app.models import WeeklyRun
    p.run = WeeklyRun()
    return p


# ============================================================================
# _stage_resolve — website resolution orchestration
# ============================================================================

class TestStageResolve:

    def test_skips_events_that_already_have_website(self, pipeline):
        # All TEST_EVENTS have websites — resolver.resolve should NOT be called
        pipeline._stage_resolve(TEST_EVENTS)
        pipeline.resolver.resolve.assert_not_called()

    def test_calls_resolver_for_events_without_website(self, pipeline):
        events = [make_funding_event(company_name="Harvey", website=None)]
        pipeline.resolver.resolve.return_value = "harvey.ai"
        pipeline._stage_resolve(events)
        pipeline.resolver.resolve.assert_called_once()

    def test_sets_website_on_event_when_resolved(self, pipeline):
        events = [make_funding_event(company_name="Harvey", website=None)]
        pipeline.resolver.resolve.return_value = "harvey.ai"
        result = pipeline._stage_resolve(events)
        assert result[0].website == "harvey.ai"

    def test_website_stays_none_when_resolver_returns_none(self, pipeline):
        events = [make_funding_event(company_name="UnknownCo", website=None)]
        pipeline.resolver.resolve.return_value = None
        result = pipeline._stage_resolve(events)
        assert result[0].website is None

    def test_handles_resolver_exception_gracefully(self, pipeline):
        events = [make_funding_event(company_name="BadCo", website=None)]
        pipeline.resolver.resolve.side_effect = Exception("DNS timeout")
        # Should not raise — errors are caught and logged
        result = pipeline._stage_resolve(events)
        assert result[0].website is None


# ============================================================================
# _stage_attribute — attribution orchestration
# ============================================================================

class TestStageAttribute:

    def test_skips_events_without_website(self, pipeline):
        events = [make_funding_event(company_name="NoDomain", website=None)]
        pipeline.attribution.attribute_startup.return_value = (None, None)
        results = pipeline._stage_attribute(events)
        # attribute_startup should NOT be called for events with no website
        pipeline.attribution.attribute_startup.assert_not_called()
        # But result list should be empty (skipped events not included)
        assert results == []

    def test_returns_result_for_each_attributable_event(self, pipeline):
        pipeline.attribution.attribute_startup.return_value = (None, None)
        results = pipeline._stage_attribute(TEST_EVENTS)
        assert len(results) == len(TEST_EVENTS)

    def test_result_contains_event_reference(self, pipeline):
        pipeline.attribution.attribute_startup.return_value = (None, None)
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert results[0]["event"] is TEST_EVENTS[0]

    def test_result_contains_cloud_attribution(self, pipeline):
        cloud_attr = _make_single_attribution("AWS", ProviderType.CLOUD)
        pipeline.attribution.attribute_startup.return_value = (cloud_attr, None)
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert results[0]["cloud_attribution"] is cloud_attr

    def test_result_contains_ai_attribution(self, pipeline):
        ai_attr = _make_single_attribution("OpenAI", ProviderType.AI)
        pipeline.attribution.attribute_startup.return_value = (None, ai_attr)
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert results[0]["ai_attribution"] is ai_attr

    def test_attributed_flag_true_when_cloud_found(self, pipeline):
        cloud_attr = _make_single_attribution("AWS", ProviderType.CLOUD)
        pipeline.attribution.attribute_startup.return_value = (cloud_attr, None)
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert results[0]["attributed"] is True

    def test_attributed_flag_false_when_nothing_found(self, pipeline):
        pipeline.attribution.attribute_startup.return_value = (None, None)
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert results[0]["attributed"] is False

    def test_handles_attribution_exception_without_crash(self, pipeline):
        pipeline.attribution.attribute_startup.side_effect = Exception("Network error")
        # Should not raise
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert len(results) == 1
        assert results[0]["attributed"] is False
        assert "error" in results[0]

    def test_passes_investors_to_attribution(self, pipeline):
        event = make_funding_event(
            company_name="Harvey",
            website="harvey.ai",
            lead_investors=["GV", "Sequoia"],
        )
        pipeline.attribution.attribute_startup.return_value = (None, None)
        pipeline._stage_attribute([event])
        call_kwargs = pipeline.attribution.attribute_startup.call_args[1]
        assert call_kwargs.get("lead_investors") == ["GV", "Sequoia"]


# ============================================================================
# Full dry-run orchestration (ports test_pipeline_dry_run)
# ============================================================================

class TestPipelineDryRun:

    def test_dry_run_resolves_and_attributes_test_events(self, pipeline):
        """
        Full orchestration test using TEST_EVENTS (all have websites).
        - Resolution stage should be skipped (all have websites)
        - Attribution stage should be called once per event
        - No storage should occur (dry_run=True)
        """
        aws_attr = _make_single_attribution("AWS", ProviderType.CLOUD)
        pipeline.attribution.attribute_startup.return_value = (aws_attr, None)

        events = pipeline._stage_resolve(list(TEST_EVENTS))
        results = pipeline._stage_attribute(events)

        # All 3 events should have been attributed
        assert len(results) == len(TEST_EVENTS)
        assert pipeline.attribution.attribute_startup.call_count == len(TEST_EVENTS)

        # resolver was never called (all events had websites)
        pipeline.resolver.resolve.assert_not_called()

        # DB was never called (dry_run=True)
        pipeline.db.create_startup.assert_not_called()

    def test_dry_run_result_has_expected_keys(self, pipeline):
        pipeline.attribution.attribute_startup.return_value = (None, None)
        results = pipeline._stage_attribute([TEST_EVENTS[0]])
        assert "event" in results[0]
        assert "cloud_attribution" in results[0]
        assert "ai_attribution" in results[0]
        assert "attributed" in results[0]
