-- Migration: Add vertical classification & cloud propensity columns
-- Run this in Supabase Dashboard > SQL Editor
-- Date: 2026-03-03

ALTER TABLE attribution_snapshots
  ADD COLUMN IF NOT EXISTS vertical                   TEXT,
  ADD COLUMN IF NOT EXISTS sub_vertical               TEXT,
  ADD COLUMN IF NOT EXISTS cloud_propensity           TEXT CHECK (cloud_propensity IN ('High', 'Medium', 'Low')),
  ADD COLUMN IF NOT EXISTS classification_confidence  TEXT CHECK (classification_confidence IN ('high', 'medium', 'low')),
  ADD COLUMN IF NOT EXISTS classification_source      TEXT;

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'attribution_snapshots'
  AND column_name IN ('vertical', 'sub_vertical', 'cloud_propensity', 'classification_confidence', 'classification_source');
