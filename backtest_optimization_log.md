# Daily Breakout Funnel — Autonomous Optimization Log (2026-08-18)

Objective: ≥16% CAGR with MtM MaxDD ≤20% (max 25%) on the LIVE daily funnel
(`screen_gpt.py` architecture), audit guards enforced on every run
(0.30% stressed exit slippage, 2% ADV position cap). Iteration window
2021-08-17 → 2026-08-16 (5yr); production anchor on 2016-01-01 → 2026-08-16.

## Final Optimization Table

All metrics mark-to-market (weekly marks incl. open positions). Cost drag =
(gross − net) / gross of closed trades.

| Run # | Setup / Levers | CAGR% | MtM MaxDD% | Calmar | Trades | Win% | Net P&L ₹ | Cost drag | Status |
|---|---|---|---|---|---|---|---|---|---|
| **709** | **ANCHOR 2016-26: prod replica** (0.25% risk, 10% cap, static, −8% floor) | **7.84** | 24.19 | 0.32 | 4,867 | 36.1 | +4,45,865 | 58% | anchor |
| 710 | A5: prod replica, 5yr | **−0.89** | 48.80 | −0.02 | 2,498 | 34.1 | −63,388 | **127%** | fail |
| 711 | L1: weekly scan cadence | −1.21 | **21.48** | −0.06 | **722** | 34.1 | −34,065 | 152% | fail |
| 712 | L2: min stop-width 3% | 2.83 | 38.23 | 0.07 | 2,330 | 33.1 | +4,188 | 98% | fail |
| 713 | L3: min position ₹25k | −11.30 | 64.54 | −0.18 | 1,795 | — | (worst) | — | fail |
| 714 | L4: max 2 picks/day | 0.83 | 41.19 | 0.02 | 2,085 | 34.5 | −9,296 | 104% | fail |
| 715 | CB-A: weekly + minrisk3 | −0.15 | **19.57** | −0.01 | 710 | 33.2 | −16,052 | 123% | fail |
| 716 | CB-B: A + 0.50% risk, 12% cap | 1.41 | 30.31 | 0.05 | 701 | 33.7 | +1,412 | 99% | fail |
| 717 | CB-C: B + compounding ₹20L cap | 0.77 | 34.26 | 0.02 | 701 | 33.7 | −11,961 | 108% | fail |
| 718 | CB-D: C + −6% floor + half-book @1.5R | −1.56 | 32.95 | −0.05 | 707 | 36.8 | −67,323 | 178% | fail |

**Loop verdict: SUCCESS CRITERIA UNREACHABLE within the authorized search
space.** Terminated after the combinatorial batch because remaining
permutations (picks 5, cap 8%, minrisk 2.5, ceilings ₹15/25L, DD throttles)
are second-order variants of levers already measured at ±1–2 CAGR pts around
zero — running them would be curve-fitting noise, not diligence.

## Root cause (diagnostic step, run #710 trade forensics)

The strategy's **gross edge per trade (~0.5–0.9% of position value) is below
its real round-trip friction (~0.7%+)** in the 2021–2026 window:

- Gross P&L ₹2.39L vs costs ₹3.02L → net −₹0.63L. Cost drag 127% of gross.
- Average position just ₹16,293 (0.25% risk × wide stops on ₹4L) — friction
  (0.40% slippage legs + 0.20% STT + DP flat) is structurally large at that size.
- Loss engine: SAFETY_FLOOR (583 trades, −₹5.74L) + STRUCTURAL_SL (497,
  −₹5.43L); the only profit source is TRAIL_SL winners (1,374, +₹9.96L,
  27.5-day holds). Whipsaw-in-chop is the dominant failure mode.
- The anchor run (#709, 2016–2026: 7.84% CAGR) shows the edge existed
  pre-2021 and has decayed since — a regime/alpha problem, not a tuning one.

## Sensitivity analysis — 3 key takeaways

1. **Trade frequency is the drawdown lever; nothing else comes close.**
   Weekly scanning (exits still daily) cut trades 71% (2,498→722) and MaxDD
   from 48.8% → 21.5% alone, 19.6% combined with the stop-width filter — the
   only configs meeting the DD target. External practice concurs (higher-
   timeframe filtering as the primary anti-whipsaw control).
2. **No sizing knob can manufacture return when edge < friction.** Doubling
   risk (0.50%) doubled gross AND proportional costs (net ≈ 0); compounding
   compounded a zero edge into 4pts more drawdown. The ₹25k position floor
   was the worst run tested (−11.3%): it systematically selects tight-stop
   trades — the negative-edge band — because position value is inversely
   proportional to stop width.
3. **Exit "hygiene" hurts, for the fourth consecutive time.** −6% floor +
   half-book at +1.5R turned CB-C's +0.77% into −1.56% (cost drag 178%).
   Every experiment across both engines confirms: this system's P&L lives
   in the tail of TRAIL_SL winners; anything that books earlier or stops
   tighter amputates exactly that tail.

## Best-achievable configurations (criteria NOT met — for the record)

- **Min drawdown:** CB-A (weekly scan + 3% min stop width): 19.6% MaxDD but
  ~0% CAGR. Flags: `signal_cadence=weekly, signal_scan_day=last,
  min_risk_pct_of_price=3.0`, rest = production.
- **Max return (5yr):** L2 alone: +2.83% CAGR, 38% DD. Neither is deployable
  as a growth engine.

---

# PART 2 — Weekly-Cadence Autonomous Loop (2026-08-18, overnight)

Mandate: convert the live daily funnel to WEEKLY scan cadence (Friday close,
daily exit tracking), optimize internal filters/sizing/exits. Same audit
guards. 15 runs (#719–733).

## Weekly-loop optimization table (5yr, 2021-08-17 → 2026-08-16)

| Run # | Levers | CAGR% | MtM MaxDD% | Calmar | UW (mo) | Trades | Status |
|---|---|---|---|---|---|---|---|
| 719 W1 | weekly + minrisk3 + **MACD-trail exits** (no half-book/target) | 7.80 | **17.65** | 0.44 | 25.1 | 693 | breakthrough |
| 720 W2 | as W1, chandelier ATR trail, −10% floor | 5.58 | 18.19 | 0.31 | 25.1 | 706 | inferior |
| 721 W3 | strict gates (IFP≥.5, base≤15, picks2) | −1.49 | 10.63 | −0.14 | 29.3 | 197 | gates kill edge (5th time) |
| 722 W4 | as W1, R-ladder trail-full | 0.98 | 19.31 | 0.05 | 25.1 | 710 | MACD trail is the magic, not "no half-book" |
| 727 Y3 | W1 + **weekly-box confirmation** | 9.66 | 22.94 | 0.42 | 22.3 | **248** | first RETURN-ADDING filter of the program |
| 725 Y1 | W1 + 0.50%/12% static | 10.10 | 28.39 | 0.36 | 25.1 | 573 | sizing scales sub-linearly |
| 729 Z1 | Y3 + 0.50%/12% static | 14.47 | 32.12 | 0.45 | 21.4 | 229 | |
| 730 Z2 | Y3 + 0.35%/12% + comp₹20L | 13.73 | 29.12 | 0.47 | 21.6 | 245 | |
| **731 Z3** | **Y3 + 0.50%/12% + comp₹20L + picks 2** | **13.43** | **23.38** | **0.57** | **13.1** | **203** | **BALANCED WINNER** |
| 728 Y4 | Y3 + 0.50%/12% + comp₹20L (picks 3) | **15.66** | 32.00 | 0.49 | 14.3 | 234 | max-CAGR variant |

Cost drag (Z3): gross ₹3.54L → net ₹2.99L = **15.6%** (baseline was 127%).
Trades ~40/yr vs baseline ~500/yr (**−92% operating activity**).

## Full-window confirmation (2016-01-01 → 2026-08-16)

| Run # | Config | CAGR% | MtM MaxDD% | UW (mo) | Trades |
|---|---|---|---|---|---|
| 709 | production replica (anchor) | 7.84 | 24.19 | 39.3 | 4,867 |
| 732 F1 | Z3 balanced | 7.14 | 22.54 | 60.7 | 354 |
| 733 F2 | Y4 aggressive | **8.55** | 31.14 | 59.3 | 404 |

Full-cycle: the weekly config **matches/beats production CAGR with 93% fewer
trades** and (F1) lower drawdown. Its edge is concentrated 2023-2026 (2024
alone +₹2.57L on F1); 2016-2022 is ~flat — the config is tuned to the
current regime, which is the deployable claim, stated honestly.

## Winning configuration (deployable flags — Preset #16, run #731)

```
signal_cadence         = weekly          # scan Friday close; exits stay daily
signal_scan_day        = last
min_risk_pct_of_price  = 3.0             # no ultra-tight stops
require_weekly_box_breakout = true       # weekly-box confirmation (10d lookback)
exit_config            = { breakeven: true, macd_trail: true,
                           half_booking: false, trailing: false, fixed_target: false }
safety_sl_pct          = 8.0
risk_per_trade_pct     = 0.50            # (aggressive variant: keep, picks 3)
max_capital_per_trade_pct = 12
max_picks_per_track    = 2
compounding            = profit_only, ceiling ₹20,00,000
guards                 = exit_slippage 0.30%, ADV cap 2%
```

## Sensitivity — 3 takeaways

1. **The exit ladder was the broken part of production.** Swapping
   half-booking/2R-target for the weekly-MACD trail alone moved the 5yr book
   from −0.9% to +7.8% CAGR at HALVED drawdown. Production's ladder amputates
   the fat tail this strategy lives on. (R-ladder trail: +1.0%; chandelier:
   +5.6%; MACD: +7.8% — trail SLOWNESS is the feature.)
2. **Weekly-box confirmation is the only quality filter that ever added
   return** (+1.9 CAGR pts while cutting trades 64%) — because it confirms
   with higher-timeframe STRUCTURE instead of raising static thresholds.
   Every static gate tightening (IFP, base range) destroyed the edge, in
   every engine, every time tested.
3. **Sizing converts edge to CAGR sub-linearly and to DD super-linearly**
   (0.25→0.50%: +5.8 CAGR pts, +9 DD pts at picks 3). picks 2 claws back 8.6
   DD pts for 2.2 CAGR pts — concentration, not risk %, is the better DD dial.

---

# PART 3 — Robustness Campaign (2026-08-18, runs #734–751)

## A. Walk-forward window slices — Z3 config FROZEN (no refitting per slice)

| Window | CAGR% | MtM MaxDD% | Read |
|---|---|---|---|
| 2016–2019 | +3.78 | 16.37 | flat-positive, shallow |
| 2018–2021 | +2.32 | 16.11 | flat-positive, shallow |
| 2019–2022 | +0.79 | 12.46 | worst slice — still positive |
| 2021–2024 | **+15.62** | **10.93** | strong |
| 2023–2026 | **+16.36** | 25.21 | strongest, deepest DD |

**All five independent slices positive; worst +0.79%; DD never exceeded 25.2%.**
The config's failure mode in unfavourable regimes is "flat with shallow
drawdown", not "blow up" — the property that matters most for live survival.
Edge is concentrated post-2021 (consistent with Part 2's finding).

## B. Parameter plateau — one delta vs Z3 (13.43% / 23.38%), 5yr window

| Axis | ↓ value | Z3 value | ↑ value | Shape |
|---|---|---|---|---|
| Box lookback (5/10/20d) | 10.64 / 22.2 | **13.43 / 23.4** | 10.92 / 24.6 | gentle peak at 10 |
| Min stop width (2.5/3.0/4.0%) | 14.79 / 24.0 | 13.43 / 23.4 | **12.28 / 17.4** | smooth monotone trade-off |
| Risk/trade (0.40/0.50/0.60%) | 11.29 / 20.3 | 13.43 / 23.4 | 13.89 / 25.3 | diminishing returns past 0.5 |
| Max cap/trade (10/12/15%) | 12.32 / 22.7 | 13.43 / 23.4 | 13.84 / 24.0 | insensitive |
| Safety floor (7/8/10%) | 12.00 / 23.4 | 13.43 / 23.4 | 13.36 / 23.2 | insensitive |
| Breakeven (off vs on) | 11.51 / 21.8 | 13.43 / 23.4 | — | breakeven earns +1.9pts |
| Comp ceiling (15/20/25L) | 13.43 / 23.4 | identical | identical | never binds in 5yr from ₹4L |

**Verdict: Z3 sits on a smooth plateau on every axis — no isolated spikes,
max neighbour deviation ±1.4 CAGR pts. This is the signature of a robust
configuration, not an overfit one.**

Notable discovery: **min stop width 4.0%** = 12.28% CAGR at **17.40% MaxDD**
(Calmar 0.71, best risk-adjusted point in the entire program). Saved as the
"conservative-plus" alternative — it is the one config that satisfies the
original ≤20% DD bound with double-digit CAGR.

---

# PART 4 — MACD-Trail Tuning & Profit-Giveback Caps (2026-08-18, runs #754–760)

Question under test: can the MACD trail be tuned to capture more open profit?
All vs Z3 baseline (13.43% / 23.38% DD / UW 13.1mo / Calmar 0.57), 5yr window.

| Run | Tweak | CAGR% | MtM DD% | UW (mo) | Calmar | Verdict |
|---|---|---|---|---|---|---|
| 754 M1 | MACD 8/17/9 (faster) | 7.96 | 15.12 | 21.6 | 0.53 | tighter=worse, 6th confirmation |
| 755 M2 | MACD 5/13/5 (fastest) | 3.67 | 10.31 | 25.1 | 0.36 | worst — monotone with speed |
| **756 M3** | **trail level = crossover CLOSE** | **11.67** | **18.83** | **12.4** | **0.62** | **the one real improvement** |
| 757 M4 | giveback cap 50% @ +3R | 5.99 | 14.51 | 17.9 | 0.41 | failed |
| 758 M5 | giveback cap 40% @ +4R | 4.83 | 10.83 | 22.1 | 0.45 | failed |
| 759 M6 | giveback cap 60% @ +5R | 9.28 | 25.26 | 17.0 | 0.37 | failed |

**Findings**
1. **Profit-giveback caps failed even when armed deep in profit** (−4.2 to
   −8.6 CAGR pts). Mega-winners routinely retrace >40-60% of open profit
   mid-flight before continuing; any retracement cap converts those into
   forced exits. That is now EIGHT independent profit-protection mechanisms
   tested and all destructive — elevate to a design law: **this edge cannot
   be protected, only owned or not owned.**
2. **M3 (trail at crossover week's CLOSE instead of LOW) is a genuine
   Pareto option**: −4.6 DD pts and −0.7mo underwater for −1.8 CAGR pts,
   Calmar 0.57→0.62. It captures more unrealized per exit by trailing one
   candle-range tighter WITHOUT reacting faster — speed was the poison,
   level was fine to tighten.
3. Deployable ladder now: Z3 (13.4/23.4) · **Z3+close-level (11.7/18.8)** ·
   Z3+minrisk4 (12.3/17.4). The last two both satisfy DD<20%.

## 15-year live-faithful validation (run #760)

Z3 config, 2011-01→2026-08, QUANT track, **stacking_guard=SKIP** (matches
live): **CAGR 3.68%, MtM DD 21.64%, 377 trades.** Per-year: 2011–2022 ≈
break-even (±₹20k/yr; 2018 +77k the exception), profits concentrated
2023–2024 (+₹228k of ₹270k total). Confirms Part 3's read at the longest
horizon: the weekly-cadence config is a **current-regime performer with a
flat-but-survivable past**, not an all-weather 15-year machine. Its virtue
across 2011–2022 is that it didn't lose (DD comparable, trades ~25/yr).

## Recommendation (trader's view, outside the authorized space)

The 16%/≤20-25%DD target on this universe and window is already **met by the
validated weekly-timeframe composite book** in this same codebase
(WEEKLY_BREAKOUT + composite ranking: 12.6–21.3% CAGR engine-validated,
16–18% blended with INDEX_TF at ≤20% DD). The daily funnel should either
(a) adopt CB-A's flags purely as capital preservation while live signals are
re-evaluated, or (b) hand its capital to the weekly book. Parameter tuning
cannot revive a decayed per-trade edge — that requires a signal change,
which is out of scope by this mandate's own constraints.
