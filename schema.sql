-- GenAI-Intel V2 Database Schema
-- Deploy to Supabase SQL Editor

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- CORE ENTITIES
-- ============================================================================

-- Startups table (core entity)
CREATE TABLE IF NOT EXISTS startups (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_name TEXT UNIQUE NOT NULL,
    website TEXT UNIQUE NOT NULL,
    industry TEXT,
    description TEXT,
    
    -- Metadata
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Constraints
    CONSTRAINT valid_website CHECK (website ~ '^[a-z0-9-]+\.[a-z]{2,}$')
);

CREATE INDEX idx_startups_name ON startups(canonical_name);
CREATE INDEX idx_startups_website ON startups(website);
CREATE INDEX idx_startups_updated ON startups(last_updated DESC);

-- ============================================================================
-- FUNDING EVENTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS funding_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    startup_id UUID NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    
    -- Funding details
    funding_amount_usd BIGINT NOT NULL CHECK (funding_amount_usd > 0),
    funding_round TEXT NOT NULL,
    funding_date DATE,
    announcement_date DATE NOT NULL,
    
    -- Investors
    lead_investors TEXT[],
    
    -- Source tracking
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    raw_article_text TEXT,
    extracted_json JSONB NOT NULL,
    
    -- Metadata
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Prevent duplicate funding events
    UNIQUE(startup_id, funding_amount_usd, announcement_date)
);

CREATE INDEX idx_funding_startup ON funding_events(startup_id);
CREATE INDEX idx_funding_date ON funding_events(announcement_date DESC);
CREATE INDEX idx_funding_amount ON funding_events(funding_amount_usd DESC);
CREATE INDEX idx_funding_source ON funding_events(source_name);

-- ============================================================================
-- ATTRIBUTION SIGNALS (Evidence Storage)
-- ============================================================================

CREATE TABLE IF NOT EXISTS attribution_signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    startup_id UUID NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    
    -- Signal classification
    provider_type TEXT NOT NULL CHECK (provider_type IN ('cloud', 'ai')),
    provider_name TEXT NOT NULL,
    signal_source TEXT NOT NULL,
    signal_strength TEXT NOT NULL CHECK (signal_strength IN ('STRONG', 'MEDIUM', 'WEAK')),
    
    -- Evidence
    evidence_text TEXT,
    evidence_url TEXT,
    confidence_weight FLOAT NOT NULL CHECK (confidence_weight IN (1.0, 0.6, 0.3)),
    
    -- Metadata
    collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Prevent duplicate signals
    UNIQUE(startup_id, provider_type, provider_name, signal_source)
);

CREATE INDEX idx_signals_startup ON attribution_signals(startup_id);
CREATE INDEX idx_signals_provider ON attribution_signals(provider_name);
CREATE INDEX idx_signals_type ON attribution_signals(provider_type);
CREATE INDEX idx_signals_source ON attribution_signals(signal_source);
CREATE INDEX idx_signals_collected ON attribution_signals(collected_at DESC);

-- ============================================================================
-- ATTRIBUTION SNAPSHOTS (Daily Attribution State)
-- ============================================================================

CREATE TABLE IF NOT EXISTS attribution_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    startup_id UUID NOT NULL REFERENCES startups(id) ON DELETE CASCADE,
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,
    
    -- Cloud attribution (multi-cloud aware)
    cloud_is_multi BOOLEAN DEFAULT FALSE,
    cloud_primary_provider TEXT,        -- NULL when multi-cloud, set when single
    cloud_providers TEXT[],             -- All detected providers e.g. {AWS, GCP}
    cloud_confidence FLOAT CHECK (cloud_confidence >= 0 AND cloud_confidence <= 1),
    cloud_entrenchment TEXT CHECK (cloud_entrenchment IN ('STRONG', 'MODERATE', 'WEAK', 'UNKNOWN')),
    cloud_evidence_count INT CHECK (cloud_evidence_count >= 0),
    cloud_raw_score FLOAT,
    
    -- AI attribution (multi-AI aware)
    ai_is_multi BOOLEAN DEFAULT FALSE,
    ai_primary_provider TEXT,           -- NULL when multi-AI, set when single
    ai_providers TEXT[],                -- All detected providers e.g. {OpenAI, Anthropic}
    ai_confidence FLOAT CHECK (ai_confidence >= 0 AND ai_confidence <= 1),
    ai_entrenchment TEXT CHECK (ai_entrenchment IN ('STRONG', 'MODERATE', 'WEAK', 'UNKNOWN')),
    ai_evidence_count INT CHECK (ai_evidence_count >= 0),
    ai_raw_score FLOAT,
    
    -- Metadata
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- One snapshot per startup per day
    UNIQUE(startup_id, snapshot_date)
);

CREATE INDEX idx_snapshots_startup ON attribution_snapshots(startup_id);
CREATE INDEX idx_snapshots_date ON attribution_snapshots(snapshot_date DESC);
CREATE INDEX idx_snapshots_cloud_primary ON attribution_snapshots(cloud_primary_provider);
CREATE INDEX idx_snapshots_ai_primary ON attribution_snapshots(ai_primary_provider);
CREATE INDEX idx_snapshots_cloud_multi ON attribution_snapshots(cloud_is_multi);
CREATE INDEX idx_snapshots_ai_multi ON attribution_snapshots(ai_is_multi);

-- ============================================================================
-- WEEKLY RUNS (Automation Tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS weekly_runs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    run_date DATE NOT NULL DEFAULT CURRENT_DATE UNIQUE,
    
    -- Metrics
    startups_discovered INT DEFAULT 0,
    startups_attributed INT DEFAULT 0,
    errors_count INT DEFAULT 0,
    execution_time_seconds INT,
    
    -- Status
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    error_log JSONB DEFAULT '{}',
    
    -- Timestamps
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_runs_date ON weekly_runs(run_date DESC);
CREATE INDEX idx_runs_status ON weekly_runs(status);

-- ============================================================================
-- VIEWS FOR ANALYTICS
-- ============================================================================

-- Latest attribution for each startup (multi-cloud aware)
-- Funding filter:
--   - No funding_events row → included (manually added; funding unknown but assumed qualifying)
--   - Largest known round >= $10M → included
--   - Largest known round < $10M  → excluded
CREATE OR REPLACE VIEW latest_attributions AS
SELECT DISTINCT ON (s.id)
    s.id,
    s.canonical_name,
    s.website,
    s.industry,
    -- Cloud fields
    a.cloud_is_multi,
    a.cloud_primary_provider,
    a.cloud_providers,
    a.cloud_confidence,
    a.cloud_entrenchment,
    a.cloud_evidence_count,
    -- AI fields
    a.ai_is_multi,
    a.ai_primary_provider,
    a.ai_providers,
    a.ai_confidence,
    a.ai_entrenchment,
    a.ai_evidence_count,
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

-- Cloud provider distribution (counts each provider in multi-cloud separately)
CREATE OR REPLACE VIEW cloud_provider_distribution AS
SELECT
    provider,
    COUNT(*) as startup_count,
    SUM(CASE WHEN cloud_is_multi THEN 1 ELSE 0 END) as multi_cloud_count,
    SUM(CASE WHEN NOT cloud_is_multi THEN 1 ELSE 0 END) as sole_provider_count,
    ROUND(AVG(cloud_confidence)::numeric, 2) as avg_confidence
FROM latest_attributions
CROSS JOIN UNNEST(cloud_providers) AS provider   -- Expands multi-cloud arrays
WHERE cloud_providers IS NOT NULL
GROUP BY provider
ORDER BY startup_count DESC;

-- AI provider distribution (counts each provider in multi-AI separately)
CREATE OR REPLACE VIEW ai_provider_distribution AS
SELECT
    provider,
    COUNT(*) as startup_count,
    SUM(CASE WHEN ai_is_multi THEN 1 ELSE 0 END) as multi_ai_count,
    SUM(CASE WHEN NOT ai_is_multi THEN 1 ELSE 0 END) as sole_provider_count,
    ROUND(AVG(ai_confidence)::numeric, 2) as avg_confidence
FROM latest_attributions
CROSS JOIN UNNEST(ai_providers) AS provider      -- Expands multi-AI arrays
WHERE ai_providers IS NOT NULL
GROUP BY provider
ORDER BY startup_count DESC;

-- Multi-cloud breakdown: which provider combinations are most common
CREATE OR REPLACE VIEW multi_cloud_combinations AS
SELECT
    array_to_string(cloud_providers, ' + ') as combination,
    COUNT(*) as startup_count,
    cloud_is_multi
FROM latest_attributions
WHERE cloud_providers IS NOT NULL
GROUP BY cloud_providers, cloud_is_multi
ORDER BY startup_count DESC;

-- Signal effectiveness by source
CREATE OR REPLACE VIEW signal_effectiveness AS
SELECT 
    provider_type,
    signal_source,
    signal_strength,
    COUNT(*) as signal_count,
    COUNT(DISTINCT startup_id) as startups_covered,
    ROUND(AVG(confidence_weight)::numeric, 2) as avg_weight
FROM attribution_signals
GROUP BY provider_type, signal_source, signal_strength
ORDER BY signal_count DESC;

-- Recent funding events with attribution (multi-cloud aware)
-- Returns structured provider fields so the dashboard component can apply
-- Multi-Cloud / Other (...) display logic consistently.
CREATE OR REPLACE VIEW recent_funding_with_attribution AS
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

-- Attribution changes over time (detects cloud migrations)
CREATE OR REPLACE VIEW attribution_changes AS
SELECT 
    s.canonical_name,
    a1.snapshot_date as from_date,
    a2.snapshot_date as to_date,
    a1.cloud_primary_provider as old_cloud,
    a2.cloud_primary_provider as new_cloud,
    a1.cloud_providers as old_cloud_set,
    a2.cloud_providers as new_cloud_set,
    a1.ai_primary_provider as old_ai,
    a2.ai_primary_provider as new_ai
FROM attribution_snapshots a1
JOIN attribution_snapshots a2 ON a1.startup_id = a2.startup_id
JOIN startups s ON a1.startup_id = s.id
WHERE a2.snapshot_date > a1.snapshot_date
  AND (
    a1.cloud_providers IS DISTINCT FROM a2.cloud_providers OR
    a1.ai_providers IS DISTINCT FROM a2.ai_providers
  )
ORDER BY a2.snapshot_date DESC;

-- ============================================================================
-- FUNCTIONS & TRIGGERS
-- ============================================================================

-- Update last_updated timestamp on startup changes
CREATE OR REPLACE FUNCTION update_startup_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_updated = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER startup_update_timestamp
    BEFORE UPDATE ON startups
    FOR EACH ROW
    EXECUTE FUNCTION update_startup_timestamp();

-- ============================================================================
-- ROW LEVEL SECURITY (Optional - Enable if needed)
-- ============================================================================

-- Enable RLS on all tables
-- ALTER TABLE startups ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE funding_events ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE attribution_signals ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE attribution_snapshots ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE weekly_runs ENABLE ROW LEVEL SECURITY;

-- Create policies (example - adjust based on your auth needs)
-- CREATE POLICY "Allow authenticated read" ON startups FOR SELECT TO authenticated USING (true);
-- CREATE POLICY "Allow authenticated insert" ON startups FOR INSERT TO authenticated WITH CHECK (true);

-- ============================================================================
-- INITIAL DATA / SEED
-- ============================================================================

-- Insert a test weekly run
INSERT INTO weekly_runs (run_date, status, started_at)
VALUES (CURRENT_DATE, 'running', NOW())
ON CONFLICT (run_date) DO NOTHING;

-- ============================================================================
-- HELPFUL QUERIES FOR VALIDATION
-- ============================================================================

-- Check table sizes
-- SELECT 
--     schemaname,
--     tablename,
--     pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
-- FROM pg_tables
-- WHERE schemaname = 'public'
-- ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Recent funding events
-- SELECT * FROM recent_funding_with_attribution LIMIT 10;

-- Cloud provider distribution
-- SELECT * FROM cloud_provider_distribution;

-- AI provider distribution
-- SELECT * FROM ai_provider_distribution;

-- Signal effectiveness
-- SELECT * FROM signal_effectiveness ORDER BY signal_count DESC;
