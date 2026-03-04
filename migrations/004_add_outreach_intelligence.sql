-- Migration: Add outreach intelligence columns to attribution_snapshots
-- Run this in Supabase Dashboard > SQL Editor
-- Date: 2026-03-04

-- ============================================================================
-- Step 1: Add outreach intelligence columns to attribution_snapshots
-- ============================================================================

ALTER TABLE attribution_snapshots
  ADD COLUMN IF NOT EXISTS engagement_timing         TEXT CHECK (engagement_timing IN ('Hot', 'Warm', 'Watch')),
  ADD COLUMN IF NOT EXISTS recommended_angle         TEXT,
  ADD COLUMN IF NOT EXISTS key_signals               JSONB,
  ADD COLUMN IF NOT EXISTS intelligence_generated_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS intelligence_model        TEXT;

-- ============================================================================
-- Step 2: Update latest_attributions view to include outreach intelligence.
-- Must CASCADE-drop because dependent views reference it.
-- Then recreate all dependent views.
-- ============================================================================

DROP VIEW IF EXISTS latest_attributions CASCADE;

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
    -- Classification fields
    a.vertical,
    a.sub_vertical,
    a.cloud_propensity,
    a.classification_confidence,
    a.classification_source,
    -- Engagement tier fields
    a.engagement_tier,
    a.engagement_tier_label,
    a.engagement_tier_rationale,
    a.tier_last_calculated,
    -- Trigger fields
    a.active_trigger_count,
    -- Outreach intelligence fields (NEW)
    a.engagement_timing,
    a.recommended_angle,
    a.key_signals,
    a.intelligence_generated_at,
    a.intelligence_model,
    a.snapshot_date,
    a.created_at
FROM startups s
LEFT JOIN (
    SELECT startup_id, MAX(funding_amount_usd) AS max_funding_usd
    FROM funding_events
    GROUP BY startup_id
) mf ON s.id = mf.startup_id
LEFT JOIN attribution_snapshots a ON s.id = a.startup_id
WHERE mf.startup_id IS NULL OR mf.max_funding_usd >= 10
ORDER BY s.id, a.snapshot_date DESC NULLS LAST;

-- Recreate cloud_provider_distribution (depends on latest_attributions)
DROP VIEW IF EXISTS cloud_provider_distribution;
CREATE VIEW cloud_provider_distribution AS
SELECT
    CASE
        WHEN cloud_is_multi AND cloud_primary_provider = 'Hybrid'   THEN 'Hybrid'
        WHEN cloud_is_multi                                          THEN 'Multi-Cloud'
        WHEN cloud_primary_provider IN ('AWS', 'GCP', 'Azure')       THEN cloud_primary_provider
        WHEN cloud_primary_provider = 'On-Premises'                  THEN 'On-Premises'
        WHEN cloud_primary_provider IS NOT NULL                      THEN 'Other'
        ELSE 'Unknown'
    END AS provider,
    COUNT(*) AS startup_count,
    ROUND(AVG(cloud_confidence)::numeric, 2) AS avg_confidence,
    0 AS multi_cloud_count,
    COUNT(*) AS sole_provider_count
FROM latest_attributions
GROUP BY 1
ORDER BY startup_count DESC;

-- Recreate ai_provider_distribution (depends on latest_attributions)
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
GROUP BY 1
ORDER BY startup_count DESC;

-- Recreate multi_cloud_combinations (depends on latest_attributions)
CREATE OR REPLACE VIEW multi_cloud_combinations AS
SELECT
    array_to_string(cloud_providers, ' + ') as combination,
    COUNT(*) as startup_count,
    cloud_is_multi
FROM latest_attributions
WHERE cloud_providers IS NOT NULL
GROUP BY cloud_providers, cloud_is_multi
ORDER BY startup_count DESC;

-- Recreate recent_funding_with_attribution (depends on latest_attributions)
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
    la.cloud_is_multi,
    la.cloud_primary_provider,
    la.cloud_providers,
    la.cloud_confidence,
    la.cloud_not_applicable,
    la.ai_is_multi,
    la.ai_primary_provider,
    la.ai_providers,
    la.ai_confidence,
    la.ai_not_applicable
FROM funding_events f
JOIN startups s ON f.startup_id = s.id
LEFT JOIN latest_attributions la ON s.id = la.id
WHERE f.funding_amount_usd >= 10
ORDER BY f.announcement_date DESC
LIMIT 50;

-- Verify
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'attribution_snapshots'
  AND column_name IN ('engagement_timing', 'recommended_angle', 'key_signals', 'intelligence_generated_at', 'intelligence_model')
ORDER BY ordinal_position;

-- Refresh PostgREST schema cache
NOTIFY pgrst, 'reload schema';
