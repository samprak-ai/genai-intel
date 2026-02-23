"""
Funding Discovery Module
Finds recent startup funding announcements from multiple sources

Sources (priority order):
1. VC News Daily  — curated feed of ~30 VC rounds/day, near-zero noise
2. Google News    — multiple targeted queries catch what VCND misses
3. PR Newswire    — companies self-announce before media sometimes
4. GlobeNewswire  — same, but heavily pre-filtered due to noise
"""

import feedparser
import requests
import re
from datetime import datetime, timedelta
from typing import List, Optional
from bs4 import BeautifulSoup
import anthropic
import os
import ssl
from urllib.parse import quote_plus
from app.models import FundingEvent
import json
import urllib3

# Fix for Mac SSL certificate issue
ssl._create_default_https_context = ssl._create_unverified_context
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class FundingDiscovery:

    def __init__(self):
        self.anthropic_client = anthropic.Anthropic(
            api_key=os.getenv('ANTHROPIC_API_KEY')
        )

    def discover_recent_funding(self, days_back: int = 7, limit: Optional[int] = None) -> List[FundingEvent]:
        print(f"\n🔍 Discovering funding events from last {days_back} days...")
        all_articles = []

        # --- Primary source: VC News Daily (highest quality, zero noise) ---
        vcnd = self._fetch_vcnewsdaily(days_back)
        print(f"  VC News Daily:  {len(vcnd)} articles found")
        all_articles.extend(vcnd)

        # --- Google News: multiple targeted queries ---
        googlenews = self._fetch_google_news(days_back)
        print(f"  Google News:    {len(googlenews)} articles found")
        all_articles.extend(googlenews)

        # --- Wire services: catch self-announcements ---
        prnews = self._fetch_prnewswire(days_back)
        print(f"  PR Newswire:    {len(prnews)} articles found")
        all_articles.extend(prnews)

        globenews = self._fetch_globenewswire(days_back)
        print(f"  GlobeNewswire:  {len(globenews)} articles found")
        all_articles.extend(globenews)

        unique = self._deduplicate(all_articles)
        print(f"  Total unique:   {len(unique)} articles")

        # Apply limit after dedup (full discovery + dedup runs, LLM extraction is capped)
        if limit and len(unique) > limit:
            print(f"  ⚠️  Limiting to {limit} articles (from {len(unique)})")
            unique = unique[:limit]

        print()

        validated_events = []
        for i, article in enumerate(unique, 1):
            print(f"  Processing {i}/{len(unique)}: {article.get('title', '')[:60]}...")
            event = self._extract_funding_data(article)
            if event:
                validated_events.append(event)
                print(f"    ✅ {event.company_name} — ${event.funding_amount_usd}M {event.funding_round}")
            else:
                print(f"    ⚠️  Skipped (not a funding announcement)")

        # Post-extraction dedup: catch cases where two articles about the same
        # company both passed extraction (e.g. "C2i" and "C2i Semiconductors")
        before = len(validated_events)
        validated_events = self._deduplicate_events(validated_events)
        if len(validated_events) < before:
            print(f"  🔄 Post-extraction dedup: merged {before - len(validated_events)} duplicate(s)")

        print(f"\n✅ Discovery complete: {len(validated_events)} validated funding events")
        return validated_events

    # ========================================================================
    # SOURCE FETCHERS
    # ========================================================================

    def _fetch_vcnewsdaily(self, days_back: int) -> List[dict]:
        """
        VC News Daily — the best source. Every item is a real VC funding round.
        ~30 articles/day, consistent format, zero noise.
        Feed: http://feeds.feedburner.com/vcnewsdaily
        """
        url = "http://feeds.feedburner.com/vcnewsdaily"
        articles = []
        cutoff = datetime.now() - timedelta(days=days_back)

        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for entry in feed.entries:
                pub_date = self._parse_entry_date(entry)
                if not pub_date or pub_date < cutoff:
                    continue
                articles.append({
                    'title': entry.title,
                    'url': entry.link,
                    'published': pub_date,
                    'summary': entry.get('summary', ''),
                    'source': 'vcnewsdaily'
                })
        except Exception as e:
            print(f"  ⚠️  VC News Daily fetch failed: {e}")
        return articles

    def _fetch_google_news(self, days_back: int) -> List[dict]:
        """
        Google News RSS — multiple targeted queries to maximize coverage.
        This is essentially Google Search scoped to news, supports query operators.
        """
        queries = [
            # Core funding language
            'startup "raises" "Series A" OR "Series B" OR "Series C" OR "seed round"',
            # Catches "led by <investor>" phrasing
            'startup funding "million" "led by"',
            # Catches "closes $X million round"
            '"closes" "million" "round" startup OR venture',
            # Growth/later stage
            '"Series D" OR "Series E" OR "growth round" raises million',
        ]

        articles = []
        cutoff = datetime.now() - timedelta(days=days_back)
        seen_urls = set()

        for query in queries:
            encoded = quote_plus(query)
            url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
            try:
                feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
                for entry in feed.entries:
                    # Cross-query dedup by URL
                    if entry.link in seen_urls:
                        continue
                    seen_urls.add(entry.link)

                    pub_date = self._parse_entry_date(entry)
                    if not pub_date or pub_date < cutoff:
                        continue

                    title_lower = entry.title.lower()
                    if any(kw in title_lower for kw in [
                        'raises', 'raised', 'funding', 'million', 'series',
                        'seed', 'secures', 'closes', 'backed', 'led by'
                    ]):
                        articles.append({
                            'title': entry.title,
                            'url': entry.link,
                            'published': pub_date,
                            'summary': entry.get('summary', ''),
                            'source': 'google_news'
                        })
            except Exception as e:
                print(f"  ⚠️  Google News query failed: {e}")

        return articles

    def _fetch_prnewswire(self, days_back: int) -> List[dict]:
        url = "https://www.prnewswire.com/rss/news-releases-list.rss"
        articles = []
        cutoff = datetime.now() - timedelta(days=days_back)
        funding_keywords = [
            'raises', 'raised', 'funding', 'series a', 'series b', 'series c',
            'seed round', 'venture', 'million', 'investment round',
            'closes', 'secures', 'led by',
        ]
        skip_keywords = [
            'real estate', 'mortgage', 'loan', 'ipo', 'acquisition', 'merger',
            'presale', 'token', 'ico',
        ]
        try:
            feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
            for entry in feed.entries:
                pub_date = self._parse_entry_date(entry)
                if not pub_date or pub_date < cutoff:
                    continue
                title_lower = entry.title.lower()
                summary_lower = entry.get('summary', '').lower()
                if any(kw in title_lower or kw in summary_lower for kw in funding_keywords):
                    if not any(sk in title_lower for sk in skip_keywords):
                        articles.append({
                            'title': entry.title,
                            'url': entry.link,
                            'published': pub_date,
                            'summary': entry.get('summary', ''),
                            'source': 'prnewswire'
                        })
        except Exception as e:
            print(f"  ⚠️  PR Newswire fetch failed: {e}")
        return articles

    def _fetch_globenewswire(self, days_back: int) -> List[dict]:
        urls = [
            "https://www.globenewswire.com/RssFeed/subjectcode/16-Venture%20Capital",
            "https://www.globenewswire.com/RssFeed/subjectcode/14-Funding%20Rounds",
        ]
        articles = []
        cutoff = datetime.now() - timedelta(days=days_back)

        # GlobeNewswire VC/Funding feeds are extremely noisy — market reports,
        # scholarships, local business PR, etc. Pre-filter before sending to LLM.
        funding_keywords = [
            'raises', 'raised', 'funding', 'series a', 'series b', 'series c',
            'series d', 'series e', 'seed round', 'seed funding', 'pre-seed',
            'venture', 'investment round', 'closes', 'secures', 'backed by',
            'led by', 'growth round', 'extension', 'oversubscribed',
        ]
        skip_keywords = [
            'market size', 'market to reach', 'cagr', 'forecast', 'report',
            'industry analysis', 'scholarship', 'announces partnership',
            'expands to', 'celebrates', 'names ', 'appoints',
            'real estate', 'mortgage', 'loan', 'ipo', 'acquisition', 'merger',
            'presale', 'token sale', 'airdrop', 'ico',
        ]

        try:
            for url in urls:
                feed = feedparser.parse(url, request_headers={'User-Agent': 'Mozilla/5.0'})
                for entry in feed.entries:
                    pub_date = self._parse_entry_date(entry)
                    if not pub_date or pub_date < cutoff:
                        continue

                    title_lower = entry.title.lower()
                    summary_lower = entry.get('summary', '').lower()

                    if any(sk in title_lower for sk in skip_keywords):
                        continue

                    if not any(kw in title_lower or kw in summary_lower for kw in funding_keywords):
                        continue

                    articles.append({
                        'title': entry.title,
                        'url': entry.link,
                        'published': pub_date,
                        'summary': entry.get('summary', ''),
                        'source': 'globenewswire'
                    })
        except Exception as e:
            print(f"  ⚠️  GlobeNewswire fetch failed: {e}")
        return articles

    # ========================================================================
    # DEDUPLICATION
    # ========================================================================

    def _deduplicate(self, events: List[dict]) -> List[dict]:
        """
        Deduplicate articles about the same funding event.
        
        Two-pass approach:
        1. Title similarity (catches exact/near-duplicate headlines)
        2. Company name extraction with substring matching
           (catches "C2i raises $15M" vs "C2i Semiconductors raises $15 million")
        
        Source priority: vcnewsdaily > prnewswire = globenewswire > google_news
        """
        source_priority = {
            'vcnewsdaily': 4,
            'prnewswire': 3,
            'globenewswire': 3,
            'google_news': 1,
        }

        def priority(event):
            return source_priority.get(event['source'], 0)

        # --- Pass 1: Title-based dedup ---
        seen_titles = {}
        for event in events:
            key = self._title_key(event['title'])
            if key not in seen_titles or priority(event) > priority(seen_titles[key]):
                seen_titles[key] = event

        title_deduped = list(seen_titles.values())

        # --- Pass 2: Company-name-based dedup with substring matching ---
        hints = []
        for event in title_deduped:
            hint = self._extract_company_hint(event['title'])
            hints.append((hint, event))

        # Group by overlapping hints (substring match in either direction)
        used = [False] * len(hints)
        final = []

        for i, (hint_i, event_i) in enumerate(hints):
            if used[i]:
                continue
            group = [event_i]
            used[i] = True

            if hint_i:
                for j, (hint_j, event_j) in enumerate(hints):
                    if used[j] or not hint_j:
                        continue
                    if hint_i in hint_j or hint_j in hint_i:
                        group.append(event_j)
                        used[j] = True

            # Keep the highest-priority article in the group
            best = max(group, key=priority)
            final.append(best)

        return final

    def _deduplicate_events(self, events: List[FundingEvent]) -> List[FundingEvent]:
        """
        Post-extraction dedup on validated FundingEvents.
        Catches cases where two articles about the same company both passed
        extraction (e.g. "C2i" vs "C2i Semiconductors" with same amount).
        """
        if not events:
            return events

        groups = []  # list of (canonical_name, [events])

        for event in events:
            name = event.company_name.lower().strip()
            matched = False
            for i, (canonical, group) in enumerate(groups):
                if name in canonical or canonical in name:
                    group.append(event)
                    # Keep the longer name as canonical (more specific)
                    if len(name) > len(canonical):
                        groups[i] = (name, group)
                    matched = True
                    break
            if not matched:
                groups.append((name, [event]))

        # From each group, keep the event with the most info
        result = []
        for canonical, group in groups:
            if len(group) == 1:
                result.append(group[0])
            else:
                def score(e):
                    s = 0
                    if e.funding_round and e.funding_round not in ('Unknown', 'UNKNOWN'):
                        s += 10
                    if e.website:
                        s += 5
                    if e.lead_investors:
                        s += 3
                    if e.description:
                        s += len(e.description)
                    return s
                best = max(group, key=score)
                result.append(best)

        return result

    @staticmethod
    def _title_key(title: str) -> str:
        """Normalize a title into a dedup key."""
        key = title.lower()
        key = ''.join(c for c in key if c.isalnum() or c.isspace())
        key = ' '.join(key.split())[:60]
        return key

    @staticmethod
    def _extract_company_hint(title: str) -> Optional[str]:
        """
        Extract a normalized company name from a headline for dedup purposes.
        
        Handles patterns like:
          "C2i Semiconductors raises $15M in Series A"
          "Intel CEO-backed startup C2i Semiconductors raises $15 million"
          "Croatia's Farseer raises $7.2 mln"
          "Seasats Scoops Up $20M Series A Round"  (VCND style)
          "Temporal Pulls In $300M Series D"        (VCND style)
        """
        funding_verbs = (
            r'(?:raises?|raised|secures?|secured|closes?|closed|gets?|nabs?|'
            r'lands?|bags?|receives?|scoops?|pulls?|snares?|walks?|completes?|'
            r'announces?)'
        )
        match = re.search(
            rf'(?:startup\s+)?([A-Z][A-Za-z0-9\s\.\-&]+?)\s+{funding_verbs}\s',
            title,
            re.IGNORECASE
        )
        if match:
            name = match.group(1).strip()
            # Remove common prefixes
            name = re.sub(r"^(?:startup|company|firm|platform)\s+", "", name, flags=re.IGNORECASE)
            # Remove possessive country prefix like "Croatia's"
            name = re.sub(r"^[A-Z][a-z]+(?:'s|\u2019s)\s+", "", name)
            # Normalize
            name = name.strip().lower()
            name = re.sub(r'\s+', ' ', name)
            if len(name) >= 2:
                return name

        return None

    # ========================================================================
    # LLM EXTRACTION
    # ========================================================================

    def _extract_funding_data(self, raw_event: dict) -> Optional[FundingEvent]:
        # For VCND articles, the title + summary is usually enough —
        # skip fetching the full article to save time and avoid rate limits
        if raw_event['source'] == 'vcnewsdaily':
            article_text = f"{raw_event['title']}. {raw_event.get('summary', '')}"
        else:
            article_text = self._fetch_article_text(raw_event['url'])
            if not article_text:
                article_text = raw_event.get('summary', '')
        if not article_text:
            return None

        pub_date = raw_event.get('published')
        pub_date_str = pub_date.strftime('%Y-%m-%d') if pub_date else datetime.now().strftime('%Y-%m-%d')

        prompt = f"""Extract funding information from this press release or article.

Title: {raw_event['title']}
Content: {article_text[:3000]}

Return JSON in this exact format:
{{
  "company_name": "Official company name",
  "funding_amount_usd": 50,
  "funding_round": "Series A",
  "funding_date": null,
  "announcement_date": "{pub_date_str}",
  "lead_investors": [],
  "website": null,
  "industry": "AI/ML",
  "description": "One sentence: what the company does"
}}

Rules:
- funding_amount_usd: number in millions (50 for $50M, 1000 for $1B). Required.
  For sub-million amounts, use decimals (0.85 for $850K).
- funding_round: one of Seed, Pre-Seed, Series A, Series B, Series C, Series D, Series E, Growth, Strategic, Unknown
- announcement_date: use "{pub_date_str}" if not stated
- lead_investors: list of strings, [] if unknown
- website: root domain only (company.com), null if not mentioned or if it is a news/social site
- If NOT a startup/company raising VC or institutional funding, return: {{"not_funding": true}}
  (This includes: token presales, ICOs, crypto fundraises, crowdfunding, grants, IPOs,
   market size reports, and government funding programs)

Return ONLY valid JSON, no explanation."""

        try:
            message = self.anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}]
            )
            response_text = message.content[0].text.strip()
            if '```json' in response_text:
                response_text = response_text.split('```json')[1].split('```')[0]
            elif '```' in response_text:
                response_text = response_text.split('```')[1].split('```')[0]
            data = json.loads(response_text.strip())
            if data.get('not_funding'):
                return None
            amount = data.get('funding_amount_usd')
            if not amount or float(amount) <= 0:
                return None
            return FundingEvent(
                company_name=data.get('company_name', 'Unknown'),
                funding_amount_usd=int(float(amount)),
                funding_round=data.get('funding_round') or 'Unknown',
                funding_date=data.get('funding_date'),
                announcement_date=data.get('announcement_date') or pub_date_str,
                lead_investors=data.get('lead_investors') or [],
                website=data.get('website'),
                industry=data.get('industry'),
                description=data.get('description'),
                source_name=raw_event['source'],
                source_url=raw_event['url'],
                raw_article_text=article_text[:2000]
            )
        except json.JSONDecodeError:
            return None
        except Exception as e:
            print(f"    ⚠️  Extraction failed: {e}")
            return None

    # ========================================================================
    # HELPERS
    # ========================================================================

    def _fetch_article_text(self, url: str) -> Optional[str]:
        try:
            response = requests.get(url, timeout=8,
                headers={'User-Agent': 'Mozilla/5.0'}, verify=False)
            if response.status_code != 200:
                return None
            soup = BeautifulSoup(response.text, 'html.parser')
            for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                tag.decompose()
            article = soup.find('article') or soup.find('div', class_=lambda c: c and 'article' in c.lower())
            if article:
                return article.get_text(separator=' ', strip=True)[:3000]
            return soup.get_text(separator=' ', strip=True)[:3000]
        except Exception:
            return None

    def _parse_entry_date(self, entry) -> Optional[datetime]:
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            try:
                return datetime(*entry.published_parsed[:6])
            except Exception:
                pass
        if hasattr(entry, 'updated_parsed') and entry.updated_parsed:
            try:
                return datetime(*entry.updated_parsed[:6])
            except Exception:
                pass
        return datetime.now()