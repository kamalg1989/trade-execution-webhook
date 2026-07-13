# AI Visual Analysis Module — Design

Adds an AI filter stage after the existing custom screener: IFP detection, base
analysis, and pattern formation via Claude vision + a deterministic numeric
feature engine. Self-contained under `backend/ai_analysis/` — each concern in
its own folder, no changes to existing screener modules beyond one router
mount in `app/main.py`.

## Finalized decisions

| Decision | Choice |
|---|---|
| AI input | Chart PNG (clean) + computed feature block as text — numbers detect footprint, vision judges structure |
| Gate | Configurable `hard` / `soft`, default **hard** (`AI_GATE_MODE`, `IFP_GATE_THRESHOLD`) |
| Timeframes | Daily + weekly, always both |
| API mode | Instant only (parallel, semaphore). Schema keeps `api_used` for future batch |
| Chart delivery | URLs; annotated copy (AI BO/stop lines) rendered locally after analysis — zero API cost |
| Results | Immutable, keyed `(symbol, analysis_date, timeframe, prompt_version, model)` — no TTL for closed dates |
| Output JSON | Forced tool-use schema (never free-text JSON parsing) |
| UI | Screener list → row tap opens modal popup (charts + full AI report), close returns to list |

## Pipeline

```
existing screener shortlist (symbols)
  → features/   IFP numerics per symbol (daily df + weekly df)
      hard gate: ifp_score < threshold → dropped (returned in `gated` list)
  → charting/   clean PNG, daily + weekly (volume + vol MA20 + EMAs)
  → ai/         Claude: image + feature text → forced JSON (phase, base count,
                base quality, patterns, IFP verdict, buy point, levels, thesis)
  → verification/  AI levels vs computed levels, ±2% → verified | mismatch
  → charting/   annotated PNG (final BO/stop lines)
  → storage/    persist result (immutable) + chart files
  → api/        response: ranked list + analysis JSON + chart URLs
```

## Folder layout

```
ai_analysis/
├── DESIGN.md
├── config.py            env: ANTHROPIC_API_KEY, AI_GATE_MODE, IFP_GATE_THRESHOLD,
│                        MAX_CONCURRENT_AI, AI_MODEL, PROMPT_VERSION, CHART_DIR
├── pipeline.py          orchestrator: analyze_symbols(...)
├── features/            deterministic numeric engine (no AI)
│   ├── ifp_features.py  reuses compute/ifp.py + absorption days, accum days,
│   │                    vol contraction, retracement depth, extension vs SMAs
│   ├── swings.py        swing highs/lows → HH-HL / LH-LL structure
│   ├── levels.py        pivot (base high), support (base low), logical stop
│   └── gate.py          hard/soft gate decision
├── charting/
│   └── render.py        pure render_chart(df, symbol, timeframe, levels=None)
│                        → PNG bytes (mplfinance; volume MA20; weekly 10/40 EMA)
├── ai/
│   ├── schema.py        tool-use JSON schema (deck taxonomy)
│   ├── prompts.py       system prompt + feature-block formatter
│   └── client.py        AsyncAnthropic, semaphore, forced tool call
├── verification/
│   └── verify.py        AI vs computed levels ±2% → status per level
├── storage/
│   └── repo.py          AiRepo: save/load results, chart file store
├── sql/
│   └── 005_ai_analysis.sql
├── api/
│   ├── models.py        pydantic request/response
│   └── router.py        POST /api/ai-analyze, GET /api/ai-analyze/{id},
│                        GET /api/ai-analyze/charts/{file}.png
└── tests/
```

## Feature engine (features/) — what the AI receives as text

Per symbol, computed on daily (300 bars) and weekly (150 bars):

| Feature | Source | Deck concept |
|---|---|---|
| `ifp_score`, `accum_days`, `quiet_down_days` | `compute/ifp.py` (reused) | Institutional footprint |
| `updown_vol_ratio`, `obv_slope` | `compute/ifp.py` (reused) | High-vol buy / low-vol sell |
| `absorption_days` (list) | new: vol > 1.5×avg, range < 0.7×ATR, close top 40%, near support | Absorb & bounce |
| `base_len_bars`, `base_depth_pct` | new: bars since last swing high; (high−low)/high | Base formation |
| `retrace_of_advance_pct` | new: base depth ÷ prior bounce height | "< 30% giveback" |
| `vol_contraction_ratio` | new: base avg vol ÷ advance avg vol | Constructive base |
| `swing_structure` | new: HH-HL / LH-LL / mixed from swing points | Advance vs decline |
| `pct_above_sma50/200`, `sma50 > sma200` | new | Not over-extended |
| `pivot`, `support`, `logical_stop` | new: base high; base low; min(base low, last swing low) | Two points |
| `dist_to_pivot_pct` | new | Trigger proximity |

Gate: `hard` → `ifp_score >= IFP_GATE_THRESHOLD` (default 0.30, tunable) else
dropped before AI. `soft` → all pass, score attached.

## AI contract (ai/)

- Model: `AI_MODEL` env (default `claude-sonnet-4-5`), `max_tokens=1500`.
- One call per symbol, **both images in one message** (daily + weekly) + one
  feature block per timeframe — halves calls and gives the model cross-timeframe
  context for base counting.
- `tool_choice={"type":"tool","name":"report_chart_analysis"}` — output is
  always schema-valid JSON.

Schema (deck taxonomy):

```
market_cycle_phase: accumulation|advance|distribution|decline
base_count:         0|1|2|3|4_plus
base_quality:       constructive|suspect|broken   + base_quality_reasons[]
patterns[]:         {type: vcp|flag|pennant|inverse_hs|double_bottom|triple_bottom|
                     double_top|hs_top|rectangle|wedge|tennis_ball, confidence,
                     timeframe: daily|weekly, description}
ifp_verdict:        {present: bool, confidence, evidence}
buy_point:          {type: pullback|reverse_hs_breakout|high_breakout|
                     breakout_retest|none, structure: hammer|hh_hl|none,
                     breakout_level, stop_level}
weekly_context:     short text (base count confirmation from weekly)
recommendation:     SETUP_READY|EARLY_STAGE|NOT_READY|AVOID
confidence:         0..1
thesis:             1–2 sentence summary
```

## Verification (verification/)

`|ai_level − computed_level| / computed_level <= 0.02` per level →
`verified`; else `mismatch` (both values returned, UI shows warning badge).
Missing AI level with computed present → `ai_missing`. Never silently override.

## Storage (storage/ + sql/)

```sql
ai_analysis_results (
  id, symbol, analysis_date, prompt_version, model,
  gate_mode, ifp_score, features JSONB,
  analysis JSONB,                -- full schema output
  verification JSONB,            -- per-level status
  recommendation, confidence,
  chart_daily_path, chart_weekly_path,        -- clean (AI input, audit)
  chart_daily_annotated_path, chart_weekly_annotated_path,
  api_used DEFAULT 'regular',    -- future batch
  processing_ms, created_at,
  user_feedback, feedback_notes, feedback_at,
  UNIQUE(symbol, analysis_date, prompt_version, model)
)
```

Immutable: closed-date results never expire; re-analysis only when
`PROMPT_VERSION` or model changes (new row). Today's date: a result computed
intraday may be superseded after the close — resolved by `created_at` recency
check in repo (`fresh_for_today`).

Charts stored on disk: `{CHART_DIR}/{symbol}_{date}_{timeframe}_{clean|annot}_{pv}.png`,
served by GET endpoint (path-traversal safe, filename whitelist regex).

## API (api/)

```
POST /api/ai-analyze
  { symbols: [...], indicatorDate?: "YYYY-MM-DD",
    gateMode?: "hard"|"soft", ifpThreshold?: float, force?: bool }
  → { indicatorDate, gate: {...}, analyzed, fromStore, gated: [...],
      results: [ { symbol, close, ifpScore, features, analysis, verification,
                   charts: { daily, weekly, dailyAnnotated, weeklyAnnotated },
                   fromStore } ] }        -- charts are URLs

GET /api/ai-analyze/charts/{filename}     → PNG
POST /api/ai-analyze/feedback             → { symbol, analysisDate, feedback, notes }
```

Existing store rows are returned without new AI calls (`force=true` bypasses,
still writes a new row only if prompt_version/model changed — else updates
nothing, returns stored).

Concurrency: `asyncio.Semaphore(MAX_CONCURRENT_AI)` (default 5); symbol
failures isolated (`error` field per symbol, pipeline continues).

## Frontend (React Native, existing screener page)

- Screener results table gains AI columns: IFP, phase, base, pattern, buy
  point, confidence, verification badge.
- Row tap → `<Modal>` popup over the list (no navigation): header (symbol,
  price, recommendation badge, close ✕), daily/weekly tabs showing annotated
  chart from URL, metric row (phase / base count / quality / buy point), IFP
  verdict card, pattern chips, AI-vs-computed levels table with ✓/⚠, thesis,
  feedback buttons (POST /feedback), TradingView link.
- Close restores list scroll position (modal, not a route).
- Wireframe approved 2026-07-13 (see chat).

## Cost & controls

- Hard gate + one-call-per-symbol: e.g. 200 screener stocks → ~30 gated in →
  30 calls × (2 images ≈ 3.2k tok + features ≈ 1k + 1.5k out) ≈ $0.02–0.03/stock.
- `AI_DAILY_CALL_CAP` (default 500): counter in DB; exceeded → 429.
- Store-first: same-day re-runs are free.

## Rollout

1. Feature engine + gate (pure, unit-testable, no API key needed)
2. Chart render module (fixes weekly-EMA bug; volume MA overlay)
3. AI client + schema (behind `ANTHROPIC_API_KEY` presence check)
4. Verification, storage, router; mount in `app/main.py`
5. Frontend modal (separate task, after backend live)
