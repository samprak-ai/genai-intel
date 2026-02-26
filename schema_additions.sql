-- GenAI-Intel Schema Additions
-- Run this AFTER schema.sql has been deployed.
-- Safe to re-run (all statements are idempotent).

-- ============================================================================
-- 1. STARTUPS — add enrichment columns
-- ============================================================================

ALTER TABLE startups
    ADD COLUMN IF NOT EXISTS lead_investors   TEXT[] DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS founder_background TEXT[] DEFAULT '{}';

-- ============================================================================
-- 2. WEEKLY RUNS — add token usage tracking for Anthropic API cost visibility
-- ============================================================================

ALTER TABLE weekly_runs
    ADD COLUMN IF NOT EXISTS llm_input_tokens  INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS llm_output_tokens INT DEFAULT 0;

-- ============================================================================
-- 3. MANUAL OVERRIDES — human-supplied enrichment from the UI
-- ============================================================================

CREATE TABLE IF NOT EXISTS manual_overrides (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    startup_id UUID NOT NULL REFERENCES startups(id) ON DELETE CASCADE,

    -- Human-supplied enrichment fields
    evidence_urls       TEXT[]  DEFAULT '{}',
    lead_investors      TEXT[]  DEFAULT '{}',
    founder_background  TEXT[]  DEFAULT '{}',
    notes               TEXT,

    -- Re-attribution lifecycle
    re_attribution_requested BOOLEAN     DEFAULT FALSE,
    re_attributed_at         TIMESTAMPTZ,

    -- Audit
    created_by  TEXT,                              -- email or 'system'
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- One override record per startup
    UNIQUE(startup_id)
);

CREATE INDEX IF NOT EXISTS idx_overrides_startup  ON manual_overrides(startup_id);
CREATE INDEX IF NOT EXISTS idx_overrides_pending  ON manual_overrides(re_attribution_requested)
    WHERE re_attribution_requested = TRUE;

-- Auto-update updated_at on changes
CREATE OR REPLACE FUNCTION update_override_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS override_update_timestamp ON manual_overrides;
CREATE TRIGGER override_update_timestamp
    BEFORE UPDATE ON manual_overrides
    FOR EACH ROW
    EXECUTE FUNCTION update_override_timestamp();

-- ============================================================================
-- 4. PIPELINE LOGS — structured per-company logs per run
--    Replaces unstructured stdout prints and the error_log JSONB blob
-- ============================================================================

CREATE TABLE IF NOT EXISTS pipeline_logs (
    id         UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_id     UUID        NOT NULL REFERENCES weekly_runs(id) ON DELETE CASCADE,
    startup_id UUID        REFERENCES startups(id) ON DELETE SET NULL,

    stage   TEXT NOT NULL CHECK (stage IN ('discovery', 'resolution', 'attribution', 'storage')),
    level   TEXT NOT NULL CHECK (level IN ('info', 'warn', 'error')),
    message TEXT NOT NULL,
    detail  JSONB DEFAULT '{}',

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_logs_run     ON pipeline_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_logs_startup ON pipeline_logs(startup_id);
CREATE INDEX IF NOT EXISTS idx_logs_level   ON pipeline_logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_stage   ON pipeline_logs(stage);
CREATE INDEX IF NOT EXISTS idx_logs_time    ON pipeline_logs(created_at DESC);

-- ============================================================================
-- 5. ATTRIBUTION SNAPSHOTS — add not-applicable support
-- ============================================================================

ALTER TABLE attribution_snapshots
    ADD COLUMN IF NOT EXISTS cloud_not_applicable      BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS cloud_not_applicable_note TEXT,
    ADD COLUMN IF NOT EXISTS ai_not_applicable         BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ai_not_applicable_note    TEXT;

-- ============================================================================
-- 6. UPDATE VIEWS to include not-applicable and new enrichment data
-- ============================================================================

-- Drop latest_attributions CASCADE (cloud_provider_distribution, ai_provider_distribution,
-- multi_cloud_combinations, recent_funding_with_attribution all depend on it)
DROP VIEW IF EXISTS latest_attributions CASCADE;

-- 1. Recreate base view with new enrichment + not-applicable columns
--    Funding filter logic:
--      - Startups with NO funding_events row are INCLUDED (manually added; funding unknown but assumed qualifying)
--      - Startups whose largest known round is >= $10M are INCLUDED
--      - Startups whose largest known round is < $10M are EXCLUDED
CREATE VIEW latest_attributions AS
SELECT DISTINCT ON (s.id)
    s.id,
    s.canonical_name,
    s.website,
    s.industry,
    s.description,
    s.lead_investors,
    s.founder_background,
    -- Cloud fields
    a.cloud_is_multi,
    a.cloud_primary_provider,
    a.cloud_providers,
    a.cloud_confidence,
    a.cloud_entrenchment,
    a.cloud_evidence_count,
    a.cloud_not_applicable,
    a.cloud_not_applicable_note,
    -- AI fields
    a.ai_is_multi,
    a.ai_primary_provider,
    a.ai_providers,
    a.ai_confidence,
    a.ai_entrenchment,
    a.ai_evidence_count,
    a.ai_not_applicable,
    a.ai_not_applicable_note,
    a.snapshot_date,
    a.created_at
FROM startups s
LEFT JOIN (
    -- Subquery: max funding per startup (NULL for startups with no funding_events row)
    SELECT startup_id, MAX(funding_amount_usd) AS max_funding_usd
    FROM funding_events
    GROUP BY startup_id
) mf ON s.id = mf.startup_id
LEFT JOIN attribution_snapshots a ON s.id = a.startup_id
-- Include if: no funding row at all (mf.startup_id IS NULL) OR max round >= $10M
WHERE mf.startup_id IS NULL OR mf.max_funding_usd >= 10
ORDER BY s.id, a.snapshot_date DESC NULLS LAST;

-- 2. Recreate cloud_provider_distribution
--    One row per startup — multi-cloud startups count as a single "Multi-Cloud" slice.
--    Major providers (AWS, GCP, Azure) are named directly; everything else → "Other".
--    Must DROP first — Postgres won't allow column renames via CREATE OR REPLACE VIEW.
DROP VIEW IF EXISTS cloud_provider_distribution;
CREATE VIEW cloud_provider_distribution AS
SELECT
    CASE
        WHEN cloud_is_multi                               THEN 'Multi-Cloud'
        WHEN cloud_primary_provider IN ('AWS','GCP','Azure') THEN cloud_primary_provider
        WHEN cloud_primary_provider IS NOT NULL           THEN 'Other'
        ELSE 'Unknown'
    END AS provider,
    COUNT(*) AS startup_count,
    ROUND(AVG(cloud_confidence)::numeric, 2) AS avg_confidence,
    -- kept for backwards-compat; will always be 0 since multi is its own slice
    0 AS multi_cloud_count,
    COUNT(*) AS sole_provider_count
FROM latest_attributions
WHERE cloud_providers IS NOT NULL OR cloud_primary_provider IS NOT NULL
GROUP BY 1
ORDER BY startup_count DESC;

-- 3. Recreate ai_provider_distribution
--    One row per startup — multi-AI startups count as a single "Multi-Provider" slice.
--    Must DROP first — Postgres won't allow column renames via CREATE OR REPLACE VIEW.
DROP VIEW IF EXISTS ai_provider_distribution;
CREATE VIEW ai_provider_distribution AS
SELECT
    CASE
        WHEN ai_is_multi                THEN 'Multi-Provider'
        WHEN ai_primary_provider IS NOT NULL THEN ai_primary_provider
        ELSE 'Unknown'
    END AS provider,
    COUNT(*) AS startup_count,
    ROUND(AVG(ai_confidence)::numeric, 2) AS avg_confidence,
    0 AS multi_ai_count,
    COUNT(*) AS sole_provider_count
FROM latest_attributions
WHERE ai_providers IS NOT NULL OR ai_primary_provider IS NOT NULL
GROUP BY 1
ORDER BY startup_count DESC;

-- 4. Recreate multi_cloud_combinations
CREATE OR REPLACE VIEW multi_cloud_combinations AS
SELECT
    array_to_string(cloud_providers, ' + ') as combination,
    COUNT(*) as startup_count,
    cloud_is_multi
FROM latest_attributions
WHERE cloud_providers IS NOT NULL
GROUP BY cloud_providers, cloud_is_multi
ORDER BY startup_count DESC;

-- 5. Recreate recent_funding_with_attribution (now respects not-applicable)
--    Returns structured provider fields so the dashboard component can apply
--    Multi-Cloud / Other (...) display logic consistently.
--    Must DROP first — column names changed from cloud_display/ai_display.
DROP VIEW IF EXISTS recent_funding_with_attribution;
CREATE VIEW recent_funding_with_attribution AS
SELECT
    s.canonical_name,
    s.website,
    s.industry,
    f.funding_amount_usd,
    f.funding_round,
    f.announcement_date,
    f.source_name,
    -- Cloud: structured fields for ProviderBadge
    la.cloud_is_multi,
    la.cloud_primary_provider,
    la.cloud_providers,
    la.cloud_confidence,
    la.cloud_not_applicable,
    -- AI: structured fields for ProviderBadge
    la.ai_is_multi,
    la.ai_primary_provider,
    la.ai_providers,
    la.ai_confidence,
    la.ai_not_applicable
FROM funding_events f
JOIN startups s ON f.startup_id = s.id
LEFT JOIN latest_attributions la ON s.id = la.id
ORDER BY f.announcement_date DESC
LIMIT 50;

-- ============================================================================
-- 7. ROW LEVEL SECURITY — enable for production auth
--    Uncomment after Supabase Auth is configured (Phase 3)
-- ============================================================================

-- ALTER TABLE startups           ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE funding_events     ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE attribution_signals ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE attribution_snapshots ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE weekly_runs        ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE manual_overrides   ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE pipeline_logs      ENABLE ROW LEVEL SECURITY;

-- CREATE POLICY "authenticated_read_all"   ON startups            FOR SELECT TO authenticated USING (true);
-- CREATE POLICY "authenticated_write_all"  ON startups            FOR ALL    TO authenticated USING (true);
-- -- repeat for each table above
