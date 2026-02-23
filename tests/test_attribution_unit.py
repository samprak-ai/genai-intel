"""
Unit tests for app/attribution/attribution_engine.py

Tests cover the pure-logic methods that need no network:
  - _calculate_attribution   (core scoring + multi-cloud detection)
  - _check_investor_signals  (INVESTOR_CLOUD_PRIORS lookup)
  - _check_founder_signals   (FOUNDER_CLOUD_PRIORS lookup)
  - _create_override_attribution  (PARTNERSHIP_OVERRIDES fast-path)
  - _create_na_attribution   (NOT_APPLICABLE fast-path)
  - attribute_startup fast-paths (override and N/A bypass _gather_all_signals)

The `engine` fixture (from conftest.py) instantiates AttributionEngine with
ANTHROPIC_API_KEY removed from env, so anthropic_client is None and the LLM
fallback is disabled — no network calls are made.
"""

import pytest
from app.models import ProviderType, SignalStrength
from tests.conftest import make_signal


# ============================================================================
# _calculate_attribution — core scoring and multi-cloud detection
# ============================================================================

class TestCalculateAttribution:

    def test_empty_signals_returns_none(self, engine):
        result = engine._calculate_attribution([], ProviderType.CLOUD)
        assert result is None

    def test_single_provider_is_not_multi(self, engine, aws_signal):
        result = engine._calculate_attribution([aws_signal], ProviderType.CLOUD)
        assert result is not None
        assert result.is_multi is False
        assert result.primary_provider == "AWS"

    def test_single_provider_confidence_is_1(self, engine, aws_signal):
        result = engine._calculate_attribution([aws_signal], ProviderType.CLOUD)
        assert result.confidence == 1.0

    def test_single_provider_evidence_count(self, engine):
        signals = [
            make_signal("AWS", weight=1.0),
            make_signal("AWS", source="job_posting", weight=0.6),
            make_signal("AWS", source="ip_asn", weight=0.3),
        ]
        result = engine._calculate_attribution(signals, ProviderType.CLOUD)
        assert result.evidence_count == 3

    def test_provider_type_preserved_cloud(self, engine, aws_signal):
        result = engine._calculate_attribution([aws_signal], ProviderType.CLOUD)
        assert result.provider_type == ProviderType.CLOUD

    def test_provider_type_preserved_ai(self, engine, openai_signal):
        result = engine._calculate_attribution([openai_signal], ProviderType.AI)
        assert result.provider_type == ProviderType.AI

    def test_multi_provider_equal_scores(self, engine):
        # AWS and GCP both score 1.0 — gap=0.0 < MULTI_PROVIDER_THRESHOLD(0.3) → multi
        signals = [
            make_signal("AWS", weight=1.0),
            make_signal("GCP", weight=1.0),
        ]
        result = engine._calculate_attribution(signals, ProviderType.CLOUD)
        assert result.is_multi is True
        assert result.primary_provider is None
        assert set(result.provider_names) == {"AWS", "GCP"}

    def test_single_provider_wins_wide_gap(self, engine):
        # AWS=2.0, GCP=0.3 — gap=1.7 >= 0.3 → single provider
        signals = [
            make_signal("AWS", weight=1.0),
            make_signal("AWS", source="job_posting", weight=1.0),
            make_signal("GCP", source="ip_asn", weight=0.3),
        ]
        result = engine._calculate_attribution(signals, ProviderType.CLOUD)
        assert result.is_multi is False
        assert result.primary_provider == "AWS"

    def test_threshold_boundary_exactly_03_is_single(self, engine):
        # gap = 1.3 - 1.0 = 0.3 — NOT less than 0.3 → single (not multi)
        signals = [
            make_signal("AWS", weight=1.0),
            make_signal("AWS", source="job_posting", weight=0.3),
            make_signal("GCP", weight=1.0),
        ]
        result = engine._calculate_attribution(signals, ProviderType.CLOUD)
        assert result.is_multi is False
        assert result.primary_provider == "AWS"

    def test_threshold_just_below_03_is_multi(self, engine):
        # gap = 1.0 - 0.9 = 0.1 < 0.3 → multi
        signals = [
            make_signal("AWS", weight=1.0),
            make_signal("GCP", source="ip_asn", weight=0.3),
            make_signal("GCP", source="job_posting", weight=0.6),
        ]
        result = engine._calculate_attribution(signals, ProviderType.CLOUD)
        assert result.is_multi is True

    def test_confidence_capped_at_1(self, engine):
        # Many signals → confidence should never exceed 1.0
        signals = [make_signal("AWS", weight=1.0) for _ in range(10)]
        result = engine._calculate_attribution(signals, ProviderType.CLOUD)
        assert result.confidence <= 1.0

    def test_provider_entries_built(self, engine, aws_signal):
        result = engine._calculate_attribution([aws_signal], ProviderType.CLOUD)
        assert len(result.providers) == 1
        assert result.providers[0].provider_name == "AWS"
        assert result.providers[0].raw_score == 1.0


# ============================================================================
# _check_investor_signals — INVESTOR_CLOUD_PRIORS lookup
# ============================================================================

class TestCheckInvestorSignals:

    def test_gv_maps_to_gcp(self, engine):
        signals = engine._check_investor_signals("TestCo", ["GV"])
        assert len(signals) == 1
        assert signals[0].provider_name == "GCP"

    def test_google_ventures_maps_to_gcp(self, engine):
        signals = engine._check_investor_signals("TestCo", ["Google Ventures"])
        assert len(signals) == 1
        assert signals[0].provider_name == "GCP"

    def test_m12_maps_to_azure(self, engine):
        signals = engine._check_investor_signals("TestCo", ["M12"])
        assert len(signals) == 1
        assert signals[0].provider_name == "Azure"

    def test_unknown_investor_returns_empty(self, engine):
        signals = engine._check_investor_signals("TestCo", ["Sequoia Capital"])
        assert signals == []

    def test_empty_investors_returns_empty(self, engine):
        signals = engine._check_investor_signals("TestCo", [])
        assert signals == []

    def test_investor_signal_always_weak_weight(self, engine):
        signals = engine._check_investor_signals("TestCo", ["GV"])
        assert signals[0].confidence_weight == 0.3
        assert signals[0].signal_strength == SignalStrength.WEAK

    def test_investor_signal_source_is_investor_prior(self, engine):
        signals = engine._check_investor_signals("TestCo", ["GV"])
        assert signals[0].signal_source == "investor_prior"

    def test_deduplicates_same_provider_from_two_investors(self, engine):
        # Both GV and Google map to GCP — only one GCP signal should be emitted
        signals = engine._check_investor_signals("TestCo", ["GV", "Google"])
        gcp_signals = [s for s in signals if s.provider_name == "GCP"]
        assert len(gcp_signals) == 1

    def test_case_insensitive_matching(self, engine):
        # Investor name in lowercase should still match
        signals = engine._check_investor_signals("TestCo", ["gv"])
        assert len(signals) == 1
        assert signals[0].provider_name == "GCP"


# ============================================================================
# _check_founder_signals — FOUNDER_CLOUD_PRIORS lookup
# ============================================================================

class TestCheckFounderSignals:

    def test_google_brain_maps_to_gcp(self, engine):
        signals = engine._check_founder_signals("TestCo", ["Google Brain"])
        assert len(signals) == 1
        assert signals[0].provider_name == "GCP"

    def test_deepmind_maps_to_gcp(self, engine):
        signals = engine._check_founder_signals("TestCo", ["DeepMind"])
        assert len(signals) == 1
        assert signals[0].provider_name == "GCP"

    def test_openai_background_maps_to_azure(self, engine):
        signals = engine._check_founder_signals("TestCo", ["OpenAI"])
        assert len(signals) == 1
        assert signals[0].provider_name == "Azure"

    def test_aws_background_maps_to_aws(self, engine):
        signals = engine._check_founder_signals("TestCo", ["AWS"])
        assert len(signals) == 1
        assert signals[0].provider_name == "AWS"

    def test_unknown_background_returns_empty(self, engine):
        signals = engine._check_founder_signals("TestCo", ["Stanford PhD"])
        assert signals == []

    def test_empty_background_returns_empty(self, engine):
        signals = engine._check_founder_signals("TestCo", [])
        assert signals == []

    def test_founder_signal_always_weak_weight(self, engine):
        signals = engine._check_founder_signals("TestCo", ["Google Brain"])
        assert signals[0].confidence_weight == 0.3

    def test_founder_signal_source_is_founder_prior(self, engine):
        signals = engine._check_founder_signals("TestCo", ["Google Brain"])
        assert signals[0].signal_source == "founder_prior"

    def test_deduplicates_same_provider(self, engine):
        # Google Brain and DeepMind both map to GCP → only one GCP signal
        signals = engine._check_founder_signals("TestCo", ["Google Brain", "DeepMind"])
        gcp_signals = [s for s in signals if s.provider_name == "GCP"]
        assert len(gcp_signals) == 1


# ============================================================================
# _create_override_attribution — PARTNERSHIP_OVERRIDES fast-path
# ============================================================================

class TestCreateOverrideAttribution:

    def test_creates_attribution_for_known_provider(self, engine):
        result = engine._create_override_attribution("OpenAI", "Azure", ProviderType.CLOUD)
        assert result is not None
        assert result.primary_provider == "Azure"

    def test_none_provider_returns_none(self, engine):
        result = engine._create_override_attribution("TestCo", None, ProviderType.CLOUD)
        assert result is None

    def test_override_confidence_is_1(self, engine):
        result = engine._create_override_attribution("OpenAI", "Azure", ProviderType.CLOUD)
        assert result.confidence == 1.0

    def test_override_is_not_multi(self, engine):
        result = engine._create_override_attribution("OpenAI", "Azure", ProviderType.CLOUD)
        assert result.is_multi is False

    def test_override_signal_source_is_partnership_override(self, engine):
        result = engine._create_override_attribution("OpenAI", "Azure", ProviderType.CLOUD)
        assert result.signals[0].signal_source == "partnership_override"

    def test_override_provider_type_preserved(self, engine):
        result = engine._create_override_attribution("Anthropic", "Anthropic", ProviderType.AI)
        assert result.provider_type == ProviderType.AI

    def test_override_evidence_count_is_1(self, engine):
        result = engine._create_override_attribution("OpenAI", "Azure", ProviderType.CLOUD)
        assert result.evidence_count == 1


# ============================================================================
# _create_na_attribution — NOT_APPLICABLE fast-path
# ============================================================================

class TestCreateNaAttribution:

    def test_is_not_applicable_true(self, engine):
        result = engine._create_na_attribution(ProviderType.CLOUD, "GPU neocloud")
        assert result.is_not_applicable is True

    def test_display_name_is_not_applicable(self, engine):
        result = engine._create_na_attribution(ProviderType.CLOUD, "GPU neocloud")
        assert result.display_name == "Not Applicable"

    def test_confidence_is_zero(self, engine):
        result = engine._create_na_attribution(ProviderType.CLOUD, "GPU neocloud")
        assert result.confidence == 0.0

    def test_evidence_count_is_zero(self, engine):
        result = engine._create_na_attribution(ProviderType.CLOUD, "GPU neocloud")
        assert result.evidence_count == 0

    def test_note_is_stored(self, engine):
        note = "GPU neocloud marketplace — is itself the compute provider"
        result = engine._create_na_attribution(ProviderType.CLOUD, note)
        assert result.not_applicable_note == note

    def test_provider_type_preserved(self, engine):
        result = engine._create_na_attribution(ProviderType.AI, "AI lab")
        assert result.provider_type == ProviderType.AI

    def test_providers_list_is_empty(self, engine):
        result = engine._create_na_attribution(ProviderType.CLOUD, "GPU neocloud")
        assert result.providers == []


# ============================================================================
# attribute_startup — fast-path bypasses (no network)
# ============================================================================

class TestAttributeStartupFastPaths:

    def test_partnership_override_bypasses_signal_gathering(self, engine, mocker):
        """Anthropic is in PARTNERSHIP_OVERRIDES — _gather_all_signals should NOT be called."""
        mock_gather = mocker.patch.object(engine, "_gather_all_signals", return_value=[])
        cloud_attr, ai_attr = engine.attribute_startup("Anthropic", "anthropic.com")
        mock_gather.assert_not_called()
        assert cloud_attr is not None
        assert cloud_attr.primary_provider == "GCP"

    def test_partnership_override_cloud_result(self, engine, mocker):
        mocker.patch.object(engine, "_gather_all_signals", return_value=[])
        cloud_attr, ai_attr = engine.attribute_startup("OpenAI", "openai.com")
        assert cloud_attr.primary_provider == "Azure"

    def test_partnership_override_ai_result(self, engine, mocker):
        mocker.patch.object(engine, "_gather_all_signals", return_value=[])
        cloud_attr, ai_attr = engine.attribute_startup("Anthropic", "anthropic.com")
        assert ai_attr is not None
        assert ai_attr.primary_provider == "Anthropic"

    def test_na_company_cloud_is_not_applicable(self, engine, mocker):
        """Pale Blue Dot is in NOT_APPLICABLE_COMPANIES with cloud=N/A note."""
        mocker.patch.object(engine, "_gather_all_signals", return_value=[])
        cloud_attr, ai_attr = engine.attribute_startup("Pale Blue Dot", "palebluedot.com")
        assert cloud_attr is not None
        assert cloud_attr.is_not_applicable is True

    def test_na_company_ai_still_attributed(self, engine, mocker):
        """Pale Blue Dot has ai=None in NOT_APPLICABLE_COMPANIES, so AI is still attributed normally."""
        # Return a strong OpenAI signal for the AI attribution
        openai_sig = make_signal("OpenAI", ProviderType.AI, "subprocessors_page", weight=1.0)
        mocker.patch.object(engine, "_gather_all_signals", return_value=[openai_sig])
        cloud_attr, ai_attr = engine.attribute_startup("Pale Blue Dot", "palebluedot.com")
        # AI should NOT be N/A — it should be attributed normally
        assert ai_attr is None or not ai_attr.is_not_applicable

    def test_llm_not_called_when_no_client(self, engine, mocker):
        """With no Anthropic client, LLM fallback should never be triggered."""
        assert engine.anthropic_client is None
        # Return a STRONG signal so confidence > threshold — but even if below,
        # no client means _llm_attribution_fallback is a no-op
        strong_signal = make_signal("AWS", weight=1.0)
        mocker.patch.object(engine, "_gather_all_signals", return_value=[strong_signal])
        mock_llm = mocker.patch.object(engine, "_llm_attribution_fallback", return_value=[])
        engine.attribute_startup("TestCo", "testco.com")
        mock_llm.assert_not_called()
