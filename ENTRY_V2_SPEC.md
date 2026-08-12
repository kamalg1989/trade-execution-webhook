# Entry v2 — pre-registered specification

**Status: NOT YET RUN.** Every parameter below is fixed *before* seeing a single
result. That is the whole point of writing this down.

This project has killed seven ideas that looked good until their neighbours or
their out-of-sample behaviour were checked (BACKTEST_REPORT §6.4). Pattern
detectors are the worst offender available: a pivot lookback, a symmetry
tolerance and a proximity band give three knobs per pattern, and with four
patterns that is twelve dimensions of search surface. Twelve dimensions will
always yield a winner. So the parameters are chosen here on *reasoning*, and the
grid is reported in full rather than searched.

---

## 1. What changes

The deck ("When to Buy", slide 15) separates two things the code has flattened
into one list:

| Level | Question | Options |
|---|---|---|
| **Buy point** | *Where* in the base are we? | pullback · reverse H&S · high breakout · breakout retest |
| **Trigger** | *Is this bar actionable?* | hammer (pin bar) · HH-HL · inside bar · trend bar |

Today `detect_entry_technique()` returns the first matching **trigger** and knows
nothing about location; `PULLBACK` and `BREAKOUT_RETEST` sit at the bottom as
*fallbacks*, reached only when no candle matched, and both are switched off. So
in production a trend bar anywhere in the base qualifies.

**Entry v2 requires BOTH levels.** This is a stricter gate and will cut candidate
count materially — which the cost evidence suggests is the right direction, but
it is a behaviour change, so v2 results are **not** comparable to any earlier run.

---

## 2. Pre-registered parameters

All fixed. None to be swept. If a value looks wrong after seeing results, the
correct response is to say so and treat the run as exploratory — not to retune
and re-report.

### 2.1 Buy points

**`HIGH_BREAKOUT`** — price at the top of the base, about to clear it.
- Base high = highest high of the last `BASE_LOOKBACK_BARS` (20, production value).
- Qualifies when the bar's high is within **2%** of the base high.
- *2% because production's `NEAR_BREAKOUT_MAX_DISTANCE` is already 5% for the
  Stage 1 gate; at the trigger we want tighter. 2% is one round step below it,
  not an optimised value.*

**`PULLBACK`** — price has retraced into support inside an uptrend.
- Close above SMA200 **and** above EMA50 (trend intact).
- Bar's low touches or breaches EMA21, close back above it.
- *EMA21 because that is the trail production already uses; introducing a
  different MA here would add a parameter with no justification.*

**`BREAKOUT_RETEST`** — cleared the base, came back to test it.
- Base high was exceeded within the last **10** sessions.
- Bar's low is within **2%** of that base high, close above it.
- *10 sessions = half the 20-bar base lookback. A retest arriving later than
  half a base-length is a new base, not a retest.*

**`REVERSE_HS`** — inverse head-and-shoulders, the one genuinely new detector.
- Pivots from a **5-bar** fractal (centre bar lowest of 5), matching
  `ai_analysis/features/swings.py` so the definition is shared, not invented.
- Need three troughs L-H-R within the last **60** sessions where
  `head < left shoulder` and `head < right shoulder`.
- Shoulder symmetry: `abs(left - right) <= 15%` of head depth.
- Neckline = higher of the two intervening peaks; qualifies when the bar's high
  is within **2%** of the neckline.
- *60 sessions ≈ one quarter, the shortest window in which a three-trough
  structure is visible on daily bars. 15% symmetry is deliberately loose — a
  tight tolerance would find almost nothing and would be fitted.*

### 2.2 Triggers

Unchanged from production, including thresholds:
`HH_HL` · `INSIDE_BAR` · `PIN_BAR` (body ≤ 35%, lower wick ≥ 55%) ·
`TREND_BAR` (close in top 70% of range).

**Priority order is unchanged** (HH-HL → inside → pin → trend, first match wins),
so entry behaviour does not silently shift. But every bar now records **all**
triggers it satisfies, not just the winner, so we can later measure how often the
arbitrary ordering actually decided anything. Measure first, change later.

### 2.3 Entry and stop

Unchanged: `entry_raw` / `sl_raw` come from the trigger bar exactly as today, then
tick rounding. The buy point decides *whether* to act, never *where* the stop
goes — introducing a second source of stop levels would confound this test with a
stop-placement test.

---

## 3. Fundamental gate (separate, testable alone)

Point-in-time YoY growth from `earnings_fundamentals`.

- For day *D*, use the latest filing with **`broadcast_date <= D`**. A filing is
  invisible until published — this is the one bias that is both easy to introduce
  and invisible in the output.
- Compare to the filing whose `period_to` is ~1 year earlier (±45 days).
- Prefer `Consolidated`, fall back to `Non-Consolidated`, but never mix the two
  within one comparison.
- Pass when **revenue growth ≥ 10%** *and* **net-profit growth ≥ 10%**, matching
  `screen_gpt`'s existing `FUND_MIN_REVENUE_GROWTH` / `FUND_MIN_EARNINGS_GROWTH`.
- Prior net profit ≤ 0: pass only if current > 0 (turnaround); fail if both ≤ 0.
  *A percentage change off a negative base is not a growth rate.*
- **Missing data → two policies, both reported.** `pass` (no constraint, the
  project's existing convention) and `fail` (strict). Coverage is 95.7% of liquid
  names and 97.6% of names actually traded, so the two should barely differ — and
  if they differ a lot, that itself is the finding.

**Window: 2018-2024.** Coverage before 2018 is too thin to test, so 2016-2017
drop out and this is 7 years across 2 regimes, not a decade.

---

## 4. Allocation

### 4.1 Base stage — the ONLY size factor

| Base | Multiplier | vs production |
|---|---|---|
| 1 | 1.00× | unchanged |
| **2** | **0.75×** | **was 1.00× — the only change** |
| 3 | 0.50× | unchanged |
| 4 | 0.25× | unchanged |

Applied exactly as production applies it — scaling **both** limits, so the
existing formula is untouched apart from the dict:

```python
risk_amt    = CAPITAL * 0.0025 * stage_mult
max_capital = CAPITAL * 0.10   * stage_mult
qty         = min(risk_amt / risk_per_share, max_capital / entry)
```

The ceiling stays 1.00×, so no position can exceed 10% of capital or 0.25% risk —
the same limits production runs today. A 2.00× Base-1 tier was considered and
rejected: it would have allowed 20% of capital in one name, and with 3 picks/day
up to 60% across three names.

Sizing already lives in `_size_qty()` (backtest) reading
`screen_gpt.BASE_STAGE_SIZE_MULTIPLIER` directly, so this is a one-dict change
that keeps backtest and production in sync by construction.

### 4.2 Trigger-based sizing — considered and REJECTED

Every trigger deploys the **same** allocation. Recorded here because it was
explicitly evaluated, not overlooked.

Two reasons it was dropped:

1. **Stop width is already neutralised.** Sizing is risk-based —
   `qty = risk_amt / (entry - stop)` — so an HH-HL with a wide stop already buys
   fewer shares than an inside bar with a tight one. The rupees at risk are
   identical. Trigger-based sizing would therefore not be compensating for stop
   geometry; it would be a pure conviction bet.
2. **No evidence exists for such a bet.** Nothing in production sizes on entry
   type — `entry_type` is captured and used only for display in the Telegram
   alert and the GPT prompt. Inventing a multiplier ladder would be a free
   parameter with nothing behind it.

If it is ever revisited, the honest route is to first measure win rate and
average R **by trigger type** from `backtest_trades`, and let those numbers set
the ladder rather than judgement.

---

## 5. Run list — one variable at a time

Each isolates a single change against the same production baseline, continuous
2018-2024, so any difference is attributable.

| # | Run | Isolates |
|---|---|---|
| 0 | production baseline, **next-open exits** | in-batch reference — see note |
| 1 | + new base ladder (1.00/0.75/0.50/0.25) | the Base-2 change |
| 2 | + entry v2 (buy point AND trigger) | the deck's two-level model |
| 3 | + fundamental gate (missing = pass) | the one new information source |
| 4 | + fundamental gate (missing = fail) | sensitivity to the coverage policy |
| 5 | best of 1-4 + weekly cadence | already-validated turnover reduction |

**The baseline uses `next_open_exit = True`.** Production's `sl_engine.py` runs at
18:00 IST, when Dhan rejects market orders, so it places forever orders with a
trigger just below the close **which fill at the next open**. The close-fill model
every earlier breakout run used is therefore *not* what production does, and it
understates it badly — measured at Rs.35,406 (close-fill) vs Rs.125,437
(next-open) over the same decade. Comparing entry v2 against the close-fill
number would flatter it by ~Rs.90k of pure modelling artifact.

The 4 × 4 buy-point × trigger grid is **reported in full, not searched.** If one
cell looks outstanding, that is 16 cells of noise doing what 16 cells of noise
do — it would need its own out-of-sample test before meaning anything.

---

## 6. Known limitations, stated up front

- **Manual verification is absent.** The decks put it in all four pipelines. Every
  number here is the funnel *without* the step the author treats as essential, so
  it is a floor, not an estimate of the method.
- **Survivorship bias applies unchanged** (§9.11) — 7 of 3,261 symbols have a
  series that ends early.
- **Reverse H&S and the buy-point detectors are new code with no ground truth.**
  Nothing validates that what the detector calls a pullback is what a trader
  would call one. Visual spot-checks against real charts are the only available
  check, and they should be done before the numbers are trusted.
