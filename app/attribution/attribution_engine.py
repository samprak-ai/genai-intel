"""
Attribution Engine
Determines cloud and AI providers using multi-source signal gathering

Philosophy: Deterministic > Heuristic > LLM
Supports: Single provider OR multi-cloud/multi-AI tagging

Signal Tiers:
  Tier 1 — Deterministic (weight 1.0, or temporally weighted):
    - Partnership overrides (hardcoded known relationships)
    - Subprocessors / DPA pages (legal docs listing providers by name)
    - Public partnership announcements (temporally weighted: recent=1.0, old=0.3)
    - Cloud provider case studies / marketplace listings
    - DNS CNAME records

  Tier 2 — Strong inference (weight 0.6):
    - Cloud marketplace listings (AWS/GCP/Azure Marketplace)
    - Job postings (tech stack requirements)
    - Integration / partners pages on startup website
    - Technical docs / developer docs (SDK references, API endpoints)
    - Trust / security / privacy pages
    - HTTP headers, security.txt

  Tier 3 — Supporting (weight 0.3):
    - IP/ASN ranges
    - Tech blog posts (migration stories, architecture posts)
    - Homepage / about page keyword mentions

  Tier 4 — LLM fallback (weight mapped from LLM confidence):
    - Triggered only when attribution confidence < 60% after Tiers 1–3
    - Claude Haiku reads homepage text + funding article text
    - LLM expresses its own 0–100 confidence → mapped to weight 0.3–1.0
    - Grounded: LLM must cite a specific quote from provided text
    - signal_source = 'llm_inference'
"""

import os
import re
import json
import dns.resolver
import requests
import socket
import feedparser
from typing import Optional, List, Tuple
from datetime import datetime
from collections import defaultdict
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus, urlparse
import anthropic
import urllib3

from app.models import (
    AttributionSignal, Attribution, ProviderEntry,
    ProviderType, SignalStrength, EntrenchmentLevel,
    SignalWeights, PARTNERSHIP_OVERRIDES, NOT_APPLICABLE_COMPANIES,
    INVESTOR_CLOUD_PRIORS, FOUNDER_CLOUD_PRIORS,
)
from app.attribution.subprocessors_parser import SubprocessorsParser

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# How much stronger one provider must be to be "primary" vs "multi"
MULTI_PROVIDER_THRESHOLD = 0.3

# Reusable keyword maps for cloud and AI provider detection
CLOUD_KEYWORDS = {
    'AWS': [
        'amazon web services', ' aws ', 'aws.amazon.com',
        ' ec2 ', ' s3 ', ' ecs ', ' eks ', 'lambda', 'cloudfront',
        'dynamodb', 'redshift', 'sagemaker', 'aws fargate',
        'amazon rds', 'amazon aurora', 'amazon sqs', 'amazon sns',
        'aws glue', 'amazon kinesis', 'aws cdk',
    ],
    'GCP': [
        'google cloud', ' gcp ', 'cloud.google.com',
        'bigquery', 'cloud run', 'cloud functions', 'gke ',
        'google kubernetes', 'cloud spanner', 'vertex ai',
        'cloud storage', 'pub/sub', 'google compute engine',
        'cloud sql', 'google cloud platform',
    ],
    'Azure': [
        'microsoft azure', ' azure ', 'azure.microsoft.com',
        'azure devops', 'azure functions', 'cosmos db', 'azure blob',
        'azure kubernetes', ' aks ', 'azure sql', 'azure cognitive',
        'azure openai', 'azure ml', 'azure pipelines',
    ],
}

AI_KEYWORDS = {
    'OpenAI': [
        'openai', 'chatgpt', 'gpt-4', 'gpt-3', 'dall-e', 'whisper',
        'openai api', 'gpt-4o', 'o1-preview', 'o1-mini',
    ],
    'Anthropic': [
        'anthropic', ' claude ', 'claude api', 'claude 3',
        'claude sonnet', 'claude opus', 'claude haiku',
    ],
    'Google AI': [
        'gemini', 'vertex ai', 'google ai studio', 'palm ',
        'gemini pro', 'gemini ultra', 'gemma ',
    ],
    'Cohere': [
        'cohere', 'cohere api', 'command-r', 'cohere embed',
    ],
    'Mistral': [
        'mistral', 'mistral ai', 'mixtral', 'mistral api',
    ],
}


def _keyword_scan(text: str, keyword_map: dict) -> dict:
    """
    Scan text for provider keywords. Returns {provider_name: [matched_keywords]}.
    Only returns providers that actually matched.
    """
    text_lower = text.lower()
    matches = {}
    for provider, keywords in keyword_map.items():
        found = [kw for kw in keywords if kw in text_lower]
        if found:
            matches[provider] = found
    return matches


class AttributionEngine:
    """
    Determines cloud and AI providers through evidence gathering

    Process:
    1. Check partnership overrides (deterministic)
    2. Parse subprocessors page (deterministic, highest value content signal)
    3. Check cloud provider case studies / marketplace
    4. Gather infrastructure signals (DNS, headers, IP)
    5. Scan job postings for tech stack signals
    6. Scan website content (integrations, docs, trust, blog, homepage)
    7. Score all signals — decide single vs multi-cloud
    8. Return Attribution with full evidence trail
    """

    HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; research bot)'}
    TIMEOUT = 6

    def __init__(self):
        self.anthropic_client = None
        self.subprocessors_parser = SubprocessorsParser()

        api_key = os.getenv('ANTHROPIC_API_KEY')
        if api_key:
            self.anthropic_client = anthropic.Anthropic(api_key=api_key)

    # Confidence threshold below which we invoke the LLM fallback (Tier 4)
    LLM_FALLBACK_THRESHOLD = 0.60

    def attribute_startup(
        self,
        company_name: str,
        website: str,
        article_text: Optional[str] = None,
        lead_investors: Optional[List[str]] = None,
        founder_background: Optional[List[str]] = None,
        evidence_urls: Optional[List[str]] = None,
    ) -> Tuple[Optional[Attribution], Optional[Attribution]]:
        """
        Main attribution method

        Args:
            company_name:       Company name e.g. "Harvey"
            website:            Domain e.g. "harvey.ai"
            article_text:       Optional funding announcement article text — passed to
                                the LLM fallback for additional context.
            lead_investors:     List of investor names from FundingEvent.lead_investors.
                                Used for investor-prior inference (WEAK signal, weight 0.3).
            founder_background: List of prior employer keywords (e.g. ["Google Brain"]).
                                Used for founder-prior inference (WEAK signal, weight 0.3).
            evidence_urls:      List of specific URLs to fetch and scan as Tier 1 signals.
                                Use for known partnership / press release pages.

        Returns:
            (cloud_attribution, ai_attribution) — each may be single or multi
        """
        print(f"\n🔍 Attributing: {company_name} ({website})")

        # 1a. Not-applicable check — company is itself a provider or attribution
        #     is structurally meaningless for this entity type
        na_entry = NOT_APPLICABLE_COMPANIES.get(company_name)
        if na_entry:
            cloud_na_note = na_entry.get("cloud")
            ai_na_note    = na_entry.get("ai")
            # Only short-circuit the fields marked N/A; let normal flow handle others
            if cloud_na_note is not None or ai_na_note is not None:
                print(f"  ℹ️  Not-applicable entry found — some fields marked N/A")
                cloud_result = self._create_na_attribution(ProviderType.CLOUD, cloud_na_note) if cloud_na_note else None
                ai_result    = self._create_na_attribution(ProviderType.AI,    ai_na_note)    if ai_na_note    else None
                # If only one side is N/A, still gather signals for the other
                if cloud_result is None or ai_result is None:
                    signals = self._gather_all_signals(
                        company_name, website,
                        lead_investors=lead_investors or [],
                        founder_background=founder_background or [],
                        evidence_urls=evidence_urls or [],
                    )
                    if cloud_result is None:
                        cloud_signals = [s for s in signals if s.provider_type == ProviderType.CLOUD]
                        cloud_result  = self._calculate_attribution(cloud_signals, ProviderType.CLOUD)
                    if ai_result is None:
                        ai_signals = [s for s in signals if s.provider_type == ProviderType.AI]
                        ai_result  = self._calculate_attribution(ai_signals, ProviderType.AI)
                return cloud_result, ai_result

        # 1b. Partnership overrides — always check first
        override = PARTNERSHIP_OVERRIDES.get(company_name)
        if override:
            print(f"  ✅ Partnership override found")
            return (
                self._create_override_attribution(company_name, override.get('cloud'), ProviderType.CLOUD),
                self._create_override_attribution(company_name, override.get('ai'), ProviderType.AI)
            )

        # 2. Gather all signals (Tiers 1–3)
        signals = self._gather_all_signals(
            company_name, website,
            lead_investors=lead_investors or [],
            founder_background=founder_background or [],
            evidence_urls=evidence_urls or [],
        )

        # 3. Separate by type and calculate attributions
        cloud_signals = [s for s in signals if s.provider_type == ProviderType.CLOUD]
        ai_signals    = [s for s in signals if s.provider_type == ProviderType.AI]

        cloud_attribution = self._calculate_attribution(cloud_signals, ProviderType.CLOUD)
        ai_attribution    = self._calculate_attribution(ai_signals, ProviderType.AI)

        # 4. LLM fallback (Tier 4) — only when deterministic signals are weak
        cloud_needs_llm = (
            cloud_attribution is None or
            cloud_attribution.confidence < self.LLM_FALLBACK_THRESHOLD
        )
        ai_needs_llm = (
            ai_attribution is None or
            ai_attribution.confidence < self.LLM_FALLBACK_THRESHOLD
        )

        if (cloud_needs_llm or ai_needs_llm) and self.anthropic_client:
            needed = []
            if cloud_needs_llm:
                needed.append('cloud')
            if ai_needs_llm:
                needed.append('ai')
            print(f"  🤖 LLM fallback triggered for: {', '.join(needed)} "
                  f"(confidence below {self.LLM_FALLBACK_THRESHOLD:.0%})")

            llm_signals = self._llm_attribution_fallback(
                company_name=company_name,
                website=website,
                article_text=article_text,
                need_cloud=cloud_needs_llm,
                need_ai=ai_needs_llm,
            )

            if llm_signals:
                print(f"    ✅ LLM inference: {len(llm_signals)} signals")
                # Merge LLM signals in and recalculate only the types that needed help
                if cloud_needs_llm:
                    new_cloud_sigs = cloud_signals + [
                        s for s in llm_signals if s.provider_type == ProviderType.CLOUD
                    ]
                    cloud_attribution = self._calculate_attribution(new_cloud_sigs, ProviderType.CLOUD)
                if ai_needs_llm:
                    new_ai_sigs = ai_signals + [
                        s for s in llm_signals if s.provider_type == ProviderType.AI
                    ]
                    ai_attribution = self._calculate_attribution(new_ai_sigs, ProviderType.AI)
            else:
                print(f"    ℹ️  LLM found no additional signals")

        # 5. Log summary
        if cloud_attribution:
            print(f"  ☁️  Cloud: {cloud_attribution.display_name} "
                  f"({cloud_attribution.confidence:.0%} confidence)")
        if ai_attribution:
            print(f"  🤖 AI:    {ai_attribution.display_name} "
                  f"({ai_attribution.confidence:.0%} confidence)")

        return cloud_attribution, ai_attribution

    # ========================================================================
    # SIGNAL GATHERING
    # ========================================================================

    # Known hosted-platform CNAME suffixes — sites on these builders have no
    # meaningful cloud infrastructure signal of their own.
    HOSTED_PLATFORM_CNAMES = [
        'wixdns.net', 'wix.com',
        'squarespace.com', 'sqsp.net',
        'webflow.io',
        'myshopify.com', 'shopify.com',
        'github.io',
        'netlify.app', 'netlify.com',
        'vercel.app',                    # Vercel is real infra — kept narrow
        'wpengine.com',
        'kinsta.cloud',
    ]

    def _resolve_canonical_domain(self, website: str) -> str:
        """
        Follow the redirect on the root path to get the real hostname.

        Many sites redirect bare domain → www (or vice-versa).  All subsequent
        HTTP-based checks use this canonical hostname so that per-path URLs
        (e.g. /pricing, /partners) are built against the domain that actually
        serves them, rather than getting 404'd on the pre-redirect domain.

        Examples
        --------
        cogentsecurity.com  →  www.cogentsecurity.com
        harvey.ai           →  harvey.ai   (no redirect, unchanged)
        """
        try:
            r = requests.get(
                f'https://{website}', timeout=self.TIMEOUT,
                headers=self.HEADERS, verify=False, allow_redirects=True
            )
            parsed = urlparse(r.url)
            # netloc includes port (e.g. "www.ascent.com:443") — strip it
            canonical = parsed.hostname or parsed.netloc.split(':')[0]
            if canonical and canonical != website:
                print(f"    ↪  Canonical domain: {website} → {canonical}")
            return canonical or website
        except Exception:
            return website

    def _gather_all_signals(
        self,
        company_name: str,
        website: str,
        lead_investors: List[str] = [],
        founder_background: List[str] = [],
        evidence_urls: List[str] = [],
    ) -> List[AttributionSignal]:
        """Gather all signals across tiers"""
        all_signals = []
        print(f"  📡 Gathering signals...")

        # Resolve the canonical hostname once up-front (handles www. redirects).
        # All HTTP-based checks use `canonical` so that per-path URLs are built
        # against the domain that actually serves them.
        canonical = self._resolve_canonical_domain(website)

        # ---- Tier 1: Deterministic / Strong ----

        # Direct evidence URLs (caller-supplied known partnership/press release pages)
        if evidence_urls:
            ev_signals = self._check_evidence_urls(company_name, evidence_urls)
            all_signals.extend(ev_signals)
            if ev_signals:
                print(f"    ✅ Direct evidence URLs: {len(ev_signals)} signals")

        # Subprocessors page (legal document — highest value content signal)
        sp_result = self.subprocessors_parser.parse(canonical)
        if sp_result.found:
            all_signals.extend(sp_result.signals)
            print(f"    ✅ Subprocessors: {len(sp_result.signals)} signals")

        # Cloud provider case studies & marketplace listings
        cs_signals = self._check_cloud_case_studies(company_name)
        all_signals.extend(cs_signals)
        if cs_signals:
            print(f"    ✅ Case studies/marketplace: {len(cs_signals)} signals")

        # Public partnership announcements (Google News search)
        partner_signals = self._check_partnership_announcements(company_name)
        all_signals.extend(partner_signals)
        if partner_signals:
            print(f"    ✅ Partnership announcements: {len(partner_signals)} signals")

        # DNS CNAME (pass both bare domain and canonical for www. coverage)
        dns_signals = self._check_dns_cname(website, canonical)
        all_signals.extend(dns_signals)
        if dns_signals:
            print(f"    ✅ DNS: {len(dns_signals)} signals")

        # ---- Tier 2: Strong Inference ----

        # Cloud marketplace listings (scan startup website for marketplace links)
        mkt_signals = self._check_cloud_marketplaces(company_name, canonical)
        all_signals.extend(mkt_signals)
        if mkt_signals:
            print(f"    ✅ Cloud marketplaces: {len(mkt_signals)} signals")

        # HTTP headers
        header_signals = self._check_http_headers(canonical)
        all_signals.extend(header_signals)
        if header_signals:
            print(f"    ✅ HTTP headers: {len(header_signals)} signals")

        # Job postings
        job_signals = self._check_job_postings(company_name)
        all_signals.extend(job_signals)
        if job_signals:
            print(f"    ✅ Job postings: {len(job_signals)} signals")

        # Website content scanning (integrations, docs, trust, blog)
        content_signals = self._scan_website_content(canonical)
        all_signals.extend(content_signals)
        if content_signals:
            print(f"    ✅ Website content: {len(content_signals)} signals")

        # security.txt
        security_signals = self._check_security_txt(canonical)
        all_signals.extend(security_signals)
        if security_signals:
            print(f"    ✅ security.txt: {len(security_signals)} signals")

        # ---- Tier 3: Supporting ----

        # IP/ASN lookup
        ip_signals = self._check_ip_asn(canonical)
        all_signals.extend(ip_signals)
        if ip_signals:
            print(f"    ✅ IP/ASN: {len(ip_signals)} signals")

        # Investor-prior inference (WEAK)
        if lead_investors:
            inv_signals = self._check_investor_signals(company_name, lead_investors)
            all_signals.extend(inv_signals)
            if inv_signals:
                print(f"    ✅ Investor priors: {len(inv_signals)} signals")

        # Founder-background inference (WEAK)
        if founder_background:
            founder_signals = self._check_founder_signals(company_name, founder_background)
            all_signals.extend(founder_signals)
            if founder_signals:
                print(f"    ✅ Founder background priors: {len(founder_signals)} signals")

        total = len(all_signals)
        if total == 0:
            print(f"    ℹ️  No signals found")
        print(f"    Total signals gathered: {total}")
        return all_signals

    # ========================================================================
    # ATTRIBUTION CALCULATION (Multi-cloud aware)
    # ========================================================================

    def _calculate_attribution(
        self,
        signals: List[AttributionSignal],
        provider_type: ProviderType
    ) -> Optional[Attribution]:
        """
        Calculate attribution from signals — handles single AND multi-provider

        Decision logic:
          1. Score each provider by summing signal weights
          2. If only one provider found → single
          3. If multiple providers and gap between top two is SMALL → multi
          4. If multiple providers and gap is LARGE → primary + secondary
        """
        if not signals:
            return None

        # Group signals and scores by provider
        provider_scores: dict[str, float] = defaultdict(float)
        provider_signals: dict[str, list] = defaultdict(list)

        for signal in signals:
            provider_scores[signal.provider_name] += signal.confidence_weight
            provider_signals[signal.provider_name].append(signal)

        # Sort providers by score descending
        ranked = sorted(provider_scores.items(), key=lambda x: x[1], reverse=True)
        total_score = sum(provider_scores.values())

        # Build ProviderEntry for each detected provider
        provider_entries = []
        for name, score in ranked:
            entrenchment = Attribution.calculate_entrenchment(score)
            provider_entries.append(ProviderEntry(
                provider_name=name,
                role=self._infer_role(name, provider_signals[name], provider_type),
                confidence=score / total_score,
                entrenchment=entrenchment,
                raw_score=score,
                signals=provider_signals[name]
            ))

        # --- Decide: single provider vs multi ---
        if len(ranked) == 1:
            winner = provider_entries[0]
            return Attribution(
                provider_type=provider_type,
                is_multi=False,
                primary_provider=winner.provider_name,
                providers=provider_entries,
                confidence=1.0,
                evidence_count=len(signals),
                signals=signals
            )

        top_score    = ranked[0][1]
        second_score = ranked[1][1]
        gap          = top_score - second_score

        if gap < MULTI_PROVIDER_THRESHOLD:
            return Attribution(
                provider_type=provider_type,
                is_multi=True,
                primary_provider=None,
                providers=provider_entries,
                confidence=min(round(total_score / (len(ranked) * 1.0), 2), 1.0),
                evidence_count=len(signals),
                signals=signals
            )
        else:
            winner = provider_entries[0]
            return Attribution(
                provider_type=provider_type,
                is_multi=False,
                primary_provider=winner.provider_name,
                providers=provider_entries,
                confidence=min(round(top_score / total_score, 2), 1.0),
                evidence_count=len(signals),
                signals=signals
            )

    def _infer_role(
        self,
        provider_name: str,
        signals: List[AttributionSignal],
        provider_type: ProviderType
    ) -> str:
        """Infer the role description from signal sources"""
        sources = {s.signal_source for s in signals}
        if "subprocessors_page" in sources:
            for s in signals:
                if s.signal_source == "subprocessors_page":
                    return s.evidence_text.split("purpose:")[1].strip(" '") if "purpose:" in s.evidence_text else "Subprocessor"
        if provider_type == ProviderType.CLOUD:
            return "Cloud infrastructure"
        return "AI service provider"

    # ========================================================================
    # TIER 1: DETERMINISTIC SIGNALS (weight 1.0)
    # ========================================================================

    def _check_dns_cname(self, website: str, canonical: Optional[str] = None) -> List[AttributionSignal]:
        """
        Check DNS CNAME records — reveals actual hosting provider.

        Checks both the bare domain AND the canonical domain (e.g. www.) because
        CNAME records are almost always on the www subdomain, not the apex domain
        (apex domains use A/ALIAS records per DNS spec).

        Also detects hosted-platform CNAMEs (Wix, Squarespace, etc.) and skips
        them — they carry no meaningful cloud infrastructure signal.
        """
        signals = []

        cloud_cname_map = {
            'amazonaws.com':               'AWS',
            'cloudfront.net':              'AWS',
            'elb.amazonaws.com':           'AWS',
            'googleusercontent.com':       'GCP',
            'ghs.googlehosted.com':        'GCP',
            'c.storage.googleapis.com':    'GCP',
            'run.app':                     'GCP',   # Cloud Run
            'azurewebsites.net':           'Azure',
            'azure.com':                   'Azure',
            'azureedge.net':               'Azure',
            'trafficmanager.net':          'Azure',
        }

        # De-duplicate: check bare domain + canonical, but don't repeat if same
        domains_to_check = [website]
        if canonical and canonical != website:
            domains_to_check.append(canonical)

        seen_cnames: set = set()

        for domain in domains_to_check:
            try:
                answers = dns.resolver.resolve(domain, 'CNAME')
                for rdata in answers:
                    cname = str(rdata.target).lower().rstrip('.')
                    if cname in seen_cnames:
                        continue
                    seen_cnames.add(cname)

                    # Skip hosted-platform CNAMEs — not a cloud infra signal
                    if any(platform in cname for platform in self.HOSTED_PLATFORM_CNAMES):
                        print(f"    ℹ️  Hosted platform CNAME detected ({cname}) — skipping")
                        continue

                    for pattern, provider in cloud_cname_map.items():
                        if pattern in cname:
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.CLOUD,
                                provider_name=provider,
                                signal_source='dns_cname',
                                signal_strength=SignalStrength.STRONG,
                                evidence_text=f'CNAME points to {cname}',
                                confidence_weight=1.0
                            ))
                            break  # one provider per CNAME record

            except dns.resolver.NoAnswer:
                pass
            except Exception:
                pass  # DNS failures are common, don't clutter output

        return signals

    def _check_cloud_case_studies(self, company_name: str) -> List[AttributionSignal]:
        """
        Check if the startup appears in cloud provider case studies or marketplace.
        These are Tier 1 signals — if AWS/GCP/Azure published a case study about
        the company, it's a strong signal they're a customer.
        
        IMPORTANT: Search pages echo the query string back in the HTML even when
        there are no results. We must check for actual result content, not just
        whether the company name appears on the page.
        """
        signals = []
        encoded_name = quote_plus(company_name)
        name_lower = company_name.lower()

        # --- AWS Case Studies ---
        try:
            url = f'https://aws.amazon.com/solutions/case-studies/?customer-references-cards.q={encoded_name}'
            r = requests.get(url, timeout=self.TIMEOUT, headers=self.HEADERS, verify=False)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                # AWS case study results appear in cards with specific classes
                # Look for the company name inside result card elements, not in
                # the search bar or page chrome
                cards = soup.find_all(['div', 'a'], class_=lambda c: c and any(
                    x in ' '.join(c) for x in ['card', 'result', 'case-study', 'customer-reference']
                ))
                card_text = ' '.join(card.get_text(separator=' ', strip=True) for card in cards).lower()
                if name_lower in card_text:
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name='AWS',
                        signal_source='case_study',
                        signal_strength=SignalStrength.STRONG,
                        evidence_text=f'{company_name} found on AWS case study page',
                        evidence_url=url,
                        confidence_weight=1.0
                    ))
        except Exception:
            pass

        # --- AWS Marketplace (Google News RSS site: search) ---
        # The AWS Marketplace search page is JS-rendered — product listing URLs
        # are not present in the server response. Instead we use Google News RSS
        # with a site: operator to find indexed Marketplace listing pages for
        # this company, then fetch and verify the listing directly.
        if not any(s.provider_name == 'AWS' for s in signals):
            try:
                q = quote_plus(f'"{company_name}" site:aws.amazon.com/marketplace')
                rss_url = f'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'
                feed = feedparser.parse(rss_url, request_headers={'User-Agent': 'Mozilla/5.0'})

                for entry in feed.entries[:5]:
                    link = entry.link
                    # Google News wraps URLs — extract the real one
                    if 'aws.amazon.com/marketplace' not in link:
                        continue
                    try:
                        lr = requests.get(
                            link, timeout=self.TIMEOUT,
                            headers=self.HEADERS, verify=False
                        )
                        if lr.status_code == 200 and name_lower in lr.text.lower():
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.CLOUD,
                                provider_name='AWS',
                                signal_source='aws_marketplace',
                                signal_strength=SignalStrength.STRONG,
                                evidence_text=f'{company_name} listed on AWS Marketplace',
                                evidence_url=link,
                                confidence_weight=1.0
                            ))
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        # --- AWS Marketplace (website reverse-lookup) ---
        # Some companies link to their Marketplace listing from their own site;
        # _check_cloud_marketplaces covers that path. As an additional catch,
        # if we have the website domain we check whether the Marketplace listing
        # page references that domain — confirming it's the same company.
        # This is handled inside _check_cloud_marketplaces via outbound link scan.

        # --- GCP Customers ---
        try:
            url = f'https://cloud.google.com/customers?hl=en&q={encoded_name}'
            r = requests.get(url, timeout=self.TIMEOUT, headers=self.HEADERS, verify=False)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                # GCP customer results appear in card/tile elements
                cards = soup.find_all(['div', 'a'], class_=lambda c: c and any(
                    x in ' '.join(c) for x in ['card', 'result', 'customer', 'tile']
                ))
                card_text = ' '.join(card.get_text(separator=' ', strip=True) for card in cards).lower()
                if name_lower in card_text:
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name='GCP',
                        signal_source='case_study',
                        signal_strength=SignalStrength.STRONG,
                        evidence_text=f'{company_name} found on GCP customer page',
                        evidence_url=url,
                        confidence_weight=1.0
                    ))
        except Exception:
            pass

        # --- Azure Case Studies ---
        try:
            url = f'https://customers.microsoft.com/en-us/search?sq={encoded_name}&ff=&p=0&so=story_publish_date%20desc'
            r = requests.get(url, timeout=self.TIMEOUT, headers=self.HEADERS, verify=False)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                cards = soup.find_all(['div', 'a'], class_=lambda c: c and any(
                    x in ' '.join(c) for x in ['card', 'result', 'story', 'customer']
                ))
                card_text = ' '.join(card.get_text(separator=' ', strip=True) for card in cards).lower()
                if name_lower in card_text:
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name='Azure',
                        signal_source='case_study',
                        signal_strength=SignalStrength.STRONG,
                        evidence_text=f'{company_name} found on Azure customer page',
                        evidence_url=url,
                        confidence_weight=1.0
                    ))
        except Exception:
            pass

        return signals

    # Maps provider search terms → (ProviderType, canonical provider name).
    # Used by _check_partnership_announcements to classify matched articles.
    # Order matters: more specific terms first so 'Google Cloud' beats 'Google'.
    _PROVIDER_TERM_MAP: list = [
        # Cloud — major hyperscalers
        ('amazon web services', ProviderType.CLOUD, 'AWS'),
        (' aws ',               ProviderType.CLOUD, 'AWS'),
        ('amazon cloud',        ProviderType.CLOUD, 'AWS'),
        ('amazon',             ProviderType.CLOUD, 'AWS'),
        ('google cloud',       ProviderType.CLOUD, 'GCP'),
        ('microsoft azure',    ProviderType.CLOUD, 'Azure'),
        (' azure ',            ProviderType.CLOUD, 'Azure'),
        ('microsoft',          ProviderType.CLOUD, 'Azure'),
        # Cloud — specialist GPU/AI cloud providers
        ('coreweave',          ProviderType.CLOUD, 'CoreWeave'),
        ('lambda labs',        ProviderType.CLOUD, 'Lambda Labs'),
        ('vast.ai',            ProviderType.CLOUD, 'Vast.ai'),
        ('oracle cloud',       ProviderType.CLOUD, 'OCI'),
        # AI
        ('openai',             ProviderType.AI,    'OpenAI'),
        ('anthropic',          ProviderType.AI,    'Anthropic'),
        ('vertex ai',          ProviderType.AI,    'Google AI'),
        ('gemini',             ProviderType.AI,    'Google AI'),
        ('cohere',             ProviderType.AI,    'Cohere'),
        ('mistral',            ProviderType.AI,    'Mistral'),
    ]

    def _check_partnership_announcements(self, company_name: str) -> List[AttributionSignal]:
        """
        Search for public partnership / deal announcements between the startup
        and cloud/AI providers.

        Strategy — two complementary sources, tried in order:

        1. Google News RSS (primary)
           • One broad query per company — fetches up to 20 news items
           • Covers all providers in a single HTTP request
           • Returns RFC-2822 pub dates → full temporal weighting
           • Limitation: ~12-month recency window for most queries

        2. DuckDuckGo HTML search (fallback, per-provider)
           • No recency window — finds older articles (e.g. 2023 GCP deals)
           • One DDG request per provider that wasn't found via Google News
           • Includes a 1-second delay between requests to avoid bot detection
           • No pub dates in DDG HTML → defaults to MEDIUM weight (0.6)

        Temporal weighting (applied where pub date is known):
          - Within last 12 months → STRONG (1.0)
          - 1–2 years old         → MEDIUM (0.6)
          - 2+ years old          → WEAK   (0.3)
          - Unknown date          → MEDIUM (0.6)
        """
        import time as _time

        signals = []
        company_lower = company_name.lower()
        found_providers: set = set()   # track which providers already have a signal

        # Reject titles where the company name is part of a *different* compound
        # entity — the main pattern being "the {company}" (e.g. "Rent the Runway").
        # We intentionally keep this narrow: only the 'the' article preceding the
        # company name is a reliable collision signal.  Prepositions like 'with',
        # 'for', 'of' are NOT reliable — "Deal With Runway" is a valid article.
        _collision_prefix_re = re.compile(
            rf'(?:^|[\s,])the\s+{re.escape(company_lower)}',
            re.IGNORECASE,
        )

        def _temporal_weight(pub_date_str: str):
            """Parse RFC-2822 date string and return (strength, weight, label)."""
            try:
                import email.utils
                pub_dt = datetime(*email.utils.parsedate(pub_date_str)[:6])
                age_days = (datetime.now() - pub_dt).days
                if age_days <= 365:
                    return SignalStrength.STRONG, 1.0, f'{pub_dt.strftime("%Y-%m-%d")}'
                elif age_days <= 730:
                    return SignalStrength.MEDIUM, 0.6, f'{pub_dt.strftime("%Y-%m-%d")}'
                else:
                    return SignalStrength.WEAK, 0.3, f'{pub_dt.strftime("%Y-%m-%d")}'
            except Exception:
                return SignalStrength.MEDIUM, 0.6, 'unknown date'

        def _classify_entry(title: str, summary: str = '') -> list:
            """
            Return list of (ptype, provider) tuples matched in the given text.
            Uses _PROVIDER_TERM_MAP — most-specific terms first.
            """
            text = f' {title} {summary} '.lower()
            matched = []
            seen_providers = set()
            for term, ptype, provider in self._PROVIDER_TERM_MAP:
                if term in text and provider not in seen_providers:
                    matched.append((ptype, provider))
                    seen_providers.add(provider)
            return matched

        def _is_valid_title(title: str) -> bool:
            """Company name must appear and not be part of a name-collision."""
            tl = title.lower()
            return company_lower in tl and not _collision_prefix_re.search(tl)

        # ------------------------------------------------------------------ #
        # SOURCE 1: Google News RSS (single broad query — all providers)      #
        #                                                                      #
        # IMPORTANT: feedparser's built-in HTTP client fails silently on       #
        # news.google.com (returns 0 entries). We use requests to fetch the   #
        # raw bytes and pass them directly to feedparser.parse(content).       #
        # ------------------------------------------------------------------ #
        action_terms = (
            'partnership OR announces OR contract OR deal OR signed '
            'OR investment OR selects OR adopts OR deploys OR integrates OR launches'
        )
        gnews_q = quote_plus(f'"{company_name}" {action_terms}')
        gnews_url = (
            f'https://news.google.com/rss/search'
            f'?q={gnews_q}&hl=en-US&gl=US&ceid=US:en'
        )

        try:
            gnews_r = requests.get(
                gnews_url, timeout=self.TIMEOUT,
                headers={'User-Agent': 'Mozilla/5.0'},
                verify=False
            )
            if gnews_r.status_code == 200 and gnews_r.content:
                feed = feedparser.parse(gnews_r.content)

                for entry in feed.entries[:100]:
                    title   = entry.get('title', '')
                    summary = entry.get('summary', '')
                    link    = entry.get('link', '')
                    pub     = entry.get('published', '')

                    if not _is_valid_title(title):
                        continue

                    matches = _classify_entry(title, summary)
                    strength, weight, age_label = _temporal_weight(pub)

                    for ptype, provider in matches:
                        if provider not in found_providers:
                            found_providers.add(provider)
                            signals.append(AttributionSignal(
                                provider_type=ptype,
                                provider_name=provider,
                                signal_source='partnership_announcement',
                                signal_strength=strength,
                                evidence_text=f'Partnership news ({age_label}): {title[:100]}',
                                evidence_url=link,
                                confidence_weight=weight
                            ))

        except Exception:
            pass

        # ------------------------------------------------------------------ #
        # SOURCE 2: DuckDuckGo HTML search (per-provider fallback)            #
        # Runs only for providers not already found via Google News.           #
        # ------------------------------------------------------------------ #
        # Providers not yet found — build targeted DDG queries for each
        provider_queries = {
            'AWS':        (ProviderType.CLOUD, ['Amazon Web Services', 'AWS', 'Amazon']),
            'GCP':        (ProviderType.CLOUD, ['Google Cloud']),
            'Azure':      (ProviderType.CLOUD, ['Microsoft Azure', 'Azure', 'Microsoft']),
            'CoreWeave':  (ProviderType.CLOUD, ['CoreWeave']),
            'OCI':        (ProviderType.CLOUD, ['Oracle Cloud']),
            'OpenAI':     (ProviderType.AI,    ['OpenAI']),
            'Anthropic':  (ProviderType.AI,    ['Anthropic']),
            'Google AI':  (ProviderType.AI,    ['Vertex AI', 'Gemini']),
            'Cohere':     (ProviderType.AI,    ['Cohere']),
            'Mistral':    (ProviderType.AI,    ['Mistral']),
        }

        # DDG User-Agent rotation — helps avoid bot detection
        _ddg_uas = [
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]
        _ua_idx = [0]   # mutable counter for rotation

        def _next_ua():
            ua = _ddg_uas[_ua_idx[0] % len(_ddg_uas)]
            _ua_idx[0] += 1
            return ua

        ddg_first_request = True

        for provider, (ptype, query_terms) in provider_queries.items():
            if provider in found_providers:
                continue   # already have a signal from Google News

            # Polite delay between DDG requests to avoid rate-limiting.
            # Skip before the very first DDG call in this run.
            if not ddg_first_request:
                _time.sleep(1.5)
            ddg_first_request = False

            # DDG query rules (learned from testing):
            #   ✅ Unquoted terms work fine
            #   ✅ Single-word quoted company name works fine
            #   ❌ Multi-word quoted company name → 202 challenge
            #   ❌ Quoting BOTH company and provider → 202 challenge
            #   ❌ Long OR chains combined with any quotes → 202 challenge
            # Strategy:
            #   - For single-word names: quote the name ("Runway" Amazon)
            #   - For multi-word names: quote first word only ("Fundamental" AI Amazon)
            #   - Keep provider term bare, use 2-3 simple action keywords
            company_words = company_name.split()
            if len(company_words) == 1:
                company_ddg = f'"{company_name}"'
            else:
                # Quote only the first word — prevents 202 on multi-word names
                company_ddg = f'"{company_words[0]}" {" ".join(company_words[1:])}'

            primary_term = query_terms[0]  # most specific / canonical term
            ddg_query = quote_plus(
                f'{company_ddg} {primary_term} partnership deal contract'
            )
            ddg_url = f'https://html.duckduckgo.com/html/?q={ddg_query}'

            try:
                r = requests.get(
                    ddg_url, timeout=self.TIMEOUT,
                    headers={'User-Agent': _next_ua()},
                    verify=False
                )
                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(r.text, 'html.parser')

                # DDG HTML layout: <div class="result"> contains
                # <a class="result__a"> (title + href) and optionally
                # <a class="result__url"> and <a class="result__snippet">
                results = soup.find_all('div', class_='result')
                if not results:
                    results = soup.find_all('a', class_='result__a')

                for result in results[:5]:
                    title_tag  = (result.find('a', class_='result__a')
                                  if result.name == 'div' else result)
                    url_tag    = (result.find('a', class_='result__url')
                                  if result.name == 'div' else None)
                    snippet_tag = (result.find('a', class_='result__snippet')
                                   if result.name == 'div' else None)

                    if not title_tag:
                        continue

                    title      = title_tag.get_text(strip=True)
                    snippet    = snippet_tag.get_text(strip=True) if snippet_tag else ''
                    result_url = (title_tag.get('href', '')
                                  or (url_tag.get_text(strip=True) if url_tag else ''))

                    if not _is_valid_title(title):
                        continue

                    # At least one provider term must appear in title or snippet
                    entry_text = f'{title} {snippet}'.lower()
                    provider_terms_lower = [t.lower() for t in query_terms]
                    if not any(t in entry_text for t in provider_terms_lower):
                        continue

                    if provider not in found_providers:
                        found_providers.add(provider)
                        # DDG HTML has no pub dates → MEDIUM weight
                        signals.append(AttributionSignal(
                            provider_type=ptype,
                            provider_name=provider,
                            signal_source='partnership_announcement',
                            signal_strength=SignalStrength.MEDIUM,
                            evidence_text=f'Partnership news (unknown date): {title[:100]}',
                            evidence_url=result_url,
                            confidence_weight=0.6
                        ))
                        break   # one signal per provider per source

            except Exception:
                continue

        return signals

    # ========================================================================
    # TIER 2: STRONG INFERENCE (weight 0.6)
    # ========================================================================

    def _check_cloud_marketplaces(self, company_name: str, website: str) -> List[AttributionSignal]:
        """
        Check if the startup is listed on cloud provider marketplaces.

        Scored as MEDIUM (0.6) — being listed indicates integration but
        startups often list on multiple marketplaces for distribution.

        Approach: Scan the startup's own website for outbound links to
        cloud marketplace listing pages. Startups that list on AWS/Azure/GCP
        Marketplace almost always link to their listing from their homepage,
        pricing page, or "Get Started" page. This avoids search engine
        rate-limiting and JS-rendering problems entirely.
        """
        signals = []

        # Marketplace URL patterns that indicate the company has a listing
        marketplace_patterns = {
            'AWS': [
                'aws.amazon.com/marketplace/pp/',
                'aws.amazon.com/marketplace/seller-profile',
            ],
            'Azure': [
                'azuremarketplace.microsoft.com/marketplace/apps/',
                'azuremarketplace.microsoft.com/en-us/marketplace/apps/',
                'appsource.microsoft.com/product/',
            ],
            'GCP': [
                'cloud.google.com/marketplace/product/',
                'console.cloud.google.com/marketplace/',
            ],
        }

        # Phase 1: hardcoded high-probability paths
        priority_pages = [
            f'https://{website}',
            f'https://{website}/pricing',
            f'https://{website}/get-started',
            f'https://{website}/aws',
            f'https://{website}/marketplace',
            f'https://{website}/partners',
            f'https://{website}/integrations',
        ]

        # Phase 2: crawl the homepage to discover actual internal paths.
        # Many startups don't use the standard paths above — their Marketplace
        # link may live on /product, /platform, /community, /resources, etc.
        # We fetch the homepage once, collect all same-origin links, and append
        # any that aren't already in the priority list (capped to avoid sprawl).
        try:
            home_r = requests.get(
                f'https://{website}', timeout=self.TIMEOUT,
                headers=self.HEADERS, verify=False, allow_redirects=True
            )
            if home_r.status_code == 200:
                home_soup = BeautifulSoup(home_r.text, 'html.parser')
                base_domain = urlparse(home_r.url).netloc  # canonical after redirect
                crawled_paths: set = set()
                for a in home_soup.find_all('a', href=True):
                    href = a['href']
                    # Resolve relative URLs
                    if href.startswith('./') or (href.startswith('/') and not href.startswith('//')):
                        full = f'https://{base_domain}{href.lstrip(".")}' if href.startswith('.') \
                               else f'https://{base_domain}{href}'
                    elif href.startswith('http') and base_domain in href:
                        full = href
                    else:
                        continue  # external link or fragment
                    # Deduplicate and skip already-covered paths
                    path = urlparse(full).path.rstrip('/')
                    if path and path not in crawled_paths and \
                            f'https://{website}{path}' not in priority_pages and \
                            f'https://{base_domain}{path}' not in priority_pages:
                        crawled_paths.add(path)

                # Sort for determinism; cap at 20 extra pages
                extra_pages = [f'https://{base_domain}{p}' for p in sorted(crawled_paths)][:20]
            else:
                extra_pages = []
        except Exception:
            extra_pages = []

        pages_to_check = priority_pages + extra_pages

        found_providers = set()

        for page_url in pages_to_check:
            try:
                r = requests.get(
                    page_url, timeout=self.TIMEOUT,
                    headers=self.HEADERS, verify=False
                )
                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(r.text, 'html.parser')

                # Scan all links on the page for marketplace URLs
                for a in soup.find_all('a', href=True):
                    href = a.get('href', '').lower()

                    for provider, patterns in marketplace_patterns.items():
                        if provider in found_providers:
                            continue
                        for pattern in patterns:
                            if pattern in href:
                                signals.append(AttributionSignal(
                                    provider_type=ProviderType.CLOUD,
                                    provider_name=provider,
                                    signal_source='cloud_marketplace',
                                    signal_strength=SignalStrength.MEDIUM,
                                    evidence_text=f'{company_name} links to {provider} Marketplace from {page_url}',
                                    evidence_url=href if href.startswith('http') else page_url,
                                    confidence_weight=0.6
                                ))
                                found_providers.add(provider)
                                break

                # Also check raw HTML for marketplace URLs (catches JS-generated links)
                html_lower = r.text.lower()
                for provider, patterns in marketplace_patterns.items():
                    if provider in found_providers:
                        continue
                    for pattern in patterns:
                        if pattern in html_lower:
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.CLOUD,
                                provider_name=provider,
                                signal_source='cloud_marketplace',
                                signal_strength=SignalStrength.MEDIUM,
                                evidence_text=f'{provider} Marketplace URL found in {page_url} source',
                                evidence_url=page_url,
                                confidence_weight=0.6
                            ))
                            found_providers.add(provider)
                            break

                # Stop scanning pages if we've found all three
                if len(found_providers) == 3:
                    break

            except Exception:
                continue

        return signals

    def _check_http_headers(self, website: str) -> List[AttributionSignal]:
        """Check HTTP response headers for cloud provider fingerprints"""
        signals = []
        try:
            # Hosted platforms (Wix, Squarespace, etc.) return their own CDN
            # headers — these reflect the platform's infra, not the startup's.
            if self._is_hosted_platform(website):
                print(f"    ℹ️  HTTP headers skipped — hosted platform detected")
                return signals

            response = requests.get(
                f'https://{website}', timeout=self.TIMEOUT,
                headers=self.HEADERS, allow_redirects=True, verify=False
            )
            headers = response.headers

            header_map = {
                ('X-Amz-Cf-Id', 'X-Amz-Request-Id', 'X-Amz-Cf-Pop'): ('AWS', 'AWS CloudFront/S3 headers'),
                ('X-Cloud-Trace-Context', 'X-Goog-Generation'): ('GCP', 'GCP trace headers'),
                ('X-Azure-Ref', 'X-Ms-Request-Id'): ('Azure', 'Azure headers'),
            }
            for header_keys, (provider, desc) in header_map.items():
                if any(h in headers for h in header_keys):
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name=provider,
                        signal_source='http_headers',
                        signal_strength=SignalStrength.MEDIUM,
                        evidence_text=f'{desc} detected',
                        confidence_weight=0.6
                    ))

            # Also check the Server header
            server = headers.get('Server', '').lower()
            if 'amazons3' in server or 'cloudfront' in server:
                signals.append(AttributionSignal(
                    provider_type=ProviderType.CLOUD,
                    provider_name='AWS',
                    signal_source='http_headers',
                    signal_strength=SignalStrength.MEDIUM,
                    evidence_text=f'Server header: {server}',
                    confidence_weight=0.6
                ))
            elif 'google' in server or 'gws' in server:
                signals.append(AttributionSignal(
                    provider_type=ProviderType.CLOUD,
                    provider_name='GCP',
                    signal_source='http_headers',
                    signal_strength=SignalStrength.MEDIUM,
                    evidence_text=f'Server header: {server}',
                    confidence_weight=0.6
                ))
        except Exception:
            pass  # HTTP failures are common for early-stage startups
        return signals

    def _check_job_postings(self, company_name: str) -> List[AttributionSignal]:
        """
        Search for engineering job postings that reveal tech stack.

        Job postings are excellent Tier 2 signals because companies explicitly
        list required technologies. "Must have 3+ years AWS experience" is a
        direct indicator of their infrastructure.

        Two-phase strategy:
          Phase 1 — Board index pages: fetch the company's job board listing
            page (e.g. jobs.lever.co/cellares) and collect all individual
            job listing URLs linked from it.
          Phase 2 — Individual job pages: fetch up to 5 individual engineering
            role pages and keyword-scan their full text.

        Scanning the index page alone is insufficient — the index typically
        shows only job titles and locations, not the tech stack requirements
        that appear in the full job description.
        """
        signals = []
        found_cloud: set = set()
        found_ai: set = set()

        slug = company_name.lower().replace(" ", "").replace("-", "")

        # Job board index URLs to probe
        board_index_urls = [
            f'https://jobs.lever.co/{slug}',
            f'https://boards.greenhouse.io/{slug}',
            f'https://jobs.ashbyhq.com/{slug}',
            f'https://apply.workable.com/{slug}',
        ]

        # Collect individual job listing URLs by crawling the board index
        individual_job_urls: list[str] = []

        for index_url in board_index_urls:
            try:
                r = requests.get(
                    index_url, timeout=self.TIMEOUT,
                    headers=self.HEADERS, verify=False
                )
                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(r.text, 'html.parser')
                board_domain = urlparse(index_url).netloc  # e.g. jobs.lever.co

                # Collect all same-board hrefs that look like individual job pages
                for a in soup.find_all('a', href=True):
                    href = a['href']
                    # Normalise relative URLs
                    if href.startswith('/'):
                        href = f'https://{board_domain}{href}'
                    # Keep only links that go deeper into this board (not back to index)
                    if (board_domain in href
                            and href != index_url
                            and href.rstrip('/') != index_url.rstrip('/')
                            and href not in individual_job_urls):
                        individual_job_urls.append(href)

                # If we found individual listings, stop trying other boards
                if individual_job_urls:
                    break

            except Exception:
                continue

        # Additionally try Google search to find job board URLs we may have missed
        # (covers companies whose slug differs from their name, e.g. "Humans and AI")
        if not individual_job_urls:
            try:
                encoded = quote_plus(f'{company_name} engineer jobs')
                search_url = (
                    f"https://www.google.com/search?q={encoded}"
                    f"+site:lever.co+OR+site:greenhouse.io+OR+site:jobs.ashbyhq.com"
                    f"+OR+site:boards.greenhouse.io+OR+site:apply.workable.com"
                )
                r = requests.get(
                    search_url, timeout=self.TIMEOUT,
                    headers=self.HEADERS, verify=False
                )
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, 'html.parser')
                    for a in soup.find_all('a', href=True):
                        href = a['href']
                        for board in ['lever.co', 'greenhouse.io', 'ashbyhq.com', 'workable.com']:
                            if board in href:
                                if '/url?q=' in href:
                                    href = href.split('/url?q=')[1].split('&')[0]
                                if href not in individual_job_urls:
                                    individual_job_urls.append(href)
                                break
            except Exception:
                pass

        # Scan up to 8 individual job pages for tech stack signals
        # Prioritise engineering/infrastructure roles
        eng_keywords = ['engineer', 'infrastructure', 'platform', 'devops', 'backend',
                        'data', 'ml', 'machine learning', 'ai ', 'cloud']
        def _is_eng_role(url: str) -> bool:
            url_lower = url.lower()
            return any(kw in url_lower for kw in eng_keywords)

        # Sort: engineering roles first, then the rest
        sorted_urls = (
            [u for u in individual_job_urls if _is_eng_role(u)] +
            [u for u in individual_job_urls if not _is_eng_role(u)]
        )

        for url in sorted_urls[:8]:
            try:
                r = requests.get(
                    url, timeout=self.TIMEOUT,
                    headers=self.HEADERS, verify=False
                )
                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(r.text, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                text = soup.get_text(separator=' ', strip=True)

                # Cloud keyword scan
                cloud_matches = _keyword_scan(text, CLOUD_KEYWORDS)
                for provider, keywords in cloud_matches.items():
                    if provider not in found_cloud:
                        found_cloud.add(provider)
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.CLOUD,
                            provider_name=provider,
                            signal_source='job_posting',
                            signal_strength=SignalStrength.MEDIUM,
                            evidence_text=f'Job posting mentions: {", ".join(keywords[:3])}',
                            evidence_url=url,
                            confidence_weight=0.6
                        ))

                # AI keyword scan
                ai_matches = _keyword_scan(text, AI_KEYWORDS)
                for provider, keywords in ai_matches.items():
                    if provider not in found_ai:
                        found_ai.add(provider)
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.AI,
                            provider_name=provider,
                            signal_source='job_posting',
                            signal_strength=SignalStrength.MEDIUM,
                            evidence_text=f'Job posting mentions: {", ".join(keywords[:3])}',
                            evidence_url=url,
                            confidence_weight=0.6
                        ))

                # Once we have at least one cloud and one AI signal, stop scanning
                if found_cloud and found_ai:
                    break

            except Exception:
                continue

        return signals

    def _scan_website_content(self, website: str) -> List[AttributionSignal]:
        """
        Scan multiple pages on the startup's website for provider signals.
        
        Pages checked (in priority order):
        - /integrations, /partners — often list cloud/AI providers explicitly
        - /docs, /developers — technical docs reference SDKs and services
        - /security, /trust, /privacy — trust pages mention infrastructure
        - /blog — tech blog posts about architecture and tool choices
        - / (homepage) — sometimes mentions "Powered by X" or "Built on Y"
        """
        signals = []

        # Pages to scan, grouped by signal strength
        page_configs = [
            # Integration/partner pages — strong signal if they list providers
            {
                'paths': ['/integrations', '/partners', '/ecosystem', '/marketplace'],
                'strength': SignalStrength.MEDIUM,
                'weight': 0.6,
                'source': 'integrations_page',
            },
            # Technical docs — references to specific services
            {
                'paths': ['/docs', '/developers', '/api', '/documentation'],
                'strength': SignalStrength.MEDIUM,
                'weight': 0.6,
                'source': 'tech_docs',
            },
            # Trust/security pages
            {
                'paths': ['/security', '/trust', '/privacy', '/compliance',
                          '/legal/privacy', '/legal/security', '/trust-center'],
                'strength': SignalStrength.MEDIUM,
                'weight': 0.6,
                'source': 'trust_page',
            },
            # Blog — look for architecture/infrastructure posts
            {
                'paths': ['/blog', '/eng/blog', '/engineering', '/tech-blog'],
                'strength': SignalStrength.WEAK,
                'weight': 0.3,
                'source': 'tech_blog',
            },
            # Homepage — weakest signal (often just marketing)
            {
                'paths': ['/'],
                'strength': SignalStrength.WEAK,
                'weight': 0.3,
                'source': 'homepage',
            },
        ]

        found_sources = set()  # Track which sources already produced signals

        for config in page_configs:
            for path in config['paths']:
                url = f'https://{website}{path}'
                try:
                    r = requests.get(
                        url, timeout=self.TIMEOUT,
                        headers=self.HEADERS, verify=False
                    )
                    if r.status_code != 200:
                        continue

                    # Extract text content
                    soup = BeautifulSoup(r.text, 'html.parser')
                    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                        tag.decompose()
                    text = soup.get_text(separator=' ', strip=True)

                    # Scan for cloud keywords
                    cloud_matches = _keyword_scan(text, CLOUD_KEYWORDS)
                    for provider, keywords in cloud_matches.items():
                        sig_key = f"cloud:{provider}:{config['source']}"
                        if sig_key not in found_sources:
                            found_sources.add(sig_key)
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.CLOUD,
                                provider_name=provider,
                                signal_source=config['source'],
                                signal_strength=config['strength'],
                                evidence_text=f'{provider} keywords on {path}: {", ".join(keywords[:3])}',
                                evidence_url=url,
                                confidence_weight=config['weight']
                            ))

                    # Scan for AI keywords
                    ai_matches = _keyword_scan(text, AI_KEYWORDS)
                    for provider, keywords in ai_matches.items():
                        sig_key = f"ai:{provider}:{config['source']}"
                        if sig_key not in found_sources:
                            found_sources.add(sig_key)
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.AI,
                                provider_name=provider,
                                signal_source=config['source'],
                                signal_strength=config['strength'],
                                evidence_text=f'{provider} keywords on {path}: {", ".join(keywords[:3])}',
                                evidence_url=url,
                                confidence_weight=config['weight']
                            ))

                except Exception:
                    continue

        return signals

    def _check_security_txt(self, website: str) -> List[AttributionSignal]:
        """Check /.well-known/security.txt for infrastructure mentions"""
        signals = []
        try:
            url = f'https://{website}/.well-known/security.txt'
            r = requests.get(url, timeout=self.TIMEOUT, headers=self.HEADERS, verify=False)
            if r.status_code == 200:
                cloud_matches = _keyword_scan(r.text, CLOUD_KEYWORDS)
                for provider, keywords in cloud_matches.items():
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name=provider,
                        signal_source='security_txt',
                        signal_strength=SignalStrength.MEDIUM,
                        evidence_text=f'{provider} mentioned in security.txt',
                        evidence_url=url,
                        confidence_weight=0.6
                    ))
        except Exception:
            pass
        return signals

    # ========================================================================
    # TIER 3: SUPPORTING SIGNALS (weight 0.3)
    # ========================================================================

    def _is_hosted_platform(self, domain: str) -> bool:
        """
        Return True if domain's CNAME resolves to a known hosted website
        builder (Wix, Squarespace, Webflow, etc.).

        Used to suppress IP/ASN and HTTP-header signals that would otherwise
        incorrectly attribute the *platform's* cloud infrastructure to the
        startup.  E.g. Wix routes through GCP — we don't want to score a
        Wix-hosted startup as a GCP customer.
        """
        try:
            answers = dns.resolver.resolve(domain, 'CNAME')
            for rdata in answers:
                cname = str(rdata.target).lower().rstrip('.')
                if any(platform in cname for platform in self.HOSTED_PLATFORM_CNAMES):
                    return True
        except Exception:
            pass
        return False

    def _check_ip_asn(self, website: str) -> List[AttributionSignal]:
        """Check IP address for cloud provider ownership"""
        signals = []
        try:
            # Skip if the site is on a hosted platform — the IP belongs to the
            # platform (e.g. Wix runs on GCP), not to the startup itself.
            if self._is_hosted_platform(website):
                print(f"    ℹ️  IP/ASN skipped — hosted platform detected")
                return signals

            ip = socket.gethostbyname(website)

            # Simplified IP prefix ranges — production would use a full ASN database
            ip_map = [
                (['52.', '54.', '3.', '18.', '34.200', '35.172'], 'AWS'),
                (['35.', '34.', '104.196', '146.148'],             'GCP'),
                (['13.', '20.', '40.', '51.'],                     'Azure'),
            ]
            for prefixes, provider in ip_map:
                if any(ip.startswith(p) for p in prefixes):
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name=provider,
                        signal_source='ip_asn',
                        signal_strength=SignalStrength.WEAK,
                        evidence_text=f'IP {ip} in {provider} range',
                        confidence_weight=0.3
                    ))
                    break
        except Exception:
            pass
        return signals

    # ========================================================================
    # DIRECT EVIDENCE URL SIGNALS (Tier 1)
    # ========================================================================

    def _check_evidence_urls(
        self,
        company_name: str,
        evidence_urls: List[str],
    ) -> List[AttributionSignal]:
        """
        Fetch caller-supplied evidence URLs (e.g. known partnership press releases
        or official announcement pages) and keyword-scan them for cloud/AI signals.

        These are treated as Tier 1 (weight 1.0) because the caller has already
        verified that the URL is a genuine partnership signal for this company.

        Returns one signal per (provider, url) combination found.
        """
        signals = []
        seen: set = set()

        for url in evidence_urls:
            url = url.strip()
            if not url:
                continue
            try:
                r = requests.get(
                    url, timeout=self.TIMEOUT,
                    headers=self.HEADERS, verify=False, allow_redirects=True
                )
                if r.status_code != 200:
                    continue

                soup = BeautifulSoup(r.text, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                text = soup.get_text(separator=' ', strip=True)

                # Cloud keyword scan
                cloud_matches = _keyword_scan(text, CLOUD_KEYWORDS)
                for provider, keywords in cloud_matches.items():
                    key = f'cloud:{provider}'
                    if key not in seen:
                        seen.add(key)
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.CLOUD,
                            provider_name=provider,
                            signal_source='evidence_url',
                            signal_strength=SignalStrength.STRONG,
                            evidence_text=f'Partnership page mentions {provider}: {", ".join(keywords[:3])}',
                            evidence_url=url,
                            confidence_weight=1.0
                        ))

                # AI keyword scan
                ai_matches = _keyword_scan(text, AI_KEYWORDS)
                for provider, keywords in ai_matches.items():
                    key = f'ai:{provider}'
                    if key not in seen:
                        seen.add(key)
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.AI,
                            provider_name=provider,
                            signal_source='evidence_url',
                            signal_strength=SignalStrength.STRONG,
                            evidence_text=f'Partnership page mentions {provider}: {", ".join(keywords[:3])}',
                            evidence_url=url,
                            confidence_weight=1.0
                        ))

                # Also check _PROVIDER_TERM_MAP for partnership-specific terms
                # (e.g. "google cloud", "amazon web services") not in CLOUD_KEYWORDS
                text_lower = f' {text} '.lower()
                for term, ptype, provider in self._PROVIDER_TERM_MAP:
                    key = f'{ptype.value}:{provider}'
                    if key not in seen and term in text_lower:
                        seen.add(key)
                        signals.append(AttributionSignal(
                            provider_type=ptype,
                            provider_name=provider,
                            signal_source='evidence_url',
                            signal_strength=SignalStrength.STRONG,
                            evidence_text=f'Partnership page contains "{term.strip()}"',
                            evidence_url=url,
                            confidence_weight=1.0
                        ))

            except Exception:
                continue

        return signals

    # ========================================================================
    # INVESTOR & FOUNDER PRIOR SIGNALS (Tier 3 — weight 0.3)
    # ========================================================================

    def _check_investor_signals(
        self,
        company_name: str,
        lead_investors: List[str],
    ) -> List[AttributionSignal]:
        """
        Emit a WEAK cloud signal when a known VC investor is associated with
        a cloud provider via INVESTOR_CLOUD_PRIORS.

        Only emits one signal per provider (the highest-confidence match).
        Does not emit AI signals — investor relationships are primarily cloud priors.
        """
        signals = []
        seen_providers: set = set()

        for investor in lead_investors:
            investor_lower = investor.lower().strip()
            for prior_key, (provider, rationale) in INVESTOR_CLOUD_PRIORS.items():
                if prior_key in investor_lower and provider not in seen_providers:
                    seen_providers.add(provider)
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name=provider,
                        signal_source='investor_prior',
                        signal_strength=SignalStrength.WEAK,
                        evidence_text=f'{rationale} (investor: {investor})',
                        confidence_weight=0.3
                    ))

        return signals

    def _check_founder_signals(
        self,
        company_name: str,
        founder_background: List[str],
    ) -> List[AttributionSignal]:
        """
        Emit a WEAK cloud signal when founder background keywords match
        FOUNDER_CLOUD_PRIORS (e.g. "Google Brain" → GCP).

        Only emits one signal per provider. Does not emit AI signals.
        """
        signals = []
        seen_providers: set = set()

        for background in founder_background:
            background_lower = background.lower().strip()
            for prior_key, (provider, rationale) in FOUNDER_CLOUD_PRIORS.items():
                if prior_key in background_lower and provider not in seen_providers:
                    seen_providers.add(provider)
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name=provider,
                        signal_source='founder_prior',
                        signal_strength=SignalStrength.WEAK,
                        evidence_text=f'{rationale} (background: {background})',
                        confidence_weight=0.3
                    ))

        return signals

    # ========================================================================
    # TIER 4: LLM FALLBACK
    # ========================================================================

    def _llm_attribution_fallback(
        self,
        company_name: str,
        website: str,
        article_text: Optional[str],
        need_cloud: bool,
        need_ai: bool,
    ) -> List[AttributionSignal]:
        """
        Use Claude Haiku to infer cloud/AI providers when deterministic signals
        are absent or weak (confidence < LLM_FALLBACK_THRESHOLD).

        Inputs fed to the LLM:
          1. Homepage text (fetched fresh — already done by _scan_website_content
             but not stored, so we refetch a lightweight version here)
          2. Funding announcement article text (if available — passed in from
             the pipeline's FundingEvent.raw_article_text)

        The LLM is asked to:
          - Identify cloud infrastructure provider(s) the startup uses (if need_cloud)
          - Identify AI/LLM provider(s) the startup uses (if need_ai)
          - Express confidence per finding as 0–100
          - Quote a specific snippet from the provided text as evidence
          - Only attribute based on text explicitly provided — no hallucination

        Confidence → weight mapping (same scale as temporal weighting):
          80–100 → STRONG  (1.0)
          50–79  → MEDIUM  (0.6)
          0–49   → WEAK    (0.3)

        Returns list of AttributionSignal objects (may be empty).
        """
        if not self.anthropic_client:
            return []

        # ── Fetch homepage text (lightweight — strip scripts/styles) ──────────
        homepage_text = ''
        try:
            r = requests.get(
                f'https://{website}', timeout=self.TIMEOUT,
                headers=self.HEADERS, verify=False, allow_redirects=True
            )
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                homepage_text = soup.get_text(separator=' ', strip=True)[:4000]
        except Exception:
            pass

        # ── Build context block ───────────────────────────────────────────────
        context_parts = []
        if homepage_text:
            context_parts.append(f"=== HOMEPAGE TEXT (from {website}) ===\n{homepage_text}")
        if article_text:
            context_parts.append(f"=== FUNDING ANNOUNCEMENT ARTICLE ===\n{article_text[:3000]}")

        if not context_parts:
            return []   # Nothing to give the LLM

        context = '\n\n'.join(context_parts)

        # ── Build task instructions ───────────────────────────────────────────
        tasks = []
        if need_cloud:
            tasks.append(
                "1. CLOUD INFRASTRUCTURE: Which cloud platform(s) does this startup run on? "
                "(e.g. AWS, GCP, Azure, CoreWeave, OCI, Lambda Labs). "
                "Look for references to specific services (EC2, S3, Cloud Run, Azure Blob, etc.) "
                "or explicit statements about their infrastructure."
            )
        if need_ai:
            tasks.append(
                f"{'2' if need_cloud else '1'}. AI/LLM PROVIDERS: Which AI or LLM provider(s) does this "
                "startup use or integrate with? "
                "(e.g. OpenAI, Anthropic, Google AI / Gemini / Vertex AI, Cohere, Mistral). "
                "Look for API references, model names, or integration statements."
            )

        task_text = '\n'.join(tasks)

        prompt = f"""You are an infrastructure analyst. Your task is to identify cloud and AI providers
used by a startup based ONLY on the text provided below. Do NOT use any prior knowledge or make
assumptions — only attribute what is explicitly supported by the provided text.

STARTUP: {company_name} ({website})

{task_text}

For each provider you identify, respond with a JSON array of objects. Each object must have:
- "provider_type": "cloud" or "ai"
- "provider_name": canonical name (e.g. "AWS", "GCP", "Azure", "OpenAI", "Anthropic", "Google AI", "Cohere", "Mistral", "CoreWeave", "OCI")
- "confidence": integer 0–100 (how confident you are this startup actually uses this provider)
- "evidence_quote": exact short quote (≤30 words) from the text below that supports this attribution
- "reasoning": one sentence explaining why this quote indicates provider usage

Rules:
- Only include providers with confidence ≥ 30
- A provider mentioned in a customer case study or as a "supported integration"
  may indicate customer use but is weaker evidence than "we run on X" or "our infrastructure uses X"
- If the text mentions no relevant providers, return an empty array []
- Return ONLY valid JSON — no markdown, no prose

--- TEXT TO ANALYSE ---
{context}
--- END TEXT ---

JSON response:"""

        # ── Call the LLM ──────────────────────────────────────────────────────
        try:
            response = self.anthropic_client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=800,
                messages=[{'role': 'user', 'content': prompt}]
            )
            raw = response.content[0].text.strip()
        except Exception as e:
            print(f"    ⚠️  LLM call failed: {e}")
            return []

        # ── Parse response ────────────────────────────────────────────────────
        signals: List[AttributionSignal] = []
        try:
            # Strip any accidental markdown fences
            if raw.startswith('```'):
                raw = re.sub(r'^```[a-z]*\n?', '', raw)
                raw = re.sub(r'\n?```$', '', raw)

            findings = json.loads(raw)
            if not isinstance(findings, list):
                return []

            # Canonical provider name validation
            valid_cloud = {'AWS', 'GCP', 'Azure', 'CoreWeave', 'OCI', 'Lambda Labs', 'Vast.ai'}
            valid_ai    = {'OpenAI', 'Anthropic', 'Google AI', 'Cohere', 'Mistral'}

            for item in findings:
                ptype_raw     = str(item.get('provider_type', '')).lower()
                provider_name = str(item.get('provider_name', '')).strip()
                confidence    = int(item.get('confidence', 0))
                evidence      = str(item.get('evidence_quote', '')).strip()
                reasoning     = str(item.get('reasoning', '')).strip()

                # Validate type
                if ptype_raw == 'cloud':
                    ptype = ProviderType.CLOUD
                    if provider_name not in valid_cloud:
                        continue
                elif ptype_raw == 'ai':
                    ptype = ProviderType.AI
                    if provider_name not in valid_ai:
                        continue
                else:
                    continue

                # Respect the need flags — don't add unsolicited types
                if ptype == ProviderType.CLOUD and not need_cloud:
                    continue
                if ptype == ProviderType.AI and not need_ai:
                    continue

                # Map confidence to signal weight
                if confidence >= 80:
                    strength = SignalStrength.STRONG
                    weight   = 1.0
                elif confidence >= 50:
                    strength = SignalStrength.MEDIUM
                    weight   = 0.6
                else:
                    strength = SignalStrength.WEAK
                    weight   = 0.3

                signals.append(AttributionSignal(
                    provider_type=ptype,
                    provider_name=provider_name,
                    signal_source='llm_inference',
                    signal_strength=strength,
                    evidence_text=f'LLM ({confidence}% conf): "{evidence}" — {reasoning}'[:200],
                    evidence_url=f'https://{website}',
                    confidence_weight=weight
                ))

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"    ⚠️  LLM response parse error: {e} | raw: {raw[:100]}")

        return signals

    # ========================================================================
    # HELPER METHODS
    # ========================================================================

    def _create_override_attribution(
        self,
        company_name: str,
        provider_name: Optional[str],
        provider_type: ProviderType
    ) -> Optional[Attribution]:
        """Create attribution from a known partnership override"""
        if not provider_name:
            return None

        signal = AttributionSignal(
            provider_type=provider_type,
            provider_name=provider_name,
            signal_source='partnership_override',
            signal_strength=SignalStrength.STRONG,
            evidence_text=f'Official partnership: {company_name} with {provider_name}',
            confidence_weight=1.0
        )
        entry = ProviderEntry(
            provider_name=provider_name,
            role="Cloud infrastructure" if provider_type == ProviderType.CLOUD else "AI service provider",
            confidence=1.0,
            entrenchment=EntrenchmentLevel.STRONG,
            raw_score=1.0,
            signals=[signal]
        )
        return Attribution(
            provider_type=provider_type,
            is_multi=False,
            primary_provider=provider_name,
            providers=[entry],
            confidence=1.0,
            evidence_count=1,
            signals=[signal]
        )

    def _create_na_attribution(
        self,
        provider_type: ProviderType,
        note: str,
    ) -> Attribution:
        """
        Create a Not-Applicable attribution object.
        Used when attribution is structurally meaningless — e.g. the company
        is itself a cloud/compute provider rather than a consumer of one.
        """
        return Attribution(
            provider_type=provider_type,
            is_not_applicable=True,
            not_applicable_note=note,
            is_multi=False,
            primary_provider=None,
            providers=[],
            confidence=0.0,
            evidence_count=0,
            signals=[],
        )