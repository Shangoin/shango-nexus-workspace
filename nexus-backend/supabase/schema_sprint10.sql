-- nexus/supabase/schema_sprint10.sql
-- Sprint 10: DeepMind + MIT Frontier Layer schema additions
-- Run in Supabase SQL Editor (safe to run multiple times — uses IF NOT EXISTS)

-- ── S10-02: Agent Scaling Monitor ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nexus_scaling_reports (
    id              BIGSERIAL PRIMARY KEY,
    coordination_overhead    FLOAT NOT NULL DEFAULT 0,
    message_density          FLOAT NOT NULL DEFAULT 0,
    redundancy_rate          FLOAT NOT NULL DEFAULT 0,
    coordination_efficiency  FLOAT NOT NULL DEFAULT 1.0,
    error_amplification      FLOAT NOT NULL DEFAULT 0,
    healthy                  BOOLEAN NOT NULL DEFAULT TRUE,
    warnings                 JSONB NOT NULL DEFAULT '[]',
    measured_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_scaling_reports_measured_at ON nexus_scaling_reports(measured_at DESC);

-- ── S10-01: EnCompass branch results ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nexus_encompass_results (
    id               BIGSERIAL PRIMARY KEY,
    pod              TEXT NOT NULL,
    task_type        TEXT NOT NULL,
    branch_count     INT NOT NULL DEFAULT 3,
    winning_branch   INT NOT NULL DEFAULT 0,
    best_score       FLOAT NOT NULL DEFAULT 0,
    all_scores       JSONB NOT NULL DEFAULT '[]',
    backtracked      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_encompass_pod ON nexus_encompass_results(pod, created_at DESC);

-- ── S10-03: Agent0 curriculum uncertainty history ─────────────────────────────
CREATE TABLE IF NOT EXISTS nexus_agent0_uncertainty (
    id           BIGSERIAL PRIMARY KEY,
    pod          TEXT NOT NULL,
    judge_score  FLOAT NOT NULL,
    uncertainty  FLOAT NOT NULL DEFAULT 0.5,
    cycle_id     TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent0_pod ON nexus_agent0_uncertainty(pod, created_at DESC);

-- ── S10-04: MEM1 sessions ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nexus_mem1_sessions (
    id               BIGSERIAL PRIMARY KEY,
    pod              TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    turn_count       INT NOT NULL DEFAULT 0,
    internal_state   TEXT NOT NULL DEFAULT '',
    action_history   JSONB NOT NULL DEFAULT '[]',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mem1_session ON nexus_mem1_sessions(pod, session_id);
CREATE INDEX IF NOT EXISTS idx_mem1_pod ON nexus_mem1_sessions(pod, updated_at DESC);

-- ── S10-07: ERS calculation history ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nexus_ers_calculations (
    id               BIGSERIAL PRIMARY KEY,
    student_id       TEXT NOT NULL,
    topic            TEXT NOT NULL,
    exam_type        TEXT NOT NULL DEFAULT 'jee',
    ers_score        FLOAT NOT NULL,
    percentile       INT NOT NULL,
    grade            TEXT NOT NULL,
    total_questions  INT NOT NULL,
    correct_answers  INT NOT NULL,
    accuracy_pct     FLOAT NOT NULL,
    strengths        JSONB NOT NULL DEFAULT '[]',
    weaknesses       JSONB NOT NULL DEFAULT '[]',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ers_student ON nexus_ers_calculations(student_id, created_at DESC);

-- ── Row Level Security ───────────────────────────────────────────────────────
ALTER TABLE nexus_scaling_reports    ENABLE ROW LEVEL SECURITY;
ALTER TABLE nexus_encompass_results  ENABLE ROW LEVEL SECURITY;
ALTER TABLE nexus_agent0_uncertainty ENABLE ROW LEVEL SECURITY;
ALTER TABLE nexus_mem1_sessions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE nexus_ers_calculations   ENABLE ROW LEVEL SECURITY;

-- Service role bypass (for backend writes)
CREATE POLICY "service_all_scaling"    ON nexus_scaling_reports     USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_all_encompass"  ON nexus_encompass_results   USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_all_agent0"     ON nexus_agent0_uncertainty  USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_all_mem1"       ON nexus_mem1_sessions       USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY "service_all_ers"        ON nexus_ers_calculations    USING (TRUE) WITH CHECK (TRUE);
