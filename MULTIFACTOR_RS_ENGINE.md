# Multi-Factor RS Momentum Engine — Research Report (2026-08-18)

Target: 18–25% full-cycle CAGR at <22% MaxDD over 2011–2026.
**Result: target met — 22.71% CAGR / 20.15% MtM MaxDD / Calmar 1.13 / Sharpe 0.87.**

Method: vectorized monthly-rebalance backtest over a 281,499-row point-in-time
month-end panel (stock_indicators, 2011-07 → 2026-08). ~350 configurations
tested across 5 phases. Costs 0.32%/leg (0.20% slippage + STT/exchange/stamp),
charged on turnover only. Equity marked monthly = true MtM throughout.

---

## 1. Comparative performance

| Book | 15yr CAGR | 2016–20 | 2021–26 | MaxDD | Calmar | Sharpe | Turnover |
|---|---|---|---|---|---|---|---|
| Old breakout funnel (`screen_gpt.py`, best config) | 3.4–3.7% | ~0% | 13–16% | 17–22% | 0.2 | 0.3 | ~40 tr/yr |
| Equal-weight liquid universe (buy & hold)* | 13.77% | 13.49% | 19.28% | 48.25% | 0.29 | 0.45 | 0 |
| Nifty 50 TRI (public reference) | ~12% | ~11% | ~15% | ~38% | ~0.3 | — | 0 |
| **Multi-factor RS engine (robust config)** | **22.71%** | **22.88%** | **25.80%** | **20.15%** | **1.13** | **0.87** | 57%/mo |

*same survivorship caveat as our strategies — see §5.

**The 2016–2020 stagnation is solved**: 22.88% vs the breakout book's ~0%.
That was the brief's core requirement and it is met by a wide margin.

## 2. Ablation matrix — what each tier actually contributes

| Config | CAGR | MaxDD | Calmar | Read |
|---|---|---|---|---|
| A pure momentum, N=12, no band | 20.25 | 48.62 | 0.42 | momentum alone already 6x the breakout book |
| B + concentric banding (2.0×) | 20.55 | 45.28 | 0.45 | +0.3 CAGR, turnover 96%→70% — free |
| C + fundamental quality filter | 16.41 | 38.81 | 0.42 | **HURTS** (see §5 caveat) |
| **D + regime shield (index>200DMA)** | **24.35** | **38.26** | **0.64** | **biggest single lever: +3.8 CAGR, −7 DD** |
| E + regime: breadth ≥40% | 20.01 | 40.81 | 0.49 | worse than trend |
| F + trend AND breadth | 21.24 | 46.44 | 0.46 | over-filtered |
| G + golden cross (50>200) | 12.20 | 42.57 | 0.29 | far too slow |
| H quality + banding + regime | 11.04 | 33.06 | 0.33 | quality drags the stack down |
| **+ ATR ceiling + N=30 (final)** | **22.71** | **20.15** | **1.13** | **DD nearly halved from D** |

**Lever ranking by drawdown reduction:** ATR ceiling (−18 pts) > regime shield
(−10 pts) > portfolio breadth N=12→30 (−7 pts) > banding (−3 pts).
**By CAGR uplift:** regime shield (+3.8) > banding (+0.3) > everything else ≈ 0.

Rejected levers (measured, negative): fundamental quality filter, breadth
regime, golden-cross regime, volatility targeting (−10 CAGR pts), fast
within-month regime scaling (−6 pts), absolute-momentum filter (≈0 effect —
the ATR ceiling and regime shield already do its job).

## 3. Robust vs. lucky — why the headline is 22.7% and not 26.3%

Naive optimization picks `ATR≤5.0, N=25, turnover≥3`: **26.28% CAGR, Calmar
1.42**. It is a spike, not a peak — its neighbours score Calmar 0.72–0.97, so
a 10% move in one threshold destroys a third of the risk-adjusted return.

Every cell was therefore scored by its **neighbourhood-minimum Calmar**, and
the winner is the best cell whose neighbours all work:

| | Naive best | **Robust choice** |
|---|---|---|
| Config | ATR≤5.0, N=25, TO≥3 | **ATR≤5.5, N=30, TO≥8** |
| CAGR / MaxDD / Calmar | 26.28 / 18.55 / 1.42 | **22.71 / 20.15 / 1.13** |
| Worst neighbour Calmar | 0.79 | **1.00** |

Paying 3.6 CAGR points for a configuration that still works when the world
moves is the correct trade — and it's the discipline that the 60-run breakout
program's failures taught.

### Stress tests on the robust config (all passed)
- **Walk-forward, 5 independent windows:** CAGR 22.5 / 23.7 / 23.9 / 36.4 /
  55.4 — **every window >22%**, Calmar 1.25–3.69. No dead window.
- **Cost shock:** 0.5%/leg → 21.2% · 0.75% → 19.1% · 1.0% (3× modelled) →
  17.1%. Degrades gracefully; still beats the index at triple friction.
- **Execution noise** (10–20% of picks randomly unfillable): 24.9% / 24.2%.
- **Factor ablation:** all four factors contribute; 12-1 momentum is the most
  valuable (−3.6 CAGR if dropped). No single-factor dependence.
- **Per-year:** 3 losing years of 15, worst −15%. Longest underwater 28 months.

## 4. Production configuration

```python
# ---- TIER 1: universe & liquidity (monthly, at month-end close) ----
universe   = active NSE EQ, SME/T2T excluded
liquidity  = turnover_1m_avg_cr >= 8.0        # ~Rs.8cr ADTV
volatility = atr_pct <= 5.5                   # ATR ceiling — the key DD lever
position_cap = 2% of 20-day ADTV              # capacity guard (already in engine)
# quality filter: NOT USED — measured negative, see caveat below

# ---- TIER 2: regime shield (evaluated at each month-end) ----
risk_on = index_level > index_200day_MA       # equal-weight universe index
if not risk_on: liquidate to cash @ 6% p.a.   # ~29% of months historically

# ---- TIER 3: composite RS score (cross-sectional z, equal weights) ----
score = z(mom_12_1) + z(mom_6m) + z(mom_3m) + z(mom_6m / atr_pct)
#   mom_12_1 = (1+pct_chg_1y)/(1+pct_chg_1m) - 1     (skip recent month)
#   z = within-month cross-sectional z-score, clipped +/-3

# ---- TIER 4: portfolio construction ----
N            = 30 equal-weighted positions
banding      = hold until rank drops below N*2 (=60), then replace
rebalance    = monthly (month-end)
stops        = NONE (regime shield + ATR ceiling are the risk control)
```
Expected: ~57%/month turnover, ~29% of months in cash, ~30 positions when
deployed. At Rs.4L capital that is ~Rs.13k per position — comfortably inside
the 2%-of-ADTV cap at the Rs.8cr liquidity floor.

## 5. Honest caveats — read before deploying

1. **Survivorship bias is worse here than for the breakout book.** The DB
   holds zero delisted symbols, and a momentum ranker systematically buys
   past winners — exactly the survivor population. Every figure above is an
   upper bound; a −3 to −6 CAGR-point haircut is a reasonable prior, which
   would still leave ~17–20% net. **This is the single largest open risk and
   it is unresolved**, exactly as flagged in the risk audit.
2. **The quality filter result is not trustworthy.** `earnings_fundamentals`
   ends 2024-12-31, so 2025–26 quality screening runs on stale data — which
   is why config C shows 39% in 2016–20 but 8.6% in 2021–26. The filter may
   genuinely add value with current data; it cannot be judged on this dataset.
3. **No Nifty 500 / TRI benchmark data exists locally** (`index_membership`
   is empty). Benchmarks above use our own equal-weight liquid universe plus
   published Nifty figures. A true TRI comparison needs that data loaded.
4. **This is a backtest, not a track record.** It has not been validated in
   the production engine (POSITIONAL/PORTFOLIO already implement most of
   Tier 3/4 — the natural next step), nor paper-traded.
5. Monthly-close execution is assumed; real fills happen next-open with gaps.

---

# ADDENDUM — Production-engine validation (2026-08-18, runs #765–772)

The composite RS score is now ported into the **POSITIONAL** engine as an
opt-in mode. Strictly additive: `pos_momentum='composite_rs'` plus three
inert-by-default guards (`pos_atr_max_pct`, `pos_regime_ma_days`,
`pos_cash_annual_pct`) and opt-in compounding via the existing shared
`compounding_*` columns. Every prior POSITIONAL run reproduces byte-identically;
`screen_gpt.py` and all live services were untouched.

| Run | Config (POSITIONAL, 15yr, N=30/buffer 60, 21-session rebal, TO>=8) | CAGR | MtM MaxDD | UW |
|---|---|---|---|---|
| 765 | control: single-factor 6m, **fixed sizing** | ~10* | — | — |
| 768 | composite RS, fixed sizing | 11.53 | 35.89 | — |
| 769 | composite + ATR<=5.5 + regime MA200, fixed sizing | 10.17 | 35.19 | — |
| 770 | control: single-factor 6m, **compounded** | 16.75 | 47.53 | 34 mo |
| **771** | **composite RS, compounded** | **19.90** | 49.69 | 32 mo |
| 772 | composite + ATR + regime, compounded | 17.27 | 39.50 | 34 mo |

\*765 predates the path-stats fix for this engine.

**The composite score is confirmed as a real improvement in the production
engine: 16.75% -> 19.90% CAGR (+3.15 pts) against the identical single-factor
control, same sizing, same everything else.** That is the port's core claim and
it holds.

**But the engine does NOT reproduce the harness's 22.71% / 20.15%.** Two
reconciled causes, both real:
1. **Fixed vs compounded sizing** (the big one). POSITIONAL historically sized
   every position at `capital/top_n` forever, so 15 years of ~20%/yr returns
   compound into nothing — linear growth ~= 11% CAGR. Enabling compounding
   moves 11.53% -> 19.90%, closing most of the gap. Now opt-in.
2. **Drawdown is far worse in-engine (49.7% vs 20.2%)**, because the engine's
   ATR/regime guards operate differently from the harness: the regime shield
   only acts on rebalance days (every 21 sessions) and a liquidated slot stays
   in cash until the *next* rebalance, so exits lag the harness's month-end
   evaluation. Run 772 shows the guards do work directionally (49.7 -> 39.5 DD)
   but nowhere near the harness's 20%.

**Honest conclusion:** the *factor* result replicates; the *risk-control*
result does not. The harness's 20% MaxDD is an artifact of idealised
month-end regime execution that the production engine cannot currently match.
Deployable claim today is therefore **~20% CAGR at ~40-50% MaxDD**, not
22.7% at 20% — a good return at institutionally unacceptable drawdown.

---

# ADDENDUM 2 — Daily Asynchronous Risk Guards (2026-08-18, runs #773–774)

## Code refactor delivered (`positional_engine.py`, additive/opt-in)

Hybrid cadence implemented exactly as specified:
- **21-session loop (unchanged):** universe filter, composite RS ranking,
  concentric banding (top 30 / buffer 60), equal-weight construction.
- **Daily close loop (new):**
  - `regime_on[day]` evaluated EVERY session. On a risk-on -> risk-off edge the
    whole book is liquidated at **day T+1 open** (`REGIME_OFF`), entries frozen
    while risk-off, and on the risk-off -> risk-on edge a **forced rebalance**
    fires immediately without waiting for the 21-session timer.
  - Daily `atr_pct` per held name; breach of `pos_atr_max_pct` liquidates that
    position at **T+1 open** (`ATR_CEILING`), freed capital held in cash.
  - Idle cash accrues `pos_cash_annual_pct` daily on (equity − cost basis).
  - Compounding sizes off `capital + realized + cash_credit` at each rebalance.
- All fills are next-open, never same-bar close. The old rebalance-day-only
  regime check (the 19-session lag) is removed.

## Comparative results — did the daily shield close the gap?

| | #772 21-session regime | #773 daily regime | #774 daily regime + daily ATR |
|---|---|---|---|
| 15yr CAGR | 17.27% | 15.70% | 12.32% |
| **MtM MaxDD** | **39.50%** | **44.43%** | **30.63%** |
| Calmar | 0.44 | 0.35 | 0.40 |
| Longest underwater | 34.2 mo | 34.5 mo | 34.2 mo |
| Harness target | — | — | 20.15% DD |

**Validation verdict: NO — the daily asynchronous regime shield does not close
the drawdown gap.** Daily regime alone made drawdown *worse* (39.5% → 44.4%)
while costing 1.6 CAGR points. Adding the daily ATR ceiling did cut drawdown
meaningfully (→ 30.6%) but cost a further 3.4 CAGR points, and still lands
10+ points above the harness's 20%.

## Why — the hypothesis was wrong, and the trade log says so

The root-cause analysis assumed the drawdown was *rebalance lag*. It is not.
Exit-reason forensics on #773: `REGIME_OFF` fired **1,314 times** for a net
+₹0.44L, against `RANK_DROP`'s +₹24.7L. The regime signal whipsaws — it exits
and re-enters constantly, each round trip paying friction, and re-entry is
often at a higher price. Reacting *faster* to a noisy signal amplifies the
noise; it does not reduce risk.

More fundamentally: **the drawdown is not regime-driven at all.** Both books
lose in 2025 (−₹4.6L / −₹6.3L) — a momentum-factor drawdown that happens
while the index is still above its 200-DMA, which no index-level shield can
see. The ATR ceiling helps precisely because it acts on *position* volatility
rather than *index* trend, which is the actual risk being taken.

**The harness's 20% MaxDD is therefore not reproducible in a next-open,
transaction-costed engine.** It came from idealised month-end evaluation with
monthly-granularity marking, which understates both whipsaw frequency and
intra-month damage. The honest production frontier for this strategy is:

| Deployable option | CAGR | MtM MaxDD | Calmar |
|---|---|---|---|
| #771 composite, compounded, no guards | **19.90%** | 49.69% | 0.40 |
| #772 + 21-session regime | 17.27% | 39.50% | 0.44 |
| **#774 + daily regime + daily ATR** | 12.32% | **30.63%** | 0.40 |

Calmar is ~0.40 across the entire frontier: the guards trade CAGR for
drawdown at roughly 1:1 and add no risk-adjusted value. **The composite RS
factor is the real, replicated finding (+3.15 CAGR pts, run #770 → #771);
the risk-control layer is not.**

## 6. Recommended next steps

1. Port the composite RS score into the existing **POSITIONAL** engine
   (`pos_momentum` currently accepts one metric — needs the 4-factor
   composite) and re-run there for engine-truth validation.
2. Load delisted-symbol history and re-measure — the one change that would
   convert this from "promising" to "trustworthy".
3. Paper-trade 3–6 months under the existing kill-criteria protocol before
   any capital move.
