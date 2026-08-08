# OHM Backtest Engine — Design Spec (v2, for review)

Status: **not implemented yet** — still nothing in the DB or codebase beyond this doc and one
unapplied draft SQL file. This revision folds in your latest round of changes: date range and
track selection become on-screen run options, exit rules become independent toggles, AI results
get cached and reused across runs, and runs are triggered from the UI (not CLI-only).

---

## 1. What changed from v1

| Area | v1 (previous doc) | v2 (this doc) |
|---|---|---|
| Date range | Fixed "last 6 months" | **User-selected** on the run-config screen |
| Track | Always quant top-3 + AI top-3 | **User-selected**: Quant only / AI only / Both |
| Exit rules | One fixed ladder | **Independently toggleable**: breakeven move, half-booking, trailing, fixed target (−8% intraday + structural-SL-on-close always on — see §4) |
| Capital / resting window / stacking guard | Fixed engine defaults | **Also exposed as run options** |
| AI calls | Fresh retroactive Gemini calls every run | **Cached per (symbol, date)**, reused across every future run that touches that date — a second run over an overlapping range makes zero new AI calls for days already analyzed |
| Trigger | Offline batch script, CLI-only | **Triggered from the UI**, runs as a background job with live status/progress, browsable while running or after |

---

## 2. Run-config screen (new)

The "New backtest run" form on the Backtest tab:

| Field | Type | Notes |
|---|---|---|
| Date range | Start date / end date pickers | No fixed default range — you pick it each run |
| Universe | Fixed: Full NSE EQ (~2,300 stocks) | Not exposed as an option in v1 (matches earlier "full universe" decision) |
| Track | Quant only / AI only / Both | See §3 for what each means |
| Exit rules | 4 independent toggles (see §4) | Any combination allowed |
| Capital base | Number input, defaults ₹4,00,000 | Used in the sizing formula, no shared-funds cap regardless of value |
| Entry order resting window | "Indefinite" or a number of trading days | If a number, an unfilled entry trigger is marked `UNFILLED_EXPIRED` after that many days |
| Position-stacking guard | On/off toggle | If on: a symbol already OPEN (filled) in this run always skips a repeat pick. A symbol already PENDING (not yet filled) follows the **stacking guard mode** below. If off (default so far): every pick becomes its own independent simulated trade, even repeats of the same symbol |
| Stacking guard mode *(only shown when guard is on)* | Skip / Override | **Skip**: leave the earlier pending order alone, ignore the new pick. **Override**: cancel the earlier pending order (marked `SUPERSEDED`) and place the new one instead |

Clicking **Run** immediately creates a `backtest_runs` row (`status=RUNNING`) and starts a
background job; the run list shows live progress (`day 42/130`) and you can navigate away and
come back — matches your "kick off in background + live status" answer.

## 3. Track modes

- **Both** (default): funnel runs, produces the survivor pool, AI re-ranks that same pool. Top 3
  by quant ranking AND top 3 by AI ranking are both simulated as trades that day (may overlap on
  a symbol — see the open question in §8).
- **Quant only**: funnel runs, quant top 3 simulated. **AI step is skipped entirely** — no Gemini
  calls, fastest/cheapest option.
- **AI only**: funnel still runs first to build the candidate pool (AI has to re-rank *something*)
  and AI calls still happen, but only AI's top 3 become simulated trades — quant's top 3 aren't
  traded that run (though the funnel survivor data + AI analysis are still cached either way, so
  a later "Both" or "Quant only" run over the same dates is cheap/instant).

## 4. Exit rules — independent toggles

Base rule, **always on** regardless of toggles (this is the floor, not optional): intraday **−8%**
stop from entry (checked against the day's low) and close-based **structural SL** (the initial
stop from the signal bar). Every trade has at least these two protections.

On top of that floor, four independently toggleable rules:

| Toggle | Effect when ON | Effect when OFF |
|---|---|---|
| **Breakeven move** | Once unrealized gain reaches +1R, structural SL moves up to entry price | SL stays at the original structural level until closed or hit |
| **Half-booking** | At +2R, sell half the position, lock in that portion's profit | Full position stays intact through +2R |
| **Trailing** | After half-booking (or from +2R if half-booking is off), the stop trails the remaining/full position instead of sitting still | No trailing — stop stays wherever breakeven/structural last left it |
| **Fixed target (2R)** | If price gaps straight through 2R without the trail catching it first, exit the untrailed portion at the 2R target | No target exit — position only exits via a stop (−8%, structural, breakeven, or trail), never a profit target |

Example presets this gives you for free, without hardcoding preset names:
- All 4 ON = the full live-methodology ladder.
- Half-booking + trailing ON, breakeven + target OFF = "let it run, protect gains at 2R, no
  early breakeven bail-out."
- All 4 OFF = "simple" mode — just the −8% and structural-SL floor, nothing else.

## 5. AI result caching (new — addresses "reuse across runs")

New table `backtest_ai_signals`, keyed by `(symbol, signal_date, prompt_version)`:
- The very first time any run needs AI analysis for a given symbol+date, it calls the real AI
  pipeline (historically-accurate chart as of that date) and **stores** the result here.
- Every subsequent run — whether it's the same date range with a different exit-rule
  combination, an overlapping date range, or a rerun after a funnel-threshold tweak — checks this
  table first and only calls Gemini for symbol+date combinations not already cached.
- Practical effect: your first "Both" run over a 6-month range does the full ~4,300-call AI pass
  once. Every future run touching any of those same dates is free on the AI side — only the trade
  *simulation* (fast, local, no external calls) re-runs with the new exit-rule/capital/etc combo.
- This cache is intentionally separate from the live `ai_analysis_results` table — backtest
  analysis stays isolated from production data, no risk of cross-contamination either direction.

This also means the funnel-survivor list itself (quant side) is cheap to recompute every time
(it's SQL against `stock_indicators`, no caching needed there) — only the AI step benefits from
caching, since that's the part with real external cost/latency.

## 6. Database schema (updated)

**`backtest_runs`**
```
id, created_at, completed_at, start_date, end_date, universe,
track_mode          (QUANT | AI | BOTH),
capital,
resting_window_days (NULL = indefinite),
stacking_guard       (boolean),
stacking_guard_mode  (SKIP | OVERRIDE, only meaningful if stacking_guard=true),
exit_config          JSONB  { breakeven, half_booking, trailing, fixed_target }  -- all booleans
status                (RUNNING | COMPLETED | FAILED),
progress_day, progress_total_days,   -- for the live "day 42/130" indicator
params                JSONB  (full snapshot incl. all gate thresholds, for reproducibility),
error, notes
```

**`backtest_trades`** — mostly unchanged from v1, one row per simulated trade, but `track` is
replaced with two nullable rank columns per §8.1:
```
id, run_id, symbol,
quant_rank, ai_rank        (nullable int 1-3, at least one set — see §8.1)
signal_date, entry_trigger_price, structural_sl, target_price, risk_per_share, quantity,
entry_type, base_stage, ai_confidence, ai_recommendation,
status                     (PENDING | OPEN | CLOSED | UNFILLED_EXPIRED | SUPERSEDED)
entry_fill_date, entry_fill_price,
half_booked, trail_sl,
exit_date, exit_price, exit_reason,
realized_pnl, r_multiple, holding_days,
meta JSONB, created_at
```

**`backtest_ai_signals`** *(new)*
```
symbol, signal_date, prompt_version, model,
recommendation, confidence, ifp_score, analysis JSONB, features JSONB,
chart_daily_path, chart_weekly_path,   -- rendered-as-of-that-date chart images, for later review
created_at
UNIQUE (symbol, signal_date, prompt_version)
```

## 7. API endpoints (updated)

| Endpoint | Purpose |
|---|---|
| `POST /api/backtest/runs` | **Now UI-triggered.** Body: date range, track_mode, exit_config, capital, resting_window_days, stacking_guard. Creates the run row, starts the background job, returns the run id immediately. |
| `GET /api/backtest/runs` | List runs with status + headline stats |
| `GET /api/backtest/runs/{id}` | Single run status/progress (for polling while RUNNING) |
| `GET /api/backtest/runs/{id}/summary` | Equity curve, P&L, win rate, drawdown, per-track breakdown |
| `GET /api/backtest/runs/{id}/trades?track=&status=` | Trade log, filterable |
| `GET /api/backtest/runs/{id}/day/{date}` | Day drill-down (picks, orders placed, open positions w/ unrealized P&L, realized P&L to date) — unchanged from v1 |

## 8. Resolved (this round)

1. **Quant/AI symbol overlap** — when track_mode=BOTH and a symbol lands in both quant's top 3
   and AI's top 3 on the same day, it's **one trade, tracked in both**. Schema change:
   `backtest_trades` no longer has a single `track` column — instead two nullable rank columns,
   `quant_rank` and `ai_rank`. A row counts toward quant-track stats if `quant_rank IS NOT NULL`,
   toward AI-track stats if `ai_rank IS NOT NULL` — both can be set on the same row. No double
   capital/exposure counting, but the trade's P&L legitimately counts in both tracks' win-rate/P&L
   numbers (same real-world trade, valid under either strategy).
   - `track_mode=BOTH`: a row is created for any symbol in quant-top-3 ∪ AI-top-3, whichever
     rank(s) apply.
   - `track_mode=QUANT`: only quant-top-3 symbols get rows; `ai_rank` always null (AI step
     skipped entirely).
   - `track_mode=AI`: only AI-top-3 symbols get rows; `quant_rank` populated too if that symbol
     also happened to be in quant's top 3 that day (informational — doesn't create an extra row).

2. **One run at a time** — confirmed. The engine runs as a background subprocess kicked off by
   the API (status persisted in `backtest_runs`, not in memory, so it survives an API restart).
   While a run is RUNNING, the UI disables the "Run" button, and the API also rejects a second
   `POST /api/backtest/runs` with a clear error as a backend-side guard (not just a UI nicety).

3. **Stacking guard, PENDING case** — now a UI option (`stacking_guard_mode`, only shown/relevant
   when the stacking guard toggle is on): **Skip** (leave the earlier pending order alone, don't
   place the new one) or **Override** (cancel the earlier pending order — marked `SUPERSEDED` —
   and place the new one at the fresh signal's trigger/SL). My assumption for the **OPEN** case
   (symbol already filled, not just pending): the guard always **skips** the new pick there — an
   already-filled real position can't be "overridden" by a fresh resting order in this model, so
   Override only makes sense for the PENDING case. Flagging this interpretation explicitly —
   correct me if you intended something else for the OPEN case.

---

Still nothing implemented. Let me know if this is good to build, or if anything above needs
another pass.
