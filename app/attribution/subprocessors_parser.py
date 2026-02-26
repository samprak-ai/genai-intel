"""
Subprocessors Parser
Parses company trust/legal pages to extract cloud and AI provider evidence

Why this matters:
- Subprocessors lists are LEGAL documents (GDPR compliance)
- The "Purpose" column distinguishes infrastructure from AI services
- Companies cannot misrepresent this — it's a legal obligation
- Treated as STRONG signal (weight: 1.0)

Harvey example:
  Microsoft Azure  → "Cloud infrastructure, AI service provider"  → PRIMARY CLOUD
  OpenAI           → "AI service provider"                        → AI PROVIDER
  AWS              → "AI service provider"                        → AI PROVIDER (not cloud!)
  Google Cloud     → "AI service provider"                        → AI PROVIDER (not cloud!)
"""

import requests
from bs4 import BeautifulSoup
from typing import Optional
from dataclasses import dataclass, field

from app.models import AttributionSignal, ProviderType, SignalStrength


# ============================================================================
# KNOWN PROVIDER NAMES (for matching)
# ============================================================================

CLOUD_PROVIDERS = {
    "amazon web services": "AWS",
    "aws": "AWS",
    "google cloud": "GCP",
    "google cloud platform": "GCP",
    "gcp": "GCP",
    "google": "GCP",                 # Listed as just "Google" with cloud infra purpose
    "microsoft azure": "Azure",
    "microsoft corp": "Azure",       # Harvey lists it this way
    "microsoft corporation": "Azure",
    "microsoft": "Azure",
    "azure": "Azure",
}

AI_PROVIDERS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "claude": "Anthropic",
    "google ai": "Google AI",
    "vertex ai": "Google AI",
    "gemini": "Google AI",
    "google cloud platform": "Google AI",  # When listed as AI provider
    "google cloud": "Google AI",            # When listed as AI provider
    "google": "Google AI",                  # When listed as just "Google" with AI purpose
    "cohere": "Cohere",
    "mistral": "Mistral",
    "aws bedrock": "AWS Bedrock",
    "amazon bedrock": "AWS Bedrock",
    "amazon web services": "AWS Bedrock",  # When listed as AI provider
    "azure openai": "Azure OpenAI",
    "microsoft azure": "Azure OpenAI",     # When listed as AI provider
    "microsoft corp": "Azure OpenAI",      # When listed as AI provider
    "hugging face": "Hugging Face",
    "elevenlabs": "ElevenLabs",
    "perplexity": "Perplexity",
}

# Purpose keywords that indicate cloud infrastructure (vs AI service)
INFRASTRUCTURE_PURPOSES = [
    "cloud infrastructure",
    "infrastructure",
    "data hosting",
    "hosting",
    "cloud hosting",
    "cloud services",
    "cloud provider",
    "compute",
    "storage",
    "data storage",
    "cloud storage",
]

AI_SERVICE_PURPOSES = [
    "ai service",
    "ai provider",
    "ai services",
    "llm",
    "language model",
    "machine learning",
    "ml",
    "natural language",
    "generative ai",
    "large language",
]

# Common subprocessors page URL patterns
SUBPROCESSOR_URLS = [
    "/legal/subprocessors",
    "/privacy/subprocessors",
    "/security/subprocessors",
    "/subprocessors",
    "/trust",
    "/legal/sub-processors",
    "/privacy/sub-processors",
    "/data-processing/subprocessors",
    "/gdpr/subprocessors",
    "/legal/data-processing",
    "/privacy/data-subprocessors",
    "/legal",
    "/security",
]


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class SubprocessorEntry:
    """One row from a subprocessors table"""
    company_name: str
    purpose: str
    location: Optional[str] = None
    raw_text: str = ""


@dataclass
class SubprocessorParseResult:
    """Result of parsing a subprocessors page"""
    found: bool = False
    source_url: str = ""
    entries: list[SubprocessorEntry] = field(default_factory=list)
    signals: list[AttributionSignal] = field(default_factory=list)
    raw_html: str = ""


# ============================================================================
# MAIN PARSER
# ============================================================================

class SubprocessorsParser:
    """
    Finds and parses subprocessors pages to extract provider signals
    """

    def parse(self, website: str) -> SubprocessorParseResult:
        """
        Main entry point — scans all known URL patterns for a subprocessors page

        Args:
            website: Company domain e.g. "harvey.ai"

        Returns:
            SubprocessorParseResult with signals ready to feed into attribution
        """
        print(f"    🔎 Scanning for subprocessors page...")

        for path in SUBPROCESSOR_URLS:
            url = f"https://{website}{path}"

            html = self._fetch_page(url)
            if not html:
                continue

            # Check if this page actually looks like a subprocessors page
            if not self._looks_like_subprocessors_page(html):
                continue

            print(f"    ✅ Found subprocessors page: {url}")

            entries = self._extract_entries(html)
            if not entries:
                continue

            signals = self._entries_to_signals(entries, url)

            return SubprocessorParseResult(
                found=True,
                source_url=url,
                entries=entries,
                signals=signals,
                raw_html=html
            )

        print(f"    ℹ️  No subprocessors page found")
        return SubprocessorParseResult(found=False)

    # ========================================================================
    # PAGE FETCHING
    # ========================================================================

    def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page, return HTML or None"""
        try:
            response = requests.get(
                url,
                timeout=8,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True
            )
            if response.status_code == 200:
                return response.text
        except Exception:
            pass
        return None

    def _looks_like_subprocessors_page(self, html: str) -> bool:
        """
        Quick check — does this page actually contain subprocessor info?
        Avoids false positives from generic /legal or /security pages
        """
        lower = html.lower()
        keywords = ["subprocessor", "sub-processor", "data processor", "third party", "third-party"]
        provider_keywords = ["amazon", "google", "microsoft", "azure", "openai", "anthropic"]

        has_subprocessor_term = any(kw in lower for kw in keywords)
        has_provider_name = any(kw in lower for kw in provider_keywords)

        return has_subprocessor_term or has_provider_name

    # ========================================================================
    # ENTRY EXTRACTION
    # ========================================================================

    def _extract_entries(self, html: str) -> list[SubprocessorEntry]:
        """
        Extract subprocessor entries from HTML

        Handles multiple formats:
        1. HTML tables (most common)
        2. Definition lists
        3. Card-based layouts (SafeBase, Whistic, Vanta)
        """
        soup = BeautifulSoup(html, "html.parser")
        entries = []

        # Strategy 1: HTML table rows
        entries = self._parse_table(soup)
        if entries:
            return entries

        # Strategy 2: Card-based layout (SafeBase, Vanta)
        entries = self._parse_cards(soup)
        if entries:
            return entries

        # Strategy 3: Definition list
        entries = self._parse_definition_list(soup)
        if entries:
            return entries

        return entries

    def _parse_table(self, soup: BeautifulSoup) -> list[SubprocessorEntry]:
        """Parse HTML table format"""
        entries = []

        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue

            # Detect column positions from header
            header_row = rows[0]
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

            name_col = self._find_column(headers, ["company", "name", "vendor", "processor", "provider"])
            purpose_col = self._find_column(headers, ["purpose", "service", "description", "use", "category"])
            location_col = self._find_column(headers, ["location", "country", "region", "jurisdiction"])

            if name_col is None:
                continue

            # Parse data rows
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= name_col:
                    continue

                name = cells[name_col].get_text(strip=True)
                purpose = cells[purpose_col].get_text(strip=True) if purpose_col is not None and len(cells) > purpose_col else ""
                location = cells[location_col].get_text(strip=True) if location_col is not None and len(cells) > location_col else None

                if name:
                    entries.append(SubprocessorEntry(
                        company_name=name,
                        purpose=purpose,
                        location=location,
                        raw_text=row.get_text(strip=True)
                    ))

        return entries

    def _parse_cards(self, soup: BeautifulSoup) -> list[SubprocessorEntry]:
        """
        Parse card-based layouts used by SafeBase, Vanta, Whistic
        These use divs instead of tables
        """
        entries = []

        # Look for common card patterns
        card_selectors = [
            {"class": lambda c: c and any(x in " ".join(c) for x in ["card", "item", "processor", "vendor"])},
        ]

        for selector in card_selectors:
            cards = soup.find_all("div", selector)
            for card in cards:
                text = card.get_text(separator=" | ", strip=True)
                if not text:
                    continue

                # Try to find name and purpose within the card
                name_el = card.find(["h3", "h4", "strong", "b"])
                name = name_el.get_text(strip=True) if name_el else ""

                # Everything else is purpose
                purpose = text.replace(name, "").strip(" |")

                if name and len(name) > 2:
                    entries.append(SubprocessorEntry(
                        company_name=name,
                        purpose=purpose,
                        raw_text=text
                    ))

        return entries

    def _parse_definition_list(self, soup: BeautifulSoup) -> list[SubprocessorEntry]:
        """Parse definition list format (dt/dd pairs)"""
        entries = []

        dls = soup.find_all("dl")
        for dl in dls:
            terms = dl.find_all("dt")
            definitions = dl.find_all("dd")

            for term, definition in zip(terms, definitions):
                name = term.get_text(strip=True)
                purpose = definition.get_text(strip=True)

                if name:
                    entries.append(SubprocessorEntry(
                        company_name=name,
                        purpose=purpose,
                        raw_text=f"{name} {purpose}"
                    ))

        return entries

    # ========================================================================
    # SIGNAL GENERATION
    # ========================================================================

    def _entries_to_signals(
        self,
        entries: list[SubprocessorEntry],
        source_url: str
    ) -> list[AttributionSignal]:
        """
        Convert subprocessor entries into attribution signals

        Key logic:
        - Provider listed as "Cloud infrastructure" → STRONG cloud signal
        - Provider listed as "AI service provider" → STRONG AI signal
        - Provider listed as BOTH → cloud signal only (avoids double-counting)
          e.g. Microsoft Corp "AI provider and cloud provider" → Azure cloud only
               The actual AI providers (OpenAI, etc.) are listed separately

        Harvey example:
          Microsoft Corp "AI provider and cloud provider"
            → Cloud signal: Azure (STRONG)  ← infrastructure role wins
            → AI signal: skipped            ← OpenAI listed separately anyway

          OpenAI "AI service provider"
            → AI signal: OpenAI (STRONG)

          AWS    "AI service provider"
            → AI signal: AWS Bedrock (STRONG, NOT as cloud provider!)
        """
        signals = []

        # Track which cloud providers we've already emitted a cloud signal for
        # so we don't also emit an AI signal for the same entry
        cloud_providers_seen = set()

        for entry in entries:
            name_lower = entry.company_name.lower()
            purpose_lower = entry.purpose.lower()

            cloud_provider = self._match_provider(name_lower, CLOUD_PROVIDERS)
            ai_provider = self._match_provider(name_lower, AI_PROVIDERS)

            is_infra_purpose = any(kw in purpose_lower for kw in INFRASTRUCTURE_PURPOSES)
            is_ai_purpose = any(kw in purpose_lower for kw in AI_SERVICE_PURPOSES)

            # --- Cloud signal ---
            # Only tag as cloud if PURPOSE says infrastructure
            if cloud_provider and is_infra_purpose:
                signals.append(AttributionSignal(
                    provider_type=ProviderType.CLOUD,
                    provider_name=cloud_provider,
                    signal_source="subprocessors_page",
                    signal_strength=SignalStrength.STRONG,
                    evidence_text=(
                        f"{entry.company_name} listed as subprocessor "
                        f"with purpose: '{entry.purpose}'"
                    ),
                    evidence_url=source_url,
                    confidence_weight=1.0
                ))
                cloud_providers_seen.add(name_lower)

            # --- AI signal ---
            # Skip AI signal if this same entry already generated a cloud signal
            # (avoids double-counting Microsoft Corp as both Azure + Azure OpenAI)
            if name_lower in cloud_providers_seen:
                continue

            if ai_provider and (is_ai_purpose or self._is_known_ai_company(name_lower)):
                signals.append(AttributionSignal(
                    provider_type=ProviderType.AI,
                    provider_name=ai_provider,
                    signal_source="subprocessors_page",
                    signal_strength=SignalStrength.STRONG,
                    evidence_text=(
                        f"{entry.company_name} listed as subprocessor "
                        f"with purpose: '{entry.purpose}'"
                    ),
                    evidence_url=source_url,
                    confidence_weight=1.0
                ))

            # --- Edge case: Cloud provider listed with NO purpose ---
            if cloud_provider and not entry.purpose and not is_infra_purpose:
                signals.append(AttributionSignal(
                    provider_type=ProviderType.CLOUD,
                    provider_name=cloud_provider,
                    signal_source="subprocessors_page",
                    signal_strength=SignalStrength.MEDIUM,
                    evidence_text=f"{entry.company_name} listed as subprocessor (no purpose specified)",
                    evidence_url=source_url,
                    confidence_weight=0.6
                ))

        return signals

    # ========================================================================
    # HELPERS
    # ========================================================================

    def _find_column(self, headers: list[str], keywords: list[str]) -> Optional[int]:
        """Find column index by matching header keywords"""
        for i, header in enumerate(headers):
            if any(kw in header for kw in keywords):
                return i
        return None

    def _match_provider(self, name_lower: str, provider_map: dict) -> Optional[str]:
        """Match a company name to a known provider"""
        for key, canonical_name in provider_map.items():
            if key in name_lower:
                return canonical_name
        return None

    def _is_known_ai_company(self, name_lower: str) -> bool:
        """Check if this is a known AI company even without purpose context"""
        known_ai = ["openai", "anthropic", "cohere", "mistral", "elevenlabs", "perplexity"]
        return any(ai in name_lower for ai in known_ai)