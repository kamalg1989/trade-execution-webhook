# Literature-Based Solutions to the Momentum-Crash Problem — Test Report (2026-08-18)

Problem taken to the literature: our momentum book sits at ~20.6% CAGR / 45.6%
MaxDD (Calmar 0.45) and **nine** exit-timing mechanisms have failed to improve
risk-adjusted return. Searched the canonical research on momentum crashes and
tested every major published remedy on our own 15-year data.

## What the literature says

| Source | Claim | Our test |
|---|---|---|
| Barroso & Santa-Clara (2015) *Momentum has its moments* | Scale exposure by the **strategy's own** realized volatility (not the market's); momentum vol is self-predictable and spikes before crashes | 1a–1d, 4a–4c |
| Daniel & Moskowitz (2016) *Momentum crashes* | Crashes occur in **rebounding bear markets** where momentum beta flips negative; scale on forecast mean+variance | 2, 6b, 6c |
| Blitz, Huij & Martens (2011) *Residual Momentum* | Rank on the **idiosyncratic** residual after removing market exposure; reported to ~double Sharpe and cut crash risk | 3a–3b, 4a–4c, 7b |
| George & Hwang (2004) *52-week high* | Rank on proximity to the 52-week high instead of past return | 5a–5b, 7a |

## Results (15yr 2011–2026, N=30, ATR≤5.5, TO≥8, 0.32%/leg, MtM monthly)

| Config | CAGR | MaxDD | **Calmar** | Sharpe | 2016–20 |
|---|---|---|---|---|---|
| BASE composite (reference) | 22.23 | 33.27 | 0.67 | 0.76 | 22.39 |
| 1a Barroso vol-target 12% | 14.28 | 31.13 | 0.46 | 0.52 | 13.24 |
| 1b Barroso vol-target 16% | 16.74 | 30.35 | 0.55 | 0.60 | 16.15 |
| 1c Barroso vol-target 20% | 18.67 | 31.55 | 0.59 | 0.65 | 18.07 |
| 1d Barroso 16% + 1.5× leverage | 14.55 | 48.31 | 0.30 | 0.48 | 10.76 |
| 2 Daniel-Moskowitz bear+vol de-risk | 21.54 | 31.81 | 0.68 | 0.74 | 21.61 |
| 3a **Residual momentum only** | 20.39 | 36.20 | 0.56 | 0.65 | **9.95** |
| 3b Residual + price blend | 22.08 | 33.00 | 0.67 | 0.71 | 12.50 |
| 5a 52-week-high only | 13.59 | 28.85 | 0.47 | 0.50 | 14.62 |
| 5b **composite + 52-week-high** | **22.92** | 30.70 | **0.75** | **0.79** | 24.11 |
| **6a BASE + regime shield** | 22.06 | **18.05** | **1.22** | **0.85** | 22.81 |
| 6d BASE + regime + vol-target 20% | 20.66 | 17.66 | 1.17 | 0.81 | 22.06 |
| 7a composite + 52w + regime + DM | 21.77 | 18.85 | 1.15 | 0.84 | 24.03 |

## Verdict on each published remedy

1. **Barroso/Santa-Clara volatility targeting — FAILS on our data.** Every
   variant reduced Calmar (0.67 → 0.46–0.59). It cuts exposure to ~78–92% and
   loses more return than drawdown. Adding leverage (1d) was the single worst
   result tested (Calmar 0.30). The published result is for a US long-short
   academic momentum factor; our book is long-only, already ATR-filtered and
   inverse-vol sized — the volatility it would manage has largely been removed
   at the position level already.
2. **Daniel/Moskowitz bear+vol de-risk — neutral.** Calmar 0.67 → 0.68, i.e.
   inside noise. Directionally correct, magnitude negligible here.
3. **Residual momentum — FAILS, and instructively.** Standalone Calmar 0.56 vs
   0.67 for price momentum, and it collapses in 2016–20 (**9.95% vs 22.39%**).
   Removing market exposure strips out precisely the systematic-trend component
   that drives returns in an emerging market with high cross-sectional
   correlation. Blending it back in recovers only to parity (0.67).
4. **52-week-high momentum — the one genuine additive find.** Standalone it is
   weak (0.47), but **added to our composite it lifts Calmar 0.67 → 0.75 and
   CAGR 22.23 → 22.92 while cutting drawdown 33.3 → 30.7** — the first
   ranking-side improvement discovered in this entire program.

## The finding that actually matters

**6 of 9 round-2 configurations clear Calmar 0.70, and every one of them
contains the regime shield.** The best is BASE + regime shield: **22.06% CAGR /
18.05% MaxDD / Calmar 1.22 / Sharpe 0.85**.

That is the reverse of the engine result (#772–774), where the same shield
*failed*. The two are reconciled by execution granularity, not by disagreement
about the signal:

- **Harness:** regime evaluated on the month-end grid, positions marked
  monthly, liquidation assumed at that mark.
- **Engine:** regime evaluated daily with next-open fills; whipsaw round-trips
  are charged individually (1,314 `REGIME_OFF` exits in run #773 for +₹0.44L).

The harness's Calmar 1.22 is therefore an **upper bound that assumes frictionless
monthly regime switching**. The engine's ~0.49 is the lower bound with realistic
fills. The truth is between, and the gap is an execution-cost problem — not a
strategy-discovery problem.

---

# ADDENDUM — Hysteresis Regime + 52-Week-High, Tested (runs #790–793)

## Harness: hysteresis band sweep (asymmetric is the point)

| exit / entry band | switches | CAGR | MaxDD | Calmar |
|---|---|---|---|---|
| 0% / 0% (plain threshold) | 26 | 21.01 | 20.15 | 1.04 |
| **0% / 2%** | 24 | **21.60** | **19.59** | **1.10** |
| 0% / 3% | 24 | 21.19 | 20.32 | 1.04 |
| 0% / 5% | 22 | 18.59 | 24.25 | 0.77 |
| 2% / 2% | 18 | 18.55 | 31.33 | 0.59 |
| 3% / 3% | 16 | 17.10 | 31.33 | 0.55 |
| 5% / 5% | 14 | 14.15 | 39.68 | 0.36 |

**Widening the EXIT side is strictly harmful** (Calmar 0.59 → 0.36) — delaying
the exit means eating more of the crash. **Widening only the ENTRY side helps**
(1.04 → 1.10). The correct design is asymmetric: leave immediately, come back
slowly.

52-week-high factor **with** the regime shield on: CAGR rises (21.60 → 22.71)
but drawdown rises more (19.59 → 22.78), so Calmar falls 1.10 → 1.00. It helped
without the regime (0.67 → 0.75) and hurts with it — the two are substitutes,
not complements. **Not adopted.**

## Engine validation — hysteresis works, and is measurable

| Run | Config | Regime switches | CAGR | MtM MaxDD | Calmar |
|---|---|---|---|---|---|
| #785 | #780 base (ATR ceiling, **no regime**) | — | 15.24 | 31.13 | **0.49** |
| #790 H1 | + daily regime, **no hysteresis** | **88** | 13.13 | 30.21 | 0.43 |
| **#791 H2** | **+ daily regime + 2% re-entry band** | **42** | **14.36** | **29.68** | **0.48** |
| #792 H3 | + 4% re-entry band | 36 | 12.04 | 32.39 | 0.37 |
| #793 H4 | 2% band, no ATR ceiling | 42 | 17.85 | 43.72 | 0.41 |

**The mechanism is confirmed: hysteresis cut regime switches 88 → 42 (−52%)
and recovered +1.23 CAGR points and +0.05 Calmar over the un-damped shield.**
The 4% band over-damps (36 switches but Calmar 0.37) — same shape as the
harness sweep, which is reassuring for robustness.

**But it does not beat simply having no regime at all** (#785, Calmar 0.49 vs
#791's 0.48). The daily regime shield remains a value-destroyer in the engine
even after its single biggest defect is fixed.

## Final reconciliation of the harness/engine gap

| | Harness | Engine |
|---|---|---|
| Regime evaluation | monthly grid | daily, next-open fills |
| Switches over 15 yrs | 24–26 | 42–88 |
| Best Calmar with regime | 1.10–1.22 | 0.48 |
| Best Calmar without regime | 0.67 | **0.49** |

Hysteresis closed roughly a third of the switch-count gap and none of the
Calmar gap. The residual difference is **not** whipsaw frequency — it is that
the harness liquidates and re-enters an entire 30-name book at a single
monthly mark with no per-name fill cost, while the engine pays a real
round-trip on every position, every time. **The harness's Calmar >1.0 regime
results should be treated as unattainable in production**, and the engine's
~0.49 as the honest number.

## Recommended next actions

1. **Adopt the 52-week-high factor** into the composite score — cheapest
   confirmed improvement available (+0.08 Calmar, harness). Needs engine
   validation via `pos_momentum='composite_rs'` scoring update.
2. **Do NOT adopt** Barroso vol-targeting, residual momentum, or leverage —
   all measured negative on our data despite strong published results.
3. **The regime shield's real value depends entirely on switching cost.**
   Worth one targeted experiment: a *hysteresis* regime (e.g. exit below
   200DMA, re-enter only above 200DMA×1.03) to cut the 1,314 whipsaws — that
   is the specific mechanism standing between engine 0.49 and harness 1.22.
4. Survivorship caveat unchanged: all CAGR figures are upper bounds.
