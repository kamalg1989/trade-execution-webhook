# Quant Research — Compounding, Risk Throttling & Multi-Strategy (2026-08-17)

All figures are **production-engine** 15-year backtests (2011-08-17 → 2026-08-16),
₹4L starting capital, composite ranking (`weekly_rank_mode='composite'`).
Research-harness numbers are excluded; where the harness and engine disagreed,
the engine is treated as truth.

## Code shipped

| Component | File | Config |
|---|---|---|
| INDEX_TF strategy engine | `backtest/index_tf_engine.py` | `strategy='INDEX_TF'`, `itf_proxy`, `itf_ma_days`, `itf_capital_pct`, `itf_cash_annual_pct` |
| Equity-curve circuit breaker | `backtest/weekly_engine.py` | `weekly_equity_throttle_mode` = `none｜dd_peak｜equity_ma｜both`, `_dd_pct`, `_cut`, `weekly_equity_ma_weeks` |
| Size-scale hook | `backtest/position_sizing.py` | `size_position(..., size_scale=)` — scales risk budgets, never available cash |
| Index proxy builder | `index_tf_engine.build_index_proxies()` | table `index_proxy_daily`; `SYNTH_EQW` (15yr) + NIFTYBEES/JUNIORBEES/SETFNIF50 (2019+) |

Presets **#14** (`WB-Composite-Conservative`, run #679) and **#15**
(`WB-Composite-Aggressive`, run #680) are frozen in the strategy library.

> Note: in this engine, "dynamic equity-based position sizing" and
> "fixed-fractional risk sizing" are the **same mechanism** —
> `compounding_enabled=true` makes both `weekly_risk_pct` and
> `max_capital_per_trade_pct` scale off current equity. They are not two
> independent tests.

---

## Deliverable 1 — Compounding & throttling vs static (preset #14 config)

picks 3, biweekly, risk 1.0%, composite ranking.

| # | Configuration | Run | CAGR% | MaxDD% | DD duration | Calmar | Sharpe | Sortino | Final equity |
|---|---|---|---|---|---|---|---|---|---|
| A1 | static sizing (baseline) | 687 | 12.59 | **15.91** | 38.6 mo | 0.79 | 0.53 | 2.49 | ₹23.3L |
| A2 | + compounding | 691 | **21.83** | 36.74 | 40.0 mo | 0.59 | **0.67** | **3.04** | **₹75.1L** |
| A3 | + compounding + DD≥10% throttle ×0.5 | 692 | 19.23 | 31.30 | 45.5 mo | 0.61 | 0.63 | 2.60 | ₹54.6L |
| A4 | + compounding + equity<4wk MA throttle ×0.5 | 693 | 21.12 | 34.02 | 40.0 mo | 0.62 | 0.66 | 3.00 | ₹69.3L |
| A5 | + compounding + both, **pause** entries | 694 | 13.63 | 16.25 | **65.8 mo** | **0.84** | 0.41 | 1.55 | ₹14.2L |

**Findings**

1. **Compounding nearly doubles CAGR (12.59 → 21.83%)** and turns ₹23.3L into
   ₹75.1L — but **MaxDD more than doubles (15.9 → 36.7%)** and Calmar *falls*
   (0.79 → 0.59). It is a move along the risk curve, not a free improvement.
2. **Throttling works, mildly.** The MA throttle (A4) is the better of the two:
   it keeps 21.12% CAGR while cutting MaxDD 2.7pts. The DD throttle (A3) gives
   up 2.6pts of CAGR for 5.4pts of DD. Both improve Calmar only marginally
   (0.59 → 0.61/0.62).
3. **Full pause (A5) is a trap.** It produces the best Calmar (0.84) and lowest
   DD, but the *worst* final equity of the compounded set (₹14.2L — below even
   static) and stretches drawdown duration to **65.8 months**. Cutting exposure
   to zero during weakness removes the recovery trades, so the account stays
   underwater far longer. Not recommended.
4. **Drawdown *duration* is the under-reported risk.** Every variant sits
   38–66 months underwater at some point. This was never measured before and
   matters more to a live account than depth does.

Preset #15 (picks 8, weekly) matrix was still executing at time of writing —
runs 695–699, queued and running autonomously.

---

## Deliverable 2 — Capacity / liquidity report

Metric: position value as % of the stock's ADV (`turnover_1m_avg_cr`).
Thresholds: ≤1% trivially fillable · 1–5% mild · 5–10% material · **>10% the
0.10% slippage assumption is not credible**.

| Book | Median position | Max position | Median %ADV | p99 %ADV | Max %ADV | ≤1% safe | **>10% breached** |
|---|---|---|---|---|---|---|---|
| #14 **static** (687) | ₹27,748 | ₹1.00L | 0.065% | 2.96% | 5.82% | 91.0% | **0** |
| #14 **compounded** (691) | ₹1,15,736 | ₹16.36L | 0.301% | **19.28%** | **49.58%** | 73.5% | **20** |

**Verdict: compounding DOES breach liquidity constraints.** 20 trades exceed
10% of ADV (max 49.6%), plus 29 more in the 5–10% band. Breaches concentrate in
the later, larger-equity years (7 in 2025 alone), with median position size
reaching ₹5.7L.

This is a direct consequence of the composite ranking's strongest factor being
**low turnover** — the edge lives in names that structurally cannot absorb size.
So the 21.83% compounded CAGR is **not fully harvestable**: the last few years
of it assume fills that would not be obtainable. Static sizing is clean at every
point in the 15 years.

Practical implication: compounding is safe up to roughly the ₹1–1.5L
position-size range (≈₹15–20L equity at 3 concurrent positions); beyond that
either add a hard `min turnover` floor to the ranking, cap
`max_capital_per_trade_pct` in absolute rupees, or route incremental capital to
INDEX_TF (which has no capacity limit at this size).

---

## Deliverable 3 — Multi-strategy: WB-composite + INDEX_TF

### INDEX_TF engine validation (15yr, SYNTH_EQW proxy, long/flat, 6% cash)

| Variant | CAGR% | MaxDD% | DD dur | Calmar | Sharpe | Sortino | Trades |
|---|---|---|---|---|---|---|---|
| ma200 static | 10.93 | 11.95 | 45.1 mo | 0.91 | 0.25 | 1.59 | 45 |
| ma200 compounded | 18.68 | 26.38 | 45.1 mo | 0.71 | 0.37 | 2.88 | 45 |
| **ma150 compounded** | **19.17** | 20.09 | 46.9 mo | **0.95** | 0.39 | **4.48** | 50 |
| NIFTYBEES ma200 comp (2019+, 6.4yr) | 10.26 | 5.33 | 31.1 mo | 1.93 | 0.27 | 1.59 | 22 |

The earlier prototype's 21.10% was **not reproduced** — the engine gives
10.93% static / 18.68% compounded for ma200. The prototype implicitly compounded
at 100% deployment with zero costs; the engine deploys 95% and pays real costs.
The compounded engine figure is the honest one.

**Walk-forward, INDEX_TF ma200 vs WB #679 — 0 of 7 independent 2-year slices had
both books losing.** WB's two negative slices (2018-19, and weakness in 2012-13)
were INDEX_TF's +8.2% and the reverse. Measured monthly correlation **−0.04**.
The diversification is real and structural, not a sample artifact.

### Drawdown-targeted frontier (WB + INDEX_TF, weight and exposure optimised)

Reference = static preset #14: **14.50% CAGR at 14.32% MaxDD** on the common
window, Calmar 1.01.

Capped at exposure ≤1.0 — i.e. **no worsening of the capacity picture**:

| DD budget | Base book | ITF weight | Exposure | CAGR% | MaxDD% | Calmar | Sharpe |
|---|---|---|---|---|---|---|---|
| **15.91%** | WB static | **40%** | 1.00 | **16.56** | 15.84 | **1.05** | 0.64 |
| 20.00% | WB static | 70% | 1.00 | 17.84 | 17.75 | 1.01 | 0.46 |
| 25.00% | WB compounded | 70% | 0.91 | 19.18 | 25.00 | 0.77 | 0.63 |
| 30.00% | WB compounded | 50% | 0.99 | 21.90 | 30.00 | 0.73 | 0.78 |

With modest leverage allowed (1.13×): **17.00% CAGR at exactly 15.91% MaxDD,
Calmar 1.07, Sharpe 0.79, Sortino 4.39.**

---

## Bottom line

- **>18% CAGR at unchanged ~16% drawdown is not reachable.** The honest ceiling
  at that risk budget is **16.5–17.0%** (from 12.6% static WB alone) — a large
  improvement, but short of 18%.
- **18%+ requires accepting ~18–20% drawdown**, which is a small, well-defined
  concession: 17.84% at 17.75% DD with no leverage and no capacity breach.
- **Compounding gets to 21.8%, but breaches liquidity** and doubles drawdown.
  Use it only up to ~₹15–20L equity, then divert new capital to INDEX_TF.
- **Recommended configuration:** preset #14 static + 40% INDEX_TF (ma150),
  no throttle, no leverage → **16.6% CAGR, 15.8% MaxDD, Calmar 1.05**, all
  capacity-clean. Step to 70% ITF for 17.8% if a 17.75% DD is acceptable.
- **Reject:** entry pausing (A5 — worst equity, 66-month underwater), and the
  DD throttle as a CAGR-preserving control (A3 costs more CAGR than the DD it saves).
