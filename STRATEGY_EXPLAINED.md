# The Strategy, End to End (config #823)

A long-only momentum book of **30 NSE mid/small-caps**, rebalanced every **21
trading sessions**. It buys what is already going up, holds until it stops being
one of the best names available, and uses **no stop loss**.

---

## 1. Universe → gates → candidates

Start with all NSE equity-series stocks (~2,300; SME/T2T excluded). Five gates,
**all must pass**, re-evaluated at *every* rebalance:

| gate | value | why |
|---|---|---|
| `ifp_score` | **≥ 0.40** | institutional sponsorship. The single biggest contributor — removing it costs 0.12 Calmar in every window tested |
| `close > sma_200` | — | only names in a primary uptrend |
| `atr_pct` | **≤ 5.0%** | excludes the wildest names; the largest single drawdown reducer found |
| `turnover_1m_avg_cr` | **≥ ₹8 cr** | tradability floor. **Do not raise** — 15/25/50cr made it monotonically worse (illiquidity premium is real) |
| `close` | **≥ ₹20** | removes the crash-prone penny tail |

~350 candidates survive on a typical day.

**Key subtlety:** because the gates are re-checked every rebalance, a holding
whose IFP decays below 0.40 drops out of the ranked set and is sold. The gate is
therefore an *entry filter and a continuous exit rule at once* — and that
continuous re-check is where the edge is (0.94 vs 0.80 Calmar when tested as
entry-only in the harness).

## 2. Ranking — a 5-factor composite

Each surviving candidate gets a cross-sectional z-score on five factors, summed
with equal weight:

```
score = z(12-1m return)      classic momentum, skipping the last month
      + z(6m return)
      + z(3m return)
      + z(6m return ÷ ATR%)  volatility-adjusted momentum
      + z(−base_range_20d)   base tightness, INVERTED (tighter = better)
```

Two things that matter:

- **z-scores are computed within that day's candidate set only** — never against
  full history, which would leak the future.
- **Base tightness works as a score, not a gate** — as a hard filter (`≤20%`,
  the old production setting) it collapses Calmar to 0.47. **But it is
  window-dependent**: removing it costs 0.26 Calmar on 2017-26 and *gains* 0.03
  on the out-of-sample 2011-2016 window. Keep it, but it is not part of the
  strategy's confirmed core. Only the IFP gate replicates in every window
  (−0.10 to −0.16 across four, including one disjoint).

## 3. Building the book

| step | rule |
|---|---|
| **hold** | top 30 by score |
| **size** | inverse volatility: `weight ∝ 1/ATR`, normalised across 30 slots |
| **exit** | when a holding's rank falls to **60 or worse** (concentric band) |
| **stop loss** | **none** |
| **cadence** | every 21 sessions |
| **execution** | rank on day D, fill at **D+1 open** |
| **cost** | 0.32% per leg (0.20 slippage + STT/exchange/stamp) |
| **compounding** | on, profit-only, ceiling ₹2 cr |

The **band** is the important bit: a name is bought only if it reaches the top 30,
but held until it drops past 60. That hysteresis is what keeps turnover near 83%
instead of thrashing on rank noise.

## 4. Why no stop loss

Roughly **eleven** exit-timing mechanisms were tested and all destroyed value:
ATR trailing stops, MACD trails, half-booking at +2R, breakeven stops, giveback
caps, index regime shields (3 variants), factor-breadth timing, relative-strength
exits, cash buffers. The consistent pattern across this whole programme:

> **This edge tolerates eligibility and ranking changes. It rejects exit rules.**

Volatility targeting (Barroso) is the one exception worth knowing about — it is
Calmar-neutral at an 18–20% target while cutting drawdown ~1.4 points. Available
as a dial (`pos_vol_target_pct`), not enabled.

## 5. What it actually returns

| window | CAGR | MaxDD | Calmar |
|---|---|---|---|
| 2011-2026 (contaminated data) | 14.90% | 24.05% | 0.62 |
| **2017-2026 (reference)** | **22.16%** | **19.74%** | **1.12** |
| 2020-2026 | 22.81% | 21.02% | 1.09 |

**Read these net of survivorship bias.** The database contains 0 of 269
wipeout delistings, worth an estimated **3–7 CAGR points**. So realistic forward
expectation is **~16–19%**.

Against benchmarks over 2020-2026:

| | CAGR |
|---|---|
| strategy (raw) | 22.81% |
| strategy (survivorship-adjusted) | ~16–19% |
| Nifty Next 50 ETF | 16.53% |
| Nifty 50 ETF | 12.27% |

**Clearly ahead of Nifty 50; roughly level with Nifty Next 50.** Since this is a
mid/small-cap book, Next 50 is the fairer comparison — beating a large-cap index
with a small-cap book is partly beta, not alpha.

## 6. Known weaknesses

1. **Survivorship** — the largest unquantified error. Needs paid NSE EOD data to fix.
2. **`stock_indicators` vs `ohlcv_data` price mismatch** — 4.4% of selected
   positions, concentrated in 2011-2015. Costs ~0.04 Calmar; distorts sub-period
   attribution badly. Not fixed (2.1 GB Timescale hypertable, risk > reward).
3. **IFP 0.40 vs 0.38 is provisional** — the two windows disagree; a threshold
   sweep is running to settle it.
4. **Turnover ~83%/month** — costs matter. Still profitable at 2× modelled friction.

## 7. Where it runs

Currently **paper only** — not wired into live execution. `paper_trading/`
tracks it forward under pre-registered kill criteria; the live `screen_gpt.py`
daily-breakout system is unchanged and untouched.
