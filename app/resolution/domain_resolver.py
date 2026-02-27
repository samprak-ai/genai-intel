"""
Domain Resolution Module
Reliably finds official company websites using multi-stage cascade
"""

import re
import dns.resolver
import requests
from typing import Optional
from urllib.parse import urlparse
import anthropic
import os
from bs4 import BeautifulSoup


class DomainResolver:
    """
    Resolves company websites using deterministic → heuristic → AI cascade
    
    Philosophy: Try facts first, AI last
    """
    
    HEADERS = {'User-Agent': 'Mozilla/5.0'}

    # Domains to skip when scanning article hyperlinks — news sites, social,
    # investor/aggregator pages that should never be the startup's own domain.
    ARTICLE_SKIP_DOMAINS = {
        # Social media
        'twitter.com', 'x.com', 'linkedin.com', 'facebook.com',
        'instagram.com', 'youtube.com', 'tiktok.com',
        # Tech / startup press
        'techcrunch.com', 'venturebeat.com', 'siliconangle.com',
        'theverge.com', 'engadget.com', 'arstechnica.com',
        'wired.com', 'fastcompany.com', 'inc.com',
        # General / business press
        'forbes.com', 'reuters.com', 'bloomberg.com', 'wsj.com',
        'nytimes.com', 'washingtonpost.com', 'theguardian.com',
        'businessinsider.com', 'cnbc.com', 'axios.com', 'fortune.com',
        'economist.com', 'ft.com', 'apnews.com', 'bbc.com', 'bbc.co.uk',
        'time.com', 'newsweek.com', 'theatlantic.com',
        # PR / press release
        'finsmes.com', 'globenewswire.com', 'prnewswire.com', 'businesswire.com',
        'prweb.com', 'accesswire.com', 'adweek.com', 'citybiz.co',
        # VC / aggregator
        'a16z.com', 'sequoiacap.com', 'accel.com', 'kleinerperkins.com',
        'greylock.com', 'lightspeedvp.com', 'crunchbase.com',
        'pitchbook.com', 'dealroom.co', 'tracxn.com',
        # Other
        'medium.com', 'substack.com', 'notion.so', 'docs.google.com',
        'google.com', 'bing.com', 'yahoo.com',
        # Cookie consent / legal
        'onetrust.com', 'cookielaw.org',
    }

    # Common startup domain prefixes — tryprofound.com, getbasis.ai, usepepper.com
    STARTUP_PREFIXES = ('try', 'get', 'use', 'go', 'meet', 'join', 'run', 'with')

    def __init__(self):
        self.anthropic_client = anthropic.Anthropic(
            api_key=os.getenv('ANTHROPIC_API_KEY')
        )
        
        # Domains to reject
        self.reject_patterns = [
            'linkedin.com',
            'twitter.com',
            'x.com',
            'facebook.com',
            'instagram.com',
            'crunchbase.com',
            'pitchbook.com',
            'medium.com',
            'substack.com',
            'youtube.com',
            # Domain parking / broker sites
            'hugedomains.com',
            'sedo.com',
            'dan.com',
            'afternic.com',
            'godaddy.com',
            'namecheap.com',
            'undeveloped.com',
            'brandbucket.com',
            'squadhelp.com',
            # Hosting/staging platforms — not official company domains
            'github.io',
            'vercel.app',
            'netlify.app',
            'pages.dev',
            'surge.sh',
            'herokuapp.com',
            'glitch.me',
            'replit.dev',
            'webflow.io',
            'framer.app',
        ]
    
    def resolve(
        self,
        company_name: str,
        article_text: Optional[str] = None,
        funding_round: Optional[str] = None,
        funding_amount_usd: Optional[int] = None,
        lead_investors: Optional[list] = None,
        description: Optional[str] = None,
        industry: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Optional[str]:
        """
        Main resolution method - tries multiple strategies in order

        Returns: Clean domain like "company.com" or None
        """
        print(f"\n🔍 Resolving domain for: {company_name}")

        # Stage 1: Extract from article (deterministic)
        if article_text:
            domain = self._extract_from_text(article_text)
            if domain:
                print(f"  ✅ Found in article: {domain}")
                return self._canonical_domain(domain)

        # Stage 2: DNS-based guessing (deterministic)
        domain = self._dns_guessing(company_name)
        if domain:
            print(f"  ✅ Found via DNS: {domain}")
            return self._canonical_domain(domain)

        # Stage 2.5: Article-link search — scan funding announcement hyperlinks
        domain = self._article_link_search(company_name)
        if domain:
            print(f"  ✅ Found via article links: {domain}")
            return self._canonical_domain(domain)

        # Stage 3: AI web search with full context (last resort)
        domain = self._ai_search(
            company_name=company_name,
            funding_round=funding_round,
            funding_amount_usd=funding_amount_usd,
            lead_investors=lead_investors,
            description=description,
            industry=industry,
            source_url=source_url,
        )
        if domain:
            print(f"  ✅ Found via AI search: {domain}")
            return self._canonical_domain(domain)

        print(f"  ❌ Could not resolve domain")
        return None
    
    # 2-part country TLDs where apex = last 3 domain labels (e.g. company.co.uk)
    _MULTI_PART_TLDS = {'co.uk', 'co.jp', 'co.nz', 'co.za', 'co.in', 'com.au', 'com.br',
                        'com.mx', 'org.uk', 'net.uk', 'gov.uk'}

    def _apex_domain(self, netloc: str) -> str:
        """Return the registrable apex domain, handling 2-part country TLDs."""
        host = netloc.lower().replace('www.', '')
        parts = host.split('.')
        if len(parts) >= 3:
            two_part = '.'.join(parts[-2:])
            if two_part in self._MULTI_PART_TLDS:
                return '.'.join(parts[-3:])
        return '.'.join(parts[-2:]) if len(parts) >= 2 else host

    def _canonical_domain(self, domain: str) -> str:
        """
        Follow HTTP redirect and return the final apex domain.
        e.g. worldlabs.com → worldlabs.ai
        Returns original domain if redirect fails or stays on same apex.
        """
        try:
            r = requests.get(
                f'https://{domain}',
                timeout=6,
                allow_redirects=True,
                headers=self.HEADERS,
                verify=False,
            )
            final = urlparse(r.url).netloc.lower().replace('www.', '')
            if not final:
                return domain
            if self._apex_domain(final) != self._apex_domain(domain):
                print(f"  ↪  Canonical redirect: {domain} → {final}")
                return final
        except Exception:
            pass
        return domain

    def _extract_from_text(self, text: str) -> Optional[str]:
        """
        Stage 1: Extract domain from article text using regex
        
        Looks for patterns like:
        - "visit company.com"
        - "https://company.com"
        - "website: company.com"
        """
        # Pattern 1: Full URLs
        url_pattern = r'https?://([a-z0-9-]+\.[a-z]{2,})'
        matches = re.findall(url_pattern, text.lower())
        
        for match in matches:
            if self._is_valid_domain(match):
                return match
        
        # Pattern 2: Domain-like strings
        domain_pattern = r'\b([a-z0-9-]+\.(?:com|ai|io|net|org|co|tech|app|dev|xyz|so|vc|cloud|studio|ventures|fund|global|systems|company))\b'
        matches = re.findall(domain_pattern, text.lower())
        
        for match in matches:
            if self._is_valid_domain(match):
                return match
        
        return None
    
    def _dns_guessing(self, company_name: str) -> Optional[str]:
        """
        Stage 2: Generate domain candidates and test with DNS
        
        Strategy:
        1. Clean company name (remove Inc, LLC, etc.)
        2. Generate variations (company.com, company.ai, etc.)
        3. Test each with DNS lookup
        4. Return first that resolves
        """
        # Clean company name
        name = company_name.lower()
        name = re.sub(r'\s+(inc\.?|llc|ltd\.?|corporation|corp\.?)$', '', name, flags=re.IGNORECASE)
        name = name.strip()
        
        # Generate candidate domains
        candidates = []
        
        # Variation 1: Remove all spaces
        clean_name = name.replace(' ', '')
        candidates.extend([
            f"{clean_name}.com",
            f"{clean_name}.ai",
            f"{clean_name}.io",
        ])
        
        # Variation 2: Hyphenated
        hyphen_name = name.replace(' ', '-')
        candidates.extend([
            f"{hyphen_name}.com",
            f"{hyphen_name}.ai",
        ])
        
        # Variation 3: First word only (for "Company Name Inc")
        first_word = name.split()[0]
        if len(name.split()) > 1:
            candidates.extend([
                f"{first_word}.com",
                f"{first_word}.ai",
            ])
        
        # Single-word company names (e.g. "Badge", "Jump", "Nimble", "Pulse") are too
        # ambiguous for DNS guessing — the .com almost certainly belongs to an unrelated
        # company and the content check can't distinguish them. Skip straight to AI search.
        if len(name.split()) == 1:
            return None

        # Test each candidate — DNS resolve then verify homepage ownership
        for candidate in candidates:
            if self._test_domain_exists(candidate):
                if self._name_appears_on_homepage(candidate, company_name):
                    return candidate
                # DNS resolves but company name not found — wrong company, skip

        return None

    def _article_link_search(self, company_name: str) -> Optional[str]:
        """
        Stage 2.5: Scan funding announcement articles for the startup's own hyperlink.

        A human solving this looks at 2-3 funding articles and follows the href —
        the startup's domain usually appears as an anchor link in the article body
        (e.g. <a href="https://www.tryprofound.com/">Profound</a>).

        Algorithm:
        1. Brave Search: "{company_name}" startup funding → top 5 results
        2. Drop ARTICLE_SKIP_DOMAINS → take first 3 usable article URLs
        3. For each article: fetch → parse with BeautifulSoup
        4. For each <a href> in the article:
           - anchor signal (+1): anchor text contains the company name
           - pattern signal (+1): domain matches try/get/use{name} or {name}.ai
           - freq  (+1 per occurrence): fallback for bare links without anchor text
        5. Require anchor>=1 OR pattern>=1 OR freq>=2, then validate via
           _test_domain_exists() + _name_appears_on_homepage()
        6. Return first that passes, else None (Stage 3 AI search takes over)
        """
        brave_key = os.getenv('BRAVE_SEARCH_API_KEY', '')
        if not brave_key:
            print('  ⚠️  Stage 2.5: BRAVE_SEARCH_API_KEY not set — skipping article-link search')
            return None

        name_lower = company_name.lower()
        name_slug = name_lower.replace(' ', '')

        # --- Step 1: Brave search for funding articles ---
        query = f'"{company_name}" startup funding'
        try:
            resp = requests.get(
                'https://api.search.brave.com/res/v1/web/search',
                params={'q': query, 'count': 5, 'text_decorations': False},
                headers={
                    'Accept': 'application/json',
                    'Accept-Encoding': 'gzip',
                    'X-Subscription-Token': brave_key,
                },
                timeout=8,
            )
            if resp.status_code != 200:
                return None
            results = resp.json().get('web', {}).get('results', [])
        except Exception:
            return None

        # --- Step 2: Filter article URLs; also score result domains directly ---
        # A Brave result whose URL apex is the startup's own site (e.g. tryprofound.com/blog)
        # is a direct hit — score it before fetching any articles.
        article_urls = []
        direct_scores: dict[str, dict] = {}

        for r in results:
            url = r.get('url', '')
            if not url:
                continue
            host = urlparse(url).netloc.lower().replace('www.', '')
            apex = self._apex_domain(host)

            if apex in self.ARTICLE_SKIP_DOMAINS:
                continue

            # Score the result domain itself using the page title as "anchor text"
            title = (r.get('title', '') + ' ' + r.get('description', '')).lower()
            if apex not in direct_scores:
                direct_scores[apex] = {'anchor': 0, 'pattern': 0, 'freq': 0}
            direct_scores[apex]['freq'] += 1
            if name_slug in title or name_lower in title:
                direct_scores[apex]['anchor'] += 1
            domain_stem = apex.split('.')[0]
            for prefix in self.STARTUP_PREFIXES:
                if domain_stem == f'{prefix}{name_slug}':
                    direct_scores[apex]['pattern'] += 1
                    break
            else:
                if domain_stem == name_slug and apex.split('.')[-1] in ('ai', 'io'):
                    direct_scores[apex]['pattern'] += 1

            article_urls.append(url)
            if len(article_urls) >= 3:
                break

        # Check direct result domains first (fast path — no article fetching needed)
        ranked_direct = sorted(direct_scores.items(),
                               key=lambda x: (x[1]['anchor'], x[1]['pattern'], x[1]['freq']),
                               reverse=True)
        for apex, s in ranked_direct:
            if s['anchor'] < 1 and s['pattern'] < 1 and s['freq'] < 2:
                continue
            if self._test_domain_exists(apex) and self._name_appears_on_homepage(apex, company_name):
                print(f'  ✅  Stage 2.5 resolved via direct result: {apex}')
                return apex

        if not article_urls:
            return None

        # --- Step 3 & 4: Fetch articles, score candidate domains ---
        # scores[apex_domain] = {'anchor': int, 'pattern': int, 'freq': int}
        scores: dict[str, dict] = {}

        for article_url in article_urls:
            try:
                ar = requests.get(
                    article_url, timeout=6,
                    headers=self.HEADERS, allow_redirects=True, verify=False,
                )
                soup = BeautifulSoup(ar.text, 'html.parser')
            except Exception:
                continue

            article_apex = self._apex_domain(urlparse(article_url).netloc.lower())

            for tag in soup.find_all('a', href=True):
                href = tag.get('href', '')
                if not href.startswith('http'):
                    continue
                parsed = urlparse(href)
                host = parsed.netloc.lower().replace('www.', '')
                apex = self._apex_domain(host)

                # Skip the article's own domain, social/news/VC aggregators, invalid domains
                if apex == article_apex:
                    continue
                if apex in self.ARTICLE_SKIP_DOMAINS:
                    continue
                if not self._is_valid_domain(apex):
                    continue

                if apex not in scores:
                    scores[apex] = {'anchor': 0, 'pattern': 0, 'freq': 0}

                scores[apex]['freq'] += 1

                # Anchor signal: anchor text contains the company name
                anchor_text = tag.get_text(strip=True).lower()
                if name_slug in anchor_text or name_lower in anchor_text:
                    scores[apex]['anchor'] += 1

                # Pattern signal: try{name}.*, get{name}.*, {name}.ai, etc.
                domain_stem = apex.split('.')[0]  # e.g. "tryprofound" from tryprofound.com
                for prefix in self.STARTUP_PREFIXES:
                    if domain_stem == f'{prefix}{name_slug}':
                        scores[apex]['pattern'] += 1
                        break
                else:
                    # Also match {name}.ai / {name}.io without a prefix
                    if domain_stem == name_slug and apex.split('.')[-1] in ('ai', 'io'):
                        scores[apex]['pattern'] += 1

        if not scores:
            return None

        # --- Step 5: Rank candidates ---
        def rank_key(item):
            d, s = item
            return (s['anchor'], s['pattern'], s['freq'])

        ranked = sorted(scores.items(), key=rank_key, reverse=True)
        print(f'  🔗  Stage 2.5 candidates: {[(d, s) for d, s in ranked[:5]]}')

        # --- Step 6: Validate top candidates ---
        for apex, s in ranked:
            # Require at least one strong signal to avoid random noise
            if s['anchor'] < 1 and s['pattern'] < 1 and s['freq'] < 2:
                continue
            if self._test_domain_exists(apex) and self._name_appears_on_homepage(apex, company_name):
                print(f'  ✅  Stage 2.5 resolved via article links: {apex}')
                return apex

        return None

    def _name_appears_on_homepage(self, domain: str, company_name: str) -> bool:
        """
        Verify the company name appears in a meaningful position on the homepage.

        Checks high-signal locations only — <title>, <meta description>, <h1>, <h2>.
        Full body text is intentionally excluded: common words like "code" or "metal"
        appear on countless unrelated pages, producing false positives when we check
        the entire HTML. A real company's homepage will always have its name in the
        title or a top-level heading.

        Falls back to full-body check only for single-token slugs that are
        uncommon enough to be safely matched anywhere (e.g. "sambanova").
        """
        name_lower = company_name.lower()
        name_slug = name_lower.replace(' ', '')
        name_words = name_lower.split()

        try:
            r = requests.get(
                f'https://{domain}', timeout=6,
                headers={'User-Agent': 'Mozilla/5.0'}, allow_redirects=True, verify=False
            )
            # Reject if redirected to a known parking/broker/hosting site
            final_host = urlparse(r.url).netloc.replace('www.', '')
            if any(p in final_host for p in self.reject_patterns):
                return False

            soup = BeautifulSoup(r.text, 'html.parser')

            # Build a focused text block from high-signal elements only
            priority_texts = []

            # <title>
            title_tag = soup.find('title')
            if title_tag:
                priority_texts.append(title_tag.get_text())

            # <meta name="description"> and <meta property="og:*">
            for meta in soup.find_all('meta'):
                content = meta.get('content', '')
                if meta.get('name', '').lower() in ('description', 'application-name') \
                        or meta.get('property', '').lower().startswith('og:'):
                    priority_texts.append(content)

            # <h1> and <h2> tags
            for tag in soup.find_all(['h1', 'h2']):
                priority_texts.append(tag.get_text())

            priority_block = ' '.join(priority_texts).lower()

            # Match slug or full name in priority locations
            if name_slug in priority_block or name_lower in priority_block:
                return True

            # For multi-word names, also accept if ALL individual words appear
            # in priority text (handles "Code Metal" → "code" + "metal" in h1)
            # — but only when the name has 3+ chars per word to avoid noise
            if len(name_words) > 1 and all(len(w) >= 3 for w in name_words):
                if all(w in priority_block for w in name_words):
                    return True

            return False

        except Exception:
            return False  # can't verify → fall through to AI search

    def _test_domain_exists(self, domain: str) -> bool:
        """
        Test if domain exists using DNS lookup
        
        Returns: True if domain has A or CNAME records
        """
        try:
            # Try A record
            dns.resolver.resolve(domain, 'A')
            return True
        except:
            try:
                # Try CNAME record
                dns.resolver.resolve(domain, 'CNAME')
                return True
            except:
                return False
    
    def _ai_search(
        self,
        company_name: str,
        funding_round: Optional[str] = None,
        funding_amount_usd: Optional[int] = None,
        lead_investors: Optional[list] = None,
        description: Optional[str] = None,
        industry: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> Optional[str]:
        """
        Stage 3: Use Claude Sonnet with web search to find official website.
        Passes full funding context so Claude can disambiguate generic company names.
        Last resort when deterministic methods fail.
        """
        # Build context block from all available signals
        context_lines = []
        if funding_round and funding_amount_usd:
            context_lines.append(f"- Recently raised ${funding_amount_usd}M {funding_round}")
        if description:
            context_lines.append(f"- Description: {description}")
        if industry:
            context_lines.append(f"- Industry: {industry}")
        if lead_investors:
            context_lines.append(f"- Lead investors: {', '.join(lead_investors)}")
        if source_url:
            context_lines.append(f"- Funding announcement URL: {source_url}")
        context_block = "\n".join(context_lines) if context_lines else "(no additional context)"

        prompt = f"""Find the official website domain for this startup:

Company: "{company_name}"
Context:
{context_block}

Search for the company's official website. Many company names are generic words (e.g. "Badge", \
"Jump", "Nimble", "Pulse") that could belong to multiple unrelated companies — use the funding \
context above to find the correct startup, not an unrelated business with the same name.

Return ONLY the root domain, e.g. "trybadge.com" or "getnimble.ai". No protocol, no paths, no \
explanation. If you cannot confidently identify the correct domain, return: NOT_FOUND"""

        try:
            message = self.anthropic_client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=200,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search"
                }],
                messages=[{"role": "user", "content": prompt}]
            )

            # Extract text from last content block (the final answer after tool use)
            response = ""
            for block in reversed(message.content):
                if hasattr(block, "text") and block.text.strip():
                    response = block.text.strip()
                    break

            if not response:
                return None

            # Strip protocol
            response = response.replace('https://', '').replace('http://', '').strip()

            # Check NOT_FOUND before regex extraction
            if "NOT_FOUND" in response.upper():
                return None

            # Extract first domain-like pattern from response
            # Handles: "trybadge.com", "The domain is trybadge.com.", "trybadge.com (official site)"
            domain_match = re.search(
                r'\b([a-z0-9][a-z0-9\-]*\.(?:com|ai|io|net|org|co|tech|app|dev|so|xyz|cc|vc|health|finance|capital|cloud|studio|ventures|fund|global|systems|company))\b',
                response.lower()
            )
            if not domain_match:
                print(f"  ⚠️  AI search returned unparseable response: {response[:80]}")
                return None

            candidate = domain_match.group(1).rstrip('.,;')

            if not self._is_valid_domain(candidate):
                return None

            # Verify the domain actually exists via DNS
            if not self._test_domain_exists(candidate):
                print(f"  ⚠️  AI suggested {candidate} but DNS lookup failed — discarding")
                return None

            # Verify company name on homepage (soft check — accept even if not found,
            # since JS-rendered pages and different trading names are common)
            if not self._name_appears_on_homepage(candidate, company_name):
                print(f"  ⚠️  AI domain {candidate} exists but company name not on homepage — accepting anyway")

            return candidate

        except Exception as e:
            print(f"  ⚠️  AI search failed: {e}")
            return None
    
    def _is_valid_domain(self, domain: str) -> bool:
        """
        Validate domain format and reject unwanted domains

        Rules:
        - Must match domain pattern (word.tld or word.co.uk style)
        - Cannot be social media, aggregator, or parking site
        - Must be a root/apex domain (no arbitrary subdomains)
        - Supports new gTLDs like .group, .computer, .technology, .health, .finance
        """
        domain = domain.lower().strip()

        # Strip www. prefix before validation
        if domain.startswith('www.'):
            domain = domain[4:]

        # Basic format: label(s) + dot + TLD (all alpha, 2+ chars)
        # Also allow 2-part ccTLDs: label.co.uk, label.com.au etc.
        if not re.match(r'^[a-z0-9][a-z0-9\-]*\.[a-z]{2,}(?:\.[a-z]{2})?$', domain):
            return False

        # Reject unwanted domains
        for pattern in self.reject_patterns:
            if pattern in domain:
                return False

        # Reject subdomains — root domain has exactly 2 parts (or 3 for ccTLDs)
        parts = domain.split('.')
        if len(parts) == 3:
            # Allow 2-part ccTLDs: co.uk, com.au, co.nz, com.br, etc.
            if parts[-2] not in ('co', 'com', 'net', 'org', 'gov') or \
               parts[-1] not in ('uk', 'au', 'nz', 'za', 'jp', 'in', 'br', 'mx'):
                return False  # 3-part but not a known ccTLD → subdomain, reject
        elif len(parts) > 3:
            return False  # definitely a subdomain

        return True
    
    def verify_domain(self, domain: str) -> dict:
        """
        Verify domain is accessible and get metadata
        
        Returns:
        {
            'accessible': bool,
            'redirects_to': Optional[str],
            'status_code': int,
            'title': Optional[str]
        }
        """
        result = {
            'accessible': False,
            'redirects_to': None,
            'status_code': None,
            'title': None
        }
        
        try:
            response = requests.get(
                f'https://{domain}',
                timeout=10,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            
            result['accessible'] = response.status_code == 200
            result['status_code'] = response.status_code
            
            # Check for redirects
            if response.url != f'https://{domain}' and response.url != f'https://{domain}/':
                final_domain = urlparse(response.url).netloc
                result['redirects_to'] = final_domain.replace('www.', '')
            
            # Extract title
            if '<title>' in response.text:
                title = re.search(r'<title>(.*?)</title>', response.text, re.IGNORECASE)
                if title:
                    result['title'] = title.group(1).strip()
            
        except Exception as e:
            print(f"  ⚠️  Verification failed: {e}")
        
        return result


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

def example_usage():
    """Show how to use the domain resolver"""
    
    resolver = DomainResolver()
    
    # Example 1: From article text
    article = """
    Anthropic, the AI safety startup, announced $500M in funding today.
    Visit anthropic.com to learn more about their research.
    """
    
    domain = resolver.resolve("Anthropic", article)
    print(f"Result: {domain}")
    
    # Example 2: Just company name (will try DNS guessing)
    domain = resolver.resolve("OpenAI")
    print(f"Result: {domain}")
    
    # Example 3: Verify domain
    if domain:
        verification = resolver.verify_domain(domain)
        print(f"Verification: {verification}")


if __name__ == "__main__":
    example_usage()
