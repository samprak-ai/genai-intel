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

  Tier 4a — Perplexity Sonar search (weight mapped from Perplexity confidence):
    - Triggered when attribution confidence < 60% after Tiers 1–3
    - Fires one live web search per needed type (cloud / AI)
    - Surfaces job postings, blog posts, press releases not fetched by Tier 1–3
    - Returns citation URLs stored as verifiable evidence_url values
    - signal_source = 'perplexity_search'

  Tier 4b — Claude Haiku fallback (weight mapped from LLM confidence):
    - Triggered when confidence still < 60% after Tier 4a
    - Reads pre-fetched homepage text + funding article text
    - LLM expresses its own 0–100 confidence → mapped to weight 0.3–1.0
    - Grounded: LLM must cite a specific quote from provided text
    - signal_source = 'llm_inference'
"""

import os
import re
import json
import ipaddress
import dns.resolver
import requests
import socket
import feedparser
from typing import Optional, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus, urlparse
import anthropic
import urllib3

from app.models import (
    AttributionSignal, Attribution, ProviderEntry,
    ProviderType, SignalStrength, EntrenchmentLevel,
    SignalWeights, PARTNERSHIP_OVERRIDES, NOT_APPLICABLE_COMPANIES,
    INVESTOR_CLOUD_PRIORS, FOUNDER_CLOUD_PRIORS, HARDWARE_INDUSTRIES,
)
from app.attribution.subprocessors_parser import SubprocessorsParser
from app.core.search import serper_search, parse_age_to_strength

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# How much stronger one provider must be to be "primary" vs "multi"
MULTI_PROVIDER_THRESHOLD = 0.3

# AI signals are noisier (no DNS/IP deterministic tier) so require a wider gap
# before declaring a single AI provider winner. This reduces false multi-AI
# attributions from low-quality signals (job requirements, comparison blog posts).
AI_MULTI_PROVIDER_THRESHOLD = 0.5

# Blog post URL slug patterns that indicate competitor/comparison pages.
# These pages list other providers as alternatives/competitors — not the company's own stack.
# Any blog post URL containing one of these patterns is skipped during blog scanning.
_COMPETITOR_SLUG_PATTERNS = [
    'alternative', '/vs-', '-vs-', '/vs/',
    'competitor', 'comparison', 'compare',
    # Evaluation / benchmark content — rarely indicates actual infrastructure usage
    'evaluat', 'benchmark', 'best-llm', 'best-ai',
    'llm-review', 'model-review', 'choosing-', 'which-llm', 'which-ai',
]

# Investor / VC portfolio job board domains.
# Many startups post jobs on their investors' portfolio job boards rather than
# (or in addition to) their own careers page — these are Tier 2 signals because
# the job descriptions list the company's actual tech stack.
# Each entry: (domain, company_page_url_template)
# The template uses {slug} (lowercased, spaces→hyphens) and {name} (raw name).
INVESTOR_JOB_BOARDS = [
    # domain,                       company listing URL template
    ('jobs.8vc.com',               'https://jobs.8vc.com/companies/{slug}'),
    ('jobs.a16z.com',              'https://jobs.a16z.com/companies/{slug}'),
    ('jobs.sequoiacap.com',        'https://jobs.sequoiacap.com/companies/{slug}'),
    ('jobs.bvp.com',               'https://jobs.bvp.com/companies/{slug}'),
    ('jobs.accel.com',             'https://jobs.accel.com/companies/{slug}'),
    ('jobs.greylock.com',          'https://jobs.greylock.com/companies/{slug}'),
    ('jobs.khoslaventures.com',    'https://jobs.khoslaventures.com/companies/{slug}'),
    ('jobs.nea.com',               'https://jobs.nea.com/companies/{slug}'),
    ('jobs.generalcatalyst.com',   'https://jobs.generalcatalyst.com/companies/{slug}'),
    ('jobs.benchmark.com',         'https://jobs.benchmark.com/companies/{slug}'),
    ('jobs.indexventures.com',     'https://jobs.indexventures.com/companies/{slug}'),
    ('jobs.insightpartners.com',   'https://jobs.insightpartners.com/companies/{slug}'),
    ('jobs.redpoint.com',          'https://jobs.redpoint.com/companies/{slug}'),
    ('jobs.lightspeedvp.com',      'https://jobs.lightspeedvp.com/companies/{slug}'),
    ('jobs.foundationcap.com',     'https://jobs.foundationcap.com/companies/{slug}'),
]

# Phrases in job posting sentences that mark hiring *requirements*, not infrastructure usage.
# "Experience with OpenAI API required" is a skill requirement, not proof they run on OpenAI.
# Applied only to AI keyword signals — cloud keywords like 'EKS', 'BigQuery' are infra-specific
# and don't typically appear as candidate skill requirements.
_JOB_HIRING_PHRASES = [
    'experience with', 'experience in', 'familiarity with', 'knowledge of',
    'proficiency in', 'proficiency with', 'understanding of',
    'preferred', 'bonus if', 'nice to have', 'nice-to-have',
    'a plus', 'is a plus', 'would be a plus',
    'worked with', 'exposure to',
]

# "Other" hosting platforms — detected via DNS CNAME when no cloud signals are found.
# Maps CNAME substring → display name. These emit WEAK (0.3) signals so the company
# still shows up with a named provider instead of Unknown, but at low confidence.
OTHER_PLATFORM_CNAMES = {
    'vercel.app':    'Vercel',
    'vercel-dns.com': 'Vercel',
    'netlify.app':   'Netlify',
    'netlify.com':   'Netlify',
    'onrender.com':  'Render',
    'github.io':     'GitHub Pages',
    'webflow.io':    'Webflow',
}

# "Other" hosting platforms — detected via HTTP response headers.
# Each entry is (header_name_lowercase, value_substring_or_None, platform_name).
# None value_substring means: signal fires on header *presence* alone.
OTHER_PLATFORM_HEADERS = [
    ('server',               'vercel',     'Vercel'),
    ('x-vercel-id',          None,         'Vercel'),
    ('server',               'netlify',    'Netlify'),
    ('x-nf-request-id',      None,         'Netlify'),
    ('cf-ray',               None,         'Cloudflare'),
    ('server',               'cloudflare', 'Cloudflare'),
    ('x-render-origin-server', None,       'Render'),
]

# Reusable keyword maps for cloud and AI provider detection
CLOUD_KEYWORDS = {
    'AWS': [
        'amazon web services', ' aws ', 'aws.amazon.com',
        ' ec2 ', ' s3 ', ' ecs ', ' eks ', 'cloudfront',   # removed: 'lambda' (matches Python lambda expressions)
        'dynamodb', 'redshift', 'sagemaker', 'aws fargate',
        'amazon rds', 'amazon aurora', 'amazon sqs', 'amazon sns',
        'aws glue', 'amazon kinesis', 'aws cdk',
    ],
    'GCP': [
        'google cloud', ' gcp ', 'cloud.google.com',
        'bigquery', 'gke ',                                 # removed: 'cloud run', 'cloud functions' (generic)
        'google kubernetes', 'cloud spanner', 'vertex ai',  # removed: 'cloud storage' (privacy policy boilerplate)
        'pub/sub', 'google compute engine',                 # removed: 'cloud sql' (generic)
        'google cloud platform',
    ],
    'Azure': [
        'microsoft azure', ' azure ', 'azure.microsoft.com',
        'azure devops', 'azure functions', 'cosmos db', 'azure blob',
        'azure kubernetes', ' aks ', 'azure sql', 'azure cognitive',
        'azure openai', 'azure ml', 'azure pipelines',
    ],
    # ------------------------------------------------------------------ #
    # Neo / GPU cloud providers                                           #
    # ------------------------------------------------------------------ #
    'Lambda': [
        'lambda.cloud', 'lambdalabs.com', 'lambda labs', 'lambda gpu cloud',
    ],
    'Crusoe': [
        'crusoe.ai', 'crusoe cloud', 'crusoecloud', 'crusoe energy',
    ],
    'OVH': [
        'ovhcloud', 'ovh cloud', 'ovh.net', 'ovh.com',
    ],
    'Vultr': [
        'vultr.com', ' vultr ',
    ],
    'Paperspace': [
        'paperspace.com', ' paperspace ', 'gradient.run',
    ],
    'Nebius': [
        'nebius.ai', 'nebius cloud',
    ],
    'Fluidstack': [
        'fluidstack.io', ' fluidstack ',
    ],
    # ------------------------------------------------------------------ #
    # On-Premises signals                                                 #
    # Deliberately conservative — phrases that clearly indicate own infra #
    # ------------------------------------------------------------------ #
    'On-Premises': [
        'on-premises', 'on-prem', 'on premise',
        'self-hosted infrastructure', 'self hosted infrastructure',
        'private datacenter', 'private data center',
        'own datacenter', 'own data center',
        'bare metal', 'colocated', 'colocation', 'colo facility',
        'air-gapped',
        'hpe proliant', 'dell emc', 'lenovo thinksystem',
    ],
}

AI_KEYWORDS = {
    'OpenAI': [
        'openai', 'openai api', 'openai.com',
        'chatgpt', 'gpt-4', 'gpt-4o', 'gpt-3', 'gpt-3.5',
        'dall-e', 'whisper api', 'o1-preview', 'o1-mini',
    ],
    'Anthropic': [
        'anthropic', 'anthropic api', 'anthropic.com',
        'claude api', 'claude.ai',
        'claude 3', 'claude sonnet', 'claude opus', 'claude haiku',
        ' claude ',   # space-padded; lower weight in _PROVIDER_TERM_MAP
    ],
    'Google AI': [
        'vertex ai', 'google ai studio', 'google ai',
        'gemini api', 'gemini pro', 'gemini ultra', 'gemini flash',
        'gemma ',     # open-weight model
        'gemini ',    # broad; lower weight in _PROVIDER_TERM_MAP
        # removed: 'palm ' — Palm 2 deprecated, too noisy (palm tree, palm beach)
    ],
    'Cohere': [
        'cohere', 'cohere api', 'cohere.ai',
        'command-r', 'command r', 'cohere embed', 'cohere generate',
    ],
    'Mistral': [
        'mistral ai', 'mistral api', 'mistral.ai',
        'mixtral', 'mistral-7b', 'mistral-8x',
        # bare 'mistral' kept for compound mentions but lower weight in _PROVIDER_TERM_MAP
        'mistral ',
    ],
    # ------------------------------------------------------------------ #
    # Open-source / inference providers — major gap filled below          #
    # ------------------------------------------------------------------ #
    'Meta / Llama': [
        'llama 3', 'llama3', 'llama 2', 'llama2', 'llama 3.1', 'llama 3.2',
        'meta llama', 'meta ai', 'meta.ai',
        'llama api', 'llama model',
    ],
    'xAI / Grok': [
        'xai grok', 'grok api', 'grok-1', 'grok-2', 'x.ai',
        'xai.com', 'grok llm',
    ],
    'Hugging Face': [
        'hugging face', 'huggingface', 'huggingface.co',
        'hugging face api', 'inference api', 'transformers library',
        'hf inference',
    ],
    'Together AI': [
        'together ai', 'together.ai', 'together api',
        'togetherai',
    ],
    'Replicate': [
        'replicate.com', 'replicate api',
        # NOTE: bare ' replicate ' excluded — too common as an English verb
        # ("easy to replicate", "hard to replicate"). Use domain/API references only.
    ],
    'Groq': [
        'groq api', 'groq.com', 'groq inference',
        # NOTE: bare 'groq' excluded — too common as a word/typo
    ],
}


def _keyword_scan(text: str, keyword_map: dict) -> dict:
    """
    Scan text for provider keywords. Returns {provider_name: [matched_keywords]}.
    Only returns providers that actually matched.

    Normalises the input by replacing non-alphanumeric characters (commas, slashes,
    hyphens, etc.) with spaces so that space-padded keywords like ' aws ' correctly
    match 'AWS,' or 'AWS/on-prem'. Multi-word phrases like 'google cloud' are
    unaffected since their internal spaces survive normalisation.
    """
    # Lowercase, replace every non-alphanumeric non-space char with a space,
    # then wrap with leading/trailing spaces so boundary keywords match at edges.
    normalised = re.sub(r'[^a-z0-9 ]', ' ', text.lower())
    normalised = f' {normalised} '
    matches = {}
    for provider, keywords in keyword_map.items():
        found = [kw for kw in keywords if kw in normalised]
        if found:
            matches[provider] = found
    return matches


# ---------------------------------------------------------------------------
# Sentence-aware job posting scanner (used by _check_job_postings only)
# ---------------------------------------------------------------------------

# Sentence / bullet-point boundaries in job description text.
# Splits on: one-or-more newlines, period + 2+ spaces (paragraph break),
# period + space + capital letter (new sentence).
# Does NOT split on abbreviations like "e.g." or "Inc." (lowercase follows).
_SENTENCE_SPLIT_RE = re.compile(
    r'\n+'               # newlines — bullet / section boundaries
    r'|\.\s{2,}'         # period + 2+ spaces — paragraph break
    r'|\.\s+(?=[A-Z])',  # period + space + capital — new sentence start
    re.UNICODE,
)

# Verbs that indicate the company itself runs on the provider
# ("built natively on AWS", "our platform runs on GCP", "powered by Azure")
_OWNERSHIP_VERBS_RE = re.compile(
    r'\b(built?\s+(?:natively\s+)?on|runs?\s+on|running\s+on|'
    r'powered\s+by|hosted?\s+on|hosted?\s+(?:in|with)|'
    r'deployed?\s+on|operates?\s+on|built?\s+(?:with|using|for)|'
    r'natively\s+on|infrastructure\s+(?:on|in|runs?\s+on)|'
    r'processed?\s+(?:by|on|through)|stored?\s+(?:on|in|by)|'
    r'transferred?\s+(?:to|for\s+processing\s+by)|'
    r'servers?\s+(?:are\s+)?(?:located|hosted|operated))\b',
    re.IGNORECASE,
)

# Possessive ownership markers in job descriptions — phrases within ~50 chars of a
# provider keyword that indicate the company's *own* infrastructure, not a skill list.
# E.g. "our internal AWS environment", "our primary GCP project", "own Azure tenant".
_JOB_OWNERSHIP_RE = re.compile(
    r'\b(our\s+(?:internal|primary|own|production|core|main)\b|'
    r'\binternal\s+\w{0,20}\s*(?:AWS|GCP|Azure|cloud)\b|'
    r'\bprimary\s+(?:cloud|provider|environment)\b)',
    re.IGNORECASE,
)

# Explicit primacy declarations in job postings — "{provider} primarily" or
# "primarily {provider}" — the strongest possible signal that a specific provider
# is the company's main cloud, even in a multi-cloud context.
# E.g. "AWS primarily, with Azure and GCP for enterprise customers"
#      "primarily on GCP, with some Azure workloads"
_PROVIDER_PRIMARILY_RE = re.compile(
    r'(?:'
    r'(?P<before>AWS|GCP|Google Cloud|Azure|Cloudflare|Vercel|Netlify)\s+primarily'
    r'|primarily\s+(?:on\s+)?(?P<after>AWS|GCP|Google Cloud|Azure|Cloudflare|Vercel|Netlify)'
    r')',
    re.IGNORECASE,
)

# Customer-deployment qualifier — phrases that indicate a provider is used for
# customer/enterprise deployments, not the company's own primary infrastructure.
# "with Azure and GCP support for enterprise customers"
# "Azure and GCP available for on-prem deployments"
_CUSTOMER_DEPLOY_RE = re.compile(
    r'\b(?:support\s+for|available\s+for|option\s+for|for\s+(?:enterprise|on.prem|self.host))\s+'
    r'(?:enterprise\s+)?(?:customers?|deployments?|installations?|tenants?)\b',
    re.IGNORECASE,
)

# Verbs that indicate the provider is a target/customer environment, not the company's own
# ("secure your AWS", "protect Azure workloads", "monitor GCP environments")
_PRODUCT_VERBS_RE = re.compile(
    r'\b(secures?|protects?|monitors?|detects?\s+(?:threats?|risks?)|'
    r'your\s+\w+\s*(?:AWS|GCP|Azure|cloud)|'
    r'(?:AWS|GCP|Azure)\s+(?:environment|workload|infrastructure|stack|account|tenant)|'
    r'across\s+your|within\s+your|integrates?\s+with|supports?\s+your|'
    r'manages?\s+your|scans?\s+your|covers?\s+your)\b',
    re.IGNORECASE,
)


def _split_job_sentences(text: str) -> list:
    """Split job description text into sentences / bullet-point fragments."""
    parts = _SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _is_conjunctive_sentence(
    sentence: str,
    matched_providers: list,
    keyword_map: dict,
    max_gap: int = 60,
) -> bool:
    """
    Return True when a sentence matching 2+ cloud providers looks like a
    skill-list enumeration rather than a real infrastructure description.

    Conjunctive (noise):
        "Knowledge of cloud services (AWS, Azure, Google Cloud)"
        "AWS or GCP experience preferred"

    Not conjunctive (signal):
        "migrated from Azure to AWS"  — 'to' is not a list connector
        "runs across mixed AWS/on-prem nodes" — only one provider

    Detection: at least one pair of provider keywords must be within
    `max_gap` characters of each other AND have a list connector
    (comma, 'or', 'and', '/') in the span between them.
    """
    if len(matched_providers) < 2:
        return False

    sentence_lower = sentence.lower()
    _connector_re = re.compile(r',|\bor\b|\band\b|\s*/\s*')

    # Find earliest occurrence of any keyword for each provider
    positions: dict = {}
    for provider in matched_providers:
        for kw in keyword_map.get(provider, []):
            pos = sentence_lower.find(kw.strip())   # strip edge spaces before find
            if pos != -1:
                if provider not in positions or pos < positions[provider]:
                    positions[provider] = pos

    if len(positions) < 2:
        return False

    items = list(positions.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            _, pos_a = items[i]
            _, pos_b = items[j]
            if abs(pos_a - pos_b) > max_gap:
                continue  # too far apart — not the same list
            span_start = min(pos_a, pos_b)
            span_end   = max(pos_a, pos_b) + 30  # extend slightly past trailing keyword
            span = sentence_lower[span_start:span_end]
            if _connector_re.search(span):
                return True  # list connector between two close provider keywords

    return False


def _scan_job_sentences(
    text: str,
    keyword_map: dict,
    ai_keyword_map: Optional[dict] = None,
) -> tuple:
    """
    Sentence-aware wrapper around _keyword_scan() for job posting text.

    Unlike _keyword_scan() which scans the full text at once, this function:
      1. Splits the text into sentences / bullet points.
      2. Scans each sentence individually.
      3. Discards "conjunctive" sentences — those matching 2+ providers where
         the providers sit close together with a list connector between them.
         Example discarded: "AWS, Azure, or GCP experience preferred"
         Example kept:      "architecture spans mixed AWS/on-prem nodes"
      3a. Rescues providers from conjunctive sentences when an explicit primacy
          declaration is present ("AWS primarily, with Azure and GCP for enterprise
          customers") — only the named-primary provider is kept, customer-deployment
          providers are suppressed, and the primary is tagged for extra weight.
      3b. Rescues providers from conjunctive sentences when a possessive/ownership
          marker is present in the surrounding context ("our internal AWS environment").
      4. For AI keywords only: discards sentences that contain hiring-requirement
         phrases ("experience with", "familiarity with", "nice to have", etc.)
         since those indicate candidate skill requirements, not infrastructure usage.
         Example discarded: "Experience with OpenAI API required"
         Example kept:      "Build pipelines integrating our OpenAI-powered API"
      5. Union-merges signals from all non-conjunctive / rescued sentences.

    Returns:
        (matches, primary_providers) where:
          matches          — {provider_name: [matched_keywords]}  (same as _keyword_scan)
          primary_providers — set of provider names explicitly declared as primary
                             via "X primarily" / "primarily X" patterns — caller should
                             boost their signal weight to reflect explicit primacy.
    """
    merged: dict = {}
    merged_primary: set = set()   # providers with explicit "X primarily" declaration
    # Identify which providers are AI providers for the hiring-phrase filter
    ai_providers = set(ai_keyword_map.keys()) if ai_keyword_map else set()

    for sentence in _split_job_sentences(text):
        sent_matches = _keyword_scan(sentence, keyword_map)
        if not sent_matches:
            continue

        matched_providers = list(sent_matches.keys())

        # Multiple providers in this sentence — check for conjunctive list noise
        if len(matched_providers) >= 2 and _is_conjunctive_sentence(
            sentence, matched_providers, keyword_map
        ):
            # ── Pass 1: explicit primacy declaration ──────────────────────────
            # "AWS primarily, with Azure and GCP support for enterprise customers"
            # The named-primary provider is rescued with a 'primarily' marker so
            # the caller can give it extra weight.  Customer-deployment providers
            # (appearing after "support for enterprise customers") are suppressed.
            prim_match = _PROVIDER_PRIMARILY_RE.search(sentence)
            if prim_match:
                primary_name = (prim_match.group('before') or prim_match.group('after') or '').upper()
                # Normalise to our provider name (GCP vs Google Cloud)
                _alias = {'GOOGLE CLOUD': 'GCP'}
                primary_name = _alias.get(primary_name, primary_name)
                # Rescue only the primary provider; suppress the rest
                rescued = {p: kws for p, kws in sent_matches.items() if p.upper() == primary_name}
                if rescued:
                    # Tag the primary provider so caller can boost weight
                    sent_matches = rescued
                    for p in rescued:
                        if p not in merged_primary:
                            merged_primary.add(p)
                    # Fall through to normal merge below
                else:
                    continue
            else:
                # ── Pass 2: possessive / ownership-verb rescue ────────────────
                # "our internal AWS environment" — rescue providers with nearby
                # ownership markers; discard the rest.
                rescued = {}
                sentence_lower = sentence.lower()
                for provider, kws in sent_matches.items():
                    for kw in kws:
                        pos = sentence_lower.find(kw.strip())
                        if pos == -1:
                            continue
                        window = sentence[max(0, pos - 50): pos + len(kw) + 50]
                        if _JOB_OWNERSHIP_RE.search(window) or _OWNERSHIP_VERBS_RE.search(window):
                            rescued[provider] = kws
                            break
                if rescued:
                    sent_matches = rescued
                else:
                    continue   # discard — "AWS, Azure, GCP" style skill-list noise

        # For AI providers: discard sentences that read as hiring requirements.
        # "Experience with OpenAI required" is a skill requirement, not infra proof.
        # Cloud signals are exempt — cloud keywords (EKS, BigQuery, S3) are infra-specific
        # and don't typically appear as candidate skill requirements.
        if ai_providers:
            sentence_lower = sentence.lower()
            has_ai_match = any(p in ai_providers for p in matched_providers)
            if has_ai_match and any(phrase in sentence_lower for phrase in _JOB_HIRING_PHRASES):
                # Drop only the AI providers from this sentence; keep cloud matches
                sent_matches = {p: kws for p, kws in sent_matches.items() if p not in ai_providers}
                if not sent_matches:
                    continue

        # Single provider, or multi-provider non-conjunctive sentence — keep
        for provider, keywords in sent_matches.items():
            if provider not in merged:
                merged[provider] = []
            for kw in keywords:
                if kw not in merged[provider]:
                    merged[provider].append(kw)

    return merged, merged_primary


def _classify_website_sentences(text: str, keyword_map: dict) -> dict:
    """
    Sentence-aware cloud/AI keyword scanner for website content.

    Unlike _keyword_scan() (which returns flat matches) or _scan_job_sentences()
    (which filters conjunctive lists), this function additionally classifies each
    matched sentence by the verb context surrounding the provider mention:

      OWNERSHIP sentence  → provider appears with "built on", "runs on", "powered by", etc.
                            → emits at weight 1.0 (STRONG) — self-declared infrastructure
      PRODUCT sentence    → provider appears with "secure your", "protect", "monitor your"
                            → discarded — describes what the product does, not what it runs on
      NEUTRAL sentence    → provider mentioned without strong verb context
                            → kept at the page's default weight (caller decides)

    Returns a dict of the same shape as _keyword_scan() but with tuple values:
      { provider_name: (matched_keywords, is_ownership) }

    where is_ownership=True means the caller should use weight 1.0 / SignalStrength.STRONG.
    """
    ownership_results: dict = {}   # provider → ([keywords], True)
    neutral_results:   dict = {}   # provider → ([keywords], False)

    for sentence in _split_job_sentences(text):
        sent_matches = _keyword_scan(sentence, keyword_map)
        if not sent_matches:
            continue

        # Skip conjunctive sentences — "AWS, Azure, GCP" skill lists
        matched_providers = list(sent_matches.keys())
        if len(matched_providers) >= 2 and _is_conjunctive_sentence(
            sentence, matched_providers, keyword_map
        ):
            continue

        # Classify the sentence
        is_product   = bool(_PRODUCT_VERBS_RE.search(sentence))
        is_ownership = bool(_OWNERSHIP_VERBS_RE.search(sentence))

        if is_product and not is_ownership:
            continue   # discard — "secure your AWS environment" etc.

        for provider, keywords in sent_matches.items():
            if is_ownership:
                # Ownership takes precedence — upgrade this provider
                if provider not in ownership_results:
                    ownership_results[provider] = ([], True)
                for kw in keywords:
                    if kw not in ownership_results[provider][0]:
                        ownership_results[provider][0].append(kw)
            else:
                # Neutral — only add if not already seen as ownership
                if provider not in ownership_results and provider not in neutral_results:
                    neutral_results[provider] = ([], False)
                if provider not in ownership_results:
                    for kw in keywords:
                        if kw not in neutral_results[provider][0]:
                            neutral_results[provider][0].append(kw)

    # Merge: ownership results take priority over neutral
    return {**neutral_results, **ownership_results}


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
        industry: Optional[str] = None,
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
                # If only one side is N/A, check for a partnership override first,
                # then fall back to full signal gathering for the non-N/A side.
                if cloud_result is None or ai_result is None:
                    # Check partnership overrides before running the full signal pipeline
                    override = PARTNERSHIP_OVERRIDES.get(company_name)
                    if override:
                        if cloud_result is None and override.get('cloud'):
                            cloud_result = self._create_override_attribution(
                                company_name, override.get('cloud'), ProviderType.CLOUD
                            )
                        if ai_result is None and override.get('ai'):
                            ai_result = self._create_override_attribution(
                                company_name, override.get('ai'), ProviderType.AI
                            )
                    # For any side still unresolved, run full signal gathering
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

        # Hardware prior — for physical-system companies (chip design, robotics, space,
        # nuclear, biotech-mfg) on-premises is the default assumption.
        #
        # Only signals with confidence_weight >= 0.6 are considered "strong enough" to
        # override the prior.  Weight 0.3 signals (ip_asn, investor_prior, founder_prior,
        # http_headers, dns_cname, homepage_investor, weak partnership news) are suppressed
        # because they reflect context (who funded or founded the company) rather than
        # actual cloud infrastructure choices.
        #
        # Signals that DO override the prior (weight >= 0.6):
        #   - Job postings explicitly naming cloud services (0.6)
        #   - Tech blog posts with specific cloud infra keywords (0.6)
        #   - DNS CNAME / IP/ASN pointing directly to a cloud range (1.0 / 0.6)
        #   - Subprocessors list entries (1.0)
        #   - Formal partnership pages (1.0)
        _HARDWARE_MIN_WEIGHT = 0.6
        is_hardware = bool(
            industry and
            any(h in industry.lower() for h in HARDWARE_INDUSTRIES)
        )
        if is_hardware:
            strong_cloud = [s for s in cloud_signals
                            if s.confidence_weight >= _HARDWARE_MIN_WEIGHT]
            if not strong_cloud:
                # No meaningful cloud evidence — clear signals so we return On-Premises
                if cloud_signals:
                    suppressed = [(s.signal_source, s.confidence_weight) for s in cloud_signals]
                    print(f"  ℹ️  Hardware prior: suppressing {len(cloud_signals)} weak "
                          f"cloud signal(s) {suppressed} → defaulting to On-Premises")
                cloud_signals = []

        if is_hardware and not cloud_signals:
            # Synthesise an On-Premises result rather than returning Unknown
            cloud_attribution = Attribution(
                provider_type=ProviderType.CLOUD,
                is_multi=False,
                primary_provider='On-Premises',
                providers=[ProviderEntry(
                    provider_name='On-Premises',
                    role='Hardware company — on-premises prior (no cloud signals found)',
                    confidence=0.15,
                    entrenchment=EntrenchmentLevel.WEAK,
                    raw_score=0.3,
                    signals=[],
                )],
                confidence=0.15,
                evidence_count=0,
                signals=[],
            )
        else:
            cloud_attribution = self._calculate_attribution(cloud_signals, ProviderType.CLOUD)

        ai_attribution = self._calculate_attribution(ai_signals, ProviderType.AI)

        # 4. Fallback tiers — only triggered when deterministic signals are weak
        cloud_needs_fallback = (
            cloud_attribution is None or
            cloud_attribution.confidence < self.LLM_FALLBACK_THRESHOLD
        )
        ai_needs_fallback = (
            ai_attribution is None or
            ai_attribution.confidence < self.LLM_FALLBACK_THRESHOLD
        )

        if cloud_needs_fallback or ai_needs_fallback:
            needed = []
            if cloud_needs_fallback:
                needed.append('cloud')
            if ai_needs_fallback:
                needed.append('ai')

            # ── Tier 4a: Perplexity live web search ──────────────────────────
            perplexity_key = os.getenv('PERPLEXITY_API_KEY', '')
            if perplexity_key:
                print(f"  🔍 Perplexity search triggered for: {', '.join(needed)} "
                      f"(confidence below {self.LLM_FALLBACK_THRESHOLD:.0%})")
                perplexity_signals = self._perplexity_attribution_search(
                    company_name=company_name,
                    website=website,
                    need_cloud=cloud_needs_fallback,
                    need_ai=ai_needs_fallback,
                )
                if perplexity_signals:
                    print(f"    ✅ Perplexity: {len(perplexity_signals)} signals found")
                    if cloud_needs_fallback:
                        new_cloud_sigs = cloud_signals + [
                            s for s in perplexity_signals if s.provider_type == ProviderType.CLOUD
                        ]
                        cloud_attribution = self._calculate_attribution(new_cloud_sigs, ProviderType.CLOUD)
                        # Re-evaluate whether we still need Claude Haiku for cloud
                        cloud_needs_fallback = (
                            cloud_attribution is None or
                            cloud_attribution.confidence < self.LLM_FALLBACK_THRESHOLD
                        )
                    if ai_needs_fallback:
                        new_ai_sigs = ai_signals + [
                            s for s in perplexity_signals if s.provider_type == ProviderType.AI
                        ]
                        ai_attribution = self._calculate_attribution(new_ai_sigs, ProviderType.AI)
                        ai_needs_fallback = (
                            ai_attribution is None or
                            ai_attribution.confidence < self.LLM_FALLBACK_THRESHOLD
                        )
                else:
                    print(f"    ℹ️  Perplexity found no additional signals")

            # ── Tier 4b: Claude Haiku — reads pre-fetched homepage + article ─
            if (cloud_needs_fallback or ai_needs_fallback) and self.anthropic_client:
                needed_b = []
                if cloud_needs_fallback:
                    needed_b.append('cloud')
                if ai_needs_fallback:
                    needed_b.append('ai')
                print(f"  🤖 LLM fallback triggered for: {', '.join(needed_b)} "
                      f"(confidence below {self.LLM_FALLBACK_THRESHOLD:.0%})")

                llm_signals = self._llm_attribution_fallback(
                    company_name=company_name,
                    website=website,
                    article_text=article_text,
                    need_cloud=cloud_needs_fallback,
                    need_ai=ai_needs_fallback,
                )

                if llm_signals:
                    print(f"    ✅ LLM inference: {len(llm_signals)} signals")
                    if cloud_needs_fallback:
                        new_cloud_sigs = cloud_signals + [
                            s for s in llm_signals if s.provider_type == ProviderType.CLOUD
                        ]
                        cloud_attribution = self._calculate_attribution(new_cloud_sigs, ProviderType.CLOUD)
                    if ai_needs_fallback:
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
        cs_signals = self._check_cloud_case_studies(company_name, website)
        all_signals.extend(cs_signals)
        if cs_signals:
            print(f"    ✅ Case studies/marketplace: {len(cs_signals)} signals")

        # Public partnership announcements (Google News search)
        partner_signals = self._check_partnership_announcements(company_name, website)
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
        job_signals = self._check_job_postings(company_name, website)
        all_signals.extend(job_signals)
        if job_signals:
            print(f"    ✅ Job postings: {len(job_signals)} signals")

        # Website content scanning (integrations, docs, trust, blog)
        content_signals = self._scan_website_content(canonical)
        all_signals.extend(content_signals)
        if content_signals:
            print(f"    ✅ Website content: {len(content_signals)} signals")

        # Blog post deep-scan (RSS/sitemap → fetch relevant posts → STRONG signals)
        blog_signals = self._check_blog_posts(company_name, canonical)
        all_signals.extend(blog_signals)
        if blog_signals:
            print(f"    ✅ Blog posts: {len(blog_signals)} signals")

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

        # Other hosting platforms (Vercel, Netlify, Cloudflare, etc.) — fallback only.
        # Only fires when no cloud signals were found so it never competes with real infra signals.
        if canonical and not any(s.provider_type == ProviderType.CLOUD for s in all_signals):
            other_signals = self._check_other_providers(canonical)
            all_signals.extend(other_signals)
            if other_signals:
                names = ', '.join(s.provider_name for s in other_signals)
                print(f"    ✅ Other hosting platform: {names}")

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

        # ── All-3-providers guard ─────────────────────────────────────────────
        # If AWS, Azure, AND GCP are all present from shallow sources only
        # (tech_blog, partnership_announcement, website content), the signals are
        # uninformative — cloud security tools, integration marketplaces, and AI
        # platforms all list all three as supported environments.
        # EXCEPTION: if any of the three has an owned-infra signal (dns, ip_asn,
        # job_posting) we trust that evidence and let it through.
        _OWNED_INFRA_SOURCES = {'dns', 'ip_asn', 'job_posting', 'subprocessors'}
        if provider_type == ProviderType.CLOUD:
            detected = {s.provider_name for s in signals}
            if {'AWS', 'GCP', 'Azure'}.issubset(detected):
                # Check if any of the big-3 has a strong owned-infra source
                big3_owned = {
                    s.provider_name for s in signals
                    if s.provider_name in {'AWS', 'GCP', 'Azure'}
                    and s.signal_source in _OWNED_INFRA_SOURCES
                }
                if not big3_owned:
                    return None   # Unknown — all-3 from shallow sources is noise

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
            # Scale confidence by raw_score so weak signals don't show 100%.
            # Ceiling is raw_score=2.0 (STRONG entrenchment threshold) → 100%.
            # Examples: ip_asn only (0.3) → 15%, 1 job posting (0.6) → 30%,
            #           1 partnership page (1.0) → 50%, 2 strong signals (2.0) → 100%.
            scaled_confidence = round(min(winner.raw_score / 2.0, 1.0), 2)
            return Attribution(
                provider_type=provider_type,
                is_multi=False,
                primary_provider=winner.provider_name,
                providers=provider_entries,
                confidence=scaled_confidence,
                evidence_count=len(signals),
                signals=signals
            )

        top_score    = ranked[0][1]
        second_score = ranked[1][1]
        gap          = top_score - second_score

        # Use a higher gap threshold for AI — signals are noisier (no DNS/IP
        # deterministic tier), so require a wider margin before declaring single winner.
        threshold = AI_MULTI_PROVIDER_THRESHOLD if provider_type == ProviderType.AI else MULTI_PROVIDER_THRESHOLD

        if gap < threshold:
            result = Attribution(
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
            result = Attribution(
                provider_type=provider_type,
                is_multi=False,
                primary_provider=winner.provider_name,
                providers=provider_entries,
                confidence=min(round(top_score / total_score, 2), 1.0),
                evidence_count=len(signals),
                signals=signals
            )

        # ------------------------------------------------------------------ #
        # Hybrid detection: On-Premises + any cloud provider = Hybrid         #
        # Applies only to cloud attributions where both own-infra and         #
        # external-cloud signals were found.                                  #
        # ------------------------------------------------------------------ #
        if provider_type == ProviderType.CLOUD:
            provider_names = {p.provider_name for p in result.providers}
            if 'On-Premises' in provider_names and len(provider_names) > 1:
                result.primary_provider = 'Hybrid'
                result.is_multi = True

        return result

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

    def _check_cloud_case_studies(self, company_name: str, website: str = "") -> List[AttributionSignal]:
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

        # --- GCP Press Corner & Blog ---
        # Scans Google Cloud's official press corner and blog for announcements
        # involving this company. Uses Google News RSS with site: filters so results
        # are not limited by the 12-month recency window of a generic query.
        try:
            search_id = website if website else company_name
            gcp_press_q = quote_plus(
                f'"{search_id}" site:googlecloudpresscorner.com OR site:cloud.google.com/blog'
            )
            gcp_press_url = (
                f'https://news.google.com/rss/search'
                f'?q={gcp_press_q}&hl=en-US&gl=US&ceid=US:en'
            )
            r = requests.get(gcp_press_url, timeout=self.TIMEOUT,
                             headers={'User-Agent': 'Mozilla/5.0'}, verify=False)
            if r.status_code == 200 and r.content:
                feed = feedparser.parse(r.content)
                for entry in feed.entries[:20]:
                    title = entry.get('title', '')
                    name_match = name_lower in title.lower()
                    domain_match = website and website.lower() in title.lower()
                    if name_match or domain_match:
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.CLOUD,
                            provider_name='GCP',
                            signal_source='case_study',
                            signal_strength=SignalStrength.STRONG,
                            evidence_text=f'Google Cloud press corner/blog: {title[:120]}',
                            evidence_url=entry.get('link', gcp_press_url),
                            confidence_weight=1.0,
                        ))
                        break
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
    # Each entry is (term, provider_type, provider_name, weight_multiplier).
    # weight_multiplier (0.0–1.0) scales the temporal confidence weight:
    #   1.0 = full confidence (e.g. "google cloud" — unambiguous)
    #   0.5 = half confidence (e.g. bare "google" — could mean Google AI, not GCP)
    # More-specific terms must come before less-specific ones (matched in order).
    _PROVIDER_TERM_MAP: list = [
        # Cloud — major hyperscalers
        ('amazon web services', ProviderType.CLOUD, 'AWS',       1.0),
        (' aws ',               ProviderType.CLOUD, 'AWS',       1.0),
        ('amazon cloud',        ProviderType.CLOUD, 'AWS',       1.0),
        ('amazon',              ProviderType.CLOUD, 'AWS',       1.0),
        ('google cloud',        ProviderType.CLOUD, 'GCP',       1.0),
        (' google ',            ProviderType.CLOUD, 'GCP',       0.5),  # weaker — bare Google mention
        ('microsoft azure',     ProviderType.CLOUD, 'Azure',     1.0),
        (' azure ',             ProviderType.CLOUD, 'Azure',     1.0),
        ('microsoft',           ProviderType.CLOUD, 'Azure',     1.0),
        # Cloud — specialist GPU/AI cloud providers
        ('coreweave',              ProviderType.CLOUD, 'CoreWeave',  1.0),
        ('lambda labs',            ProviderType.CLOUD, 'Lambda',     1.0),
        ('lambda.cloud',           ProviderType.CLOUD, 'Lambda',     1.0),
        ('crusoe',                 ProviderType.CLOUD, 'Crusoe',     1.0),
        ('ovhcloud',               ProviderType.CLOUD, 'OVH',        1.0),
        ('ovh cloud',              ProviderType.CLOUD, 'OVH',        1.0),
        ('vultr',                  ProviderType.CLOUD, 'Vultr',      1.0),
        ('paperspace',             ProviderType.CLOUD, 'Paperspace', 1.0),
        ('nebius',                 ProviderType.CLOUD, 'Nebius',     1.0),
        ('fluidstack',             ProviderType.CLOUD, 'Fluidstack', 1.0),
        ('vast.ai',                ProviderType.CLOUD, 'Vast.ai',    1.0),
        ('oracle cloud',           ProviderType.CLOUD, 'OCI',        1.0),
        # On-Premises — MEDIUM weight (indirect signals); air-gapped is STRONG
        ('on-premises',            ProviderType.CLOUD, 'On-Premises', 0.6),
        ('on-prem',                ProviderType.CLOUD, 'On-Premises', 0.6),
        ('private datacenter',     ProviderType.CLOUD, 'On-Premises', 0.6),
        ('private data center',    ProviderType.CLOUD, 'On-Premises', 0.6),
        ('bare metal',             ProviderType.CLOUD, 'On-Premises', 0.6),
        ('air-gapped',             ProviderType.CLOUD, 'On-Premises', 1.0),
        # AI — unambiguous strong signals (1.0)
        ('openai',              ProviderType.AI,    'OpenAI',        1.0),
        ('openai api',          ProviderType.AI,    'OpenAI',        1.0),
        ('anthropic',           ProviderType.AI,    'Anthropic',     1.0),
        ('anthropic api',       ProviderType.AI,    'Anthropic',     1.0),
        ('claude api',          ProviderType.AI,    'Anthropic',     1.0),
        ('claude.ai',           ProviderType.AI,    'Anthropic',     1.0),
        ('vertex ai',           ProviderType.AI,    'Google AI',     1.0),
        ('google ai studio',    ProviderType.AI,    'Google AI',     1.0),
        ('gemini api',          ProviderType.AI,    'Google AI',     1.0),
        ('cohere',              ProviderType.AI,    'Cohere',        1.0),
        ('mistral ai',          ProviderType.AI,    'Mistral',       1.0),
        ('mistral api',         ProviderType.AI,    'Mistral',       1.0),
        ('mixtral',             ProviderType.AI,    'Mistral',       1.0),
        # Open-source / inference providers (1.0 — specific enough)
        ('llama 3',             ProviderType.AI,    'Meta / Llama',  1.0),
        ('llama 2',             ProviderType.AI,    'Meta / Llama',  1.0),
        ('meta llama',          ProviderType.AI,    'Meta / Llama',  1.0),
        ('llama api',           ProviderType.AI,    'Meta / Llama',  1.0),
        ('grok api',            ProviderType.AI,    'xAI / Grok',    1.0),
        ('grok-1',              ProviderType.AI,    'xAI / Grok',    1.0),
        ('grok-2',              ProviderType.AI,    'xAI / Grok',    1.0),
        ('hugging face',        ProviderType.AI,    'Hugging Face',  1.0),
        ('huggingface',         ProviderType.AI,    'Hugging Face',  1.0),
        ('together ai',         ProviderType.AI,    'Together AI',   1.0),
        ('together.ai',         ProviderType.AI,    'Together AI',   1.0),
        ('replicate.com',       ProviderType.AI,    'Replicate',     1.0),
        ('replicate api',       ProviderType.AI,    'Replicate',     1.0),
        ('groq api',            ProviderType.AI,    'Groq',          1.0),
        ('groq.com',            ProviderType.AI,    'Groq',          1.0),
        # AI — weaker/ambiguous signals (0.5) — common words that need corroboration
        (' claude ',            ProviderType.AI,    'Anthropic',     0.5),
        ('gemini ',             ProviderType.AI,    'Google AI',     0.5),
        ('mistral ',            ProviderType.AI,    'Mistral',       0.5),
        (' replicate ',         ProviderType.AI,    'Replicate',     0.5),
    ]

    def _check_partnership_announcements(self, company_name: str, website: str = "") -> List[AttributionSignal]:
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
            Return list of (ptype, provider, weight_multiplier) tuples matched
            in the given text. Uses _PROVIDER_TERM_MAP — most-specific terms first.
            weight_multiplier scales the temporal confidence weight for the signal.

            Roundup-title guard: news roundup headlines often join unrelated stories
            with semicolons (e.g. "A joins Amazon; ex-CEO lands at Code Metal").
            If the title contains semicolons, we split into clauses and only accept
            a provider match when the company name (or domain stem) appears in the
            SAME clause as the provider term. This prevents cross-clause false
            positives while still matching single-clause titles normally.
            """
            # Build per-clause segments when semicolons are present in the title
            title_lower = title.lower()
            if ';' in title_lower:
                # Split on semicolons; only keep clauses that contain the company
                clauses = [c.strip() for c in title_lower.split(';')]
                company_clauses = [
                    c for c in clauses
                    if company_lower in c or (domain_stem and domain_stem in c)
                ]
                # If company appears in none of the clauses, fall back to full text
                # (shouldn't happen given _is_valid_title already checked, but safe)
                search_text = f' {" ".join(company_clauses) if company_clauses else title_lower} {summary.lower()} '
            else:
                search_text = f' {title_lower} {summary.lower()} '

            matched = []
            seen_providers = set()
            for term, ptype, provider, wt in self._PROVIDER_TERM_MAP:
                if term in search_text and provider not in seen_providers:
                    matched.append((ptype, provider, wt))
                    seen_providers.add(provider)
            return matched

        # Domain stem = first label of the domain (e.g. "runwayml" from "runwayml.com",
        # "ricursive" from "ricursive.ai").  Used as a discriminating token in titles:
        # more specific than the company name (avoids "Runway Girl Network") and
        # catches legal-name variants (e.g. "Ricursive Intelligence" contains "ricursive").
        domain_stem = website.split('.')[0].lower() if website else ''
        _domain_stem_re = (
            re.compile(rf'\b{re.escape(domain_stem)}\b', re.IGNORECASE)
            if domain_stem else None
        )

        def _is_valid_title(title: str) -> bool:
            """
            Company must appear in the title, not as part of a name-collision.
            Two ways to match:
              (a) company_lower substring in title  (e.g. 'elevenlabs' in 'ElevenLabs launches...')
              (b) domain stem as a word token        (e.g. 'ricursive' in 'Ricursive Intelligence...')
            The domain-stem check is more discriminating than the raw company name
            (e.g. 'runwayml' won't match 'Runway Girl Network').
            """
            tl = title.lower()
            if _collision_prefix_re.search(tl):
                return False
            if _domain_stem_re and _domain_stem_re.search(tl):
                return True
            return company_lower in tl

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
        # Query strategy: '"domain.com" OR domain_stem [OR "company name"] action_terms'
        # - Quoted domain catches articles that mention the URL directly
        # - Unquoted stem catches press articles that use the legal/full company name
        #   (e.g. '"ricursive.ai" OR ricursive' catches "Ricursive Intelligence raises $335M")
        #   (e.g. '"runwayml.com" OR runwayml' avoids "Runway Girl Network" at title-filter time)
        # - Quoted company name (when multi-word) catches articles that spell it with spaces
        #   (e.g. '"worldlabs.com" OR worldlabs OR "world labs"' catches TechCrunch "World Labs")
        # The title filter (_is_valid_title) enforces stem-as-word-token to avoid false positives.
        if website and domain_stem:
            # Add quoted company name when it's multi-word — articles may spell it
            # with spaces (e.g. "World Labs") while the domain stem is one word ("worldlabs").
            # Skip when company name is single-word (already covered by domain_stem token).
            if ' ' in company_lower:
                gnews_q = quote_plus(f'"{website}" OR {domain_stem} OR "{company_lower}" {action_terms}')
            else:
                gnews_q = quote_plus(f'"{website}" OR {domain_stem} {action_terms}')
        else:
            search_id = website if website else company_name
            gnews_q = quote_plus(f'"{search_id}" {action_terms}')
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
                    link    = entry.get('link', '')
                    pub     = entry.get('published', '')

                    if not _is_valid_title(title):
                        continue

                    # GNews summaries are redirect URL blobs, not readable text —
                    # pass only the title to avoid false positives from URL tokens
                    # (e.g. ' google ' matching inside 'news.google.com/rss/...')
                    matches = _classify_entry(title)
                    strength, weight, age_label = _temporal_weight(pub)

                    for ptype, provider, wt_multiplier in matches:
                        if provider not in found_providers:
                            found_providers.add(provider)
                            signals.append(AttributionSignal(
                                provider_type=ptype,
                                provider_name=provider,
                                signal_source='partnership_announcement',
                                signal_strength=strength,
                                evidence_text=f'Partnership news ({age_label}): {title[:100]}',
                                evidence_url=link,
                                confidence_weight=weight * wt_multiplier
                            ))

        except Exception:
            pass

        # ------------------------------------------------------------------ #
        # SOURCE 2: Brave Search API — 3 batched queries (was: 21+16=37)    #
        #                                                                    #
        # Instead of one query per provider (21 calls), we fire 3 batched   #
        # OR-queries covering all providers in a single round-trip each:    #
        #   Batch A — Hyperscalers (AWS / GCP / Azure)                      #
        #   Batch B — Neo/GPU clouds                                         #
        #   Batch C — AI providers                                           #
        #                                                                    #
        # Results are parsed with _classify_entry (same as Google News),    #
        # which already handles all provider terms via _PROVIDER_TERM_MAP.  #
        # Inverted site: searches are kept only for the 3 hyperscalers      #
        # (they publish customer case studies; AI providers don't).         #
        # ------------------------------------------------------------------ #

        _STRENGTH_MAP = {'strong': SignalStrength.STRONG, 'medium': SignalStrength.MEDIUM, 'weak': SignalStrength.WEAK}

        company_q = f'"{company_name}"'
        if domain_stem and domain_stem not in company_lower:
            company_q = f'{company_q} OR {domain_stem}'
        action_kw = 'partnership OR announces OR integrates OR "built on" OR "powered by" OR deal OR raises'

        # Provider terms grouped into 3 batches — all piped into one query each
        _search_batches = [
            # Batch A: Hyperscalers
            ('cloud_hyper', ProviderType.CLOUD,
             '"Amazon Web Services" OR AWS OR "Google Cloud" OR "Microsoft Azure" OR Azure'),
            # Batch B: Neo/GPU clouds
            ('cloud_neo', ProviderType.CLOUD,
             'CoreWeave OR "Lambda Labs" OR Crusoe OR OVHcloud OR Vultr OR Paperspace OR Nebius OR "Oracle Cloud"'),
            # Batch C: AI providers
            ('ai', ProviderType.AI,
             'OpenAI OR Anthropic OR "Vertex AI" OR "Gemini API" OR Cohere OR Mistral OR '
             '"Meta Llama" OR "Hugging Face" OR "Together AI" OR Groq OR "xAI"'),
        ]

        for _batch_id, _ptype, provider_terms_str in _search_batches:
            try:
                batch_q = f'{company_q} ({provider_terms_str}) {action_kw}'
                results = serper_search(batch_q, num=10, source='attribution')

                for item in results:
                    title   = item.get('title', '')
                    url     = item.get('url', '')
                    snippet = item.get('snippet', '')
                    date    = item.get('date', '')

                    if not _is_valid_title(title):
                        continue

                    str_strength, weight, age_label = parse_age_to_strength(date)
                    strength = _STRENGTH_MAP.get(str_strength, SignalStrength.MEDIUM)

                    for ptype, provider, wt_multiplier in _classify_entry(title, snippet):
                        if provider not in found_providers:
                            found_providers.add(provider)
                            signals.append(AttributionSignal(
                                provider_type=ptype,
                                provider_name=provider,
                                signal_source='partnership_announcement',
                                signal_strength=strength,
                                evidence_text=f'Partnership news ({age_label}): {title[:100]}',
                                evidence_url=url,
                                confidence_weight=weight * wt_multiplier,
                            ))

            except Exception:
                continue

            # ---------------------------------------------------------------- #
            # INVERTED SEARCH — hyperscalers only (3 calls max)               #
            # site:aws.amazon.com / cloud.google.com / azure.microsoft.com    #
            # Skipped for AI providers — they rarely publish startup-specific  #
            # content on their own domains that would rank in Brave.           #
            # ---------------------------------------------------------------- #
            _inverted_cloud = {
                'AWS':   'aws.amazon.com',
                'GCP':   'cloud.google.com',
                'Azure': 'azure.microsoft.com',
            }
            for provider, inv_domain in _inverted_cloud.items():
                if provider in found_providers:
                    continue  # already have a signal
                ptype = ProviderType.CLOUD
                inverted_q = f'site:{inv_domain} "{company_name}"'
                try:
                    inv_results = serper_search(inverted_q, num=5, source='attribution')

                    for item in inv_results:
                        title   = item.get('title', '')
                        url     = item.get('url', '')
                        snippet = item.get('snippet', '')
                        date    = item.get('date', '')

                        if not _is_valid_title(title):
                            continue

                        entry_text = f'{title} {snippet}'.lower()
                        if company_lower not in entry_text and (
                            not domain_stem or domain_stem not in entry_text
                        ):
                            continue

                        str_strength, weight, age_label = parse_age_to_strength(date)
                        strength = _STRENGTH_MAP.get(str_strength, SignalStrength.MEDIUM)

                        if provider not in found_providers:
                            found_providers.add(provider)
                            signals.append(AttributionSignal(
                                provider_type=ptype,
                                provider_name=provider,
                                signal_source='partnership_announcement',
                                signal_strength=strength,
                                evidence_text=f'Provider news ({age_label}): {title[:100]}',
                                evidence_url=url,
                                confidence_weight=weight,
                            ))
                            break

                except Exception:
                    continue

        # ------------------------------------------------------------------ #
        # SOURCE 3: Broad company article scan (body-level keyword)         #
        #                                                                    #
        # The per-provider queries above require the provider term to       #
        # appear in the title or snippet — missing providers that are       #
        # mentioned only in the article body (e.g. a funding article that   #
        # says "we use OpenAI and Gemini" in the body, not the title).      #
        #                                                                    #
        # This source fetches the top 10 company-focused news articles      #
        # (no provider filter) and runs the full cloud/AI keyword scan      #
        # on the article body — catching any provider mentions regardless   #
        # of where they appear in the text.                                 #
        # ------------------------------------------------------------------ #
        try:
            # Broad company news query — no provider terms, just recency signal
            broad_q = f'"{company_name}" (funding OR launches OR raises OR announces OR "built on" OR "powered by")'
            if domain_stem and domain_stem not in company_lower:
                broad_q = f'("{company_name}" OR {domain_stem}) (funding OR launches OR raises OR announces OR "built on" OR "powered by")'

            broad_results = serper_search(broad_q, num=10, source='attribution')
            for item in broad_results:
                art_url   = item.get('url', '')
                art_title = item.get('title', '')
                art_date  = item.get('date', '')
                if not art_url or not _is_valid_title(art_title):
                    continue

                # Fetch and scan the article body
                body = self._fetch_article_excerpt(art_url, max_chars=4000)
                if not body:
                    continue

                # Keyword-scan body for cloud and AI provider terms
                cloud_hits = _keyword_scan(body, CLOUD_KEYWORDS)
                ai_hits    = _keyword_scan(body, AI_KEYWORDS)

                str_strength, weight, age_label = parse_age_to_strength(art_date)
                strength = _STRENGTH_MAP.get(str_strength, SignalStrength.MEDIUM)

                for provider, kws in {**cloud_hits, **ai_hits}.items():
                    if provider in found_providers:
                        continue
                    ptype = (
                        ProviderType.CLOUD if provider in cloud_hits
                        else ProviderType.AI
                    )
                    found_providers.add(provider)
                    signals.append(AttributionSignal(
                        provider_type=ptype,
                        provider_name=provider,
                        signal_source='partnership_announcement',
                        signal_strength=strength,
                        evidence_text=f'Provider mention in article ({age_label}): {art_title[:100]}',
                        evidence_url=art_url,
                        confidence_weight=weight,
                    ))
        except Exception:
            pass

        # Step 3.5: Batch LLM relevance filter — discard signals about unrelated entities
        if signals and self.anthropic_client:
            signals = self._filter_signals_by_relevance(signals, company_name, website)
        return signals

    def _fetch_article_excerpt(self, url: str, max_chars: int = 1200) -> str:
        """
        Fetch a news article URL and return a plain-text excerpt of its body.

        Google News links redirect through their own router — we follow redirects.
        Returns '' on any failure (timeout, paywall, bot-block).
        """
        try:
            r = requests.get(
                url, timeout=8, headers=self.HEADERS,
                verify=False, allow_redirects=True
            )
            if r.status_code != 200:
                return ''
            soup = BeautifulSoup(r.text, 'html.parser')
            # Remove nav/header/footer/script clutter
            for tag in soup(['script', 'style', 'nav', 'footer', 'header',
                             'aside', 'form', 'noscript']):
                tag.decompose()
            # Prefer article body; fall back to full page text
            article = soup.find('article') or soup.find(attrs={'role': 'main'}) or soup
            text = article.get_text(separator=' ', strip=True)
            # Collapse whitespace
            text = re.sub(r'\s+', ' ', text)
            return text[:max_chars]
        except Exception:
            return ''

    def _filter_signals_by_relevance(
        self,
        signals: List[AttributionSignal],
        company_name: str,
        website: str,
    ) -> List[AttributionSignal]:
        """
        Batch-filter partnership announcement signals using Claude Haiku.

        Two-stage process per signal:
          1. Fetch the article body (following any redirects from Google News).
          2. Pass title + article excerpt to Haiku to confirm whether the article
             is genuinely about THIS startup partnering with the identified
             cloud/AI provider — not just a title keyword match.

        Title-only filtering is insufficient for generic company names (e.g.
        "loyal", "vector", "arc") where the word appears in unrelated articles.
        Reading even 1000 chars of article body gives the LLM enough context to
        distinguish "Loyal.com (dog longevity startup) + GCP" from "loyal wingman
        drone programme + GCP".

        Falls back to returning all signals unfiltered if the LLM call or JSON
        parse fails — a network or billing error never silently drops signals.

        Cost: ~$0.0002 per call (~800 tokens at Haiku pricing).
        """
        # Build article blocks: title + fetched body excerpt
        lines = []
        for i, sig in enumerate(signals):
            raw = sig.evidence_text or ''
            colon_pos = raw.find(': ')
            title_part = raw[colon_pos + 2:] if colon_pos != -1 else raw

            # Fetch article body for richer context
            excerpt = ''
            if sig.evidence_url:
                excerpt = self._fetch_article_excerpt(sig.evidence_url)

            if excerpt:
                lines.append(
                    f'{i + 1}. TITLE: {title_part}\n'
                    f'   PROVIDER DETECTED: {sig.provider_name}\n'
                    f'   ARTICLE EXCERPT: {excerpt[:1000]}'
                )
            else:
                lines.append(
                    f'{i + 1}. TITLE: {title_part}\n'
                    f'   PROVIDER DETECTED: {sig.provider_name}\n'
                    f'   ARTICLE EXCERPT: (could not fetch)'
                )

        articles_block = '\n\n'.join(lines)

        prompt = f"""You are a signal validator for a startup cloud-infrastructure intelligence system.

Company: {company_name}
Website: {website}

The system found news articles while searching for partnerships between
"{company_name}" (the startup at {website}) and cloud or AI providers.

For each article below, decide:
- KEEP: the article is genuinely about THIS startup ({company_name} at {website})
  using, partnering with, or deploying the detected cloud/AI provider.
- DISCARD: the article is about a different entity that shares a word with the
  company name, OR the provider mention is incidental/unrelated to the startup.

Be strict: if the article body does not clearly tie THIS specific startup to the
detected provider, DISCARD it. A title keyword match alone is not enough.

Articles to validate:
{articles_block}

Respond with a JSON array — one object per article in order:
  "index": integer (1-based)
  "keep": boolean
  "reason": one short phrase explaining the decision

Return ONLY valid JSON — no markdown, no prose.

JSON:"""

        try:
            response = self.anthropic_client.messages.create(
                model='claude-haiku-4-5-20251001',
                max_tokens=600,
                messages=[{'role': 'user', 'content': prompt}]
            )
            raw = response.content[0].text.strip()
        except Exception as e:
            print(f"    ⚠️  Relevance filter LLM call failed: {e} — passing all signals through")
            return signals

        try:
            if raw.startswith('```'):
                raw = re.sub(r'^```[a-z]*\n?', '', raw)
                raw = re.sub(r'\n?```$', '', raw)

            decisions = json.loads(raw)
            if not isinstance(decisions, list):
                print(f"    ⚠️  Relevance filter: unexpected JSON shape — passing all signals through")
                return signals

            keep_indices: set = set()
            for item in decisions:
                idx = int(item.get('index', 0))
                keep = bool(item.get('keep', True))
                reason = item.get('reason', '')
                if keep and 1 <= idx <= len(signals):
                    keep_indices.add(idx - 1)
                elif not keep:
                    print(f"    🗑️  Discarded signal #{idx} ({signals[idx-1].provider_name}): {reason}")

            # Any signal the LLM didn't mention defaults to KEEP
            mentioned = {int(item.get('index', 0)) - 1 for item in decisions}
            for i in range(len(signals)):
                if i not in mentioned:
                    keep_indices.add(i)

            filtered = [sig for i, sig in enumerate(signals) if i in keep_indices]
            discarded = len(signals) - len(filtered)
            if discarded:
                print(f"    🔍 Relevance filter: discarded {discarded} off-topic signal(s) "
                      f"({len(filtered)} kept)")
            return filtered

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            print(f"    ⚠️  Relevance filter parse error: {e} — passing all signals through")
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

    def _check_other_providers(self, website: str) -> List[AttributionSignal]:
        """
        Detect "Other" hosting platforms (Vercel, Netlify, Cloudflare, etc.) via HTTP
        headers and DNS CNAMEs.

        This is a fallback — called only when no cloud signals (AWS/GCP/Azure) were found.
        Emits WEAK (0.3) signals so companies show a named platform instead of Unknown,
        but at low confidence since we're identifying the hosting layer, not the underlying cloud.
        """
        signals: List[AttributionSignal] = []
        seen: set = set()

        # --- Header check ---
        try:
            response = requests.get(
                f'https://{website}', timeout=self.TIMEOUT,
                headers=self.HEADERS, allow_redirects=True, verify=False
            )
            headers_lower = {k.lower(): v for k, v in response.headers.items()}

            for header_name, value_substr, platform in OTHER_PLATFORM_HEADERS:
                if header_name not in headers_lower:
                    continue
                header_val = headers_lower[header_name].lower()
                if value_substr is None or value_substr in header_val:
                    if platform not in seen:
                        seen.add(platform)
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.CLOUD,
                            provider_name=platform,
                            signal_source='http_headers',
                            signal_strength=SignalStrength.WEAK,
                            evidence_text=f'Hosted on {platform} (HTTP header: {header_name})',
                            confidence_weight=0.3,
                        ))
        except Exception:
            pass

        # --- DNS CNAME check (only if headers didn't already identify the platform) ---
        if not seen:
            try:
                answers = dns.resolver.resolve(website, 'CNAME')
                for rdata in answers:
                    cname = str(rdata.target).lower().rstrip('.')
                    for pattern, platform in OTHER_PLATFORM_CNAMES.items():
                        if pattern in cname and platform not in seen:
                            seen.add(platform)
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.CLOUD,
                                provider_name=platform,
                                signal_source='dns_cname',
                                signal_strength=SignalStrength.WEAK,
                                evidence_text=f'Hosted on {platform} (CNAME: {cname})',
                                confidence_weight=0.3,
                            ))
            except Exception:
                pass

        return signals

    def _extract_ashby_job_urls(
        self, index_url: str, soup: BeautifulSoup
    ) -> List[str]:
        """
        Extract individual job posting URLs from Ashby's embedded window.__appData.

        Ashby (jobs.ashbyhq.com) is a React SPA — the board index page renders all
        job listings client-side from JavaScript, so no <a href> job links appear in
        the initial HTML. However, the full job list is embedded as JSON in a plain
        <script> tag: ``window.__appData = { jobBoard: { jobPostings: [...] } }``.

        Each posting object contains an ``id`` (UUID) which forms the individual job
        URL as: ``https://jobs.ashbyhq.com/{org_slug}/{posting_id}``

        Returns a list of individual job page URLs, or [] on any failure.
        """
        script = soup.find('script', string=re.compile(r'window\.__appData'))
        if not script or not script.string:
            return []
        try:
            raw = script.string
            # Strip the "window.__appData = " prefix to get raw JSON
            brace_start = raw.index('{')
            json_str = raw[brace_start:]
            # Find the matching closing brace via depth counting (avoids regex on
            # potentially large JSON blobs with escaped braces in description HTML)
            depth, end = 0, 0
            for i, ch in enumerate(json_str):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            data = json.loads(json_str[:end])
            postings = data.get('jobBoard', {}).get('jobPostings', [])
            # Org slug from the index URL path, e.g. "/northwoodspace" → "northwoodspace"
            org_slug = urlparse(index_url).path.strip('/')
            # Ashby URLs are UUID-based — no job title in the path — so the
            # _is_eng_role() URL-keyword sorter in _check_job_postings() won't
            # fire.  Sort here by job title using the shared cloud-role keywords.
            def _is_eng_title(posting: dict) -> bool:
                title = posting.get('title', '').lower()
                return any(kw in title for kw in self._CLOUD_ROLE_KEYWORDS)

            sorted_postings = (
                [p for p in postings if _is_eng_title(p)] +
                [p for p in postings if not _is_eng_title(p)]
            )
            return [
                f'https://jobs.ashbyhq.com/{org_slug}/{p["id"]}'
                for p in sorted_postings
                if p.get('id')
            ]
        except Exception:
            return []

    def _extract_ashby_posting_text(self, soup: BeautifulSoup) -> str:
        """
        Extract the plain-text job description from an Ashby individual job page.

        Ashby individual job pages are also React SPAs — the job description is
        stored in window.__appData.posting.descriptionPlainText (inside an inline
        <script> tag), not in any rendered HTML element.  This method pulls that
        text out before the script tags are stripped, so keyword scanning works.

        Returns the plain-text description string, or '' on any failure.
        """
        script = soup.find('script', string=re.compile(r'window\.__appData'))
        if not script or not script.string:
            return ''
        try:
            raw = script.string
            brace_start = raw.index('{')
            json_str = raw[brace_start:]
            depth, end = 0, 0
            for i, ch in enumerate(json_str):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            data = json.loads(json_str[:end])
            return data.get('posting', {}).get('descriptionPlainText', '')
        except Exception:
            return ''

    def _extract_getro_posting_text(self, soup: BeautifulSoup) -> str:
        """
        Extract the HTML job description from a Getro-based VC board individual job page.

        Getro-powered job boards (8VC, a16z, Sequoia, etc.) are Next.js SPAs.
        The job description is embedded in the __NEXT_DATA__ <script> tag at:
          props.pageProps.initialState.jobs.currentJob.description  (HTML string)

        Returns plain text extracted from the description HTML, or '' on failure.
        """
        script = soup.find('script', id='__NEXT_DATA__')
        if not script or not script.string:
            return ''
        try:
            data = json.loads(script.string)
            current_job = (
                data.get('props', {})
                    .get('pageProps', {})
                    .get('initialState', {})
                    .get('jobs', {})
                    .get('currentJob', {})
            )
            description_html = current_job.get('description', '') or ''
            if not description_html:
                return ''
            # Parse the HTML description to get plain text
            desc_soup = BeautifulSoup(description_html, 'html.parser')
            return desc_soup.get_text(separator=' ', strip=True)
        except Exception:
            return ''

    def _discover_job_board_urls(
        self, company_name: str, website: str = ""
    ) -> tuple[list[str], dict[str, str]]:
        """
        Discover all job listing URLs for a company across known job boards.

        Three-phase strategy:
          Phase 1 — Board index pages: fetch the company's job board listing
            page (e.g. jobs.lever.co/cellares) and collect all individual
            job listing URLs linked from it.
          Phase 2 — Investor job boards: probe VC portfolio job boards
            (8VC, a16z, Sequoia, BVP, etc.) for the company's listings.
          Phase 3 — Google search fallback for missed boards.

        Returns:
            (all_job_urls, job_title_map) where:
            - all_job_urls: deduplicated list of individual job posting URLs
            - job_title_map: dict mapping URL → job title from anchor text
        """

        slug = company_name.lower().replace(" ", "").replace("-", "")
        # Hyphenated variant — many boards (Ashby, Rippling, Gem) use hyphens
        hyphen_slug_job = company_name.lower().replace(" ", "-")

        # Known job board domains — used both in the fixed probe list and to
        # detect career links on the company's own website.
        _KNOWN_JOB_BOARD_DOMAINS = {
            'jobs.lever.co', 'boards.greenhouse.io', 'jobs.ashbyhq.com',
            'apply.workable.com', 'jobs.gem.com', 'jobs.rippling.com', 'ats.rippling.com',
            'careers.jobvite.com', 'job-boards.greenhouse.io',
            'bamboohr.com', 'jobs.smartrecruiters.com', 'icims.com',
            'myworkdayjobs.com',
        }

        # Job board index URLs to probe — try both no-hyphen and hyphenated slugs
        # (Ashby, Gem, and Rippling commonly use hyphens; Lever/Greenhouse don't)
        board_index_urls = [
            f'https://jobs.lever.co/{slug}',
            f'https://boards.greenhouse.io/{slug}',
            f'https://jobs.ashbyhq.com/{slug}',
            f'https://jobs.ashbyhq.com/{hyphen_slug_job}',   # e.g. letter-ai
            f'https://apply.workable.com/{slug}',
        ]

        # Discover career links from the company's own website.
        # Startups that use non-standard boards (Gem, Rippling, Workday, etc.)
        # almost always link to their careers page from their homepage or /blog footer.
        # We scrape those links and add any recognised job board URLs to the probe list.
        #
        # Also handles Ashby embedded on the company's own domain — some companies
        # (e.g. braintrust.dev/careers?ashby_jid=...) serve Ashby's board directly
        # from their own subdomain/path rather than from jobs.ashbyhq.com.  These pages
        # still embed window.__appData with the full job listing JSON, so we detect the
        # pattern by probing /careers and /jobs for ashby_jid query-param links and,
        # when found, treat the company career page as an Ashby board index.
        if website:
            # Strip any existing scheme safely — .lstrip() strips *characters*,
            # not substrings, so 'thirdway.health'.lstrip('https://') incorrectly
            # eats the leading 't' and 'h'. Use re.sub instead.
            _clean = re.sub(r'^https?://', '', website)
            base = f'https://{_clean}'
            for home_url in [base, f'{base}/careers', f'{base}/jobs']:
                try:
                    r_home = requests.get(
                        home_url, timeout=self.TIMEOUT, headers=self.HEADERS, verify=False
                    )
                    if r_home.status_code != 200:
                        continue
                    home_soup = BeautifulSoup(r_home.text, 'html.parser')
                    ashby_embedded = False
                    for a in home_soup.find_all('a', href=True):
                        href = a['href']
                        # Absolute links to known external job board domains
                        if href.startswith('http'):
                            parsed_href = urlparse(href)
                            if any(parsed_href.netloc == bd or parsed_href.netloc.endswith('.' + bd)
                                   for bd in _KNOWN_JOB_BOARD_DOMAINS):
                                if href not in board_index_urls:
                                    board_index_urls.append(href)
                        # Relative links with ?ashby_jid= — Ashby embedded on own domain
                        if 'ashby_jid=' in href and not ashby_embedded:
                            # The current page IS the Ashby board index — add it
                            if home_url not in board_index_urls:
                                board_index_urls.append(home_url)
                            ashby_embedded = True
                except Exception:
                    continue

        # Collect individual job listing URLs by crawling the board index
        individual_job_urls: list[str] = []
        # Maps URL → job title text extracted from anchor text on the board index.
        # Populated when the board renders job titles in the <a> link text (e.g.
        # Rippling ATS uses UUID paths like /jobs/<uuid> with no role keywords in
        # the URL itself, so _is_eng_role() can't sort by URL alone).
        job_title_map: dict[str, str] = {}

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
                        # Capture anchor text as job title when present — used
                        # by _is_eng_role() to sort UUID-based job board URLs
                        # (e.g. Rippling ATS) where the URL has no role keywords.
                        title_text = a.get_text(strip=True)
                        if title_text:
                            job_title_map[href] = title_text

                # Ashby renders job listings client-side (React SPA) — the initial
                # HTML has no <a href> job links, only CDN assets.  Fall back to
                # parsing window.__appData from the inline <script> tag, which
                # contains the full job listing JSON including individual posting IDs.
                #
                # This also covers companies that embed Ashby on their own domain
                # (e.g. braintrust.dev/careers?ashby_jid=...) — the page still
                # contains window.__appData even though the URL is not on ashbyhq.com.
                is_ashby_page = (
                    'ashbyhq.com' in index_url
                    or bool(soup.find('script', string=re.compile(r'window\.__appData')))
                )
                if not individual_job_urls and is_ashby_page:
                    individual_job_urls.extend(
                        self._extract_ashby_job_urls(index_url, soup)
                    )

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

        # ------------------------------------------------------------------ #
        # Investor job board scan                                            #
        # VC portfolio job boards (8vc, a16z, Sequoia, etc.) often host job #
        # postings for their portfolio companies.  These are valuable Tier 2 #
        # signals because the descriptions list the actual tech stack.       #
        # Postings are included even if no longer active, provided they are  #
        # less than 6 months old (or undated — include by default).          #
        #                                                                    #
        # Most VC boards use Getro (a Next.js SPA) — the company listing    #
        # page does not embed job links in its HTML; they load via private   #
        # API after page render.  We use Google search to discover individual#
        # job page URLs (which Getro does render with full description in    #
        # __NEXT_DATA__), then fetch those pages directly.                   #
        # ------------------------------------------------------------------ #
        investor_job_urls: list[str] = []
        hyphen_slug = company_name.lower().replace(' ', '-')
        cutoff_date = datetime.utcnow() - timedelta(days=180)
        investor_board_domains = [d for d, _ in INVESTOR_JOB_BOARDS]

        # Two-phase discovery of individual investor board job pages:
        #
        # Phase A — Sitemap scan: Getro boards expose XML sitemaps that list all
        #   job URLs including inactive postings.  Parse them to find company-specific
        #   URLs.  This is the most complete source.
        #
        # Phase B — Brave Search fallback: Brave indexes these pages better than
        #   Google and returns direct job URLs (no redirect unwrapping needed).
        #   Used when sitemap is absent or company not found via sitemap.

        # Phase A: sitemap scan
        for board_domain, company_url_tpl in INVESTOR_JOB_BOARDS:
            if investor_job_urls:
                break
            company_path_prefix = f'/{hyphen_slug}/jobs/'  # e.g. /cellares/jobs/
            # Getro sitemaps: sitemap_jobs1.xml, sitemap_jobs2.xml, ...
            for sitemap_n in range(1, 4):
                sitemap_url = f'https://{board_domain}/sitemap_jobs{sitemap_n}.xml'
                try:
                    r = requests.get(
                        sitemap_url, timeout=self.TIMEOUT,
                        headers=self.HEADERS, verify=False
                    )
                    if r.status_code != 200:
                        break  # no more sitemap pages for this board
                    # Extract URLs matching this company
                    for loc in re.findall(r'<loc>(https?://[^<]+)</loc>', r.text):
                        parsed = urlparse(loc)
                        if (parsed.netloc == board_domain
                                and company_path_prefix in parsed.path
                                and loc not in investor_job_urls
                                and loc not in individual_job_urls):
                            investor_job_urls.append(loc)
                except Exception:
                    break

        # Phase B: Search fallback (when sitemap found nothing)
        if not investor_job_urls:
            for board_domain in investor_board_domains:
                if investor_job_urls:
                    break
                try:
                    search_query = f'"{company_name}" site:{board_domain}'
                    results = serper_search(search_query, num=5, source='attribution')
                    for result in results:
                        url = result.get('url', '')
                        if not url:
                            continue
                        parsed_host = urlparse(url).netloc
                        # Only individual job pages — skip board-level nav pages
                        if (parsed_host == board_domain
                                and '/jobs/' in url
                                and url not in investor_job_urls
                                and url not in individual_job_urls):
                            investor_job_urls.append(url)
                except Exception:
                    continue

        if investor_job_urls:
            print(f"    🏦 Investor job boards: {len(investor_job_urls)} postings found")

        # Merge investor board URLs into the scan list (deduplicated)
        all_job_urls = individual_job_urls + investor_job_urls

        return all_job_urls, job_title_map

    def count_engineering_roles(
        self, company_name: str, website: str = ""
    ) -> tuple[int, list[str]]:
        """
        Count engineering/infrastructure job postings for a company.

        Reuses the same board discovery logic as _check_job_postings() but
        returns (count, urls) instead of scanning pages for cloud keywords.
        Used by trigger detection for hiring surge detection.
        """
        all_job_urls, job_title_map = self._discover_job_board_urls(company_name, website)

        def _is_eng_role(url: str) -> bool:
            candidate = job_title_map.get(url, url).lower()
            return any(kw in candidate for kw in self._CLOUD_ROLE_KEYWORDS)

        eng_urls = [u for u in all_job_urls if _is_eng_role(u)]
        return len(eng_urls), eng_urls

    def _check_job_postings(self, company_name: str, website: str = "") -> List[AttributionSignal]:
        """
        Search for engineering job postings that reveal tech stack.

        Job postings are excellent Tier 2 signals because companies explicitly
        list required technologies. "Must have 3+ years AWS experience" is a
        direct indicator of their infrastructure.

        Uses _discover_job_board_urls() for board discovery, then scans up to
        8 individual job pages for cloud/AI keyword signals.
        """
        signals = []
        found_cloud: set = set()
        found_ai: set = set()

        all_job_urls, job_title_map = self._discover_job_board_urls(company_name, website)

        investor_board_domains = [d for d, _ in INVESTOR_JOB_BOARDS]

        # Scan up to 8 individual job pages for tech stack signals.
        # Prioritise software/infra/cloud roles — these are most likely to list
        # cloud stack requirements.  Hardware, mechanical, RF, FPGA, and other
        # discipline-specific engineering titles rarely mention AWS/GCP/Azure.
        def _is_eng_role(url: str) -> bool:
            # Prefer job title from the board index HTML (captured in job_title_map)
            # over URL-keyword matching.  UUID-based boards like Rippling ATS use
            # paths such as /jobs/<uuid> with no role keywords in the URL — relying
            # solely on URL text causes relevant postings (e.g. "Information Security
            # Lead") to sort after unrelated roles and fall outside the 8-page cap.
            candidate = job_title_map.get(url, url)
            candidate_lower = candidate.lower()
            return any(kw in candidate_lower for kw in self._CLOUD_ROLE_KEYWORDS)

        # Sort: engineering roles first, then the rest
        sorted_urls = (
            [u for u in all_job_urls if _is_eng_role(u)] +
            [u for u in all_job_urls if not _is_eng_role(u)]
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

                # Ashby individual job pages are React SPAs — job description
                # text lives in window.__appData.posting.descriptionPlainText,
                # not in rendered HTML.  Extract it before stripping <script> tags.
                # Also fires when Ashby is embedded on the company's own domain
                # (detected via window.__appData in the page, or ashby_jid in URL).
                extra_text = ''
                is_ashby_job = (
                    'ashbyhq.com' in url
                    or 'ashby_jid=' in url
                    or bool(soup.find('script', string=re.compile(r'window\.__appData')))
                )
                if is_ashby_job:
                    extra_text = self._extract_ashby_posting_text(soup)
                # Getro-based VC job boards (8VC, a16z, etc.) are Next.js SPAs.
                # Job description text is in __NEXT_DATA__ initialState.jobs.currentJob.description.
                elif any(bd in url for bd in investor_board_domains):
                    extra_text = self._extract_getro_posting_text(soup)

                for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
                    tag.decompose()
                text = soup.get_text(separator=' ', strip=True)
                if extra_text:
                    text = text + ' ' + extra_text

                # Cloud keyword scan — sentence-aware to suppress conjunctive
                # skill-list noise ("AWS, Azure, or GCP experience preferred").
                # Returns (matches, primary_providers) — primary_providers are
                # explicitly declared via "X primarily" patterns and get boosted
                # weight (1.0 vs 0.6) since they name the company's main cloud.
                cloud_matches, cloud_primary = _scan_job_sentences(text, CLOUD_KEYWORDS)
                for provider, keywords in cloud_matches.items():
                    if provider not in found_cloud:
                        found_cloud.add(provider)
                        is_primary = provider in cloud_primary
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.CLOUD,
                            provider_name=provider,
                            signal_source='job_posting',
                            signal_strength=SignalStrength.STRONG if is_primary else SignalStrength.MEDIUM,
                            evidence_text=(
                                f'Job posting declares primary cloud: {", ".join(keywords[:3])}'
                                if is_primary else
                                f'Job posting mentions: {", ".join(keywords[:3])}'
                            ),
                            evidence_url=url,
                            confidence_weight=1.0 if is_primary else 0.6
                        ))

                # AI keyword scan — includes hiring-phrase filter to suppress
                # candidate skill requirements ("experience with OpenAI required")
                ai_matches, ai_primary = _scan_job_sentences(text, AI_KEYWORDS, ai_keyword_map=AI_KEYWORDS)
                for provider, keywords in ai_matches.items():
                    if provider not in found_ai:
                        found_ai.add(provider)
                        is_primary = provider in ai_primary
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.AI,
                            provider_name=provider,
                            signal_source='job_posting',
                            signal_strength=SignalStrength.STRONG if is_primary else SignalStrength.MEDIUM,
                            evidence_text=(
                                f'Job posting declares primary AI provider: {", ".join(keywords[:3])}'
                                if is_primary else
                                f'Job posting mentions: {", ".join(keywords[:3])}'
                            ),
                            evidence_url=url,
                            confidence_weight=1.0 if is_primary else 0.6
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
                          '/legal/privacy', '/legal/security', '/trust-center',
                          '/privacy-policy', '/legal/privacy-policy',
                          '/data-processing', '/dpa'],
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

                    # Scan for cloud keywords — ownership-aware sentence classifier
                    cloud_matches = _classify_website_sentences(text, CLOUD_KEYWORDS)
                    for provider, (keywords, is_ownership) in cloud_matches.items():
                        sig_key = f"cloud:{provider}:{config['source']}"
                        if sig_key not in found_sources:
                            found_sources.add(sig_key)
                            # Ownership sentences → STRONG regardless of page type
                            if is_ownership:
                                strength = SignalStrength.STRONG
                                weight   = 1.0
                                src      = 'ownership_declaration'
                            else:
                                strength = config['strength']
                                weight   = config['weight']
                                src      = config['source']
                            signals.append(AttributionSignal(
                                provider_type=ProviderType.CLOUD,
                                provider_name=provider,
                                signal_source=src,
                                signal_strength=strength,
                                evidence_text=f'{provider} {"ownership" if is_ownership else "keywords"} on {path}: {", ".join(keywords[:3])}',
                                evidence_url=url,
                                confidence_weight=weight,
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

                    # Homepage only — scan for investor/backer language
                    # ("backed by Google Ventures", "funded by GV", etc.)
                    if path == '/':
                        inv_signals = self._extract_investors_from_text(text, url)
                        signals.extend(inv_signals)

                except Exception:
                    continue

        return signals

    def _check_blog_posts(self, company_name: str, website: str) -> List[AttributionSignal]:
        """
        Discover and scan company blog posts for cloud/AI provider signals.

        Strategy:
        0. Brave Search API (most powerful — finds indexed posts Google/Brave know
           about even if they're not in the company's own sitemap/RSS)
           Query: "<company> site:domain.com aws OR kubernetes OR ..."
        1. RSS/Atom feed (reliable for WordPress/Ghost/Substack blogs)
           Paths tried: /feed, /rss, /blog/feed, /feed.xml, /atom.xml, /blog/rss.xml
        2. XML sitemap (fallback — extracts /blog/ URLs sorted newest-first by lastmod)
           Paths tried: /sitemap.xml, /blog-sitemap.xml, /sitemap-blog.xml

        For each post discovered:
        - Check if title or excerpt contains a cloud/AI provider keyword
        - Only fetch the full post URL if it looks relevant (avoids scanning every post)
        - Pass matching URLs through _check_evidence_urls() (STRONG signals, weight 1.0)

        Posts are treated as STRONG signals because they are first-person engineering
        content — equivalent to a partnership declaration (e.g. "Reducing AWS Costs by
        $150k: our Kubernetes workloads on EKS").
        """
        import feedparser

        signals = []
        post_urls_to_fetch: List[str] = []

        # Provider keywords to match in RSS titles/excerpts (specific)
        title_keywords = [
            'aws', 'amazon web services', 'amazon',
            'gcp', 'google cloud',
            'azure', 'microsoft azure',
            'openai', 'anthropic', 'vertex', 'gemini',
            'cloudfront', 'eks', 'ec2', 's3', 'lambda',
            'kubernetes', 'k8s',          # often co-occur with provider names
            'coreweave', 'lambda labs',
        ]

        # Slug keywords for sitemap fallback — broader set catches infra/engineering posts
        # (slug = URL path, so words are hyphenated; "cost-optim" catches "cost-optimization")
        slug_keywords = title_keywords + [
            'cost-optim', 'infrastructure', 'architect', 'engineer',
            'scaling', 'scale', 'deploy', 'devops', 'cloud', 'hosting',
            'performance', 'latency', 'reliability', 'migration',
        ]

        # --- Step 0: Google Search (via Serper) ---
        # Finds indexed blog posts that the company's own RSS/sitemap may miss.
        try:
            apex = website.lstrip('www.')  # xflowpay.com
            search_q = (
                f'"{company_name}" {apex} '
                f'aws OR "amazon web services" OR "google cloud" OR azure '
                f'OR openai OR anthropic OR kubernetes OR eks OR "machine learning"'
            )
            search_results = serper_search(search_q, num=10, source='attribution')
            matched = [
                res['url'] for res in search_results
                if apex in res.get('url', '')       # must be on this domain
            ]
            if matched:
                print(f'    🔍 Google Search: {len(matched)} blog post(s) found')
                post_urls_to_fetch.extend(matched)
        except Exception:
            pass

        # --- Step 1: RSS/Atom feed ---
        feed_paths = ['/feed', '/rss', '/blog/feed', '/feed.xml',
                      '/atom.xml', '/blog/rss.xml', '/blog/feed.xml']
        for path in feed_paths:
            url = f'https://{website}{path}'
            try:
                r = requests.get(url, timeout=self.TIMEOUT,
                                 headers=self.HEADERS, verify=False)
                if r.status_code != 200:
                    continue
                feed = feedparser.parse(r.content)
                if not feed.entries:
                    continue
                print(f'    📰 Blog RSS found: {url} ({len(feed.entries)} posts)')
                for entry in feed.entries[:20]:      # cap at 20 most recent
                    title   = (entry.get('title', '') or '').lower()
                    summary = (entry.get('summary', '') or '').lower()
                    link    = entry.get('link', '') or ''
                    if not link:
                        continue
                    if any(kw in title or kw in summary for kw in title_keywords):
                        post_urls_to_fetch.append(link)
                break  # stop at first working feed
            except Exception:
                continue

        # --- Step 2: Sitemap fallback (if no RSS) ---
        if not post_urls_to_fetch:
            sitemap_paths = ['/sitemap.xml', '/blog-sitemap.xml', '/sitemap-blog.xml']
            for path in sitemap_paths:
                url = f'https://{website}{path}'
                try:
                    r = requests.get(url, timeout=self.TIMEOUT,
                                     headers=self.HEADERS, verify=False)
                    if r.status_code != 200:
                        continue
                    # Extract <url> blocks to get both <loc> and <lastmod>
                    import re as _re
                    # Parse full <url>...</url> blocks so we can pair loc with lastmod
                    url_blocks = _re.findall(
                        r'<url>(.*?)</url>', r.text, _re.IGNORECASE | _re.DOTALL
                    )
                    blog_entries = []  # list of (lastmod, loc)
                    for block in url_blocks:
                        loc_m = _re.search(r'<loc>\s*(https?://[^<\s]+)\s*</loc>', block, _re.IGNORECASE)
                        if not loc_m:
                            continue
                        loc = loc_m.group(1).strip()
                        if '/blog/' not in loc:
                            continue
                        lastmod_m = _re.search(r'<lastmod>\s*([^<\s]+)\s*</lastmod>', block, _re.IGNORECASE)
                        lastmod = lastmod_m.group(1).strip() if lastmod_m else '0000-00-00'
                        blog_entries.append((lastmod, loc))

                    if not blog_entries:
                        continue

                    # Sort newest-first by lastmod date string (ISO format sorts correctly)
                    blog_entries.sort(key=lambda x: x[0], reverse=True)
                    blog_locs_sorted = [loc for _, loc in blog_entries]

                    # Tier 1: slug contains provider/infra keyword (check all)
                    slug_matched = [
                        loc for loc in blog_locs_sorted
                        if any(kw in loc.lower() for kw in slug_keywords)
                    ]
                    if slug_matched:
                        selected = slug_matched[:15]
                        print(f'    📰 Blog sitemap: {len(slug_matched)} slug-matched post(s)')
                    else:
                        # Tier 2: no slug keyword — sample the 10 most recently updated posts
                        selected = blog_locs_sorted[:10]
                        print(f'    📰 Blog sitemap: sampling {len(selected)} recent posts (no slug match)')
                    for loc in selected:
                        post_urls_to_fetch.append(loc)
                    break
                except Exception:
                    continue

        if not post_urls_to_fetch:
            return signals

        # --- Step 3: Fetch and scan relevant posts ---
        # Filter out competitor/comparison pages — they list other providers as alternatives,
        # not the company's own stack. e.g. "/blog/elevenlabs-alternatives" mentions AWS,
        # Azure, GCP as competitors — those would be false positive signals.
        unique_urls = [
            u for u in dict.fromkeys(post_urls_to_fetch)   # deduplicate, preserve order
            if not any(pat in u.lower() for pat in _COMPETITOR_SLUG_PATTERNS)
        ]
        if not unique_urls:
            return signals

        print(f'    📰 Scanning {len(unique_urls)} relevant blog post(s)')
        blog_signals = self._check_evidence_urls(company_name, unique_urls)

        # Re-label source from 'evidence_url' → 'tech_blog' and downweight to MEDIUM (0.6).
        # Blog posts are self-published content — credible but not externally verifiable
        # like DNS records or subprocessors pages. Job postings also use 0.6 for the same reason.
        for sig in blog_signals:
            sig.signal_source = 'tech_blog'
            sig.confidence_weight = 0.6
            sig.signal_strength = SignalStrength.MEDIUM

        signals.extend(blog_signals)
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

    # Class-level cache so IP ranges are fetched at most once per process lifetime
    _ip_range_cache: dict = {}

    def _load_cloud_ip_ranges(self) -> dict:
        """
        Fetch and cache published IP range lists from AWS, Azure, and GCP.

        Returns a dict mapping provider name → list of ipaddress.IPv4Network objects.
        Falls back to an empty dict for any provider whose URL fails — the caller
        degrades gracefully and simply skips that provider.

        Sources:
          AWS   — https://ip-ranges.amazonaws.com/ip-ranges.json
          Azure — https://download.microsoft.com/download/7/1/D/71D86715-5596-4529-9B13-DA13A5DE5B63/ServiceTags_Public_20220404.json
                  (We use the well-known static redirect that always serves the latest file)
          GCP   — https://www.gstatic.com/ipranges/cloud.json
        """
        if self._ip_range_cache:
            return self._ip_range_cache

        ranges: dict = {'AWS': [], 'Azure': [], 'GCP': []}

        # ── AWS ──────────────────────────────────────────────────────────────
        try:
            r = requests.get(
                'https://ip-ranges.amazonaws.com/ip-ranges.json',
                timeout=10
            )
            for prefix in r.json().get('prefixes', []):
                cidr = prefix.get('ip_prefix')
                if cidr:
                    try:
                        ranges['AWS'].append(ipaddress.IPv4Network(cidr, strict=False))
                    except ValueError:
                        pass
        except Exception as e:
            print(f"    ⚠️  Could not load AWS IP ranges: {e}")

        # ── Azure ─────────────────────────────────────────────────────────────
        # Microsoft updates the download URL weekly. We scrape the download page
        # to extract the current URL, then fetch that JSON.
        try:
            page = requests.get(
                'https://www.microsoft.com/en-us/download/details.aspx?id=56519',
                timeout=10,
            )
            # Extract the direct download URL from the page HTML
            azure_url_match = re.search(
                r'(https://download\.microsoft\.com/download/[^"\']+ServiceTags_Public_\d+\.json)',
                page.text,
            )
            if azure_url_match:
                azure_url = azure_url_match.group(1)
                r = requests.get(azure_url, timeout=10, allow_redirects=True)
                for value in r.json().get('values', []):
                    for cidr in value.get('properties', {}).get('addressPrefixes', []):
                        if ':' not in cidr:  # skip IPv6
                            try:
                                ranges['Azure'].append(ipaddress.IPv4Network(cidr, strict=False))
                            except ValueError:
                                pass
            else:
                print(f"    ⚠️  Could not find Azure IP ranges download URL in page")
        except Exception as e:
            print(f"    ⚠️  Could not load Azure IP ranges: {e}")

        # ── GCP ───────────────────────────────────────────────────────────────
        try:
            r = requests.get(
                'https://www.gstatic.com/ipranges/cloud.json',
                timeout=10
            )
            for prefix in r.json().get('prefixes', []):
                cidr = prefix.get('ipv4Prefix')
                if cidr:
                    try:
                        ranges['GCP'].append(ipaddress.IPv4Network(cidr, strict=False))
                    except ValueError:
                        pass
        except Exception as e:
            print(f"    ⚠️  Could not load GCP IP ranges: {e}")

        AttributionEngine._ip_range_cache = ranges
        return ranges

    def _check_ip_asn(self, website: str) -> List[AttributionSignal]:
        """
        Check IP address for cloud provider ownership using published IP range lists.

        Uses the official CIDR databases from AWS, Azure, and GCP instead of naive
        string-prefix matching (which was wrong — e.g. '13.' was mapped to Azure
        but 13.248.x.x is actually AWS Global Accelerator).

        Falls back silently if DNS resolution or range fetching fails — IP/ASN is
        a Tier-3 weak signal and should never block attribution.
        """
        signals = []
        try:
            # Skip if the site is on a hosted platform — the IP belongs to the
            # platform (e.g. Wix runs on GCP), not to the startup itself.
            if self._is_hosted_platform(website):
                print(f"    ℹ️  IP/ASN skipped — hosted platform detected")
                return signals

            ip_str = socket.gethostbyname(website)
            ip_addr = ipaddress.IPv4Address(ip_str)

            cloud_ranges = self._load_cloud_ip_ranges()

            for provider, networks in cloud_ranges.items():
                if any(ip_addr in net for net in networks):
                    signals.append(AttributionSignal(
                        provider_type=ProviderType.CLOUD,
                        provider_name=provider,
                        signal_source='ip_asn',
                        signal_strength=SignalStrength.WEAK,
                        evidence_text=f'IP {ip_str} in {provider} range',
                        confidence_weight=0.3
                    ))
                    break  # Only one provider per IP

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

    # Job title keywords that identify cloud/infra-relevant engineering roles.
    # Used in two places: URL-based sort (Lever/Greenhouse/Workable) and
    # title-based sort (Ashby, where URLs are UUID-only).
    #
    # Deliberately excludes hardware disciplines (electrical, mechanical, RF,
    # FPGA, embedded, antenna) — those rarely mention AWS/GCP/Azure and would
    # push the 8-page scan cap before reaching software/infra postings.
    _CLOUD_ROLE_KEYWORDS = [
        'software',              # Software Engineer — highest signal
        'backend',               # Backend Engineer
        'infrastructure',        # Infrastructure Engineer
        'platform',              # Platform Engineer
        'devops',                # DevOps Engineer
        'site reliability',      # Site Reliability Engineer / SRE
        ' sre',                  # SRE shorthand in URL slugs
        'cloud',                 # Cloud Engineer / Cloud Architect
        'full stack',            # Full Stack Engineer
        'fullstack',
        'data engineer',         # Data Engineer (not "data analyst" or "data scientist")
        'ml engineer',           # ML Engineer
        'machine learning engineer',
        'security',              # Security Engineer / Information Security Lead / Cloud Security
        'network engineer',      # Cloud networking roles (VPCs, transit gateways, etc.)
        'data scientist',        # BigQuery, Vertex AI, SageMaker commonly in job body
        'data analyst',          # Analytics roles at cloud-native companies mention cloud tools
    ]

    # Phrases on company homepages that introduce a list of investors/backers.
    # Deliberately broad — false positives are safe because only names that match
    # INVESTOR_CLOUD_PRIORS produce a signal; unrecognised VC names are ignored.
    _BACKER_PHRASES = re.compile(
        r'(?:backed by|investors include|funded by|supported by|'
        r'investment from|our investors|raised\s+(?:\$[\d.,]+\s*[MBK]?\s+)?from|'
        r'with participation from|with support from)\s*[:—\-]?\s*',
        re.IGNORECASE,
    )

    def _extract_investors_from_text(
        self,
        text: str,
        source_url: str,
    ) -> List[AttributionSignal]:
        """
        Scan plain page text for investor/backer language and emit WEAK cloud
        signals when discovered investor names match INVESTOR_CLOUD_PRIORS.

        Handles patterns like:
          "We are backed by Google Ventures, Sequoia Capital, and Index Ventures"
          "Funded by GV, M12, and Andreessen Horowitz"
          "Our investors include Google, Microsoft, and a16z"

        Returns at most one signal per cloud provider (deduped by `seen_providers`).
        Unknown investors (not in INVESTOR_CLOUD_PRIORS) are silently ignored —
        no false positives from unrecognised VC names.
        """
        signals: List[AttributionSignal] = []
        seen_providers: set = set()

        for match in self._BACKER_PHRASES.finditer(text):
            # Take a 300-char window right after the trigger phrase
            window_start = match.end()
            window = text[window_start: window_start + 300]

            # Truncate at the first sentence-ending boundary
            for stop_char in ('.', '\n', ')'):
                idx = window.find(stop_char)
                if idx != -1:
                    window = window[:idx]

            # Split on commas, semicolons, and " and "
            raw_names = re.split(r',|;|\band\b', window, flags=re.IGNORECASE)

            for raw in raw_names:
                candidate = raw.strip().strip('"\'').strip()
                # Reject clearly non-name fragments (too long, contains digits, empty)
                if not candidate or len(candidate) > 60 or re.search(r'\d', candidate):
                    continue

                candidate_lower = candidate.lower()
                for prior_key, (provider, rationale) in INVESTOR_CLOUD_PRIORS.items():
                    if prior_key in candidate_lower and provider not in seen_providers:
                        seen_providers.add(provider)
                        signals.append(AttributionSignal(
                            provider_type=ProviderType.CLOUD,
                            provider_name=provider,
                            signal_source='homepage_investor',
                            signal_strength=SignalStrength.WEAK,
                            evidence_text=f'{rationale} (mentioned on homepage: "{candidate}")',
                            evidence_url=source_url,
                            confidence_weight=0.3,
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

        Consensus rule: only emits a signal when ALL matched founders point
        to the SAME provider. Conflicting backgrounds (e.g. one ex-Google,
        one ex-Microsoft) are suppressed entirely — mixed priors are noise,
        not signal. Backgrounds that don't match any prior are ignored.

        Only emits one signal per provider. Does not emit AI signals.
        """
        # Collect (provider, rationale, background) for every matched background
        matched: list[tuple[str, str, str]] = []
        seen_providers: set = set()

        for background in founder_background:
            background_lower = background.lower().strip()
            for prior_key, (provider, rationale) in FOUNDER_CLOUD_PRIORS.items():
                if prior_key in background_lower and provider not in seen_providers:
                    seen_providers.add(provider)
                    matched.append((provider, rationale, background))

        # Suppress if founders point to more than one different provider
        unique_providers = {m[0] for m in matched}
        if len(unique_providers) != 1:
            return []

        # Consensus — emit one signal for the agreed provider
        provider, rationale, background = matched[0]
        return [AttributionSignal(
            provider_type=ProviderType.CLOUD,
            provider_name=provider,
            signal_source='founder_prior',
            signal_strength=SignalStrength.WEAK,
            evidence_text=f'{rationale} (background: {background})',
            confidence_weight=0.3
        )]

    # ========================================================================
    # TIER 4a: PERPLEXITY SEARCH FALLBACK
    # ========================================================================

    def _perplexity_attribution_search(
        self,
        company_name: str,
        website: str,
        need_cloud: bool,
        need_ai: bool,
    ) -> List[AttributionSignal]:
        """
        Tier 4a: Use Perplexity Sonar (live web search + LLM) to find cloud/AI
        provider signals not captured by deterministic tiers 1–3.

        Unlike the Claude Haiku fallback (Tier 4b), Perplexity performs a fresh
        web search — surfacing job postings, blog posts, press releases, and case
        studies we never fetched. Each finding comes with citation URLs that we
        store as verifiable evidence_url values.

        One Sonar query is fired per needed provider type (cloud / AI), using
        OR-grouped provider names so a single search covers all candidates.

        Confidence → weight mapping (matches Tier 4b scale):
          80–100 → STRONG  (1.0)
          50–79  → MEDIUM  (0.6)
          0–49   → WEAK    (0.3)

        Returns list of AttributionSignal objects (may be empty).
        """
        api_key = os.getenv('PERPLEXITY_API_KEY', '')
        if not api_key:
            return []

        signals: List[AttributionSignal] = []

        # Canonical provider sets (mirrors Tier 4b validation)
        valid_cloud = {'AWS', 'GCP', 'Azure', 'CoreWeave', 'OCI', 'Lambda', 'Vast.ai',
                       'Crusoe', 'OVH', 'Vultr', 'Paperspace', 'Nebius', 'Fluidstack',
                       'On-Premises', 'Hybrid'}
        valid_ai    = {'OpenAI', 'Anthropic', 'Google AI', 'Cohere', 'Mistral',
                       'Meta / Llama', 'xAI / Grok', 'Hugging Face', 'Together AI',
                       'Groq', 'Replicate'}

        # Build one query per needed type
        queries: list[tuple[str, str, set]] = []   # (label, query_text, valid_set)
        if need_cloud:
            cloud_providers = (
                'AWS OR "Amazon Web Services" OR "Google Cloud" OR GCP OR '
                '"Microsoft Azure" OR Azure OR CoreWeave OR OCI OR "Lambda Labs"'
            )
            queries.append((
                'cloud',
                f'"{company_name}" site:{website} OR "{company_name}" '
                f'cloud infrastructure {cloud_providers}',
                valid_cloud,
            ))
        if need_ai:
            ai_providers = (
                'OpenAI OR Anthropic OR "Google AI" OR "Vertex AI" OR Gemini OR '
                'Cohere OR Mistral OR "Meta Llama" OR "Hugging Face" OR Groq OR '
                '"Together AI" OR "xAI" OR Replicate'
            )
            queries.append((
                'ai',
                f'"{company_name}" AI LLM provider {ai_providers}',
                valid_ai,
            ))

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        for ptype_label, query, valid_set in queries:
            ptype = ProviderType.CLOUD if ptype_label == 'cloud' else ProviderType.AI

            system_prompt = (
                f"You are an infrastructure analyst. The user will ask about {company_name} "
                f"({website}). Answer ONLY based on what you find in web search results. "
                f"Be concise and factual. If you cannot find clear evidence, say so."
            )

            user_prompt = (
                f"What {ptype_label} provider(s) does {company_name} ({website}) use? "
                f"Search for evidence in their job postings, blog posts, press releases, "
                f"privacy/trust pages, and case studies.\n\n"
                f"Respond with a JSON array of objects. Each object:\n"
                f"- \"provider_name\": canonical name from this list exactly: "
                f"{sorted(valid_set)}\n"
                f"- \"confidence\": integer 0–100\n"
                f"- \"evidence_quote\": ≤30-word quote from a source you found\n"
                f"- \"reasoning\": one sentence\n"
                f"- \"source_url\": the URL where you found this evidence\n\n"
                f"Rules:\n"
                f"- Only include providers with confidence ≥ {'60' if ptype_label == 'ai' else '30'}\n"
                f"- If no clear evidence, return []\n"
                f"- Return ONLY valid JSON — no markdown, no prose"
            )

            try:
                resp = requests.post(
                    'https://api.perplexity.ai/chat/completions',
                    headers=headers,
                    json={
                        'model': 'sonar',
                        'messages': [
                            {'role': 'system', 'content': system_prompt},
                            {'role': 'user',   'content': user_prompt},
                        ],
                        'max_tokens': 600,
                        'temperature': 0.1,   # Low temp for factual attribution
                        'search_recency_filter': 'year',
                    },
                    timeout=20,
                )
                if resp.status_code != 200:
                    print(f"    ⚠️  Perplexity API error {resp.status_code}: {resp.text[:120]}")
                    continue

                data = resp.json()
                raw = data['choices'][0]['message']['content'].strip()

                # Citations returned by Perplexity — use as fallback evidence_url
                citations: list[str] = data.get('citations', [])
                fallback_url = citations[0] if citations else f'https://{website}'

            except Exception as e:
                print(f"    ⚠️  Perplexity request failed: {e}")
                continue

            # ── Parse JSON response ──────────────────────────────────────────
            try:
                # Sonar often wraps JSON in markdown fences or appends prose.
                # Extract the first [...] JSON array robustly rather than
                # parsing the whole response string.
                json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
                if not json_match:
                    # No JSON array found at all — treat as empty result
                    continue
                raw = json_match.group(0)

                findings = json.loads(raw)
                if not isinstance(findings, list):
                    continue

                for item in findings:
                    provider_name = str(item.get('provider_name', '')).strip()
                    confidence    = int(item.get('confidence', 0))
                    evidence      = str(item.get('evidence_quote', '')).strip()
                    reasoning     = str(item.get('reasoning', '')).strip()
                    source_url    = str(item.get('source_url', '')).strip() or fallback_url

                    if provider_name not in valid_set:
                        continue

                    # Hard floors (same as Tier 4b)
                    if ptype == ProviderType.AI and confidence < 60:
                        continue
                    if ptype == ProviderType.CLOUD and confidence < 30:
                        continue

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
                        signal_source='perplexity_search',
                        signal_strength=strength,
                        evidence_text=(
                            f'Perplexity ({confidence}% conf): "{evidence}" — {reasoning}'
                        )[:200],
                        evidence_url=source_url,
                        confidence_weight=weight,
                    ))

            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                print(f"    ⚠️  Perplexity parse error: {e} | raw: {raw[:100]}")
                continue

        return signals

    # ========================================================================
    # TIER 4b: LLM FALLBACK (Claude Haiku — reads pre-fetched text)
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
                "startup actively use in their own product infrastructure? "
                "(e.g. OpenAI, Anthropic, Google AI / Gemini / Vertex AI, Cohere, Mistral, "
                "Meta Llama, xAI Grok, Hugging Face, Together AI, Groq, Replicate). "
                "Look for direct API usage, model deployment statements, or 'powered by' claims. "
                "Do NOT include: providers they merely evaluated or benchmarked, "
                "providers listed as compatible integrations for customers, "
                "or providers mentioned only in comparison/tutorial content."
            )

        task_text = '\n'.join(tasks)

        prompt = f"""You are an infrastructure analyst. Your task is to identify cloud and AI providers
used by a startup based ONLY on the text provided below. Do NOT use any prior knowledge or make
assumptions — only attribute what is explicitly supported by the provided text.

STARTUP: {company_name} ({website})

{task_text}

For each provider you identify, respond with a JSON array of objects. Each object must have:
- "provider_type": "cloud" or "ai"
- "provider_name": canonical name (e.g. "AWS", "GCP", "Azure", "OpenAI", "Anthropic", "Google AI", "Cohere", "Mistral", "CoreWeave", "OCI", "Meta / Llama", "xAI / Grok", "Hugging Face", "Together AI", "Groq", "Replicate")
- "confidence": integer 0–100 (how confident you are this startup actually uses this provider)
- "evidence_quote": exact short quote (≤30 words) from the text below that supports this attribution
- "reasoning": one sentence explaining why this quote indicates provider usage

Rules:
- For cloud providers: only include if confidence ≥ 30
- For AI providers: only include if confidence ≥ 60 — AI inference requires explicit evidence
  (e.g. "powered by OpenAI", "uses Claude API", "built on Gemini") not mere proximity to AI terminology
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
            valid_cloud = {'AWS', 'GCP', 'Azure', 'CoreWeave', 'OCI', 'Lambda', 'Vast.ai',
                           'Crusoe', 'OVH', 'Vultr', 'Paperspace', 'Nebius', 'Fluidstack',
                           'On-Premises', 'Hybrid'}
            valid_ai    = {'OpenAI', 'Anthropic', 'Google AI', 'Cohere', 'Mistral',
                           'Meta / Llama', 'xAI / Grok', 'Hugging Face', 'Together AI',
                           'Groq', 'Replicate'}

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

                # Hard confidence floors — AI requires explicit evidence (≥60),
                # cloud is more permissive (≥30) since infra signals are more deterministic
                if ptype == ProviderType.AI and confidence < 60:
                    continue
                if ptype == ProviderType.CLOUD and confidence < 30:
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
        provider_name,   # str | list[str] | None
        provider_type: ProviderType
    ) -> Optional[Attribution]:
        """Create attribution from a known partnership override.

        provider_name may be a single string OR a list of strings for
        multi-cloud/multi-AI verified partnerships (e.g. ["GCP", "AWS"]).
        """
        if not provider_name:
            return None

        # Normalise to list so the rest of the logic is uniform
        providers_list: list = (
            provider_name if isinstance(provider_name, list) else [provider_name]
        )
        is_multi = len(providers_list) > 1
        role = "Cloud infrastructure" if provider_type == ProviderType.CLOUD else "AI service provider"

        # Pull the citation URL stored alongside the override entry
        source_url = PARTNERSHIP_OVERRIDES.get(company_name, {}).get("source_url")

        signals = []
        entries = []
        for pname in providers_list:
            sig = AttributionSignal(
                provider_type=provider_type,
                provider_name=pname,
                signal_source='partnership_override',
                signal_strength=SignalStrength.STRONG,
                evidence_text=f'Official partnership: {company_name} with {pname}',
                evidence_url=source_url,
                confidence_weight=1.0
            )
            signals.append(sig)
            entries.append(ProviderEntry(
                provider_name=pname,
                role=role,
                confidence=1.0,
                entrenchment=EntrenchmentLevel.STRONG,
                raw_score=1.0,
                signals=[sig]
            ))

        return Attribution(
            provider_type=provider_type,
            is_multi=is_multi,
            primary_provider=None if is_multi else providers_list[0],
            providers=entries,
            confidence=1.0,
            evidence_count=len(signals),
            signals=signals,
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