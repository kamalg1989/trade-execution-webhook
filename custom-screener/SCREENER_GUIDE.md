# Custom Screener + AI Visual Analysis — Complete Guide

NSE swing/positional screener built around the **Base-and-Bounce** methodology
(market cycle phases, base counting, Institutional Footprint), with a Claude
vision AI stage for IFP detection, base analysis, and pattern recognition.

- **URL**: https://ohmstockvault.duckdns.org/custom-screener/
- **Backend**: FastAPI on VPS `:8005` (`custom-screener/backend/`), PostgreSQL/TimescaleDB (`market_data`)
- **Frontend**: React + Vite + Tailwind (`custom-screener/frontend/`), served from the nginx web root
- **AI module**: `custom-screener/backend/ai_analysis/` (self-contained; see its `DESIGN.md`)

---

## 1. End-to-end flow

```
                     ┌──────────── DAILY DATA JOBS (cron/systemd) ────────────┐
                     │ 18:00  update_ohlcv.py       → ohlcv_data (Dhan API)   │
                     │ 18:30  compute_stock_indicators → stock_indicators,    │
                     │        market_snapshot (nightly, latest bar date)      │
                     │ 18:45  ai_analysis.outcomes  → forward returns scoring │
                     │ Sun 10:00 update_symbols_meta.py    (SME/lot flags)    │
                     │ Sun 10:10 update_index_membership.py (index/sector/mcap)│
                     └────────────────────────────────────────────────────────┘

USER FLOW
1. Pick date → market snapshot banner (regime, breadth)
2. PRIMARY filters (universe definition) + ADVANCED filters (setup quality)
   → POST /api/filter → in-memory filter over that day's indicator slice
3. Optional: "Tune IFP" — recompute IFP live with custom params on the results
4. AI analysis panel → POST /api/ai-analyze
   a. Store lookup   (symbol+date+prompt_version+model already analyzed? free)
   b. Feature engine (deterministic IFP numerics + levels, no AI)
   c. Gate           (hard: weak IFP dropped before AI = zero cost)
   d. Chart render   (clean daily+weekly PNG: candles, volume+MA20, EMAs)
   e. Claude vision  (images + numeric feature block → forced JSON schema)
   f. Verification   (AI levels vs computed pivot/stop, ±2% tolerance)
   g. Annotate + store (immutable row per symbol/date/prompt_version/model)
   h. Background: outcome scoring kicks off automatically
5. Results table → row click → popup: charts, AI report, levels, feedback
6. Aftermath tab → what actually happened after the call (backtest review)
7. AI performance table → win rates per engine/verdict accumulate over time
```

**Division of labor** (core design principle): *numbers detect the footprint,
vision classifies the base, rules verify the levels.* Volume signatures are
computed deterministically (a chart PNG can't convey exact volume magnitudes);
Claude judges structural shape (base form, pattern geometry, cross-timeframe
base counting); a verification layer cross-checks every AI level against the
computed one and flags disagreement instead of trusting either.

---

## 2. Screener filters

### Primary tier — "define your universe"

| Filter | What it does | Why (trade setup) |
|---|---|---|
| Universe (index) | Nifty 50/100/200/500, Midcap 150, Smallcap 250, Microcap 250 | Midcap/Smallcap = swing sweet spot. Weekly refresh from niftyindices.com |
| Sector chips | NSE industry classification, multi-select | Sector tailwind (deck: theme development) |
| Market cap chips | Large/Mid/Small/Micro — derived from index membership (SEBI-aligned) | No shares-outstanding feed needed |
| Avg daily turnover | 1-month avg price×volume (₹Cr) | Liquidity gate: clean fills, reliable stops |
| Min price | ₹20–1000 | Penny-stock excluder |
| Trend ladder | One dropdown, nested levels: Uptrend (C>SMA200) → Confirmed (+SMA50>200) → Momentum (+C>EMA21) → Power (full stack C>EMA10>21>SMA50>200) | Replaces 5 separate MA controls |
| 52W high proximity | Within 5–25% / >20% below | Bases that matter form near highs |
| Exclude SME (default ON) | Drops NSE EMERGE series SM/ST (lot-traded) | 550 symbols flagged via Dhan master; thin books, unreliable stops |

### Advanced tier (collapsed)

| Filter | Purpose |
|---|---|
| Base tightness (20d) | 20d range % — tight = supply absorbed |
| Near 20d high | Trigger proximity (breakout imminent) |
| Prior upmove | The "bounce" credential (deck: ≥50%) |
| Giveback | Constructive base gives back <30% of advance (deck rule) |
| Vol dry-up | Base vol ÷ advance vol ≤1 = sellers exhausted |
| Vol expansion (1d) | Breakout-day confirmation (>1.5× avg) |
| ATR % | Volatility / stop sizing |
| IFP score | Composite institutional footprint (nightly, default 100d/1.5×/0.60) |
| Flow confirm toggle | One click = up/down vol ratio ≥1.2 AND OBV slope positive |

Every control has an ⓘ tooltip explaining its purpose w.r.t. the setup.

### Tune IFP panel
Recomputes IFP live on the filtered subset with custom params (lookback days,
vol surge ×, close position) — updates the IFP column (marked `*`), stores
nothing. Note: the AI gate uses the stored nightly score, not tuned values.

---

## 3. AI analysis

### Engines and costs (per stock, daily+weekly charts)

| Engine | Model string | Cost/stock | Character |
|---|---|---|---|
| Haiku | `claude-haiku-4-5-20251001` | ~$0.007 (~₹0.6) | Fast cheap scan. Head-to-head test (5 symbols, same date): too optimistic — rated EARLY_STAGE/SETUP_READY on charts Sonnet rated AVOID at 0.85–0.90 confidence, and 4/5 of its levels mismatched computed pivots. Treat SETUP_READY loosely |
| Sonnet | `claude-sonnet-4-5` | ~$0.030 (~₹2.6) | Best judgment, most reliable AVOID calls. Use before committing money |
| Hybrid | Haiku → Sonnet | ~$0.013 (~₹1.1) | Haiku scans all; Sonnet auto re-checks anything Haiku rates SETUP_READY/EARLY_STAGE (result badge `S✓`) |

"Daily only" chart scope: one image instead of two, ~40% cheaper, weaker base
counting (no weekly context).

Cost levers stacked into the pipeline:
- **Hard gate** (default): weak-IFP stocks never reach the API
- **Store-first**: same symbol+date+prompt_version+model = free replay
- **Prompt caching**: system prompt + tool schema cached across calls
- **Output cap**: `max_tokens=900` (output is 5× input price)
- **Compact features**: minified JSON, trimmed absorption detail
- **Daily call cap**: `AI_DAILY_CALL_CAP=500` (DB counter → HTTP 429)

Token budget per call: ~2×1,120 image tokens + ~1,100 system/schema + ~600
features in; ~600 out.

### Current system prompt (prompt_version `v2`)

```
You are an expert technical analyst specialising in the Base-and-Bounce
methodology: market cycle phases (accumulation, advance, distribution, decline),
base counting (base 0 = accumulation through base 4+), constructive-base
assessment, and Institutional Footprint (IFP) — the principle that institutions
can manipulate price but cannot hide volume.

You receive, for one stock:
1. A DAILY candlestick chart (volume panel with 20-day volume average line,
   EMAs 10/21/50/200).
2. A WEEKLY candlestick chart (EMAs 10/40 week).
3. COMPUTED FEATURES: exact numbers derived from OHLCV — IFP score,
   accumulation days, absorption days, up/down volume ratio, volume
   contraction, base depth, retracement of prior advance, swing structure,
   and computed levels (pivot/support/logical stop).

Rules:
- Trust the COMPUTED FEATURES for anything volume- or level-related; charts
  cannot convey exact volume magnitudes. Use the charts for structural shape:
  base form, pattern geometry, trend context, and cross-timeframe base counting.
- A constructive base: gives back < 30% of the prior advance, contracting
  volume, no wild bars. Flag violations.
- Base 4+ or distribution signatures (high-volume churn, LH-LL, double/triple
  tops, H&S) mean caution or AVOID.
- Buy point types: pullback, reverse head-and-shoulders breakout, high
  breakout, breakout retest. Structures: hammer, HH-HL.
- breakout_level and stop_level must be concrete prices consistent with the
  computed pivot and logical stop unless the chart clearly shows a better
  structural level — if you deviate, say why in the thesis.
- Be conservative: if evidence is mixed, prefer EARLY_STAGE or NOT_READY over
  SETUP_READY.
- Be brief: evidence and thesis max 2 sentences each; base_quality_reasons max
  4 short phrases; report at most 4 patterns (highest confidence first).

Report your analysis ONLY via the report_chart_analysis tool.
```

The user message contains: daily chart image, weekly chart image (unless
daily-only scope), and a compact feature block:

```
Stock: ATUL
FEATURES daily: {"timeframe":"daily","bars":300,"close":6430.0,"ifp_score":0.36,
  "accum_days":3,"quiet_down_days":33,"updown_vol_ratio":0.63,"obv_slope":-0.23,
  "pivot":6894.0,"support":6401.5,"logical_stop":6401.5,"base_len_bars":24,
  "base_depth_pct":7.14,"retrace_of_advance_pct":57.5,"vol_contraction_ratio":0.81,
  "swing_structure":"lh_ll","absorption_days_count":0,"pct_above_sma50":-3.2,
  "pct_above_sma200":8.9,"sma50_above_sma200":true,"dist_to_pivot_pct":7.2}
FEATURES weekly: {...same shape, weekly bars...}
Image 1 = daily chart, image 2 = weekly chart. Analyse base structure, IFP,
patterns and buy point per your instructions.
```

### Response structure (forced tool use — always schema-valid JSON)

`tool_choice` forces the `report_chart_analysis` tool, so the response is never
free text:

```json
{
  "market_cycle_phase": "accumulation | advance | distribution | decline",
  "base_count": "0 | 1 | 2 | 3 | 4_plus",
  "base_quality": "constructive | suspect | broken",
  "base_quality_reasons": ["up to 4 short phrases"],
  "patterns": [
    { "type": "vcp | flag | pennant | inverse_hs | double_bottom | triple_bottom |
               double_top | triple_top | hs_top | rectangle | wedge | tennis_ball",
      "confidence": 0.82, "timeframe": "daily | weekly", "description": "..." }
  ],
  "ifp_verdict": { "present": true, "confidence": 0.8, "evidence": "max 2 sentences" },
  "buy_point": {
    "type": "pullback | reverse_hs_breakout | high_breakout | breakout_retest | none",
    "structure": "hammer | hh_hl | none",
    "breakout_level": 6894.0, "stop_level": 6401.5
  },
  "weekly_context": "how weekly confirms/contradicts the daily base count",
  "recommendation": "SETUP_READY | EARLY_STAGE | NOT_READY | AVOID",
  "confidence": 0.84,
  "thesis": "1-2 sentence summary"
}
```

### Verification layer
`|AI level − computed level| / computed ≤ 2%` → `verified`, else `mismatch`
(⚠ badge; both values shown side by side in the popup). AI giving no level when
a computed one exists → `partial`. Never silently overridden.

---

## 4. Result popup

Row click opens a modal (close returns to the list in place):
- **Daily / Weekly tabs** — annotated charts (green dash = AI breakout, red = AI stop, gray = computed support; volume panel with 20d MA)
- Metric row: phase, base count, base quality, buy point type
- IFP verdict (AI text + computed score), pattern chips with confidence
- Levels table: AI vs computed with ✓/⚠ per level; risk per share
- AI thesis, weekly context, base-quality reasons
- **Feedback buttons** (Correct / Partial / Wrong) → stored, feeds accuracy stats
- TradingView link
- **Aftermath tab** — see below

## 5. Aftermath & outcome tracking

- **Aftermath tab**: forward charts (daily + weekly) extending ~3 months past
  the analysis date. Purple dash-dot vertical line marks the analysis date —
  everything right of it happened AFTER the AI's call (the AI never sees
  forward data; aftermath charts are rendered separately from AI-input charts).
  AI breakout/stop lines carried across. Outcome strip: +5d/+20d/+60d returns,
  BO hit, Stop hit.
- **Backtests**: for historical dates the forward data already exists, so
  outcomes populate immediately (auto-scored in the background after each run).
- **Nightly job** (18:45): scores pending rows as forward days accumulate.
  `hit_breakout` / `hit_stop` = level touched within 20 bars; NULL while the
  window is incomplete and untouched.
- **AI performance table** (collapsible, in the AI panel): per engine ×
  recommendation — N, avg 5/20/60d returns, 20d win rate, breakout/stop hit
  rates, manual feedback tally. This is the ground truth for Haiku-vs-Sonnet
  and threshold decisions.

## 6. API endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/filter` | Screener filtering (all primary+advanced fields) |
| `POST /api/ifp` | Tunable IFP recompute on a symbol subset |
| `GET /api/market-snapshot?date=` | Regime/breadth banner |
| `GET /api/meta/sectors` | Sector + index counts for filter UI |
| `POST /api/ai-analyze` | `{symbols, indicatorDate?, gateMode?, ifpThreshold?, aiMode?, chartScope?, force?}` |
| `GET /api/ai-analyze/charts/{file}.png` | Stored analysis charts |
| `GET /api/ai-analyze/aftermath/{symbol}?date=` | Outcome numbers + forward chart URLs |
| `GET /api/ai-analyze/aftermath-chart?symbol&date&timeframe` | Live-rendered forward chart |
| `GET /api/ai-analyze/outcomes/summary` | Win rates per engine/verdict |
| `POST /api/ai-analyze/feedback` | `{symbol, analysisDate, feedback: CORRECT|PARTIAL|WRONG, notes?}` |

nginx maps `/custom-screener/api/` → `:8005/api/` (600s proxy timeout for long
AI runs; `/custom-screener` without trailing slash 301-redirects).

## 7. Database tables (AI module)

- `ai_analysis_results` — immutable, `UNIQUE(symbol, analysis_date, prompt_version, model)`.
  Holds features JSON, full analysis JSON, verification JSON, chart paths,
  outcome columns (`ret_5d/20d/60d`, `hit_breakout`, `hit_stop`), user feedback.
  Chart scope is encoded in prompt_version (`v2` = daily+weekly, `v2-d` = daily-only).
- `ai_call_budget` — per-day API call counter (cap enforcement).
- `symbols_meta` — series, lot_size, is_sme, sector, mcap_bucket per symbol.
- `index_membership` — (symbol, index_name), weekly refresh.

Migrations: `backend/sql/001–004, 006, 007` + `backend/ai_analysis/sql/005, 008`.

## 8. Scheduled jobs (VPS)

| When | Job | Notes |
|---|---|---|
| 18:00 daily | `update_ohlcv.py` (cron) | Dhan API; looks back 3 days only — longer outages need a manual run |
| 18:30 daily | `custom-screener-compute` (systemd timer) | Indicators + snapshot for latest bar date |
| 18:45 daily | `ai_analysis.outcomes` (cron) | Forward-return scoring |
| Sun 10:00 | `update_symbols_meta.py` (cron) | SME flags from Dhan master |
| Sun 10:10 | `update_index_membership.py` (cron) | Index/sector/mcap from niftyindices.com (keeps previous data on fetch failure) |

## 9. Known limitations / open items

- Index membership & mcap buckets are **current-day** — historical screens carry
  survivorship/look-ahead bias in backtests.
- Nightly compute processes only the latest bar date; late-arriving data can
  leave gaps (self-healing patch pending). No failure alerting yet.
- OHLCV updater only heals 3-day gaps automatically.
- Haiku optimism: verified level mismatches are flagged, but recommendations
  are the model's own — prefer Hybrid/Sonnet for actionable decisions.
- Chart PNGs accumulate in `AI_CHART_DIR` (default `/tmp/ai_analysis_charts`,
  cleared on reboot); no rotation yet.
- AI batch mode (50% discount for large historical runs) designed but not built
  (`api_used` column reserved).

## 10. Configuration (env)

```
AI_MODE=haiku|hybrid|sonnet        default engine (UI overrides per run)
AI_HAIKU_MODEL / AI_SONNET_MODEL   model strings
AI_MAX_TOKENS=900                  output cap
AI_PROMPT_VERSION=v2               bump to re-analyze after prompt changes
AI_GATE_MODE=hard|soft             default gate
IFP_GATE_THRESHOLD=0.30
MAX_CONCURRENT_AI=5                parallel Claude calls
AI_DAILY_CALL_CAP=500
AI_CHART_DIR=/tmp/ai_analysis_charts
AI_LEVEL_TOLERANCE=0.02            verification tolerance
ANTHROPIC_API_KEY                  required for AI endpoints
```
