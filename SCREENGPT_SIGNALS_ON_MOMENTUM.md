# Do production screen_gpt signals improve the momentum engine? (2026-08-18)

Tested every screen_gpt Stage-1/Stage-2 concept — IFP, base tightness, VCP,
give-back, prior up-move, proximity to 20d high — plus universe hygiene
(penny stocks, liquidity tiers), each as a hard FILTER and as a SCORE
component. Harness, 15yr 2011–2026, N=30/band 2, ATR≤5.5, TO≥8, 0.32%/leg.
Reference BASE = composite RS momentum: **21.93% CAGR / 33.27% DD / Calmar 0.66**.

## Headline: two signals help, most hurt

| Winner | Mode | Calmar |
|---|---|---|
| **IFP ≥ 0.40** | hard filter | 0.66 → **0.86** |
| **price ≥ ₹20** (penny removal) | hard filter | 0.66 → **0.74** |
| **base tightness** | **SCORE** (not filter) | 0.66 → **0.73** |
| **all three combined** | — | **0.90** |

**Best configuration found: 24.85% CAGR / 27.60% MaxDD / Calmar 0.90 / Sharpe
0.86** — and with a tighter ATR ceiling (≤5.0), **24.40% / 25.92% / Calmar
0.94**, the best risk-adjusted result produced anywhere in this program.

## What helped

- **IFP ≥ 0.40** is the single strongest addition (+0.20 Calmar). Crucially it
  is a genuine **plateau**, not a spike: 0.36→0.71, 0.38→0.83, 0.40→0.86,
  0.42→0.71 — a smooth rise and fall with the peak in the middle. Beyond 0.44
  it collapses (0.41, then 0.29) as the universe thins below ~170 names.
  **Note production uses IFP ≥ 0.25, which is measurably too loose** — at 0.25
  the filter is inert (Calmar 0.67 vs 0.66 unfiltered).
- **Penny removal at ₹20** (+0.08 Calmar), also a clean plateau: ₹15→0.73,
  ₹20→0.74, ₹30→0.73, tailing off by ₹40. Removes only ~4 names/month but
  they are disproportionately the crash-prone tail.
- **Base tightness helps as a SCORE and hurts as a FILTER** — the most useful
  distinction in this test. As a hard gate `base_range ≤ 20%` (production's
  setting) crushes the strategy (Calmar 0.47); as a z-scored ranking input the
  same information lifts it to 0.73. Tightness is *informative for ranking*
  but must not be *binary eligibility*.

## What hurt (and should not be ported)

| Signal | Mode | Calmar | Note |
|---|---|---|---|
| base_range ≤ 20% (production gate) | filter | **0.47** | universe 344→219 |
| base_range ≤ 10% | filter | 0.25 | turnover explodes to 177% |
| **VCP proxy** (tight base + vol dry-up) | filter | **0.02** | universe collapses to 41 |
| giveback ≤ 25% | filter | 0.33 | |
| near 20d high (≥ −10%) | filter | 0.60 | |
| IFP as a score component | score | 0.54 | works as a gate, not as a rank |
| **liquidity TO ≥ 15 / 25 / 50** | filter | **0.57 / 0.51 / 0.42** | monotonically worse |
| Full production gate stack | filter | 0.39–0.47 | |

Two findings worth stating plainly:

1. **Raising the liquidity floor is strictly harmful** (0.66 → 0.57 → 0.51 →
   0.42 as TO goes 8→15→25→50). This is the illiquidity premium again — the
   same result the weekly-breakout research found. Removing *penny* stocks
   helps; removing *small* stocks destroys the edge. They are different things.
2. **The VCP concept is actively destructive here** (Calmar 0.02). VCP selects
   for consolidation/contraction, which is the opposite of what a momentum
   ranker needs; the two strategies want opposite states of the same stock.

## Robustness of the winner

| Stress | CAGR | MaxDD | Calmar |
|---|---|---|---|
| baseline (cost 0.32%) | 24.85 | 27.60 | 0.90 |
| cost 0.50%/leg | 22.59 | 28.70 | 0.79 |
| cost 0.75%/leg | 19.52 | 31.73 | 0.61 |
| ATR ≤ 5.0 | 24.40 | 25.92 | **0.94** |
| ATR ≤ 6.0 | 23.59 | 30.00 | 0.79 |
| no ATR ceiling | 23.90 | 35.39 | 0.68 |
| TO ≥ 5 (looser) | 24.06 | 30.52 | 0.79 |
| N=20 | 26.50 | 35.67 | 0.74 |
| N=40 | 21.73 | 27.07 | 0.80 |

Degrades gracefully on every axis; no cliff. Still profitable at >2× modelled
friction.

## Recommended configuration (harness — engine validation pending)

```python
pos_momentum        = "composite_rs"      # z(12-1)+z(6m)+z(3m)+z(6m/atr)
                                          #   + z(-base_range_20d_pct)   <-- NEW
gate_min_ifp_score  = 0.40                # NEW (production uses 0.25 = inert)
min_close           = 20                  # NEW penny filter
pos_min_turnover_cr = 8.0                 # do NOT raise
pos_atr_max_pct     = 5.0                 # 5.0 edges 5.5 here
pos_top_n           = 30 ; pos_buffer_n = 60
pos_size_mode       = "inverse_vol"
pos_sl_mode         = "none"              # no stops, no regime — both measured harmful
compounding_enabled = True
# harness: 24.40% CAGR / 25.92% MaxDD / Calmar 0.94
```

---

# ENGINE VALIDATION (runs #794–800, 2026-08-18)

Tasks A and B implemented in `positional_engine.py`, strictly additive
(`pos_min_ifp_score`, `pos_min_close`, `pos_base_range_score_w` — all
None/0 = inert, every prior POSITIONAL run reproduces byte-identically).
Live services untouched throughout.

| Run | Config | CAGR | MtM MaxDD | Calmar |
|---|---|---|---|---|
| #780/785 | prior best (no screen_gpt signals) | 15.24 | 31.13 | 0.49 |
| **#794 V1** | **FULL SPEC: IFP≥.40 + price≥20 + baseScore + ATR5.0** | 14.29 | **24.21** | **0.59** |
| #795 V2 | V1 − base-range score | 13.78 | 25.98 | 0.53 |
| #796 V3 | V1 − penny filter | 14.38 | 24.56 | 0.59 |
| #797 V4 | V1 − IFP gate | 13.59 | 29.09 | 0.47 |
| #798 V5 | V1 with ATR 5.5 | 14.50 | 27.19 | 0.53 |
| #799 V6 | V1 with IFP 0.38 | **14.90** | **24.05** | **0.62** |
| #800 V7 | V1 with IFP 0.42 | 13.13 | 26.91 | 0.49 |

**The port succeeded. Calmar 0.49 → 0.59 (+20%), and drawdown 31.13% → 24.21%
— the lowest engine drawdown achieved in this program**, while CAGR gave up
just 0.95 pts.

## Ablation — what actually carried the gain (engine, not harness)

| Removed from V1 | Calmar impact | Verdict |
|---|---|---|
| **IFP gate** | 0.59 → **0.47** (−0.12) | **the dominant contributor** |
| **base-range score** | 0.59 → 0.53 (−0.06) | **genuine, second-largest** |
| penny filter (price≥20) | 0.59 → 0.59 (0.00) | **no effect in-engine** |

Both harness findings that mattered replicated: the IFP gate is the engine's
biggest single improvement, and base tightness works **as a score** (removing
it costs 0.06). The penny filter did **not** replicate — it was worth +0.08
Calmar in the harness and exactly nothing here, because the engine's
`close > sma_200` requirement plus the ₹8cr turnover floor already exclude
essentially the same names. **It is harmless but pointless; keep it only as
cheap insurance.**

## Modification found: IFP 0.38 beats the specified 0.40

| IFP threshold | CAGR | MaxDD | Calmar |
|---|---|---|---|
| 0.38 | **14.90** | **24.05** | **0.62** |
| 0.40 (spec) | 14.29 | 24.21 | 0.59 |
| 0.42 | 13.13 | 26.91 | 0.49 |

The engine optimum sits one notch below the harness optimum (0.38 vs 0.40) —
expected, since the engine's universe is already pre-thinned by `close >
sma_200`, so the same threshold bites harder. The shape is the same
plateau-then-cliff (0.62 → 0.59 → 0.49), which is the reassuring part: it is
a peak with a smooth left side, not a spike. **Recommend IFP ≥ 0.38.**

ATR 5.0 also confirmed better than 5.5 in-engine (0.59 vs 0.53), matching the
harness.

## Task C — handled as a preset, NOT as code defaults (deliberate)

Changing the engine's NULL-fallback defaults (`pos_top_n or 10` → 30 etc.)
would silently alter how any historical run with a NULL column reproduces,
breaking the audit trail that this whole program depends on. The winning
parameter set is therefore frozen as a **saved preset** instead, which is how
presets #14–18 already work. One line changes this if you disagree.

## FINAL RECOMMENDED CONFIG (engine-validated)

```python
strategy               = "POSITIONAL"
pos_momentum           = "composite_rs"   # 5-factor: z(12-1)+z(6m)+z(3m)+z(6m/atr)+z(-base_range)
pos_min_ifp_score      = 0.38             # ENGINE optimum (harness said 0.40)
pos_min_close          = 20.0             # harmless; no measured effect in-engine
pos_min_turnover_cr    = 8.0              # do NOT raise — illiquidity premium
pos_atr_max_pct        = 5.0
pos_size_mode          = "inverse_vol"
pos_top_n = 30 ; pos_buffer_n = 60 ; pos_rebalance_days = 21
pos_sl_mode            = "none"           # no stops, no regime — both measured harmful
pos_base_range_score_w = 1.0
compounding_enabled    = True             # profit_only, ceiling Rs.2cr
# 15yr engine result: 14.90% CAGR / 24.05% MaxDD / Calmar 0.62
```

## Caveats

1. **Harness numbers, not engine.** Every prior harness→engine port has lost
   ground (idealised monthly marks, no per-name fill cost). Expect materially
   lower; the engine currently sits at Calmar 0.49 for run #780. The *relative*
   gains (IFP gate, penny filter, base-range-as-score) are what should port.
2. Requires two engine changes: an IFP/price filter in the composite SQL, and
   `base_range_20d_pct` added to the composite score.
3. Survivorship bias unchanged — all CAGR figures are upper bounds.
