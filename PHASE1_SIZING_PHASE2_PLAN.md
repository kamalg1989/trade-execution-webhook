# Phase 1 — Sizing Audit · Phase 2 — Composite Architecture Plan
**2026-08-20 · runs #963–967 plus factor diagnostics**

---

# PHASE 1 RESULT: inverse-vol survives, but only just — DO NOT DROP IT

Equal weight vs inverse-volatility, five disjoint walk-forward windows,
identical configs otherwise (persist-2, N=30, IFP 0.38, ATR 5.0).

| window | inverse-vol Calmar | equal Calmar | winner | inv-vol DD | equal DD |
|---|---|---|---|---|---|
| W1 2011-14 | **0.55** | 0.52 | inverse-vol | 19.93 | 20.33 |
| W2 2014-17 | **0.99** | 0.89 | inverse-vol | 22.55 | 22.90 |
| **W3 2017-20 (crash)** | **0.23** | 0.23 | tie | **21.07** | **20.77** |
| W4 2020-23 | 3.22 | **3.31** | equal | 14.77 | 14.62 |
| W5 2023-26 | **0.45** | 0.40 | inverse-vol | 26.81 | 27.74 |

**Score: inverse-vol 3, equal 1, tie 1.**

### Against the pre-registered criteria

> *"Equal Weight matches or beats Inverse-Vol in at least 3 of the 5 windows and
> doesn't expand W3 (Crash) drawdown"*

- **3-of-5 test: FAILED.** Equal weight wins 1 and ties 1 — it needed 3.
- W3 drawdown test: passed (20.77 vs 21.07, marginally better).

**Verdict: keep inverse-volatility sizing.** It wins the majority of windows,
including the two most recent, and its mean Calmar advantage is +0.03.

### But the honest caveat

The advantage is **small and window-dependent**: +0.03 on average, and it
*loses* in W4 (the strongest bull window). It contributes nothing on the full
2011-2026 run (0.67 either way, which is what prompted this audit). The fair
description is "marginally useful, not load-bearing" — the earlier framing of it
as a meaningful drawdown-control was overstated.

Since it costs nothing to keep and the criteria explicitly failed, it stays. But
it should not be counted among the strategy's real sources of edge.

---

# PHASE 2: the composite is NOT five factors

Before proposing changes, I measured what the composite actually contains.

## Diagnostic 1 — the factors are heavily redundant

Within-month z-score correlations, eligible universe, 44,495 rows:

| | mom12_1 | mom6 | mom3 | mom_vadj | neg_base |
|---|---|---|---|---|---|
| **mom12_1** | 1.000 | 0.617 | 0.359 | 0.595 | −0.103 |
| **mom6** | 0.617 | 1.000 | 0.691 | **0.969** | −0.227 |
| **mom3** | 0.359 | 0.691 | 1.000 | 0.663 | −0.271 |
| **mom_vadj** | 0.595 | **0.969** | 0.663 | 1.000 | −0.169 |
| **neg_base** | −0.103 | −0.227 | −0.271 | −0.169 | 1.000 |

> **`mom6` and `mom_vadj` correlate at 0.969 — they are the same factor.**

That makes sense mechanically: `mom_vadj = mom6 / atr_pct`, and ATR is already
gated to ≤5%, so dividing by a value confined to a narrow band barely reorders
anything. We are effectively double-weighting 6-month momentum.

**PCA: effective number of independent factors = 2.96 of 5.** The first
principal component alone explains 61.1% of variance.

## Diagnostic 2 — the factors are not equally predictive

Information Coefficient = monthly Spearman rank correlation of factor vs next
month's return.

| factor | IC mean | IC IR | hit rate | weight today |
|---|---|---|---|---|
| **mom12_1** | **+0.0606** | **+0.287** | 65.6% | 1.0 |
| **mom_vadj** | +0.0419 | +0.248 | **66.7%** | 1.0 |
| mom6 | +0.0384 | +0.227 | 62.2% | 1.0 |
| mom3 | +0.0213 | +0.124 | 59.4% | 1.0 |
| **neg_base** | +0.0199 | +0.131 | **52.2%** | 1.0 |
| *(mom1, not used directly)* | **−0.0030** | −0.019 | 53.3% | — |

Three things stand out:

1. **`mom12_1` is by far the strongest** — 3× the IC of `mom3` — yet carries the
   same weight.
2. **`neg_base` has a 52.2% hit rate.** That is a coin flip. It is the weakest
   factor by hit rate and it already **failed out-of-sample** (removing it
   *improved* 2011-16). Its presence is now doubly questionable.
3. **`mom1` has negative IC (−0.0030)** — direct confirmation of short-term
   reversal, and vindication of skipping the recent month in `12-1`.

---

## THE THREE PROPOSED ARCHITECTURAL TESTS

### Proposal A — De-duplicate the momentum leg (highest expected value)

**Problem:** `mom6` and `mom_vadj` are 0.969 correlated; the composite silently
gives 6-month momentum a double vote at the expense of differentiated signal.

**Tests:**
| id | change |
|---|---|
| A1 | drop `mom_vadj` entirely → 4 factors |
| A2 | **redefine `mom_vadj` as `mom12_1 / atr_pct`** — vol-adjust the *strongest* factor instead of a duplicate |
| A3 | keep both, halve `mom6` weight to 0.5 |
| A4 | drop `mom3` (weakest momentum leg, 0.69 correlated with mom6) |

**Prediction:** A2 is the most promising — it preserves the vol-adjustment
concept while removing the collinearity, and vol-adjusted momentum has the best
hit rate (66.7%) of any current factor.

### Proposal B — Weight by information content

**Problem:** all five weights are 1.0 despite a 3× spread in IC.

**Tests:**
| id | mom12_1 | mom6 | mom3 | mom_vadj | neg_base |
|---|---|---|---|---|---|
| B1 (IC-proportional) | 1.5 | 1.0 | 0.5 | 1.25 | 0.5 |
| B2 (lean) | 1.0 | 1.0 | 0.0 | 1.0 | 0.0 |
| B3 (12-1 emphasis) | 2.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| B4 (drop neg_base only) | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 |

**Prediction:** B4 and B2 are the interesting ones, given `neg_base`'s coin-flip
hit rate and OOS failure. Note the counter-evidence: removing `neg_base`
*hurt* on 2017-26 (−0.26) while *helping* on 2011-16 (+0.03) — genuinely
window-dependent, so this needs both windows.

### Proposal C — Trend Information Ratio replaces the weakest factor

**Problem:** `neg_base` measures *tightness of a 20-day range* — a structural
proxy, weakly predictive (IR 0.131, hit 52.2%) and OOS-fragile.

**Replacement:** the information ratio of the trend itself —

```
regress log(price) on time over 126 sessions
trend_IR = slope / standard_error_of_residuals
```

This measures **trend consistency** (how reliably the stock advanced) rather
than magnitude or range width, and is conceptually independent of every existing
factor. The rolling-regression machinery already exists and is **validated
against brute-force `polyfit` (193/193 exact)** after the look-ahead bug found
and fixed earlier today.

**Tests:**
| id | change |
|---|---|
| C1 | replace `neg_base` with `trend_IR` |
| C2 | replace `mom3` with `trend_IR` |
| C3 | add `trend_IR` as a 6th factor (weight 1.0) |

**Prediction:** modest. A related construct (Clenow slope × R²) was tested today
and came out null (0.91–0.94 vs 0.94) — **but only as an addition to the full
composite, never as a replacement for a weak factor**, which is the actual
question here. `trend_IR` also normalises by residual error rather than R², so it
is not the same quantity.

---

## Execution protocol

Given six candidates have now died at the out-of-sample hurdle, every test runs
on **both disjoint windows (2017-26 and 2011-16) from the start** — no
promoting anything on a single window. Anything that clears both goes to the
five-window walk-forward before adoption.

Order by expected value: **A2 → B4 → C1 → A1 → B2 → C2 → rest.**

## Honest expectation

The factor diagnostics say the composite is carrying ~3 independent signals
dressed as 5, with one near-duplicate pair and one coin-flip factor. Cleaning
that up is *principled* — but it mostly removes redundancy rather than adding
information, so I would expect **small Calmar gains and a simpler model**, not a
step change. The one genuine shot at new information is `trend_IR` (Proposal C).
