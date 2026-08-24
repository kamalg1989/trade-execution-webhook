# Research Proposal — Orthogonal Momentum-Crash Mitigation
**Lead Quant Research · 2026-08-18 · 5 hypotheses, all net-new to this program**

Current edge: 5-factor composite RS + inverse-vol sizing + 5.0% ATR ceiling +
IFP≥0.38 gate. Engine Calmar 0.62 (run #799), harness 0.94.

Everything rejected so far shares one property: it acts on **price, after the
fact** (stops, index trend, breadth, base geometry). All five hypotheses below
act on **covariance, fundamentals, or the microstructure of the momentum
itself** — none is a timing rule, none is on the excluded list.

**Data ceiling, stated up front:** `earnings_fundamentals` carries only
revenue, other_income, net_profit, profit_continuing, eps_basic, eps_diluted
(2,378 symbols, ~33.8 quarters each, ending 2024-12-31). **There is no balance
sheet** — so Debt/Equity, ROIC, ROE and true accruals are *not computable*.
H2 is therefore specified against income-statement-only quality, which is a
weaker but honest formulation.

---

## H1 — Correlation-Penalized Selection (Diversified Risk Parity)

### Rationale
Inverse-vol sizing solves `w_i ∝ 1/σ_i`, which equalises **standalone** risk
and completely ignores the off-diagonal of Σ. Portfolio variance is
`w'Σw` — two names with identical ATR but ρ=0.9 contribute roughly twice the
portfolio variance of two with ρ=0.1. **A momentum crash *is* a correlation
event**: cross-sectional dispersion collapses and everything the ranker owns
(which is, by construction, whatever recently worked) moves as one asset.
Choueifaty & Coignard (2008) formalise this as the Diversification Ratio;
Meucci (2009) as the *effective number of bets* via PCA of Σ.

Critically, this is **not** a re-run of the failed sector cap (#775, Calmar
0.39). That failed substantially because `symbols_meta.sector` covers only
**42%** of the liquid universe — the cap bound on fewer than half the names.
Realised correlation is **100% covered** and captures economic linkage without
needing labels (it will group two "Capital Goods" names only if they actually
co-move, and will group a PSU bank with an infra name if they do).

### Formula
Rolling correlation from 126 daily returns, computed at each rebalance:

```
Σ  = corr matrix of daily returns, trailing 126 sessions, over candidates
Greedy selection, walking the composite-RS ranking in order:
    accept candidate c  iff  mean( ρ(c, s) for s in selected ) <= rho_max
    else skip to the next-ranked name
```
Optional stricter variant — **Effective Number of Bets** (Meucci):
```
Σ = w'Σw decomposed via PCA -> variance contributions p_i (normalised)
ENB = exp( -Σ p_i ln p_i )          # entropy of the principal-portfolio mix
maximise ENB subject to holding N names from the top-K ranked
```
Greedy is preferred first: O(N·K), no matrix inversion, no optimiser
instability on a 30×217 problem.

### Test configuration
```python
pos_corr_max          = 0.55 | 0.65 | 0.75 | None   # sweep, None = current
pos_corr_lookback_d   = 126
pos_corr_pool_mult    = 3      # scan top N*3 ranked names to fill N slots
# engine: new helper _corr_filtered_pick() between _score_composite() and the
# banding logic; needs a per-rebalance daily-return matrix (reuse the existing
# per-symbol series cache).
```
**Kill criterion:** if Calmar does not exceed 0.62 at any rho_max, correlation
is already priced by the ATR ceiling and the hypothesis is dead.

---

## H2 — Income-Statement Quality as a SCORE (not a gate)

### Rationale
Daniel & Moskowitz (2016) show crashes are driven by the **junk unwinding** —
in a long-only book, the analogue is that the lowest-quality names in the long
leg fall hardest when the factor turns. Asness, Frazzini & Pedersen (*Quality
Minus Junk*) show quality is compensated and, crucially, **negatively
correlated with momentum crash risk**. Novy-Marx (2013) shows profitability is
the most robust single quality axis.

The earlier quality test (ablation config C, Calmar 0.42) failed — but it was
a **hard gate** (`profitable AND growth>10%`), and this program has now twice
shown the same information helps as a score and destroys as a gate
(base tightness: 0.47 as filter → 0.73 as score). Quality has not been tested
as a score. That is the actual hypothesis.

### Formula
Point-in-time on `broadcast_date`, TTM windows:
```
margin_ttm      = net_profit_ttm / revenue_ttm
margin_trend    = margin_ttm  -  margin_ttm(4 quarters ago)
earnings_stab   = -stdev( yoy growth of net_profit_ttm, last 8 quarters )
profit_persist  = count(quarters with net_profit > 0, last 8) / 8
quality_z = z(margin_ttm) + z(margin_trend) + z(earnings_stab) + z(profit_persist)
score = composite_RS  +  w_q * quality_z          # w_q swept
```
`earnings_stab` is the crash-relevant term: stable earners de-rate far less
violently than story stocks when momentum reverses.

### Test configuration
```python
pos_quality_score_w = 0.0 | 0.5 | 1.0 | 1.5    # 0.0 reproduces current
pos_quality_min     = None | -1.0              # optional soft floor, NOT a growth gate
# Data caveat: fundamentals end 2024-12-31. Run BOTH 2011-2024 (clean) and
# 2011-2026 (stale tail) — a result that only appears in the stale window is
# an artifact, exactly as the earlier quality test proved to be.
```
**Kill criterion:** no improvement at any w_q on the 2011-2024 clean window.

---

## H3 — Barroso Volatility Targeting, Correctly Implemented

### Rationale
**I tested this and it failed (Calmar 0.67 → 0.46–0.59) — but the
implementation was methodologically weak and the result should not be
trusted.** Barroso & Santa-Clara estimate momentum variance from **126 daily
returns of the momentum portfolio**. My test estimated it from **6 monthly
returns** — a variance estimate on 6 degrees of freedom, which has a standard
error of roughly 30% of the estimate itself. Scaling exposure by a number that
noisy will destroy returns regardless of whether the underlying signal is real.

The published result (near-doubling of Sharpe) rests on the estimator being
accurate. This hypothesis is to test it properly, not to re-run it.

### Formula
```
r_p,d  = daily return of the CURRENTLY HELD portfolio (equal-weight or inv-vol)
σ̂_t   = sqrt( 252/126 * Σ_{d=t-126}^{t-1} r²_p,d )       # annualised, daily-based
w_t    = min( w_max , σ_target / σ̂_t )
```
`σ_target` ≈ the strategy's own long-run realised vol (~18–20% here, NOT the
12% used for US academic long-short momentum — that mismatch alone would
explain part of the earlier failure, since it forced average exposure to 78%).

### Test configuration
```python
pos_voltarget_pct     = None | 16 | 18 | 20 | 22
pos_voltarget_lb_days = 126          # DAILY, not monthly — the whole point
pos_voltarget_max_lev = 1.0          # no leverage (1.5x already tested, worst result)
# engine: accumulate daily portfolio returns in the existing day loop; scale
# `base_capital` at each rebalance. Cash earns pos_cash_annual_pct.
```
**Kill criterion:** if the daily-estimator version still loses to unscaled at
every σ_target, Barroso is genuinely inapplicable here and is closed for good.

---

## H4 — Information Discreteness ("Frog in the Pan")

### Rationale
Da, Gurun & Warachka (2014): momentum built from **many small moves**
(continuous information) persists and reverses gently; momentum built from
**few large jumps** (discrete information) is precisely the momentum that
crashes. Investors under-react to gradual information and over-react to
dramatic information. This is a direct, published, *orthogonal* predictor of
which momentum names are crash-prone — and it is measured entirely from daily
returns, which we have complete.

This is the most attractive hypothesis of the five: it targets the crash
mechanism at the *name* level without touching exits, exposure, or timing.

### Formula
Over the 12-month formation window (~252 sessions):
```
PRET = cumulative formation return
%pos = fraction of days with r > 0 ;  %neg = fraction with r < 0
ID   = sign(PRET) × ( %neg − %pos )
```
ID < 0 = **continuous** (many small up-days) → preferred.
ID > 0 = **discrete** (few big jumps) → crash-prone.
```
score = composite_RS  −  w_id * z(ID)        # penalise discrete momentum
```
Also testable as a soft gate: drop the top decile of ID.

### Test configuration
```python
pos_id_score_w   = 0.0 | 0.5 | 1.0 | 1.5
pos_id_lookback  = 252
pos_id_max_pctl  = None | 90        # optional: exclude worst decile
```
**Kill criterion:** no Calmar gain at any weight AND no reduction in the
2018/2025 factor-crash drawdowns specifically.

---

## H5 — Beta Acceleration Cap (Daniel-Moskowitz optionality, long-only form)

### Rationale
DM's core mechanic: momentum's beta is **asymmetric and state-dependent** —
after a run, winners have accumulated market beta, so the portfolio is
implicitly short a call on the market and gets run over in a rebound. Their
fix scales on forecast mean/variance. The long-only analogue that has *not*
been tested here: penalise names whose **beta has risen sharply during the
formation period**, since those are the names carrying the hidden beta bet.

Distinct from the rejected index-trend filter — it is a **cross-sectional
name-level** measure, requires no market forecast, and never sits in cash.

### Formula
```
β_t     = rolling 126-day beta of stock vs equal-weight universe index
Δβ      = β_t − β_{t-126}
score   = composite_RS − w_b * z(Δβ)
```
Optional portfolio-level variant: cap the weighted-average β of the 30 holdings
at ~1.2 by substituting lower-Δβ names from the next ranks.

### Test configuration
```python
pos_beta_accel_w   = 0.0 | 0.5 | 1.0
pos_beta_lookback  = 126
pos_portfolio_beta_max = None | 1.1 | 1.2
```
**Kill criterion:** no improvement, or improvement that vanishes once H1
(correlation penalty) is applied — Δβ and correlation may be measuring the
same latent factor exposure, in which case keep whichever is cheaper.

---

## Recommended execution order

| # | Hypothesis | Data ready? | Expected difficulty | Priority |
|---|---|---|---|---|
| H4 | Information discreteness | ✅ daily returns | low | **1st — cheapest, most targeted** |
| H1 | Correlation penalty | ✅ daily returns | medium | **2nd — largest structural gap** |
| H3 | Barroso done right | ✅ daily returns | medium | 3rd — corrects a flawed prior test |
| H5 | Beta acceleration | ✅ daily returns | low | 4th — may overlap H1 |
| H2 | Quality score | ⚠️ income-stmt only, ends 2024 | medium | 5th — weakest data support |

Run H4 and H1 first in the vectorized harness (15yr in ~1s each, so full
weight sweeps are free), promote anything clearing **Calmar 1.00 harness /
0.65 engine** to engine validation, and pre-register the kill criteria above
before looking at results.
