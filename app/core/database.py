"""
Database client for GenAI-Intel
Wraps Supabase client with typed methods
"""

import os
from typing import Optional
from datetime import date, datetime
from supabase import create_client, Client
from app.models import (
    Startup, FundingEvent, AttributionSignal,
    AttributionSnapshot, WeeklyRun, Attribution
)


class DatabaseClient:
    """Type-safe Supabase database client"""

    def __init__(self):
        url = os.getenv('SUPABASE_URL')
        key = os.getenv('SUPABASE_KEY')

        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment")

        self.client: Client = create_client(url, key)

    # ========================================================================
    # STARTUPS
    # ========================================================================

    def create_startup(self, startup: Startup) -> dict:
        """Upsert startup by website — returns raw row dict (includes id)"""
        data = {
            'canonical_name': startup.canonical_name,
            'website': startup.website,
            'industry': startup.industry,
            'description': startup.description,
        }

        result = self.client.table('startups') \
            .upsert(data, on_conflict='website') \
            .execute()

        if result.data:
            return result.data[0]

        raise Exception(f"Failed to upsert startup: {startup.canonical_name}")

    def get_startup_by_id(self, startup_id: str) -> Optional[dict]:
        """Get startup row by UUID"""
        result = self.client.table('startups') \
            .select('*') \
            .eq('id', startup_id) \
            .single() \
            .execute()
        return result.data

    def get_startup_by_website(self, website: str) -> Optional[dict]:
        """Find startup row by website"""
        result = self.client.table('startups') \
            .select('*') \
            .eq('website', website.lower()) \
            .execute()
        return result.data[0] if result.data else None

    def get_startup_by_name(self, name: str) -> Optional[dict]:
        """Find startup row by canonical name"""
        result = self.client.table('startups') \
            .select('*') \
            .eq('canonical_name', name) \
            .execute()
        return result.data[0] if result.data else None

    def list_startups(
        self,
        cloud_provider: Optional[str] = None,
        ai_provider: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        """
        Paginated company list from latest_attributions view.
        Supports filter by cloud/AI provider, name search, and snapshot_date range.
        date_from / date_to are inclusive ISO date strings (YYYY-MM-DD).
        """
        query = self.client.table('latest_attributions').select('*')

        if cloud_provider:
            query = query.contains('cloud_providers', [cloud_provider])
        if ai_provider:
            query = query.contains('ai_providers', [ai_provider])
        if search:
            query = query.ilike('canonical_name', f'%{search}%')
        if date_from:
            query = query.gte('snapshot_date', date_from)
        if date_to:
            query = query.lte('snapshot_date', date_to)

        offset = (page - 1) * per_page
        result = query \
            .order('snapshot_date', desc=True) \
            .range(offset, offset + per_page - 1) \
            .execute()

        return result.data if result.data else []

    # ========================================================================
    # FUNDING EVENTS
    # ========================================================================

    def create_funding_event(self, startup_id: str, event: FundingEvent) -> str:
        """Create funding event — returns new row id"""
        data = {
            'startup_id': startup_id,
            'funding_amount_usd': event.funding_amount_usd,
            'funding_round': event.funding_round,
            'funding_date': event.funding_date.isoformat() if event.funding_date else None,
            'announcement_date': event.announcement_date.isoformat(),
            'lead_investors': event.lead_investors,
            'source_name': event.source_name,
            'source_url': event.source_url,
            'raw_article_text': event.raw_article_text,
            'extracted_json': event.model_dump(mode='json'),
        }

        result = self.client.table('funding_events') \
            .upsert(data, on_conflict='startup_id,funding_amount_usd,announcement_date') \
            .execute()

        if result.data:
            return result.data[0]['id']

        raise Exception(f"Failed to create funding event for startup {startup_id}")

    def get_funding_events_for_startup(self, startup_id: str) -> list[dict]:
        """Get all funding events for a startup, newest first"""
        result = self.client.table('funding_events') \
            .select('*') \
            .eq('startup_id', startup_id) \
            .order('announcement_date', desc=True) \
            .execute()
        return result.data if result.data else []

    # ========================================================================
    # ATTRIBUTION SIGNALS
    # ========================================================================

    def create_signal(self, startup_id: str, signal: AttributionSignal) -> str:
        """Upsert attribution signal — returns row id"""
        data = {
            'startup_id': startup_id,
            'provider_type': signal.provider_type.value,
            'provider_name': signal.provider_name,
            'signal_source': signal.signal_source,
            'signal_strength': signal.signal_strength.value,
            'evidence_text': signal.evidence_text,
            'evidence_url': signal.evidence_url,
            'confidence_weight': signal.confidence_weight,
        }

        result = self.client.table('attribution_signals') \
            .upsert(data, on_conflict='startup_id,provider_type,provider_name,signal_source') \
            .execute()

        if result.data:
            return result.data[0]['id']

        raise Exception(f"Failed to upsert signal for startup {startup_id}")

    def get_signals_for_startup(
        self,
        startup_id: str,
        provider_type: Optional[str] = None,
    ) -> list[dict]:
        """Get all signals for a startup, newest first"""
        query = self.client.table('attribution_signals') \
            .select('*') \
            .eq('startup_id', startup_id)

        if provider_type:
            query = query.eq('provider_type', provider_type)

        result = query.order('collected_at', desc=True).execute()
        return result.data if result.data else []

    def delete_signals_for_startup(
        self,
        startup_id: str,
        provider_type: Optional[str] = None,
    ) -> None:
        """Delete all signals for a startup (used before re-attribution)"""
        query = self.client.table('attribution_signals') \
            .delete() \
            .eq('startup_id', startup_id)

        if provider_type:
            query = query.eq('provider_type', provider_type)

        query.execute()

    # ========================================================================
    # ATTRIBUTION SNAPSHOTS
    # ========================================================================

    def create_snapshot(self, snapshot: dict) -> str:
        """
        Upsert attribution snapshot.

        Expects a pre-built dict (from pipeline._build_snapshot()) with keys
        matching the attribution_snapshots schema columns exactly:
            startup_id, snapshot_date,
            cloud_is_multi, cloud_primary_provider, cloud_providers,
            cloud_confidence, cloud_entrenchment, cloud_evidence_count, cloud_raw_score,
            ai_is_multi, ai_primary_provider, ai_providers,
            ai_confidence, ai_entrenchment, ai_evidence_count, ai_raw_score
        """
        result = self.client.table('attribution_snapshots') \
            .upsert(snapshot, on_conflict='startup_id,snapshot_date') \
            .execute()

        if result.data:
            return result.data[0]['id']

        raise Exception(f"Failed to upsert snapshot for startup {snapshot.get('startup_id')}")

    def get_latest_snapshot(self, startup_id: str) -> Optional[dict]:
        """Get most recent attribution snapshot for a startup"""
        result = self.client.table('attribution_snapshots') \
            .select('*') \
            .eq('startup_id', startup_id) \
            .order('snapshot_date', desc=True) \
            .limit(1) \
            .execute()
        return result.data[0] if result.data else None

    def get_snapshot_history(self, startup_id: str) -> list[dict]:
        """Get full attribution history for a startup — used for trend charts"""
        result = self.client.table('attribution_snapshots') \
            .select('*') \
            .eq('startup_id', startup_id) \
            .order('snapshot_date', desc=True) \
            .execute()
        return result.data if result.data else []

    # ========================================================================
    # WEEKLY RUNS
    # ========================================================================

    def create_weekly_run(self, run: WeeklyRun) -> str:
        """Insert weekly run record — returns new row id, sets run.id"""
        data = {
            'run_date': run.run_date.isoformat(),
            'status': run.status,
            'started_at': run.started_at.isoformat(),
        }

        result = self.client.table('weekly_runs') \
            .upsert(data, on_conflict='run_date') \
            .execute()

        if result.data:
            run_id = result.data[0]['id']
            run.id = run_id
            return run_id

        raise Exception("Failed to create weekly run")

    def update_weekly_run(self, run: WeeklyRun) -> None:
        """Update weekly run with final metrics — requires run.id to be set"""
        if not run.id:
            raise ValueError("Cannot update weekly run: run.id is not set")

        updates = {
            'status': run.status,
            'startups_discovered': run.startups_discovered,
            'startups_attributed': run.startups_attributed,
            'errors_count': run.errors_count,
            'execution_time_seconds': run.execution_time_seconds,
            'error_log': run.error_log,
            'completed_at': run.completed_at.isoformat() if run.completed_at else None,
        }

        self.client.table('weekly_runs') \
            .update(updates) \
            .eq('id', run.id) \
            .execute()

    def get_weekly_run(self, run_id: str) -> Optional[dict]:
        """Get a specific weekly run by id"""
        result = self.client.table('weekly_runs') \
            .select('*') \
            .eq('id', run_id) \
            .single() \
            .execute()
        return result.data

    def get_latest_run(self) -> Optional[dict]:
        """Get most recent weekly run"""
        result = self.client.table('weekly_runs') \
            .select('*') \
            .order('run_date', desc=True) \
            .limit(1) \
            .execute()
        return result.data[0] if result.data else None

    def list_weekly_runs(self, limit: int = 20) -> list[dict]:
        """Get recent weekly runs, newest first"""
        result = self.client.table('weekly_runs') \
            .select('*') \
            .order('run_date', desc=True) \
            .limit(limit) \
            .execute()
        return result.data if result.data else []

    # ========================================================================
    # MANUAL OVERRIDES
    # ========================================================================

    def upsert_manual_override(self, startup_id: str, override: dict) -> dict:
        """
        Upsert manual enrichment data for a startup.
        override dict may contain: evidence_urls, lead_investors,
        founder_background, notes, re_attribution_requested
        """
        data = {'startup_id': startup_id, **override}
        result = self.client.table('manual_overrides') \
            .upsert(data, on_conflict='startup_id') \
            .execute()

        if result.data:
            return result.data[0]
        raise Exception(f"Failed to upsert manual override for {startup_id}")

    def get_manual_override(self, startup_id: str) -> Optional[dict]:
        """Get manual override record for a startup"""
        result = self.client.table('manual_overrides') \
            .select('*') \
            .eq('startup_id', startup_id) \
            .execute()
        return result.data[0] if result.data else None

    # ========================================================================
    # PIPELINE LOGS
    # ========================================================================

    def log(
        self,
        run_id: str,
        stage: str,
        level: str,
        message: str,
        startup_id: Optional[str] = None,
        detail: Optional[dict] = None,
    ) -> None:
        """
        Write a structured pipeline log entry.
        level: 'info' | 'warn' | 'error'
        stage: 'discovery' | 'resolution' | 'attribution' | 'storage'
        """
        data = {
            'run_id': run_id,
            'startup_id': startup_id,
            'stage': stage,
            'level': level,
            'message': message,
            'detail': detail or {},
        }
        self.client.table('pipeline_logs').insert(data).execute()

    def get_logs_for_run(
        self,
        run_id: str,
        level: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> list[dict]:
        """Get pipeline logs for a specific run"""
        query = self.client.table('pipeline_logs') \
            .select('*') \
            .eq('run_id', run_id)

        if level:
            query = query.eq('level', level)
        if stage:
            query = query.eq('stage', stage)

        result = query.order('created_at').execute()
        return result.data if result.data else []

    # ========================================================================
    # ANALYTICS (read from views)
    # ========================================================================

    def get_cloud_distribution(self) -> list[dict]:
        """Cloud provider distribution from view"""
        result = self.client.table('cloud_provider_distribution').select('*').execute()
        return result.data if result.data else []

    def get_ai_distribution(self) -> list[dict]:
        """AI provider distribution from view"""
        result = self.client.table('ai_provider_distribution').select('*').execute()
        return result.data if result.data else []

    def get_recent_funding(self, limit: int = 20) -> list[dict]:
        """Recent funding events with attribution from view"""
        result = self.client.table('recent_funding_with_attribution') \
            .select('*') \
            .limit(limit) \
            .execute()
        return result.data if result.data else []

    def get_signal_effectiveness(self) -> list[dict]:
        """Signal effectiveness metrics from view"""
        result = self.client.table('signal_effectiveness').select('*').execute()
        return result.data if result.data else []

    def get_attribution_changes(self, limit: int = 50) -> list[dict]:
        """Attribution changes (cloud migrations) from view"""
        result = self.client.table('attribution_changes') \
            .select('*') \
            .limit(limit) \
            .execute()
        return result.data if result.data else []
