# Pre-Deployment Structural Findings & Kill-Criteria Protocol (2026-08-17)

Basis: fully audited curves only — WB = run #701 (#14 comp-capped ₹20L, MtM,
0.30% exit slippage, 2% ADV cap, −2.5%/yr survivorship haircut), ITF = run #705
(JUNIORBEES ma200, tradeable). Window 2020-03 → 2026-08 (6.5y, includes the
COVID crash).

---

## 1. Rebalancing policy — fixed CALENDAR MONTHLY wins; dynamic rejected

| Policy | CAGR% | MaxDD% | UW (mo) | Calmar | Recovery Factor |
|---|---|---|---|---|---|
| Drift (never rebalance — prior reported basis) | 14.61 | 21.21 | 21.0 | 0.69 | 4.92 |
| **Calendar monthly 30/70** | **19.62** | **19.57** | 21.6 | **1.00** | **6.62** |
| Calendar quarterly | 15.77 | 21.94 | 22.6 | 0.72 | 5.49 |
| Dynamic D1: WB→0% below own 3m MA | 1.98 | 41.56 | 56.3 | 0.05 | 0.31 |
| Dynamic D1: 6m MA | 2.45 | 38.33 | 56.3 | 0.06 | 0.43 |
| Dynamic D1: 12m MA | 6.29 | 28.31 | 31.0 | 0.22 | 1.20 |
| Dynamic D2: rel-momentum tilt 45/15 | 18.09 | 26.74 | 22.5 | 0.68 | 5.12 |

**Verdict — the question answers itself decisively:**
- **Equity-curve-momentum switching is catastrophic** (D1: 2–6% CAGR, 56 months
  underwater at every lookback tested). This is the *third* time this failure
  mode has appeared (pause-throttle A5, dd-throttle, now D1): any rule that
  cuts the WB book to zero during weakness deletes the recovery trades, and the
  book's returns are too lumpy to time with its own equity curve. Not overfit —
  robustly *bad* across all four lookbacks.
- **Fixed monthly rebalancing is a genuine, parameter-free improvement**:
  +5.0 CAGR pts over drift, lower MaxDD, Calmar 0.69 → 1.00, recovery factor
  4.92 → 6.62. Mechanism is the classic rebalancing premium — ρ≈0 and a large
  volatility differential means monthly resets systematically sell WB spikes
  and buy WB dips against the smooth ETF leg. The frequency response is
  monotone (drift < quarterly < monthly), a plateau not a spike, and "rebalance
  monthly to fixed weights" has zero tuned parameters — minimal overfit risk.
- Mild dynamic tilt (D2) is inferior to fixed monthly on every risk metric.

**Adopted: 30/70 fixed weights, rebalanced on the first trading day of each month.**

## 2. Idle cash yield — verified, material for return, NOT a fix for underwater duration

- **ITF leg:** 6% cash accrual during flat periods is already inside the engine
  (`index_tf_engine.py`: `equity *= (1+cash_daily)` on flat days) — integrated
  in every reported ITF number. Confirmed.
- **WB leg:** engine credits idle cash at 0%. True ledger reconstruction
  (capital + realized − open cost basis) shows the capped-compounding book
  holds a **mean 38.2% of equity in idle cash** (the ₹20L sizing cap means
  equity keeps growing past what the book deploys). Crediting 6%:

| | CAGR% | MaxDD% | UW (mo) | Calmar |
|---|---|---|---|---|
| WB #701 alone, cash @0% | 13.47 | 51.80 | 39.8 | 0.26 |
| WB #701 alone, cash @6% | 14.65 | 45.73 | 39.5 | 0.32 |
| 30/70 monthly blend, cash @0% | 19.62 | 19.57 | 21.6 | 1.00 |
| **30/70 monthly blend, cash @6%** | 18.83 | **17.35** | 21.4 | **1.09** |

**Verdict:** implement the cash sweep in live ops (park uncommitted WB capital
in a liquid fund — ~₹7L mean balance is far too large to leave at 0%). It cuts
blend MaxDD ~2pts and lifts Calmar to 1.09. **But it does NOT meaningfully
shorten the underwater spell (21.6 → 21.4 months)** — the spell is driven by
the WB book's drawdown depth, not by carry. The ~21-month spell must be
accepted as a design parameter of this system.

### Final locked configuration (all conservatisms + monthly rebal + cash sweep)
> **30/70 WB(#14-comp-capped ₹20L) / JUNIORBEES-TF(ma200), monthly rebalance:**
> **≈18.8% CAGR · ≈17.4% true MtM MaxDD · ≈21 months max underwater · Calmar ≈1.09**
> (6.5-year audited window; includes one true crash)

---

## 3. Kill-Criteria Protocol (pre-registered 2026-08-17, before first live trade)

Thresholds derive from the audited blend's own distributions (rolling 12-month:
p5 −6.6%, p1 −9.4%, worst −11.3% · drawdown: p99 19.6%, max 21.9% · monthly:
p1 −14.2%, worst −14.6% · longest underwater 22.6 mo), each with a buffer so a
trigger means "outside anything the backtest ever produced", not "a bad month".

| Level | Trigger (MtM, incl. open positions) | Action |
|---|---|---|
| **L1 – Review** | Any calendar month ≤ **−15%** (worse than backtest's worst −14.6%) | No new WB entries for 1 week; verify data/fills/slippage; resume only after written review |
| **L2 – De-risk** | Rolling 12-month return ≤ **−12%** (backtest worst −11.3%) | Halve WB risk budget (`size_scale=0.5`); restore only when rolling 12m > 0 |
| **L3 – Halt** | Peak-to-trough drawdown ≥ **25%** (backtest max 21.9%) | Stop all new entries both books; existing positions exit per their normal rules; full post-mortem before any restart |
| **L4 – Decommission review** | Underwater > **30 months** (backtest max 22.6) OR equity < 75% of starting capital at any time | Formal strategy review: assume the edge may be gone; restart requires fresh OOS evidence, not hope |
| **S1 – Slippage kill** | Realized exit slippage > **0.60%** (2× model) averaged over any 20 consecutive stop-exits | Halt WB book; recalibrate cost model before resuming |
| **S2 – Correlation kill** | Rolling 12-month WB↔ITF monthly ρ > **+0.5** | The diversification premise is broken; drop to 100% ITF weight pending review |
| **S3 – Reconciliation kill** | Any week where live fills cannot be reconciled to signals (missed/duplicate/unknown orders) | Immediate halt until the discrepancy is explained |

Operating rules: (i) triggers are evaluated on **marked-to-market** equity every
Friday close — the audit showed realized-only equity hides half the risk;
(ii) actions are mandatory and mechanical — the entire point is that the
decision was made today, calmly, not in month 18 of a drawdown; (iii) any
override requires writing down, *before* acting, what evidence justifies it;
(iv) L2/L3 releases are rule-based (stated above), never discretionary.

**Deployment gate recap (from audit):** staged entry at 50% of intended
capital; 3–6 months paper trading with S1 slippage capture running; delisted-
data remediation remains open (haircut stands in for it until then).
