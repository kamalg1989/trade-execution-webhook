-- AI visual analysis: immutable results store + daily call cap counter.
-- Immutable: closed-date rows never expire; re-analysis = new prompt_version/model row.

CREATE TABLE IF NOT EXISTS ai_analysis_results (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    analysis_date   DATE NOT NULL,
    prompt_version  TEXT NOT NULL DEFAULT 'v1',
    model           TEXT NOT NULL,

    gate_mode       TEXT,
    ifp_score       DOUBLE PRECISION,
    features        JSONB,          -- {daily: {...}, weekly: {...}}
    analysis        JSONB,          -- full tool-use output
    verification    JSONB,          -- level cross-check
    recommendation  TEXT,
    confidence      DOUBLE PRECISION,

    chart_daily_path            TEXT,
    chart_weekly_path           TEXT,
    chart_daily_annotated_path  TEXT,
    chart_weekly_annotated_path TEXT,

    api_used        TEXT NOT NULL DEFAULT 'regular',   -- future: 'batch'
    processing_ms   INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    user_feedback   TEXT CHECK (user_feedback IN ('CORRECT', 'PARTIAL', 'WRONG')),
    feedback_notes  TEXT,
    feedback_at     TIMESTAMPTZ,

    UNIQUE (symbol, analysis_date, prompt_version, model)
);

CREATE INDEX IF NOT EXISTS idx_ai_results_symbol_date
    ON ai_analysis_results (symbol, analysis_date DESC);
CREATE INDEX IF NOT EXISTS idx_ai_results_date
    ON ai_analysis_results (analysis_date DESC);

CREATE TABLE IF NOT EXISTS ai_call_budget (
    day         DATE PRIMARY KEY,
    calls       INT NOT NULL DEFAULT 0
);
