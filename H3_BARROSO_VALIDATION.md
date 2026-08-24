# H3 Validation — Barroso Volatility Targeting, Done Properly (runs #809–816)
**2026-08-19 · VERDICT: does not beat the incumbent on Calmar, but it is the
first clean drawdown dial this programme has produced.**

Two separate findings, and they should not be conflated:

1. **My earlier test of Barroso was methodologically wrong, and correcting it
   recovered +0.15 Calmar.** The criticism I made of my own work was right.
2. **Even done correctly, it does not improve risk-adjusted return.** Best
   engine Calmar is 0.63 vs 0.62 incumbent — inside noise.

---

## 1. THE ESTIMATOR FIX WAS REAL (harness, isolated)

Same target, same everything — only the variance estimator differs:

| σ_target | OLD: 6 monthly obs | **NEW: 126 daily obs** | gain |
|---|---|---|---|
| 14% | 0.67 | **0.84** | +0.17 |
| 18% | 0.77 | **0.92** | +0.15 |
| 22% | 0.84 | **0.92** | +0.08 |
| 25% | 0.89 | **0.92** | +0.03 |

The earlier failure (Calmar 0.67 → 0.46–0.59) was **substantially my
implementation, not the method.** A variance estimate on 6 degrees of freedom
carries ~30% standard error; scaling exposure by that is scaling by noise. This
is worth recording as a methodology lesson: *the earlier "Barroso fails on our
data" conclusion was not safe to draw, and it stood unchallenged for a day.*

**Leverage is now cleanly refuted too.** The old 1.5× test confounded three
defects at once (bad estimator + 16% target + leverage) and produced the
programme's worst result (Calmar 0.30). Isolated with the correct estimator:

| max_lev | 1.0 | 1.1 | 1.2 | 1.3 |
|---|---|---|---|---|
| Calmar (target 18%) | **0.92** | 0.84 | 0.78 | 0.77 |

Monotone harm. Leverage adds drawdown faster than return. **Closed properly.**

---

## 2. ENGINE RESULTS (15yr, MtM, real fills)

Baseline for comparison is **#810** (incumbent + 6% cash yield, which vol
targeting needs since it parks capital — comparing against a no-yield baseline
would be unfairly harsh).

| Run | σ_target | CAGR | MtM MaxDD | Calmar | CAGR cost per DD pt saved |
|---|---|---|---|---|---|
| #809 | — (inertness) | 14.90 | 24.05 | 0.62 | — |
| #810 | — + cash yield | 15.49 | 24.83 | 0.62 | — |
| #815 | 20% | 15.18 | 24.27 | **0.63** | 0.55 |
| #814 | 18% | 14.57 | 23.46 | 0.62 | 0.67 |
| #813 | 16% | 13.75 | 23.04 | 0.60 | 0.97 |
| #812 | 14% | 12.52 | 21.70 | 0.58 | 0.95 |
| #811 | 12% | 11.08 | **20.38** | 0.54 | 0.99 |

Perfectly monotone in both axes, no cliff, converging on the baseline as the
constraint stops binding. That is a well-behaved mechanism — unlike H1
(inert then destructive) or H4 (non-monotone).

**Estimator lookback is robust** — the result does not depend on the window:

| run | σ_target | lookback | CAGR | MaxDD | Calmar |
|---|---|---|---|---|---|
| #817 | 12% | 63d | 10.88 | 20.11 | 0.54 |
| #811 | 12% | **126d** | 11.08 | 20.38 | 0.54 |
| #812 | 14% | **126d** | 12.52 | 21.70 | 0.58 |
| #816 | 14% | 252d | 12.77 | 22.02 | 0.58 |

Identical Calmar at each target across a 4× range of lookback. So the estimator
is no longer the binding constraint — which is precisely what distinguishes this
test from the earlier flawed one, and why the negative result can be trusted
this time.

### The exchange rate explains everything

Calmar = C/D improves only if the CAGR given up per drawdown point saved is
**below the current Calmar itself**:

```
(C-c)/(D-d) > C/D   ⟺   c/d < C/D = 0.62
```

The measured exchange rate is ~0.95–0.99 at tight targets and only drops below
0.62 at target 20%, where the constraint barely binds. **Vol targeting trades
drawdown for CAGR at roughly 1:1 on this book, and 1:1 is a bad trade when the
Calmar is 0.62.** That is the whole result in one line, and it is a structural
property of the strategy, not a tuning failure.

### My cost-interaction prediction was wrong

The harness showed vol targeting's relative value *rising* with friction (at
0.75%/leg it beat unscaled: 0.68 vs 0.62), and I predicted the engine's harsher
per-name fills would therefore favour it. **They did not.** The engine's extra
cost is concentrated in per-name entry/exit, not in the aggregate turnover the
harness models, and vol targeting reduces *position size*, not *trade count* —
so it never harvests the saving. Worth remembering: "harsher friction" is not
one dial, and which kind of friction matters.

---

## 3. WHAT IS ACTUALLY ON OFFER

H3 fails the pre-registered Calmar bar. But it is the only mechanism tested in
this entire programme that reduces drawdown *without* also reducing Calmar:

| option | CAGR | MaxDD | Calmar | vs incumbent |
|---|---|---|---|---|
| incumbent (#810) | 15.49 | 24.83 | 0.62 | — |
| **σ_target 20%** | 15.18 | 24.27 | 0.63 | −0.31 CAGR, **−0.56 DD**, Calmar + |
| **σ_target 18%** | 14.57 | 23.46 | 0.62 | −0.92 CAGR, **−1.37 DD**, Calmar = |
| σ_target 12% | 11.08 | 20.38 | 0.54 | −4.41 CAGR, **−4.45 DD**, Calmar − |

For context, the dedicated drawdown-reduction programme (sector caps, breadth
scaling, inverse-vol, N expansion, regime shields, ATR stops) never achieved a
Calmar-neutral drawdown reduction at all — every mechanism cost Calmar. Two
cells here do not.

**This is a preference question, not a research question**, so I am not deciding
it: 18% buys 1.4 drawdown points for 0.9 CAGR points at unchanged Calmar. If
drawdown tolerance is the binding constraint on position sizing in live
trading, that is worth having. If CAGR is the objective, it is not.

---

## 4. RECOMMENDATION

1. **Do not adopt as a Calmar improvement.** +0.01 is noise; keep #799/#810 as
   the production candidate.
2. **Consider σ_target 18–20% as an optional risk dial** if lower drawdown has
   standalone value. Fully implemented and inert by default, so this costs
   nothing to leave available.
3. **Correct the record on Barroso.** The earlier report's "FAILS on our data"
   verdict was driven by my 6-observation estimator. The method works as
   advertised; it simply does not pay on a book whose Calmar is already 0.62.
4. **Methodology, restated:** three hypotheses (H1, H4, H3) all passed harness
   screening and all failed engine validation. The harness has now mispriced
   *eight* consecutive findings in the same direction. Harness results should
   be treated as hypothesis generation only and never recorded as results.

## 5. WHAT WAS BUILT (additive, inert, BAU untouched)

- `positional_engine.py`: `pos_vol_target_pct` (None = inert),
  `pos_vol_lb_days` (126), `pos_vol_max_lev` (1.0). Maintains a rolling daily
  return series of the held book; `warm_series` now also warms when vol
  targeting is on but no stop is configured. Exposure multiplies into the
  existing `exposure` variable, so it composes with breadth scaling / cash
  buffer rather than fighting them.
- `backtest.py` router: three config fields + INSERT wiring.
- `backtest_runs`: `pos_vol_target_pct`, `pos_vol_lb_days`, `pos_vol_max_lev`.
- **Inertness verified twice** — #801 and #809 both reproduce #799 exactly
  (14.90 / 24.05 / 0.62) after each round of edits.

## Caveats

- Survivorship bias unchanged; CAGR figures are upper bounds.
- Vol estimate needs ≥60 daily observations, so the first ~3 months of any run
  are unscaled. Immaterial over 15 years, relevant for a short backtest.
- `pos_vol_max_lev > 1.0` is permitted by the schema but measured harmful; it
  also lets `base_capital` exceed running equity, which the engine does not
  otherwise guard. Do not use it.
