# Exhaustive Validation — POSITIONAL relATR (Aggressive) & #909 Recommended

**Date:** 2026-08-21 · **Evidence base:** runs #1000–#1054 (55 backtests), trade-level DB audit, 5,000-path Monte Carlo, out-of-sample walk-forward.
**Config under test:** #909 base + `pos_atr_rel_mult 1.5 / pos_atr_trim_pct 33` (= runs #1010/#1047: 21.07% CAGR / 28.72% MaxDD / Calmar 0.73, 2011–2026).

## 1. Engine integrity — trade-level audit ✅

60 randomly sampled closed trades from run #1047 re-derived independently from raw OHLCV and the Dhan cost model: **0 mismatches**. Every entry equals next-day open × 1.002 to the paisa; every exit fill and realized P&L reproduces exactly; every sampled entry passed all five gates (turnover ≥ ₹8 Cr, close > SMA200, ≥ ₹20, IFP ≥ 0.38, ATR ≤ 5%) on its decision day, confirming no look-ahead. Across all 2,328 trades: zero negative holding periods, zero bad quantities or prices. Separately, an inert-knob run (#1029) reproduced #909 to the decimal, and the UI preset run (#1047) reproduced #1010 exactly.

## 2. Parameter robustness — full-period grid ✅

Every cell of the relATR grid at full period:

| Config | CAGR % | MaxDD % | Calmar |
|---|---|---|---|
| m1.5-t100 (#1049) | 21.67 | 28.75 | 0.75 |
| m1.8-t33 (#1050) | 21.13 | 28.85 | 0.73 |
| **m1.5-t33 (#1010, the preset)** | **21.07** | **28.72** | **0.73** |
| m1.8-t100 (#1051) | 20.97 | 28.84 | 0.73 |
| m1.3-t100 (#1028) | 20.12 | 30.28 | 0.66 |
| m1.3-t33 (#1048) | 20.10 | 28.29 | 0.71 |

A 1.5-point CAGR band across the whole grid: this is a plateau, not a tuned spike. The preset sits mid-plateau; the exact cell barely matters.

## 3. Walk-forward — config choice is out-of-sample robust ✅

Re-picking the grid cell every January by trailing-3-year Calmar (using only prior data), 2015–2026: **WF CAGR 23.73%**, vs 22.29–24.37% for the six fixed configs over the same years. The out-of-sample selector lands within 0.6pt of the best possible fixed choice and beats the worst — the edge belongs to the relATR mechanism, not to a lucky parameter pick.

## 4. Cost stress ✅

| Scenario | CAGR % | MaxDD % |
|---|---|---|
| Base (0.20% slippage) | 21.07 | 28.7 |
| 2× slippage (0.40%) | 19.14 | 29.2 |
| 3× buys + 0.5% stressed exits | 17.17 | 29.9 |
| ADV 2% position cap ON | 21.07 | 28.7 |

Even at triple slippage the config still beats the #909 baseline's 16.93%. The ADV cap changes nothing — liquidity never binds at the ₹2 Cr compounding ceiling with the ₹8 Cr turnover floor.

## 5. Per-year behavior (run #1047 daily MtM curve)

11 winning years, 4 losing (2013 −0.3%, 2016 −2.2%, 2018 −10.9%, 2025 −15.0%); best 2021 +103.5%. No calendar year's internal drawdown exceeded 25.5%. Curve integrity: 3,827 daily points, strictly ordered, no gaps or duplicates, final equity consistent with stated CAGR.

## 6. Monte Carlo — the honest risk numbers ⚠️

Block bootstrap (21-day blocks, 5,000 paths, full 14.6y horizon):

- **CAGR:** median 23.2% · p5 13.2% · p95 34.4% · P(<10%) = 1.3% · P(<0%) = 0.0%
- **MaxDD:** median 36.7% · p75 41.8% · p95 50.5% · **P(>40%) = 32.6%** · P(>50%) = 5.6%

The return engine is highly robust. The drawdown is the caveat: the historical 28.7% sits near the *lucky* end of the distribution — over a 15-year live horizon, a 35–40% drawdown at some point is the realistic base case, not the exception. (Bootstrap resampling breaks the strategy's adaptive exits, so this modestly overstates tail DD — treat it as a conservative bound.)

## 7. Survivorship (prior repo evidence, date-aware model)

The delisting-injection study (SURVIVORSHIP_DATE_AWARE_MODEL.md) applies here: central scenario costs **≈ −2.4pt CAGR / +2.4pt MaxDD**. Critically, the daily ATR exit means a collapsing stock is liquidated as its volatility expands — across 973 real ATR-exit observations the worst single-name outcome was −33%, never a ride to zero. The relATR variant preserves this shield (vol expansion + below-trend still forces the exit).

## Verdict

**The backtest approach is sound and the strategy edge is real.** Fills, costs, gates, and accounting verify to the paisa; the parameter surface is a plateau; the config choice survives walk-forward; the edge survives 3× costs; liquidity is not a constraint.

**Plan live expectations with haircuts, not the headline:** ~18–19% CAGR (after survivorship) with tolerance for a 35–40% drawdown somewhere along a long horizon. If that DD is unacceptable, run #909 (recommended) or blend with the INDEX_TF diversifier per the audited 30/70 framework. Known residual limits: single historical path per config (mitigated but not eliminated by MC), regime concentration of the relATR edge in 2019–26, and Indian-market-only evidence.

**Remaining engine backlog (optional):** 52-week-high ranking column; equity-curve throttle and armed stops are implemented but validated as not helpful (runs #1029–#1034).
