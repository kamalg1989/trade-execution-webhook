# Phase 2 — Composite Factor Architecture: RESULTS
**2026-08-20 · runs #968–999 · 16 candidates, 32 runs, both disjoint windows**

## VERDICT: no change. The equal-weighted 5-factor composite stands.

Reference: **2017-26 Calmar 1.16 · 2011-16 Calmar 0.57**
(inertness verified — #988/#989 reproduce both exactly)

---

## Full results

| candidate | 2017-26 | 2011-16 | both? |
|---|---|---|---|
| **REF — current composite** | **1.16** | **0.57** | — |
| **C4 · add trend_IR @ w=0.5** | **1.17** | **0.58** | ✅ **+0.01 / +0.01** |
| C5 · add trend_IR 63d @ w=1.0 | **1.29** | 0.56 | ✗ |
| A3 · halve mom6 | 1.23 | 0.52 | ✗ |
| C4 (see above) | | | |
| A1 · drop mom_vadj | 1.13 | 0.47 | ✗ |
| A2 · vol-adjust mom12 not mom6 | 1.08 | 0.48 | ✗ |
| C3 · add trend_IR @ w=1.0 | 1.07 | 0.52 | ✗ |
| B1 · IC-proportional weights | 1.06 | 0.54 | ✗ |
| B2 · lean 3-factor | 1.03 | 0.57 | ✗ |
| C1 · trend_IR replaces neg_base | 1.02 | **0.63** | ✗ |
| A4 · drop mom3 | 1.01 | 0.58 | ✗ |
| C2 · trend_IR replaces mom3 | 0.94 | 0.58 | ✗ |
| A2B4 · combo | 0.92 | 0.51 | ✗ |
| B3 · mom12 weight 2.0 | 0.91 | 0.50 | ✗ |
| B4 · drop neg_base | 0.91 | 0.58 | ✗ |

**One candidate clears both windows — C4, by +0.01 and +0.01.**

That is noise. For comparison, the persist-2 exit change (adopted) delivered
+0.15 / +0.06. C4 would require a new feature table, two engine parameters, and
a daily production compute job to earn a hundredth of a Calmar point.

**Not adopted.** The cost/benefit is clearly negative.

---

## What the diagnostics got wrong, and why it matters

The Phase 2 hypotheses came from real measurements:

- `mom6` and `mom_vadj` correlate at **0.969** — genuinely near-duplicate
- effective independent factors: **2.96 of 5**
- information coefficients span 3×, `neg_base` sits at a **52.2% hit rate**

All true. **Every configuration derived from them made things worse.**

The clearest case is `neg_base`. By IC and hit rate it looked like dead weight.
Removing it (B4) costs **0.25 Calmar** on 2017-26. It is also the only factor with
*negative* correlation to the others (−0.10 to −0.27) — and that, not its
standalone accuracy, is what earns its place.

Two conclusions worth keeping:

1. **IC measures standalone power, not marginal contribution.** A weak,
   uncorrelated factor can add more to a composite than a strong, correlated
   one. Ranking factors by IC and reweighting accordingly is exactly backwards.
2. **The redundancy is doing work.** Averaging correlated momentum signals
   cancels estimation noise. This is the documented robustness of 1/N weighting:
   "optimised" weights fit estimation error and lose out of sample. B1
   (IC-proportional) demonstrates it — 1.06 and 0.54, worse on both.

---

## The interesting near-misses

**C1 — trend_IR replaces neg_base — produced 0.63 on 2011-16**, the best
out-of-sample figure of all 16 candidates (+0.06 over reference). But it scores
1.02 on 2017-26 (−0.14). So on the older window trend_IR beats base-tightness,
and on the newer window base-tightness beats trend_IR. Window-dependent, not
adoptable — but it does suggest the two factors measure the same underlying
thing (trend quality) with different regime sensitivities.

**C5 — trend_IR 63d at full weight — produced 1.29 on 2017-26**, the best figure
in all of Phase 2, with the lowest drawdown of any variant (17.55%). Out of
sample: 0.56, marginally *below* reference. The same trap shape as persist-3
(1.46 → 0.45) and D-20 (1.14 → 0.31). Rejected.

---

## Programme-level position

Across Phases 1 and 2 combined, **17 candidates tested, 1 adopted**
(nothing in Phase 2; the sizing audit retained inverse-vol on its
pre-registered criteria).

The strategy's components, by strength of evidence:

| component | evidence |
|---|---|
| **composite RS ranking** | swapping it for single-factor collapses CAGR 16.93% → 4.97% |
| **IFP gate** | −0.10 to −0.16 across four windows, one disjoint |
| **ATR ceiling + persist-2** | persist-2 positive in 3 of 5 walk-forward windows |
| N=30, no stop loss | confirmed; concentration and every price stop rejected |
| inverse-vol sizing | marginal (+0.03 mean), retained on criteria |
| the 5 factors as weighted | **all 16 reweightings tested are worse** |

## Recommendation

Stop optimising the composite. Sixteen architectural variants across three
theoretically-motivated families all failed, including several with strong
diagnostic support. The equal-weighted composite is more robust than the
factor statistics implied.

The remaining uncertainty in this system is **survivorship (3-7 CAGR points,
unmeasured) and regime (Calmar 0.28-2.72 across three-year windows)** — neither
of which responds to further in-sample search. Forward paper-trading evidence is
now the only thing that will move the picture.

## Artefacts retained

`stock_trend_ir` (3,841,472 rows) and the `pos_trend_ir_w` / `pos_trend_ir_col`
parameters are left in place and inert by default. They cost nothing and make
trend_IR available if a future test wants it in combination with something else.
Drop with `DROP TABLE stock_trend_ir;`
