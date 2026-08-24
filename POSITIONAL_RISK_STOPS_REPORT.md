# Inverse-Vol Sizing + Position-Level Stops — Runs #775–789 (2026-08-18)

15-year horizon (2011–2026), POSITIONAL engine, composite RS ranking,
compounded sizing, 21-session rebalance, 0.32%/leg friction. All runs
engine-executed; metrics are mark-to-market (open positions marked weekly).

## 1. Comparative execution table

| Run | Configuration | CAGR | MtM MaxDD | Calmar | Avg hold | Exits: RANK / TRAIL / CEIL |
|---|---|---|---|---|---|---|
| #771 | baseline: composite RS, N=30, **no stops** | **19.90%** | 49.69% | 0.40 | 106 d | 100 / 0 / 0 |
| #776 | + inverse-vol sizing | **20.64%** | 45.55% | **0.45** | 106 d | 100 / 0 / 0 |
| **#780 (785)** | **+ daily ATR ceiling 5.5%** | 15.24% | **31.13%** | **0.49** | 62 d | 42 / 0 / 58 |
| #781 (786) | + 3.0×ATR trailing stop | 12.96% | 39.17% | 0.33 | 58 d | 30 / 70 / 0 |
| #782 (787) | + 3×ATR trail + sector cap 20% | 11.71% | 37.76% | 0.31 | 56 d | 33 / 67 / 0 |
| #782b (788) | + 2.5×ATR trail (tighter) | 10.42% | 47.17% | 0.22 | — | — |
| #782c (789) | + 3×ATR trail + ATR ceiling 5.5% | 11.83% | 32.68% | 0.36 | 43 d | 16 / 40 / 43 |

Supporting matrix (#775–784, earlier mission): sector cap 20% 19.13/48.90 ·
sector cap 15% 18.36/49.65 · factor-breadth scaling 18.10/50.86 · N=40
19.61/47.06 · N=50 18.19/45.83 · RS-exit 18.38/47.02 · cash buffer 15%
18.40/47.58 · stack(A+B+C+D) 16.87/45.57 · stack minus breadth 18.55/44.17.

## 2. Trade-off evaluation — did stops push Calmar past 0.50?

**No. The best Calmar achieved is 0.49 (#780), and the target was not met.**

- **ATR ceiling (#780) is the only stop that helps risk-adjusted return**:
  DD 45.55% → 31.13% (−14.4 pts, into the requested 25–35% band) for −5.4 CAGR
  pts, lifting Calmar 0.45 → 0.49. It is the single best drawdown mechanism
  found anywhere in this program.
- **The 3×ATR trailing stop actively destroys value**: −7.7 CAGR pts for only
  −6.4 DD pts (Calmar 0.45 → 0.33). It fires on **70% of all exits** and cuts
  average hold from 106 → 58 days, i.e. it systematically amputates the
  long-hold winners the composite ranker exists to find.
- **Tightening the trail to 2.5× makes it worse on BOTH axes** (10.42% CAGR,
  47.17% DD) — the definitive refutation: a tighter stop delivered *more*
  drawdown, because exiting early forces re-entry into the same falling names
  at the next rebalance.
- **Stacking does not rescue it.** #782 (trail + sector cap) = Calmar 0.31.
  #782c (trail + ceiling) = 0.36, still below the ceiling-only 0.49 — the
  trail subtracts from what the ceiling achieves.

**Ranked by Calmar improvement over the #776 base (0.45):**
1. ATR ceiling 5.5% → **0.49** (+0.04) — the only positive
2. trail + ceiling → 0.36 (−0.09)
3. 3×ATR trail → 0.33 (−0.12)
4. trail + sector cap → 0.31 (−0.14)
5. 2.5×ATR trail → 0.22 (−0.23)

## 3. Why: a volatility *filter* works, a volatility *stop* does not

Both mechanisms read the same input (`atr_pct`) and produce opposite results.
The ATR **ceiling** exits a name because it has become structurally unstable —
a state that persists — so the exit is durable. The ATR **trail** exits on a
price give-back that a high-momentum name produces routinely mid-advance; the
strategy's edge lives in 106-day holds, and a rule that halves holding period
removes the tail it is built to capture.

This is now the **ninth** distinct profit-protection/timing mechanism tested
across the whole program (half-booking ×2, breakeven, giveback caps ×3, index
regime ×3, factor breadth, RS-exit, cash buffer, ATR trail ×2) — and, with the
single exception of the ATR ceiling, every one has reduced risk-adjusted
return. The consistent finding: **this edge tolerates position-level
*eligibility* filters, and rejects *exit-timing* rules of every kind.**

## 4. Recommended production configuration

Two defensible points on the frontier — the choice is a genuine risk-appetite
decision, not a performance ranking:

```python
# ---- OPTION A — max risk-adjusted return (recommended)  [run #780/785]
pos_momentum          = "composite_rs"   # z(12-1)+z(6m)+z(3m)+z(6m/atr)
pos_top_n             = 30
pos_buffer_n          = 60               # concentric banding
pos_rebalance_days    = 21
pos_min_turnover_cr   = 8.0
pos_size_mode         = "inverse_vol"    # weight_i = (1/ATR_i)/sum(1/ATR_j)
pos_atr_max_pct       = 5.5              # DAILY ceiling, exit at T+1 open
pos_sl_mode           = "none"           # no trailing stop — measured harmful
compounding_enabled   = True             # profit_only
compounding_max_capital = 20_000_000
slippage_pct = 0.20 ; exit costs = STT/stamp/exchange/DP as configured
# -> 15yr: CAGR 15.24% | MtM MaxDD 31.13% | Calmar 0.49 | avg hold 62d

# ---- OPTION B — max absolute return, wider drawdown  [run #776]
#   identical, but pos_atr_max_pct = None
# -> 15yr: CAGR 20.64% | MtM MaxDD 45.55% | Calmar 0.45 | avg hold 106d
```

**Do not deploy:** any ATR trailing stop (#781/#782/#782b/#782c), factor-breadth
scaling, index-regime timing, RS-exit, or cash buffer — each measured negative.

## 5. Caveats (unchanged and still binding)

1. **Survivorship bias** — the DB contains no delisted symbols and a momentum
   ranker preferentially buys survivors. Every CAGR here is an upper bound;
   a −3 to −6 point haircut is a reasonable prior.
2. **Sector cap is a lower-bound test** — sector data covers only 42% of the
   liquid universe, so the cap binds on fewer than half the names.
3. Not paper-traded. The kill-criteria protocol in `DEPLOYMENT_PROTOCOL.md`
   applies before any capital commitment.
