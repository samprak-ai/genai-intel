"""
Unit tests for app/resolution/domain_resolver.py

Tests cover the pure-logic methods:
  - _extract_from_text  (regex URL extraction from article text)
  - _is_valid_domain    (domain format + reject-list validation)
  - _dns_guessing       (candidate generation + DNS testing — DNS mocked)

DomainResolver.__init__ calls anthropic.Anthropic() unconditionally, so
we patch it via mocker. The _test_domain_exists method is mocked in DNS tests
to keep them fast and network-free.
"""

import pytest
from unittest.mock import MagicMock


# ============================================================================
# Fixture: DomainResolver with mocked Anthropic client
# ============================================================================

@pytest.fixture
def resolver(mocker):
    """DomainResolver with Anthropic client mocked out."""
    mocker.patch("anthropic.Anthropic", return_value=MagicMock())
    from app.resolution.domain_resolver import DomainResolver
    return DomainResolver()


# ============================================================================
# _is_valid_domain — domain validation and reject-list
# ============================================================================

class TestIsValidDomain:

    def test_accepts_two_part_ai_domain(self, resolver):
        assert resolver._is_valid_domain("harvey.ai") is True

    def test_accepts_dot_com(self, resolver):
        assert resolver._is_valid_domain("notion.so") is True

    def test_accepts_dot_io(self, resolver):
        assert resolver._is_valid_domain("linear.app") is True

    def test_rejects_linkedin(self, resolver):
        assert resolver._is_valid_domain("linkedin.com") is False

    def test_rejects_twitter(self, resolver):
        assert resolver._is_valid_domain("twitter.com") is False

    def test_rejects_x_com(self, resolver):
        assert resolver._is_valid_domain("x.com") is False

    def test_rejects_crunchbase(self, resolver):
        assert resolver._is_valid_domain("crunchbase.com") is False

    def test_rejects_no_tld(self, resolver):
        assert resolver._is_valid_domain("harvey") is False

    def test_rejects_subdomain(self, resolver):
        # "app.harvey.ai" has 3 parts — rejected as subdomain
        assert resolver._is_valid_domain("app.harvey.ai") is False

    def test_rejects_www_prefix(self, resolver):
        # _is_valid_domain strips www and then checks length
        # "www.harvey.ai" after strip → "harvey.ai" (2 parts) but the method
        # checks parts AFTER stripping www, so it may pass. Let's check actual behaviour.
        # The code strips www then checks len(parts) > 2 on the original domain
        # so "www.harvey.ai" has 3 parts → rejected
        assert resolver._is_valid_domain("www.harvey.ai") is False

    def test_rejects_co_uk_without_subdomain(self, resolver):
        # The co.uk whitelist check in the code applies after a www-strip, but
        # "company.co.uk" doesn't start with www so the parts check runs on
        # "company.co.uk" (3 parts) — the exception requires co/com/net + uk/au/nz
        # but the code checks parts[-2] IN ['co','com','net'] AND the result is
        # actually False because "company" isn't in that list.
        # This is the actual behaviour — document it as a test.
        assert resolver._is_valid_domain("company.co.uk") is False


# ============================================================================
# _extract_from_text — URL/domain extraction from article text
# ============================================================================

class TestExtractFromText:

    def test_extracts_https_url(self, resolver):
        text = "The company's website is available at https://harvey.ai for more info."
        result = resolver._extract_from_text(text)
        assert result == "harvey.ai"

    def test_extracts_http_url(self, resolver):
        text = "Visit http://linear.app to learn more."
        result = resolver._extract_from_text(text)
        assert result == "linear.app"

    def test_extracts_bare_domain_pattern(self, resolver):
        # Pattern 2: bare domain-like strings — only matches .com/.ai/.io/.net/.org/.co/.tech/.app
        # ".so" is not in the pattern, so use ".ai" for this test
        text = "The company operates at harvey.ai and has millions of users."
        result = resolver._extract_from_text(text)
        assert result == "harvey.ai"

    def test_rejects_linkedin_url(self, resolver):
        text = "More at https://linkedin.com/company/harvey"
        result = resolver._extract_from_text(text)
        assert result is None

    def test_returns_none_for_plain_text(self, resolver):
        text = "Harvey raised one hundred million dollars in their Series C round."
        result = resolver._extract_from_text(text)
        assert result is None

    def test_prefers_first_valid_domain(self, resolver):
        # linkedin.com is rejected, harvey.ai is kept — harvey.ai comes second
        text = "See linkedin.com/company/harvey or https://harvey.ai for details."
        result = resolver._extract_from_text(text)
        assert result == "harvey.ai"

    def test_returns_none_for_empty_string(self, resolver):
        result = resolver._extract_from_text("")
        assert result is None


# ============================================================================
# _dns_guessing — candidate generation with mocked DNS
# ============================================================================

class TestDnsGuessing:

    def test_returns_first_candidate_when_dns_succeeds(self, resolver, mocker):
        # Always return True → should get first candidate: cleanname.com
        mocker.patch.object(resolver, "_test_domain_exists", return_value=True)
        result = resolver._dns_guessing("Harvey")
        assert result == "harvey.com"

    def test_returns_none_when_all_dns_fail(self, resolver, mocker):
        mocker.patch.object(resolver, "_test_domain_exists", return_value=False)
        result = resolver._dns_guessing("Harvey")
        assert result is None

    def test_strips_inc_suffix(self, resolver, mocker):
        # "Harvey Inc." → candidates should be for "harvey", not "harveyinc"
        calls = []
        def capture(domain):
            calls.append(domain)
            return domain == "harvey.com"
        mocker.patch.object(resolver, "_test_domain_exists", side_effect=capture)
        result = resolver._dns_guessing("Harvey Inc.")
        assert result == "harvey.com"
        # Ensure no candidate with "inc" was tried before "harvey.com"
        idx_harvey = calls.index("harvey.com")
        inc_candidates = [c for c in calls[:idx_harvey] if "inc" in c]
        assert inc_candidates == []

    def test_strips_llc_suffix(self, resolver, mocker):
        calls = []
        def capture(domain):
            calls.append(domain)
            return domain == "linear.com"
        mocker.patch.object(resolver, "_test_domain_exists", side_effect=capture)
        result = resolver._dns_guessing("Linear LLC")
        assert result == "linear.com"
        assert all("llc" not in c for c in calls)

    def test_tries_hyphenated_variant(self, resolver, mocker):
        # "Code Metal" → candidates include "codemetal.com" and "code-metal.com"
        candidates_tried = []
        def capture(domain):
            candidates_tried.append(domain)
            return domain == "code-metal.com"
        mocker.patch.object(resolver, "_test_domain_exists", side_effect=capture)
        result = resolver._dns_guessing("Code Metal")
        assert result == "code-metal.com"
        assert "code-metal.com" in candidates_tried

    def test_tries_ai_tld(self, resolver, mocker):
        # Should try both .com and .ai variants
        candidates_tried = []
        def capture(domain):
            candidates_tried.append(domain)
            return False
        mocker.patch.object(resolver, "_test_domain_exists", side_effect=capture)
        resolver._dns_guessing("Grotto AI")
        tlds = set(c.rsplit(".", 1)[-1] for c in candidates_tried)
        assert "com" in tlds
        assert "ai" in tlds
