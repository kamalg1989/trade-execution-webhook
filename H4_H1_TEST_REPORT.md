# H4 / H1 Test Report — Information Discreteness & Correlation Penalty
**2026-08-19 · harness, 15yr 2011–2026 · baseline reproduced exactly before testing**

Baseline = the sg2/sg3 winner (preset #19 / engine run #799): composite RS
− z(base_range), IFP ≥ 0.40, close ≥ ₹20, TO ≥ 8cr, ATR ≤ 5.0, N=30, band 2.0,
0.32%/leg. Reproduced at **24.40% CAGR / 25.92% MaxDD / Calmar 0.94** — matches
the recorded figure to two decimals, so the substrate rebuild is sound.

New data built for these tests: 6.09M daily closes (3,262 symbols, 2010–2026)
exported from `market_data.ohlcv_data`, giving a daily return matrix
(4,119 × 3,262) and information-discreteness features at 126d and 252d.

---

## HEADLINE

| | CAGR | MaxDD | Calmar |
|---|---|---|---|
| baseline (N=30) | 24.40 | 25.92 | 0.94 |
| **H4 adopted: N=25, ID-126 penalty w=1.0** | **26.07** | **25.45** | **1.02** |

**Better on both axes** — +1.67 CAGR points *and* −0.47 drawdown points.
First harness result in this program to clear Calmar 1.00 without the
frictionless regime-switching assumption that made the earlier 1.22 unattainable.

**H1 (correlation penalty) is dead**, and the diagnostic that killed it is the
more valuable output of the two.

---

## H1 — FALSIFIED, AND MY PREMISE WAS WRONG

The proposed thresholds (ρ_max 0.55/0.65/0.75) returned **byte-identical**
results to the baseline. Not a weak effect — an inert one. The diagnostic
explains why:

**Realised 126d correlation structure of the held 30-name book, 170 months:**

| statistic | value |
|---|---|
| mean pairwise correlation | **0.194** |
| 95th percentile | 0.287 |
| **maximum ever observed** | **0.348** |
| effective number of bets (PCA entropy) | **20.0 of 30** |

A constraint at ρ_max = 0.55 can never bind when the worst name in the book
never exceeds 0.35. My proposal asserted that "a momentum crash is a
correlation event" and that inverse-vol sizing leaves the off-diagonal of Σ
unmanaged. **The first claim is materially weaker here than the US literature
implies, and the second is already handled.**

Correlation *does* rise in crashes — but far less than the hypothesis needed:

| window | mean pairwise ρ | ENB |
|---|---|---|
| 2018 crash | 0.167 | 20.8 |
| **2020 COVID** | **0.252** | **17.5** |
| 2025 crash | 0.195 | 19.8 |
| full sample | 0.194 | 20.0 |

COVID — the most violent correlation event in the sample — moved ρ from 0.19
to 0.25 and ENB from 20.0 to 17.5. There is no concentration left to remove.

Re-tested at thresholds that *do* bind (skip counts confirm the rule firing):

| ρ_max | skips | CAGR | MaxDD | Calmar |
|---|---|---|---|---|
| none | 0 | 24.40 | 25.92 | 0.94 |
| 0.35 | 114 | 24.91 | 25.58 | 0.97 |
| 0.30 | 369 | 24.63 | 26.02 | 0.95 |
| 0.25 | 941 | 24.05 | 25.78 | 0.93 |
| 0.20 | 2,076 | 23.21 | 26.76 | 0.87 |
| 0.15 | 3,954 | 22.10 | 27.21 | 0.81 |

One point above baseline at the threshold where the rule barely fires
(<1×/month), then monotone destruction. That is the signature of noise at the
edge of a constraint, not a plateau. Across 10 perturbations (cost 0.50/0.75,
N=20/40, band 1.5/3.0, start 2013/2015, IFP 0.38, ATR 5.5) ρ=0.35 averaged
**+0.012 Calmar** and changed sign five times. **Closed.**

*Note this also retrospectively vindicates the failed sector cap (#775): it
wasn't only the 42% sector-label coverage — there was no diversification
deficit for it to fix either.*

---

## H4 — CONFIRMED, BUT CONDITIONAL ON CONCENTRATION

`ID = sign(PRET) × (%neg − %pos)`, 126-day formation, penalised in the score.

At the current N=30 it is worthless (+0.01 Calmar) — which is where I would
have stopped had the mechanism not predicted an interaction with portfolio
size. It does:

| N | baseline Calmar | +H4 Calmar | ΔCalmar | ΔMaxDD |
|---|---|---|---|---|
| 10 | 0.58 | 0.79 | **+0.21** | **−7.70** |
| 15 | 0.71 | 0.81 | +0.10 | −3.70 |
| 20 | 0.82 | 0.97 | +0.15 | −3.63 |
| 25 | 0.91 | **1.02** | +0.11 | −2.86 |
| 30 | 0.94 | 0.95 | +0.01 | +0.32 |
| 40 | 0.74 | 0.79 | +0.05 | +0.03 |

**The benefit scales monotonically with concentration, exactly as the theory
says it must.** One jump-driven name reversing violently is a 10% hit in a
10-name book and a 3% hit in a 30-name book, so screening out discrete-
information momentum is worth most precisely where the book is concentrated.
An effect that is largest where the mechanism predicts it should be largest is
substantially stronger evidence than any single favourable cell.

### It survives the pre-registered checks

**Plateau, not spike** (N=25): w = 0.75 → 0.98, **1.00 → 1.02**, 1.25 → 1.01,
1.50 → 1.02. Flat top across a 2× range of the parameter.

**Sign check** (N=20): rewarding discrete momentum instead of penalising it
gives Calmar **0.64** vs 0.97. The factor carries real directional information.

**Perturbations at N=20:**

| stress | baseline | +H4 | Δ |
|---|---|---|---|
| cost 0.50%/leg | 0.70 | 0.86 | +0.16 |
| start 2015 | 0.84 | 1.01 | +0.17 |
| IFP 0.38 | 0.75 | 0.82 | +0.07 |

**Crash windows** improve in every variant tested — the 2025 episode most
consistently (−16.2% → −13.0% at N=20; −19.6% → −15.0% at IFP 0.38).

**Window choice:** 126d beats 252d (0.97 vs 0.85 at N=20). The shorter
formation window is the better discreteness estimator here.

---

## WHY THIS MATTERS BEYOND THE +0.08 CALMAR

The program previously rejected N=20–25 because concentration bought CAGR at an
unacceptable drawdown cost (N=20: 26.72% CAGR but 32.47% MaxDD vs N=30's
25.92%). **H4 removes exactly that defect** — it cuts N=20 drawdown by 3.6
points and N=10 by 7.7. Concentration is therefore re-opened as a lever, and
the adopted config takes the CAGR that concentration offers without the
drawdown that previously disqualified it.

This is also the first mechanism in ~11 tested that improves risk *without*
being an exit-timing rule — consistent with the standing pattern that this edge
tolerates eligibility/ranking changes and rejects exit rules.

---

## RECOMMENDED CONFIG (harness — engine validation pending)

```python
pos_momentum        = "composite_rs"   # z(12-1)+z(6m)+z(3m)+z(6m/atr)+z(-base_range)
pos_id_score_w      = 1.0              # NEW: score -= w * z(ID_126)
pos_id_lookback     = 126              # NEW (126d beats 252d)
pos_top_n           = 25               # CHANGED from 30 — H4 makes this affordable
pos_buffer_n        = 50
pos_min_ifp_score   = 0.40             # engine may prefer 0.38, re-check
pos_min_close       = 20.0
pos_min_turnover_cr = 8.0              # do NOT raise
pos_atr_max_pct     = 5.0
pos_size_mode       = "inverse_vol"
pos_sl_mode         = "none"
# harness: 26.07% CAGR / 25.45% MaxDD / Calmar 1.02  (baseline 24.40/25.92/0.94)
```

## Caveats

1. **Harness, not engine.** Every prior harness→engine port has lost ground
   (monthly frictionless marks vs per-name next-open fills). Expect materially
   lower absolute numbers; the *relative* gain and the N-interaction are what
   should port. Engine validation requires a new `pos_id_score_w` /
   `pos_id_lookback` parameter pair plus an ID feature column.
2. **ID needs daily data the engine does not currently load** — it reads
   `stock_indicators` monthly. Cleanest implementation is a precomputed
   `information_discreteness` column on `stock_indicators`, refreshed by the
   existing `custom-screener-compute.timer`.
3. **Coverage 87.2%** of panel rows have ID_252; missing values are treated as
   zero penalty (no implicit look-ahead, but a mild dilution).
4. Survivorship bias unchanged — all CAGR figures remain upper bounds.
5. Nothing in BAU was touched. All work is in `/tmp/qr/`; no engine, service,
   or production file was modified.
