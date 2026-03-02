-- ============================================================
-- Shango Nexus — Sprint 9 Schema Migration
-- Prometheus Intelligence Layer
-- Run in Supabase SQL Editor AFTER schema.sql
-- ============================================================

-- ── nexus_causal_graph ────────────────────────────────────────────────────────
-- Stores causal edges between agent actions and outcomes (AMA S9-02)

CREATE TABLE IF NOT EXISTS nexus_causal_graph (
    id            uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    event_id      text NOT NULL UNIQUE,
    pod           text NOT NULL,
    action        text NOT NULL,
    outcome       text NOT NULL,
    caused_by     jsonb DEFAULT '[]'::jsonb,  -- array of parent event_ids
    caused        jsonb DEFAULT '[]'::jsonb,  -- array of child event_ids
    metadata      jsonb DEFAULT '{}'::jsonb,
    created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_causal_pod ON nexus_causal_graph (pod);
CREATE INDEX IF NOT EXISTS idx_causal_event_id ON nexus_causal_graph (event_id);

ALTER TABLE nexus_causal_graph ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access causal_graph" ON nexus_causal_graph;
CREATE POLICY "Service role full access causal_graph"
    ON nexus_causal_graph FOR ALL
    USING (auth.role() = 'service_role');


-- ── nexus_constitution_versions ───────────────────────────────────────────────
-- Tracks every COCOA constitution evolution and prune event (S9-03)

CREATE TABLE IF NOT EXISTS nexus_constitution_versions (
    id            uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    pod           text NOT NULL,
    action        text NOT NULL CHECK (action IN ('evolved', 'pruned')),
    rule_text     text,              -- the new rule (for evolved) or pruned rule
    judger_score  float,             -- 0–1; stored for audit
    reason        text,
    created_at    timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_const_versions_pod ON nexus_constitution_versions (pod);
CREATE INDEX IF NOT EXISTS idx_const_versions_action ON nexus_constitution_versions (action);

ALTER TABLE nexus_constitution_versions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Service role full access constitution_versions" ON nexus_constitution_versions;
CREATE POLICY "Service role full access constitution_versions"
    ON nexus_constitution_versions FOR ALL
    USING (auth.role() = 'service_role');


-- ── nexus_memories — add memory_type + weight columns (HiMem S9-04) ──────────

ALTER TABLE nexus_memories
    ADD COLUMN IF NOT EXISTS memory_type text DEFAULT 'semantic'
        CHECK (memory_type IN ('episodic', 'semantic', 'procedural', 'causal'));

ALTER TABLE nexus_memories
    ADD COLUMN IF NOT EXISTS weight float DEFAULT 1.0
        CHECK (weight >= 0.0 AND weight <= 2.0);

CREATE INDEX IF NOT EXISTS idx_memories_type_weight
    ON nexus_memories (memory_type, weight);


-- ── nexus_variant_stats — add constitutional_violations column (DAN S9-06) ───

ALTER TABLE nexus_variant_stats
    ADD COLUMN IF NOT EXISTS constitutional_violations integer DEFAULT 0;

-- ── Verification ─────────────────────────────────────────────────────────────

SELECT 'nexus_causal_graph'            AS table_name, COUNT(*) AS rows FROM nexus_causal_graph
UNION ALL
SELECT 'nexus_constitution_versions',               COUNT(*) FROM nexus_constitution_versions
UNION ALL
SELECT 'nexus_memories (with memory_type)',         COUNT(*) FROM nexus_memories
UNION ALL
SELECT 'nexus_variant_stats (with violations)',     COUNT(*) FROM nexus_variant_stats;
