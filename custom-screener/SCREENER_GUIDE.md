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
3. AI analysis panel → POST /api/ai-analyze (always analyzes every listed
   symbol — no IFP pre-filter gate; see §3)
   a. Store lookup   (symbol+date+prompt_version+model already analyzed? free)
   b. Feature engine (deterministic IFP numerics + levels — v2 feeds these to
      the AI; v3 computes them only for the post-hoc cross-check, never sent)
   c. Chart render   (clean PNG: candles, volume+MA20, EMAs — daily only for
      v3, daily+weekly for v2 unless "daily only" scope is picked)
   d. AI vision call (v3: pure chart image + cached few-shot examples; v2:
      images + numeric feature block) → forced JSON schema
   e. Verification   (AI levels vs computed pivot/stop, ±2% tolerance —
      independent check, never fed back into the prompt)
   f. Annotate + store (immutable row per symbol/date/prompt_version/model)
   g. Background: outcome scoring kicks off automatically
4. Results table → row click → popup: charts, AI report, levels, feedback
5. Aftermath tab → what actually happened after the call (backtest review)
6. AI performance table → win rates per engine/verdict accumulate over time
```

**Division of labor.** Two philosophies coexist, selectable per run:
- **v3 (default) — trust the eyes.** The AI never sees our computed numbers.
  It reads the chart image cold, calibrated by two real few-shot examples
  (a strong-IFP and a weak-IFP stock from this account's own trades). Our
  math runs anyway, but purely as an *independent* post-hoc cross-check —
  it can flag disagreement, never anchor the AI's judgment.
- **v2 (legacy) — numbers detect, vision classifies.** Volume signatures are
  computed deterministically (a chart PNG can't convey exact volume
  magnitudes) and fed to the model; Claude judges structural shape (base
  form, pattern geometry, cross-timeframe base counting) on top of them.

Either way, a verification layer cross-checks every AI level against the
computed one and flags disagreement instead of trusting either blindly.

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

Note: the live "Tune IFP" recompute panel was removed from the AI analysis
UI — the IFP column now only ever shows the stored nightly score, and the AI
stage no longer gates on it (see §3).

---

## 3. AI analysis

The panel has three selectors: **prompt version** (v3 default / v2), **AI
engine** (Gemini default / Haiku / Hybrid / Sonnet), and — v2 only — **chart
scope** (daily / daily+weekly; v3 is always daily-only). No gate or threshold
control any more: every symbol passed into the panel gets analyzed, always.
Results are cached per `symbol + date + prompt_version + model`, so re-running
with the same combination is free (store lookup, no API call).

### Prompt versions

**v3 — Visual (default).** Pure chart-reading. The AI receives ONLY the
daily chart image plus two fixed calibration examples (real charts from this
account's own trades, rendered once and cached) — no computed numbers are in
the prompt at all. Cheapest, and the philosophy the module is built around:
*don't trust the math, trust what a trained eye sees on the chart.* Our
feature engine still runs, but purely as an independent post-hoc
cross-check (§ Verification below), never as model input.

**v2 — Grounded (legacy).** The AI receives the chart(s) plus a computed
feature block (IFP score, absorption days, volume ratios, computed levels)
and is told to trust those numbers for anything volume/level-related, using
the chart only for structural shape. Kept for comparison — the outcome
tracking table (§5) reports v2 vs v3 win rates separately so the two
philosophies can be judged on actual forward returns, not just intuition.

### Engines and costs (per stock)

| Engine | Model string | Cost/stock | Character |
|---|---|---|---|
| Gemini | `gemini-3.1-flash-lite` | ~₹0.15 (v3, daily-only; no prompt caching support) | Cheapest, now the default. "Lite" tier — chart-reading quality not yet A/B tested against Sonnet, treat any single call loosely until cross-checked |
| Haiku | `claude-haiku-4-5-20251001` | ~₹0.5–0.6 | Fast cheap scan. Head-to-head test (v2, 5 symbols): too optimistic — rated EARLY_STAGE/SETUP_READY on charts Sonnet rated AVOID at 0.85–0.90 confidence. A first v3 test on the same symbol (ATUL) matched Sonnet's earlier AVOID call — the few-shot calibration examples appear to correct some of that optimism, but still treat SETUP_READY loosely |
| Sonnet | `claude-sonnet-4-5` | ~₹1.6–2.6 | Best judgment, most reliable AVOID calls. Use before committing real money |
| Hybrid | Haiku → Sonnet | ~₹1.1 | Haiku scans all; Sonnet auto re-checks anything Haiku rates SETUP_READY/EARLY_STAGE (result badge `S✓`). Gemini is not part of this chain |

"Daily only" scope (v2): one image instead of two, ~40% cheaper, weaker base
counting (no weekly context). v3 is daily-only unconditionally.

Cost levers stacked into the pipeline:
- **Store-first**: same symbol+date+prompt_version+model = free replay
- **Prompt caching** (Anthropic only): v2 caches the system prompt; v3 caches
  the system prompt + both example images + captions as one prefix, so only
  the candidate chart + ~20 tokens of text are "fresh" per call after the
  first. Gemini has no caching support, so its v3 calls carry the full
  ~2,000-token example-image cost every time
- **Slim v3 schema**: `base_type`, three IFP ratings, `extended`, two price
  levels, `recommendation`, `confidence`, a ≤2-sentence `verdict` — far
  fewer output tokens than the v2 schema's patterns/reasons/thesis
- **Output cap**: `max_tokens=900` (v2) / `400` (v3) — output is ~5× input price
- **Daily call cap**: `AI_DAILY_CALL_CAP=500` (DB counter → HTTP 429)

Measured token usage per stock (logged on every call):
- **v3, Anthropic (cached)**: ~1,150 fresh input tokens + ~250 output after
  the first call of the day primes the cache
- **v3, Gemini (no caching)**: ~4,087 input + ~160 output — the two example
  chart images (~900–1,000 tokens each) are resent every call
- **v2, Gemini, for comparison**: ~2,050 input — the ~2,030-token gap
  between v2 and v3 Gemini calls is almost exactly the two example images,
  confirming they're transmitted correctly on every v3 call
- **v2, Anthropic, daily+weekly**: ~2×1,120 image tokens + ~1,100
  system/schema + ~600 features in; ~600 out

### v3 system prompt (prompt_version `v3`)

```
You are an expert swing-trading analyst for Indian stocks (NSE/BSE). You rank breakout
candidates by the quality of their Institutional Footprint (IFP) — the visible evidence
that institutions accumulated and are defending a stock. Institutions can manipulate
price but cannot hide volume.

Analyse ONLY the chart image. Read price structure, volume bars versus the volume-average
line, moving averages, and base boundaries yourself. Price action structure is the primary
decision tool.

BASE TYPE (context, not a filter):
A = base after uptrend: strong move up (big candles, volume spike), then a consolidation
base, now breaking out.
B = accumulation after distribution: downtrend or long sideways period, quiet accumulation
at lows, base at the bottom, now breaking out.

RATE THREE IFP CRITERIA (strong / moderate / weak):
1. volume_pattern (most important): strong = big spike on the initial move, volume dries
up during the base; weak = choppy/random volume, no distinction between move and base.
2. base_structure: strong = tight orderly consolidation respecting clear levels; weak =
wide messy chop, no readable structure.
3. pullback_depth: strong = shallow pullback with a clear floor, institutions defending;
weak = deep unstructured pullback giving back most of the move.

EXTENSION: extended=true if the stock has already moved far from its base without
consolidating. Extended = lower priority even with good IFP. A stock just starting to
move off a clean fresh base is the highest priority.

ENTRY & LEVELS (read visually from the chart):
- The entry trigger is a coiling-pattern breakout inside the base — inside bar is the
primary coil; any tight consolidation counts.
- breakout_level = top of the coil / base high you identify.
- stop_level = below the low of the inside bar (or tightest recent coil). This stop rule
is fixed — never suggest alternatives.

VERDICT:
- SETUP_READY: 2-3 criteria strong, near a clean base breakout, not extended.
- EARLY_STAGE: base still forming, or criteria moderate.
- NOT_READY: criteria weak or structure unreadable.
- AVOID: distribution signs (high-volume churn at highs, lower highs/lows, double/triple
top, head and shoulders) or a broken base.
Be conservative: mixed evidence = EARLY_STAGE or NOT_READY, never SETUP_READY. Ties
between similar stocks: cleaner base wins; less-extended wins.

Do not comment on exits, position sizing, or targets. Indicator overlays other than
price, volume and the moving averages shown are visual reference only.

verdict field: max 2 sentences, specific to what you see on THIS chart (e.g. "Tight
5-week base on dry volume after the March rally; inside bar at the pivot") — never
generic filler.
```

**Few-shot calibration** (real charts, rendered once from this account's own
DB by `scripts/render_v3_examples.py`, cached and reused on every call):

- **COHANCE, Jan–Jun 2026 (strong IFP)** — "explosive rally mid-April 2026
  from ~Rs 260 to ~Rs 520 on a massive volume spike (tallest bars on the
  chart). May–June: orderly base between Rs 400-460, volume visibly drying
  up bar after bar. Shallow pullback — gave back little of the April move.
  Late June: tight coil / inside bar inside the base = entry trigger.
  volume_pattern=strong, base_structure=strong, pullback_depth=strong.
  Type A. Not extended."
- **TNPETRO, Jan–Jun 2026 (weak IFP)** — "no institutional move anywhere on
  the chart. Months of wide choppy oscillation ~Rs 84-94 with no readable
  base and no floor. Volume random throughout — spikes with no
  follow-through, no dry-up phase. volume_pattern=weak, base_structure=weak,
  pullback_depth=weak. Skipped even though it passed the same scanner."

The candidate message is just the daily chart image plus `Stock: {SYMBOL}.
Analyse per your methodology and report via the tool.`

### v3 response schema (forced tool use)

```json
{
  "base_type": "A | B",
  "ifp": {
    "volume_pattern": "strong | moderate | weak",
    "base_structure": "strong | moderate | weak",
    "pullback_depth": "strong | moderate | weak"
  },
  "extended": true,
  "buy_point": { "breakout_level": 520.0, "stop_level": 495.0 },
  "recommendation": "SETUP_READY | EARLY_STAGE | NOT_READY | AVOID",
  "confidence": 0.84,
  "verdict": "max 2 sentences, chart-specific"
}
```

### v2 system prompt (prompt_version `v2`, legacy)

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

### v2 response schema (forced tool use — always schema-valid JSON)

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
a computed one exists → `partial`. Never silently overridden — this applies to
both prompt versions, and for v3 it is the *only* place computed numbers ever
touch the AI's output, strictly after the fact.

### Result table columns
`IFP ⓘ` = our computed nightly score (not AI). Everything marked `✦` is
AI-generated: `Base type` (A/B, hover for definitions), `IFP quality` (Vol /
Str / Pull chips, strong=green/moderate=amber/weak=red, hover each for its
rating), `Ext` (extended flag), `BO / Stop` (breakout in green / stop in
red), `Conf`, `Verdict`. Header tooltips use the browser's native title
attribute (not a custom popup) so they're never clipped by the table's
horizontal scroll.

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
| `POST /api/ai-analyze` | `{symbols, indicatorDate?, gateMode?, ifpThreshold?, aiMode?, chartScope?, promptVersion?, force?}` — UI always sends `gateMode: "soft"` (no pre-filtering); `hard`/`ifpThreshold` remain in the API for scripted/backtest use only |
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
  Chart scope is encoded in prompt_version (`v2` = daily+weekly, `v2-d` =
  daily-only, `v3` = always daily-only). v2 and v3 rows for the same
  symbol/date/model coexist — that's what lets the outcomes summary compare
  them directly.
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
- No pre-AI gate any more: every symbol handed to the panel (up to 50) is
  analyzed on every run unless already cached for that date/prompt/model —
  cost control now relies entirely on store-first caching + the daily call cap,
  not on filtering out weak-IFP stocks beforehand.
- Gemini (v3 default engine) is Google's "Lite" tier and hasn't been A/B
  tested against Sonnet on this task yet — early observation is it rates most
  criteria "moderate" rather than committing to strong/weak; treat single
  Gemini calls loosely until cross-checked against Hybrid/Sonnet.
- Haiku optimism (v2 finding): verified level mismatches are flagged, but
  recommendations are the model's own — prefer Hybrid/Sonnet for actionable
  decisions. A first v3 Haiku test suggested the few-shot examples reduce
  this, but it's one data point, not a pattern yet.
- v3 has no weekly chart or cross-timeframe base counting by design (daily
  chart only) — if a setup needs weekly confirmation, switch to v2 with
  daily+weekly scope.
- Chart PNGs accumulate in `AI_CHART_DIR` (default `/tmp/ai_analysis_charts`,
  cleared on reboot); no rotation yet. v3 example charts live outside `/tmp`
  in `AI_EXAMPLES_DIR` so they survive reboots.
- AI batch mode (50% discount for large historical runs) designed but not built
  (`api_used` column reserved).

## 10. Configuration (env)

```
AI_MODE=gemini|haiku|hybrid|sonnet default engine (UI overrides per run)
AI_HAIKU_MODEL / AI_SONNET_MODEL   Anthropic model strings
AI_GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_API_KEY                     required for the Gemini engine
AI_MAX_TOKENS=900                  v2 output cap
AI_MAX_TOKENS_V3=400               v3 output cap (slim schema)
AI_PROMPT_VERSION=v3               default prompt version (UI overrides per run)
AI_GATE_MODE=hard|soft             default gate (UI always sends soft; hard/threshold are API-only now)
IFP_GATE_THRESHOLD=0.30
AI_EXAMPLES_DIR                    v3 few-shot chart cache (default: ai_analysis/examples/, persistent)
MAX_CONCURRENT_AI=5                parallel AI calls
AI_DAILY_CALL_CAP=500
AI_CHART_DIR=/tmp/ai_analysis_charts
AI_LEVEL_TOLERANCE=0.02            verification tolerance
ANTHROPIC_API_KEY                  required for Haiku/Sonnet/Hybrid engines
```
