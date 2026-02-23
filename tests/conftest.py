"""
Shared fixtures and helpers for the GenAI-Intel test suite.

pytest adds the project root to sys.path when pytest.ini is present there,
so no sys.path hacks are needed in individual test files.
"""

import pytest
from datetime import date
from unittest.mock import MagicMock

from app.models import (
    AttributionSignal, ProviderType, SignalStrength,
    ProviderEntry, EntrenchmentLevel, FundingEvent, Attribution,
)


# ============================================================================
# Helper factories (plain functions, not fixtures — importable by test files)
# ============================================================================

def make_signal(
    provider_name: str,
    provider_type: ProviderType = ProviderType.CLOUD,
    source: str = "dns_cname",
    strength: SignalStrength = SignalStrength.STRONG,
    weight: float = 1.0,
    evidence: str = "test evidence",
) -> AttributionSignal:
    return AttributionSignal(
        provider_type=provider_type,
        provider_name=provider_name,
        signal_source=source,
        signal_strength=strength,
        evidence_text=evidence,
        confidence_weight=weight,
    )


def make_funding_event(**kwargs) -> FundingEvent:
    defaults = dict(
        company_name="TestCo",
        funding_amount_usd=50,
        funding_round="Series A",
        announcement_date=date.today(),
        source_name="techcrunch",
        source_url="https://techcrunch.com/test",
    )
    defaults.update(kwargs)
    return FundingEvent(**defaults)


def make_provider_entry(name: str, score: float = 1.0) -> ProviderEntry:
    return ProviderEntry(
        provider_name=name,
        role="Cloud infrastructure",
        confidence=1.0,
        entrenchment=Attribution.calculate_entrenchment(score),
        raw_score=score,
    )


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_anthropic_client():
    """A MagicMock for anthropic.Anthropic with a default no-op response."""
    client = MagicMock()
    response = MagicMock()
    response.content = [MagicMock(text='{"providers": []}')]
    client.messages.create.return_value = response
    return client


@pytest.fixture
def aws_signal():
    return make_signal("AWS", ProviderType.CLOUD, "dns_cname", SignalStrength.STRONG, 1.0)


@pytest.fixture
def gcp_signal():
    return make_signal("GCP", ProviderType.CLOUD, "dns_cname", SignalStrength.STRONG, 1.0)


@pytest.fixture
def azure_signal():
    return make_signal("Azure", ProviderType.CLOUD, "dns_cname", SignalStrength.STRONG, 1.0)


@pytest.fixture
def openai_signal():
    return make_signal("OpenAI", ProviderType.AI, "subprocessors_page", SignalStrength.STRONG, 1.0)


@pytest.fixture
def engine(monkeypatch):
    """
    AttributionEngine with no API key — LLM fallback is disabled.
    Safe to use in unit tests: no network, no Anthropic calls.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from app.attribution.attribution_engine import AttributionEngine
    e = AttributionEngine()
    assert e.anthropic_client is None, "Expected no Anthropic client in unit tests"
    return e
