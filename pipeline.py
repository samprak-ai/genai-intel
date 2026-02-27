"""
Main Pipeline
Orchestrates the full weekly run: Discovery → Resolution → Attribution → Storage

Run weekly:         python3 pipeline.py
Run dry run:        python3 pipeline.py --dry-run
Run single co:      python3 pipeline.py --company "Harvey" --website "harvey.ai"
Run manual batch:   python3 pipeline.py --domains "Harvey:harvey.ai" "Runway:runway.ml"
Run from CSV:       python3 pipeline.py --domains-file companies.csv
"""

import os
import sys
import time
import argparse
import csv
import json
from datetime import datetime, date
from typing import Optional
from dotenv import load_dotenv

from app.models import FundingEvent, Startup, WeeklyRun
from app.discovery.funding_discovery import FundingDiscovery
from app.resolution.domain_resolver import DomainResolver
from app.attribution.attribution_engine import AttributionEngine

load_dotenv(override=True)  # override=True so .env wins over empty shell vars


class Pipeline:
    """
    Full weekly pipeline

    Stages:
      1. DISCOVER  — find funding events from RSS feeds
      2. RESOLVE   — find official website for each startup
      3. ATTRIBUTE — determine cloud + AI providers
      4. STORE     — save everything to Supabase (skipped in dry-run)
    """

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.discovery = FundingDiscovery()
        self.resolver = DomainResolver()
        self.attribution = AttributionEngine()

        # Only import database if not dry-running
        # (allows testing without Supabase credentials)
        self.db = None
        if not dry_run:
            from app.core.database import DatabaseClient
            self.db = DatabaseClient()

        # Run metrics
        self.run = WeeklyRun()
        self.errors: list[dict] = []

    # ========================================================================
    # MAIN ENTRY POINTS
    # ========================================================================

    def run_weekly(self, days_back: int = 7, limit: Optional[int] = None) -> WeeklyRun:
        """
        Full weekly run — discovers all recent funding and attributes each startup

        Args:
            days_back: How many days back to look for funding (default 7)
            limit: Max articles to process through LLM extraction (None = no limit)

        Returns:
            WeeklyRun with metrics for this execution
        """
        start_time = time.time()
        self._print_header(f"WEEKLY PIPELINE RUN — Last {days_back} days")

        if self.dry_run:
            print("  ⚠️  DRY RUN MODE — nothing will be saved to database\n")
        if limit:
            print(f"  ℹ️  Limit: processing up to {limit} articles\n")

        # Track run in database
        if self.db:
            self.db.create_weekly_run(self.run)

        try:
            # Stage 1: Discovery
            funding_events = self._stage_discover(days_back, limit=limit)
            if not funding_events:
                print("\n⚠️  No funding events found. Exiting.")
                return self._complete_run(start_time, "completed")

            # Stage 2: Resolution (find websites for events missing them)
            funding_events = self._stage_resolve(funding_events)

            # Post-resolution dedup: same website = same company
            # Catches duplicates that slipped through name-based dedup (e.g. Nimble/Ubicquia)
            seen_websites: set = set()
            deduped: list = []
            for event in funding_events:
                if event.website and event.website in seen_websites:
                    print(f"  ♻️  Skipping duplicate domain: {event.company_name} ({event.website})")
                    continue
                if event.website:
                    seen_websites.add(event.website)
                deduped.append(event)
            if len(deduped) < len(funding_events):
                print(f"  🔄 Post-resolution dedup: removed {len(funding_events) - len(deduped)} duplicate(s)")
            funding_events = deduped

            # Stage 3: Attribution (cloud + AI providers)
            results = self._stage_attribute(funding_events)

            # Stage 4: Storage
            if not self.dry_run:
                self._stage_store(results)

            # Print summary
            self._print_summary(results, start_time)

        except Exception as e:
            self._log_error("pipeline", str(e))
            return self._complete_run(start_time, "failed")

        return self._complete_run(start_time, "completed")

    def run_manual(
        self,
        entries: list[tuple[str, str | None, list[str], list[str], list[str]]],
    ) -> WeeklyRun:
        """
        Run attribution for a manually specified list of companies.
        Skips Stage 1 (discovery).  If a website is omitted for an entry
        the resolver is called just as it would be in the weekly run.

        Args:
            entries: List of 5-tuples:
                       (company_name, website_or_None, evidence_urls, investors, founder_background)
                     website:           None → resolver finds it
                     evidence_urls:     known partnership/press release pages (Tier 1)
                     investors:         investor names → cloud prior inference (Tier 3)
                     founder_background: prior employer tags → cloud prior (Tier 3)

        Returns:
            WeeklyRun with metrics for this execution
        """
        start_time = time.time()
        self._print_header(f"MANUAL RUN — {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")

        if self.dry_run:
            print("  ⚠️  DRY RUN MODE — nothing will be saved to database\n")

        # Track run in database
        if self.db:
            self.db.create_weekly_run(self.run)

        try:
            from datetime import date as _date
            funding_events: list[FundingEvent] = []
            evidence_url_map: dict[str, list[str]] = {}

            for company_name, website, evidence_urls, investors, founder_bg in entries:
                funding_events.append(FundingEvent(
                    company_name=company_name,
                    website=website or None,
                    funding_amount_usd=0,           # placeholder — not stored in manual mode
                    funding_round="Manual",
                    announcement_date=_date.today(),
                    source_name="manual",
                    source_url="manual",
                    lead_investors=investors or [],
                    founder_background=founder_bg or [],
                ))
                if evidence_urls:
                    evidence_url_map[company_name] = evidence_urls

            self.run.startups_discovered = len(funding_events)

            # Stage 2: Resolution (fill in missing websites)
            funding_events = self._stage_resolve(funding_events)

            # Stage 3: Attribution
            results = self._stage_attribute(funding_events, evidence_url_map=evidence_url_map)

            # Stage 4: Storage (skips placeholder funding events — only saves
            # startup + signals + snapshot)
            if not self.dry_run:
                self._stage_store_manual(results)

            # Print summary
            self._print_summary(results, start_time)

        except Exception as e:
            self._log_error("pipeline", str(e))
            return self._complete_run(start_time, "failed")

        return self._complete_run(start_time, "completed")

    def run_single(self, company_name: str, website: str, industry: Optional[str] = None) -> dict:
        """
        Run attribution for a single company — useful for testing or manual checks

        Args:
            company_name: Company name e.g. "Harvey"
            website: Domain e.g. "harvey.ai"
            industry: Optional industry tag e.g. "AI Chip Design" — enables hardware prior

        Returns:
            Dict with cloud and AI attribution results
        """
        self._print_header(f"SINGLE COMPANY RUN — {company_name}")

        cloud_attr, ai_attr = self.attribution.attribute_startup(
            company_name=company_name,
            website=website,
            article_text=None,  # single runs have no article text
            industry=industry,
        )

        result = {
            "company": company_name,
            "website": website,
            "cloud": {
                "display": cloud_attr.display_name if cloud_attr else "Unknown",
                "is_multi": cloud_attr.is_multi if cloud_attr else False,
                "providers": cloud_attr.provider_names if cloud_attr else [],
                "confidence": f"{cloud_attr.confidence:.0%}" if cloud_attr else "0%",
                "evidence_count": cloud_attr.evidence_count if cloud_attr else 0,
            },
            "ai": {
                "display": ai_attr.display_name if ai_attr else "Unknown",
                "is_multi": ai_attr.is_multi if ai_attr else False,
                "providers": ai_attr.provider_names if ai_attr else [],
                "confidence": f"{ai_attr.confidence:.0%}" if ai_attr else "0%",
                "evidence_count": ai_attr.evidence_count if ai_attr else 0,
            }
        }

        self._print_single_result(result, cloud_attr, ai_attr)
        return result

    # ========================================================================
    # STAGE 1: DISCOVERY
    # ========================================================================

    def _stage_discover(self, days_back: int, limit: Optional[int] = None) -> list[FundingEvent]:
        """Find recent funding events from RSS sources"""
        self._print_stage(1, "DISCOVERY", "Fetching funding announcements")

        events = self.discovery.discover_recent_funding(days_back=days_back, limit=limit)
        self.run.startups_discovered = len(events)

        print(f"\n  ✅ Found {len(events)} funding events")
        return events

    # ========================================================================
    # STAGE 2: RESOLUTION
    # ========================================================================

    def _stage_resolve(self, events: list[FundingEvent]) -> list[FundingEvent]:
        """Resolve official website for events that don't have one"""
        self._print_stage(2, "RESOLUTION", "Finding official websites")

        missing = [e for e in events if not e.website]
        already_have = len(events) - len(missing)

        print(f"  Already have website: {already_have}/{len(events)}")
        print(f"  Need to resolve:      {len(missing)}/{len(events)}\n")

        for event in missing:
            try:
                website = self.resolver.resolve(
                    company_name=event.company_name,
                    article_text=event.raw_article_text,
                    funding_round=event.funding_round,
                    funding_amount_usd=event.funding_amount_usd,
                    lead_investors=event.lead_investors,
                    description=event.description,
                    industry=event.industry,
                    source_url=event.source_url,
                )
                if website:
                    event.website = website
            except Exception as e:
                self._log_error(f"resolve:{event.company_name}", str(e))

        # Count results
        resolved = sum(1 for e in events if e.website)
        unresolved = len(events) - resolved

        print(f"\n  ✅ Resolution complete:")
        print(f"     With website:    {resolved}/{len(events)}")
        print(f"     Without website: {unresolved}/{len(events)} (will skip attribution)")

        return events

    # ========================================================================
    # STAGE 3: ATTRIBUTION
    # ========================================================================

    def _stage_attribute(
        self,
        events: list[FundingEvent],
        evidence_url_map: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        """Run cloud + AI attribution for each startup with a website"""
        self._print_stage(3, "ATTRIBUTION", "Determining cloud and AI providers")

        # Only attribute startups that have a website
        attributable = [e for e in events if e.website]
        skipped = len(events) - len(attributable)

        if skipped:
            print(f"  Skipping {skipped} startups (no website resolved)\n")

        results = []
        attributed_count = 0
        evidence_url_map = dict(evidence_url_map or {})  # mutable copy

        # Enrich FundingEvents from existing DB startup rows.
        # When using --domains or --domains-file the FundingEvent only carries
        # what was passed on the CLI (often just company + website).  Pull
        # industry, lead_investors, and founder_background from the DB so that
        # hardware priors, investor priors, and founder priors all fire correctly
        # even when those fields weren't supplied on the command line.
        # Also load evidence_urls from manual_overrides so they persist across
        # all future runs (cron, manual, re-attribution) without needing to
        # pass them explicitly every time.
        if self.db:
            for event in attributable:
                startup_row = self.db.get_startup_by_website(event.website or '')
                if startup_row:
                    if not event.industry and startup_row.get('industry'):
                        event.industry = startup_row['industry']
                    if not event.lead_investors and startup_row.get('lead_investors'):
                        event.lead_investors = startup_row['lead_investors']
                    if not event.founder_background and startup_row.get('founder_background'):
                        event.founder_background = startup_row['founder_background']
                    override = self.db.get_manual_override(startup_row['id'])
                    if override and override.get('evidence_urls'):
                        existing = evidence_url_map.get(event.company_name, [])
                        merged = list(dict.fromkeys(existing + override['evidence_urls']))
                        evidence_url_map[event.company_name] = merged

        for i, event in enumerate(attributable, 1):
            print(f"\n  [{i}/{len(attributable)}] {event.company_name} ({event.website})")

            try:
                cloud_attr, ai_attr = self.attribution.attribute_startup(
                    company_name=event.company_name,
                    website=event.website,
                    article_text=event.raw_article_text,
                    lead_investors=event.lead_investors or [],
                    founder_background=event.founder_background or [],
                    evidence_urls=evidence_url_map.get(event.company_name, []),
                    industry=event.industry,
                )

                result = {
                    "event": event,
                    "cloud_attribution": cloud_attr,
                    "ai_attribution": ai_attr,
                    "attributed": bool(cloud_attr or ai_attr)
                }
                results.append(result)

                if cloud_attr or ai_attr:
                    attributed_count += 1

            except Exception as e:
                self._log_error(f"attribute:{event.company_name}", str(e))
                results.append({
                    "event": event,
                    "cloud_attribution": None,
                    "ai_attribution": None,
                    "attributed": False,
                    "error": str(e)
                })

        self.run.startups_attributed = attributed_count
        print(f"\n  ✅ Attribution complete: {attributed_count}/{len(attributable)} attributed")

        return results

    # ========================================================================
    # STAGE 4: STORAGE
    # ========================================================================

    def _stage_store(self, results: list[dict]) -> None:
        """Save everything to Supabase"""
        self._print_stage(4, "STORAGE", "Saving to Supabase")

        saved = 0
        for result in results:
            event = result["event"]
            cloud_attr = result["cloud_attribution"]
            ai_attr = result["ai_attribution"]

            try:
                # Upsert startup
                startup = self.db.create_startup(Startup(
                    canonical_name=event.company_name,
                    website=event.website,
                    industry=event.industry,
                    description=event.description
                ))

                if not startup:
                    continue

                startup_id = startup["id"]

                # Save funding event
                self.db.create_funding_event(startup_id, event)

                # Clear stale signals before writing new ones — prevents old false
                # positives from surviving re-attribution runs where the engine now
                # correctly discards a previously-accepted signal.
                self.db.delete_signals_for_startup(startup_id)

                # Save attribution signals
                if cloud_attr:
                    for signal in cloud_attr.signals:
                        self.db.create_signal(startup_id, signal)

                if ai_attr:
                    for signal in ai_attr.signals:
                        self.db.create_signal(startup_id, signal)

                # Save attribution snapshot
                snapshot_data = self._build_snapshot(startup_id, cloud_attr, ai_attr)
                self.db.create_snapshot(snapshot_data)

                saved += 1
                print(f"  ✅ Saved: {event.company_name}")

            except Exception as e:
                self._log_error(f"store:{event.company_name}", str(e))
                print(f"  ❌ Failed: {event.company_name} — {e}")

        print(f"\n  ✅ Storage complete: {saved}/{len(results)} saved")

    def _stage_store_manual(self, results: list[dict]) -> None:
        """
        Like _stage_store but omits saving funding events
        (manual entries don't have real funding data).
        Saves startup entity + attribution signals + snapshot.
        """
        self._print_stage(4, "STORAGE", "Saving to Supabase (startup + attribution only)")

        saved = 0
        for result in results:
            event = result["event"]
            cloud_attr = result["cloud_attribution"]
            ai_attr = result["ai_attribution"]

            if not event.website:
                print(f"  ⚠️  Skipping {event.company_name} — no website resolved")
                continue

            try:
                # Upsert startup
                startup = self.db.create_startup(Startup(
                    canonical_name=event.company_name,
                    website=event.website,
                    industry=event.industry,
                    description=event.description,
                ))

                if not startup:
                    continue

                startup_id = startup["id"]

                # Clear stale signals before writing new ones
                self.db.delete_signals_for_startup(startup_id)

                # Save attribution signals
                if cloud_attr:
                    for signal in cloud_attr.signals:
                        self.db.create_signal(startup_id, signal)

                if ai_attr:
                    for signal in ai_attr.signals:
                        self.db.create_signal(startup_id, signal)

                # Save attribution snapshot
                snapshot_data = self._build_snapshot(startup_id, cloud_attr, ai_attr)
                self.db.create_snapshot(snapshot_data)

                saved += 1
                print(f"  ✅ Saved: {event.company_name}")

            except Exception as e:
                self._log_error(f"store:{event.company_name}", str(e))
                print(f"  ❌ Failed: {event.company_name} — {e}")

        print(f"\n  ✅ Storage complete: {saved}/{len(results)} saved")

    def _build_snapshot(
        self,
        startup_id: str,
        cloud_attr,
        ai_attr
    ) -> dict:
        """Build the snapshot dict for database storage — matches attribution_snapshots schema exactly"""
        snapshot = {
            "startup_id": startup_id,
            "snapshot_date": str(date.today()),

            # Cloud fields
            "cloud_is_multi":         cloud_attr.is_multi          if cloud_attr else False,
            "cloud_primary_provider": cloud_attr.primary_provider  if cloud_attr else None,
            "cloud_providers":        cloud_attr.provider_names    if cloud_attr else [],
            "cloud_confidence":       cloud_attr.confidence        if cloud_attr else None,
            "cloud_entrenchment":     cloud_attr.providers[0].entrenchment.value if (cloud_attr and cloud_attr.providers) else None,
            "cloud_evidence_count":   cloud_attr.evidence_count    if cloud_attr else 0,
            "cloud_raw_score":        cloud_attr.providers[0].raw_score if (cloud_attr and cloud_attr.providers) else None,
            "cloud_not_applicable":   cloud_attr.is_not_applicable if cloud_attr else False,
            "cloud_not_applicable_note": cloud_attr.not_applicable_note if cloud_attr else None,

            # AI fields
            "ai_is_multi":         ai_attr.is_multi          if ai_attr else False,
            "ai_primary_provider": ai_attr.primary_provider  if ai_attr else None,
            "ai_providers":        ai_attr.provider_names    if ai_attr else [],
            "ai_confidence":       ai_attr.confidence        if ai_attr else None,
            "ai_entrenchment":     ai_attr.providers[0].entrenchment.value if (ai_attr and ai_attr.providers) else None,
            "ai_evidence_count":   ai_attr.evidence_count    if ai_attr else 0,
            "ai_raw_score":        ai_attr.providers[0].raw_score if (ai_attr and ai_attr.providers) else None,
            "ai_not_applicable":   ai_attr.is_not_applicable if ai_attr else False,
            "ai_not_applicable_note": ai_attr.not_applicable_note if ai_attr else None,
        }
        return snapshot

    # ========================================================================
    # RUN TRACKING
    # ========================================================================

    def _complete_run(self, start_time: float, status: str) -> WeeklyRun:
        """Finalise run metrics"""
        self.run.status = status
        self.run.execution_time_seconds = int(time.time() - start_time)
        self.run.errors_count = len(self.errors)
        self.run.error_log = {str(i): e for i, e in enumerate(self.errors)}
        self.run.completed_at = datetime.now()

        if self.db:
            self.db.update_weekly_run(self.run)

        return self.run

    def _log_error(self, context: str, message: str) -> None:
        self.errors.append({"context": context, "error": message, "time": str(datetime.now())})
        print(f"  ⚠️  Error in {context}: {message}")

    # ========================================================================
    # DISPLAY HELPERS
    # ========================================================================

    def _print_header(self, title: str) -> None:
        print("\n" + "=" * 70)
        print(f"  {title}")
        print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)

    def _print_stage(self, num: int, name: str, description: str) -> None:
        print(f"\n{'─' * 70}")
        print(f"  STAGE {num}: {name}")
        print(f"  {description}")
        print(f"{'─' * 70}\n")

    def _print_single_result(self, result: dict, cloud_attr, ai_attr) -> None:
        print(f"\n{'=' * 70}")
        print(f"  RESULT: {result['company']}")
        print(f"{'=' * 70}")

        print(f"\n  ☁️  CLOUD:  {result['cloud']['display']}")
        print(f"      Confidence: {result['cloud']['confidence']}  |  Entrenchment: {cloud_attr.providers[0].entrenchment.value if cloud_attr and cloud_attr.providers else 'N/A'}  |  Signals: {result['cloud']['evidence_count']}")
        if cloud_attr and cloud_attr.providers:
            for p in cloud_attr.providers:
                print(f"      • {p.provider_name} — {p.role}  [{p.confidence:.0%} share, score {p.raw_score:.1f}, {p.entrenchment.value}]")
                for s in p.signals:
                    weight_label = {1.0: "STRONG", 0.6: "MEDIUM", 0.3: "WEAK"}.get(s.confidence_weight, f"{s.confidence_weight}")
                    print(f"        – [{weight_label}] {s.signal_source}: {s.evidence_text[:80]}")

        print(f"\n  🤖 AI:     {result['ai']['display']}")
        print(f"      Confidence: {result['ai']['confidence']}  |  Entrenchment: {ai_attr.providers[0].entrenchment.value if ai_attr and ai_attr.providers else 'N/A'}  |  Signals: {result['ai']['evidence_count']}")
        if ai_attr and ai_attr.providers:
            for p in ai_attr.providers:
                print(f"      • {p.provider_name} — {p.role}  [{p.confidence:.0%} share, score {p.raw_score:.1f}]")
                for s in p.signals:
                    weight_label = {1.0: "STRONG", 0.6: "MEDIUM", 0.3: "WEAK"}.get(s.confidence_weight, f"{s.confidence_weight}")
                    print(f"        – [{weight_label}] {s.signal_source}: {s.evidence_text[:80]}")

    def _print_summary(self, results: list[dict], start_time: float = None) -> None:
        print(f"\n{'=' * 70}")
        print(f"  PIPELINE SUMMARY")
        print(f"{'=' * 70}\n")

        print(f"  {'Company':<28} {'Cloud':<18} {'Conf':>5}  {'AI':<22} {'Conf':>5}")
        print(f"  {'─'*28} {'─'*18} {'─'*5}  {'─'*22} {'─'*5}")

        na_notes: list[tuple[str, str, str]] = []  # (company, field, note)

        for r in results:
            event = r["event"]
            cloud = r["cloud_attribution"]
            ai = r["ai_attribution"]

            cloud_str  = cloud.display_name if cloud else "Unknown"
            ai_str     = ai.display_name    if ai    else "Unknown"
            cloud_conf = "N/A" if (cloud and cloud.is_not_applicable) else (f"{cloud.confidence:.0%}" if cloud else "—")
            ai_conf    = "N/A" if (ai    and ai.is_not_applicable)    else (f"{ai.confidence:.0%}"    if ai    else "—")

            # Collect N/A notes for footer
            if cloud and cloud.is_not_applicable and cloud.not_applicable_note:
                na_notes.append((event.company_name, "Cloud", cloud.not_applicable_note))
            if ai and ai.is_not_applicable and ai.not_applicable_note:
                na_notes.append((event.company_name, "AI", ai.not_applicable_note))

            # Truncate for display
            cloud_str = cloud_str[:16] + ".." if len(cloud_str) > 18 else cloud_str
            ai_str    = ai_str[:20]    + ".." if len(ai_str)    > 22 else ai_str

            print(f"  {event.company_name:<28} {cloud_str:<18} {cloud_conf:>5}  {ai_str:<22} {ai_conf:>5}")

        # Print N/A notes as a footer
        if na_notes:
            print(f"\n  ℹ️  Not Applicable notes:")
            for company, field, note in na_notes:
                print(f"     • {company} [{field}]: {note}")

        duration = int(time.time() - start_time) if start_time else self.run.execution_time_seconds
        print(f"\n  Total discovered:  {self.run.startups_discovered}")
        print(f"  Total attributed:  {self.run.startups_attributed}")
        print(f"  Errors:            {len(self.errors)}")
        print(f"  Duration:          {duration}s")

        if self.dry_run:
            print(f"\n  ⚠️  DRY RUN — nothing was saved to database")
        else:
            print(f"\n  ✅ All results saved to Supabase")


# ============================================================================
# CLI HELPERS
# ============================================================================

def _parse_domain_args(raw: list[str]) -> list[tuple[str, str | None, list[str], list[str], list[str]]]:
    """
    Parse inline --domains arguments.

    Token format (all fields after company:website use '|' as list separator):

      "Company"
          → (company, None, [], [], [])

      "Company:website"
          → (company, website, [], [], [])

      "Company:website:ev_url1|ev_url2"
          → (company, website, [ev_url1, ev_url2], [], [])

      "Company:website:ev_url1|ev_url2::GV|Sequoia"
          → (company, website, [ev_url1, ev_url2], ["GV", "Sequoia"], [])

      "Company:website:::Google Brain|DeepMind"
          → (company, website, [], [], ["Google Brain", "DeepMind"])

      "Company:website:ev_url:GV:Google Brain"
          → (company, website, [ev_url], ["GV"], ["Google Brain"])

    Fields in order:  company : website : evidence_urls : investors : founder_background
    All list fields are pipe-separated (|).  Empty fields use empty string.

    NOTE: evidence_urls start with 'http' — used to auto-detect the field if
    the user omits the separator between website and evidence block.
    """
    entries: list[tuple[str, str | None, list[str], list[str], list[str]]] = []

    for token in raw:
        token = token.strip()
        if not token:
            continue

        # Split on ':' but stop at 5 fields to preserve any colons inside URLs
        # The format is:  company : website : ev_urls : investors : founder
        # Evidence URLs contain ':' (https://) so we only split the first 2 colons
        # and treat everything up to the third ':' as the evidence block.
        # Strategy: find first two ':' to isolate company and website, then
        # split the remainder by ':' for the optional metadata fields.

        idx1 = token.find(":")
        if idx1 == -1:
            # No colons at all — just a company name
            entries.append((token.strip(), None, [], [], []))
            continue

        company = token[:idx1].strip()
        after_company = token[idx1+1:]

        # Find where the website ends — it ends at the next ':' that is NOT
        # followed by '/' (which would indicate an https:// URL).
        # We scan for ':' characters that aren't part of a URL scheme.
        website = None
        after_website = ""
        i = 0
        while i < len(after_company):
            if after_company[i] == ':':
                # Check if this colon is part of "://" (URL scheme)
                if after_company[i:i+3] == '://':
                    i += 3
                    continue
                # This is a field separator
                website = after_company[:i].strip() or None
                after_website = after_company[i+1:]
                break
            i += 1
        else:
            # No separator found after website
            website = after_company.strip() or None
            after_website = ""

        # Now split after_website by ':' for the remaining 3 optional fields.
        # But URLs in field 0 (evidence) may still contain '://' — so we split
        # on ':' that are NOT preceded by a letter that could be a URL scheme.
        # Simplest safe approach: split on ':' that are NOT followed by '//'
        remaining_fields: list[str] = []
        buf = ""
        j = 0
        while j < len(after_website):
            if after_website[j] == ':' and after_website[j:j+3] != '://':
                remaining_fields.append(buf)
                buf = ""
            else:
                buf += after_website[j]
            j += 1
        remaining_fields.append(buf)

        def _split_pipe(s: str) -> list[str]:
            return [x.strip() for x in s.split("|") if x.strip()]

        ev_raw        = remaining_fields[0] if len(remaining_fields) > 0 else ""
        inv_raw       = remaining_fields[1] if len(remaining_fields) > 1 else ""
        founder_raw   = remaining_fields[2] if len(remaining_fields) > 2 else ""

        evidence_urls    = [u for u in _split_pipe(ev_raw) if u.startswith("http")]
        investors        = _split_pipe(inv_raw)
        founder_bg       = _split_pipe(founder_raw)

        if company:
            entries.append((company, website, evidence_urls, investors, founder_bg))

    return entries


def _parse_domains_file(path: str) -> list[tuple[str, str | None, list[str], list[str], list[str]]]:
    """
    Parse a CSV file for manual batch runs.

    Column layout (header row optional, auto-detected):
      company_name  |  website  |  evidence_urls  |  investors  |  founder_background

    • website:           optional — resolver fills it in when blank
    • evidence_urls:     pipe-separated URLs  e.g.  https://a.com|https://b.com
    • investors:         pipe-separated names e.g.  GV|Sequoia
    • founder_background: pipe-separated tags  e.g.  Google Brain|DeepMind

    Any trailing columns may be omitted.
    """
    entries: list[tuple[str, str | None, list[str], list[str], list[str]]] = []

    def _split_pipe(s: str) -> list[str]:
        return [x.strip() for x in s.split("|") if x.strip()]

    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            rows = list(reader)

        if not rows:
            return entries

        # Auto-detect header
        start = 0
        first_cell = rows[0][0].strip().lower() if rows[0] else ""
        header_keywords = {"company", "company_name", "name", "startup", "website", "domain"}
        if first_cell in header_keywords:
            start = 1

        for row in rows[start:]:
            if not row:
                continue
            company      = row[0].strip()
            website      = (row[1].strip() or None) if len(row) > 1 else None
            ev_raw       = row[2].strip() if len(row) > 2 else ""
            inv_raw      = row[3].strip() if len(row) > 3 else ""
            founder_raw  = row[4].strip() if len(row) > 4 else ""

            evidence_urls = [u for u in _split_pipe(ev_raw) if u.startswith("http")]
            investors     = _split_pipe(inv_raw)
            founder_bg    = _split_pipe(founder_raw)

            if company:
                entries.append((company, website, evidence_urls, investors, founder_bg))

    except FileNotFoundError:
        print(f"❌ File not found: {path}")
    except Exception as e:
        print(f"❌ Error reading {path}: {e}")

    return entries


# ============================================================================
# CLI
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="GenAI-Intel Pipeline — weekly startup intelligence"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without saving to database (great for testing)"
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=7,
        help="How many days back to look for funding (default: 7)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of articles to process through LLM extraction (useful for dry runs)"
    )
    parser.add_argument(
        "--company",
        type=str,
        help="Run attribution for a single company name"
    )
    parser.add_argument(
        "--website",
        type=str,
        help="Website for single company run (required with --company)"
    )
    parser.add_argument(
        "--industry",
        type=str,
        default=None,
        help="Industry tag for single company run — enables hardware prior if applicable "
             "(e.g. 'AI Chip Design', 'Humanoid Robotics', 'Space Infrastructure')"
    )
    parser.add_argument(
        "--domains",
        nargs="+",
        metavar="ENTRY",
        help=(
            'Manual batch. Each entry is a colon-separated token: '
            '"Company:website:evidence_urls:investors:founder_background". '
            'All fields after website are optional and pipe-separated (|). '
            'Examples: '
            '"Harvey:harvey.ai" '
            '"Ricursive:ricursive.ai:::GV:Google Brain|DeepMind" '
            '"Axiom Space:axiomspace.com:https://axiomspace.com/release/aws-all-in" '
        )
    )
    parser.add_argument(
        "--domains-file",
        type=str,
        metavar="FILE",
        help=(
            "Manual batch from CSV file. "
            "Columns: company_name, website, evidence_urls, investors, founder_background. "
            "All columns after company_name are optional. "
            "Lists within cells are pipe-separated (|). "
            "First row may be a header (auto-detected). "
            "Example: --domains-file companies.csv"
        )
    )

    args = parser.parse_args()

    # ── Single company mode ──────────────────────────────────────────────────
    if args.company:
        if not args.website:
            print("❌ --website is required when using --company")
            sys.exit(1)

        pipeline = Pipeline(dry_run=True)  # Single runs are always dry
        pipeline.run_single(args.company, args.website, industry=args.industry)
        return

    # ── Manual batch mode — inline domains ──────────────────────────────────
    if args.domains:
        entries = _parse_domain_args(args.domains)
        if not entries:
            print("❌ No valid entries found in --domains argument")
            sys.exit(1)
        pipeline = Pipeline(dry_run=args.dry_run)
        run = pipeline.run_manual(entries)
        if run.status == "failed":
            sys.exit(1)
        return

    # ── Manual batch mode — CSV file ─────────────────────────────────────────
    if args.domains_file:
        entries = _parse_domains_file(args.domains_file)
        if not entries:
            print(f"❌ No valid entries found in {args.domains_file}")
            sys.exit(1)
        pipeline = Pipeline(dry_run=args.dry_run)
        run = pipeline.run_manual(entries)
        if run.status == "failed":
            sys.exit(1)
        return

    # ── Full weekly run ──────────────────────────────────────────────────────
    pipeline = Pipeline(dry_run=args.dry_run)
    run = pipeline.run_weekly(days_back=args.days_back, limit=args.limit)

    # Exit with error code if pipeline failed
    if run.status == "failed":
        sys.exit(1)


if __name__ == "__main__":
    main()