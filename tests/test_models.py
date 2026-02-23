"""
Unit tests for app/models.py

All tests are pure in-memory — no network, no API calls, no mocking needed.
Tests cover: FundingEvent validators, Startup validators, AttributionSignal
validator, Attribution properties, and calculate_entrenchment.
"""

import pytest
from datetime import date
from pydantic import ValidationError

from app.models import (
    FundingEvent, Startup, AttributionSignal,
    Attribution, ProviderEntry, ProviderType, SignalStrength, EntrenchmentLevel,
)


# ============================================================================
# Helpers
# ============================================================================

def base_funding_event(**kwargs) -> dict:
    """Minimal valid FundingEvent kwargs."""
    defaults = dict(
        company_name="TestCo",
        funding_amount_usd=50,
        funding_round="Series A",
        announcement_date=date.today(),
        source_name="techcrunch",
        source_url="https://techcrunch.com/test",
    )
    defaults.update(kwargs)
    return defaults


# ============================================================================
# FundingEvent — funding_amount_usd validator
# ============================================================================

def test_funding_amount_rejects_negative():
    with pytest.raises(ValidationError):
        FundingEvent(**base_funding_event(funding_amount_usd=-1))


def test_funding_amount_rejects_unrealistically_high():
    with pytest.raises(ValidationError):
        FundingEvent(**base_funding_event(funding_amount_usd=1_000_001))


def test_funding_amount_accepts_zero():
    event = FundingEvent(**base_funding_event(funding_amount_usd=0))
    assert event.funding_amount_usd == 0


def test_funding_amount_accepts_typical_series_a():
    event = FundingEvent(**base_funding_event(funding_amount_usd=50))
    assert event.funding_amount_usd == 50


def test_funding_amount_accepts_large_but_realistic():
    event = FundingEvent(**base_funding_event(funding_amount_usd=500))
    assert event.funding_amount_usd == 500


# ============================================================================
# FundingEvent — website validator
# ============================================================================

def test_website_strips_https_prefix():
    event = FundingEvent(**base_funding_event(website="https://harvey.ai"))
    assert event.website == "harvey.ai"


def test_website_strips_http_prefix():
    event = FundingEvent(**base_funding_event(website="http://harvey.ai"))
    assert event.website == "harvey.ai"


def test_website_strips_trailing_slash():
    event = FundingEvent(**base_funding_event(website="https://harvey.ai/"))
    assert event.website == "harvey.ai"


def test_website_accepts_valid_domain():
    event = FundingEvent(**base_funding_event(website="harvey.ai"))
    assert event.website == "harvey.ai"


def test_website_accepts_com_domain():
    event = FundingEvent(**base_funding_event(website="notion.so"))
    assert event.website == "notion.so"


def test_website_rejects_linkedin():
    event = FundingEvent(**base_funding_event(website="linkedin.com/company/harvey"))
    assert event.website is None


def test_website_rejects_twitter():
    event = FundingEvent(**base_funding_event(website="twitter.com/harvey_ai"))
    assert event.website is None


def test_website_rejects_x_com():
    event = FundingEvent(**base_funding_event(website="x.com/harvey_ai"))
    assert event.website is None


def test_website_rejects_crunchbase():
    event = FundingEvent(**base_funding_event(website="crunchbase.com/organization/harvey"))
    assert event.website is None


def test_website_rejects_invalid_format():
    event = FundingEvent(**base_funding_event(website="not-a-domain"))
    assert event.website is None


def test_website_none_stays_none():
    event = FundingEvent(**base_funding_event(website=None))
    assert event.website is None


# ============================================================================
# FundingEvent — funding_round normalizer
# ============================================================================

def test_funding_round_normalizes_seed_lowercase():
    event = FundingEvent(**base_funding_event(funding_round="seed"))
    assert event.funding_round == "Seed"


def test_funding_round_normalizes_seed_uppercase():
    event = FundingEvent(**base_funding_event(funding_round="SEED"))
    assert event.funding_round == "Seed"


def test_funding_round_normalizes_series_a():
    event = FundingEvent(**base_funding_event(funding_round="SERIES A"))
    assert event.funding_round == "Series A"


def test_funding_round_normalizes_series_b_mixed_case():
    event = FundingEvent(**base_funding_event(funding_round="Series B"))
    assert event.funding_round == "Series B"


def test_funding_round_preserves_unknown_round():
    event = FundingEvent(**base_funding_event(funding_round="Growth Round"))
    # Not in the round_map → returned as-is (uppercased by .upper() then no match)
    assert event.funding_round == "GROWTH ROUND"


# ============================================================================
# Startup — canonical_name normalizer
# ============================================================================

def test_startup_strips_inc_suffix():
    s = Startup(canonical_name="Harvey Inc.", website="harvey.ai")
    assert s.canonical_name == "Harvey"


def test_startup_strips_inc_no_dot():
    s = Startup(canonical_name="Harvey Inc", website="harvey.ai")
    assert s.canonical_name == "Harvey"


def test_startup_strips_llc():
    s = Startup(canonical_name="Linear LLC", website="linear.app")
    assert s.canonical_name == "Linear"


def test_startup_strips_ltd():
    s = Startup(canonical_name="Notion Ltd.", website="notion.so")
    assert s.canonical_name == "Notion"


def test_startup_title_cases_name():
    s = Startup(canonical_name="harvey ai", website="harvey.ai")
    assert s.canonical_name == "Harvey Ai"


def test_startup_preserves_normal_name():
    s = Startup(canonical_name="Harvey", website="harvey.ai")
    assert s.canonical_name == "Harvey"


# ============================================================================
# Startup — website validator
# ============================================================================

def test_startup_website_strips_protocol():
    s = Startup(canonical_name="Harvey", website="https://harvey.ai/about")
    assert s.website == "harvey.ai"


def test_startup_website_lowercases():
    s = Startup(canonical_name="Harvey", website="Harvey.AI")
    assert s.website == "harvey.ai"


def test_startup_website_rejects_invalid_format():
    with pytest.raises(ValidationError):
        Startup(canonical_name="Harvey", website="not-valid")


# ============================================================================
# AttributionSignal — confidence_weight validator
# ============================================================================

def test_signal_rejects_invalid_weight():
    with pytest.raises(ValidationError):
        AttributionSignal(
            provider_type=ProviderType.CLOUD,
            provider_name="AWS",
            signal_source="dns_cname",
            signal_strength=SignalStrength.STRONG,
            confidence_weight=0.5,  # invalid — must be 1.0, 0.6, or 0.3
        )


def test_signal_accepts_weight_10():
    s = AttributionSignal(
        provider_type=ProviderType.CLOUD, provider_name="AWS",
        signal_source="dns_cname", signal_strength=SignalStrength.STRONG,
        confidence_weight=1.0,
    )
    assert s.confidence_weight == 1.0


def test_signal_accepts_weight_06():
    s = AttributionSignal(
        provider_type=ProviderType.CLOUD, provider_name="AWS",
        signal_source="job_posting", signal_strength=SignalStrength.MEDIUM,
        confidence_weight=0.6,
    )
    assert s.confidence_weight == 0.6


def test_signal_accepts_weight_03():
    s = AttributionSignal(
        provider_type=ProviderType.CLOUD, provider_name="GCP",
        signal_source="investor_prior", signal_strength=SignalStrength.WEAK,
        confidence_weight=0.3,
    )
    assert s.confidence_weight == 0.3


# ============================================================================
# Attribution.display_name
# ============================================================================

def _make_attribution(**kwargs) -> Attribution:
    defaults = dict(
        provider_type=ProviderType.CLOUD,
        is_multi=False,
        primary_provider="AWS",
        providers=[ProviderEntry(
            provider_name="AWS", role="Cloud infrastructure",
            confidence=1.0, entrenchment=EntrenchmentLevel.STRONG, raw_score=2.0,
        )],
        confidence=1.0,
        evidence_count=1,
    )
    defaults.update(kwargs)
    return Attribution(**defaults)


def test_display_name_single_provider():
    attr = _make_attribution(primary_provider="AWS", is_multi=False)
    assert attr.display_name == "AWS"


def test_display_name_multi_provider():
    attr = _make_attribution(
        is_multi=True,
        primary_provider=None,
        providers=[
            ProviderEntry(provider_name="AWS", role="Cloud infrastructure",
                          confidence=0.5, entrenchment=EntrenchmentLevel.STRONG, raw_score=1.0),
            ProviderEntry(provider_name="GCP", role="Cloud infrastructure",
                          confidence=0.5, entrenchment=EntrenchmentLevel.STRONG, raw_score=1.0),
        ],
    )
    assert attr.display_name == "Multi (AWS, GCP)"


def test_display_name_not_applicable():
    attr = _make_attribution(
        is_not_applicable=True,
        primary_provider=None,
        providers=[],
        confidence=0.0,
        evidence_count=0,
    )
    assert attr.display_name == "Not Applicable"


def test_display_name_unknown_when_no_primary():
    attr = _make_attribution(
        is_multi=False,
        primary_provider=None,
        providers=[],
        confidence=0.0,
        evidence_count=0,
    )
    assert attr.display_name == "Unknown"


# ============================================================================
# Attribution.provider_names
# ============================================================================

def test_provider_names_empty():
    attr = _make_attribution(providers=[], primary_provider=None,
                             confidence=0.0, evidence_count=0)
    assert attr.provider_names == []


def test_provider_names_single():
    attr = _make_attribution()
    assert attr.provider_names == ["AWS"]


def test_provider_names_multi():
    attr = _make_attribution(
        is_multi=True,
        primary_provider=None,
        providers=[
            ProviderEntry(provider_name="AWS", role="Cloud infrastructure",
                          confidence=0.5, entrenchment=EntrenchmentLevel.STRONG, raw_score=1.0),
            ProviderEntry(provider_name="GCP", role="Cloud infrastructure",
                          confidence=0.5, entrenchment=EntrenchmentLevel.STRONG, raw_score=1.0),
        ],
    )
    assert attr.provider_names == ["AWS", "GCP"]


# ============================================================================
# Attribution.calculate_entrenchment
# ============================================================================

def test_entrenchment_strong():
    assert Attribution.calculate_entrenchment(2.0) == EntrenchmentLevel.STRONG


def test_entrenchment_strong_above_threshold():
    assert Attribution.calculate_entrenchment(3.5) == EntrenchmentLevel.STRONG


def test_entrenchment_moderate():
    assert Attribution.calculate_entrenchment(1.0) == EntrenchmentLevel.MODERATE


def test_entrenchment_moderate_below_strong():
    assert Attribution.calculate_entrenchment(1.9) == EntrenchmentLevel.MODERATE


def test_entrenchment_weak():
    assert Attribution.calculate_entrenchment(0.3) == EntrenchmentLevel.WEAK


def test_entrenchment_weak_below_moderate():
    assert Attribution.calculate_entrenchment(0.9) == EntrenchmentLevel.WEAK


def test_entrenchment_unknown():
    assert Attribution.calculate_entrenchment(0.1) == EntrenchmentLevel.UNKNOWN


def test_entrenchment_unknown_at_zero():
    assert Attribution.calculate_entrenchment(0.0) == EntrenchmentLevel.UNKNOWN


def test_entrenchment_boundary_strong_exactly_2():
    # 2.0 is exactly STRONG threshold
    assert Attribution.calculate_entrenchment(2.0) == EntrenchmentLevel.STRONG


def test_entrenchment_boundary_just_below_strong():
    # 1.99 is MODERATE (not STRONG)
    assert Attribution.calculate_entrenchment(1.99) == EntrenchmentLevel.MODERATE
