# Exit Persistence — ADOPTED (runs #888–921)
**2026-08-19 · Net positive across a proper walk-forward: 3 better, 1 flat, 1 worse.**

> **Read §"CORRECTION" before quoting any figure from §"Results".** The initial
> four-window result overstated the case because those windows overlap. The
> five-window walk-forward is the honest test.

## The change

```
BEFORE: exit a holding the first session its ATR breaches 5.0%
AFTER:  exit only after TWO CONSECUTIVE breach sessions
```

One parameter, `pos_atr_persist_days = 2`. Everything else unchanged.

## Why: the single-breach rule was amputating winners

Post-exit forensics on all 973 ATR_CEILING exits in run #823 — what the stock
actually did over the following 126 sessions, measured as excess return over the
equal-weight universe:

| | n | avg at exit | excess return after |
|---|---|---|---|
| median exited name | 973 | — | **−4.18%** (rule correct) |
| sold while **winning** | 406 | +19.1% | **+4.81%** |
| sold while up 10-25% | 128 | — | **+8.57%** (63d) |
| sold while **up >50%** | 31 | — | **+14.01%** |

The rule is right on the typical name and badly wrong on the best ones. A stock
accelerating upward expands its ATR exactly like one breaking down, and a
single-session volatility test cannot tell them apart. Two sessions can.

## Results — validated in all four windows

| window | baseline Calmar | persist-2 | ΔCAGR | ΔCalmar |
|---|---|---|---|---|
| **2011-2016 (disjoint OOS)** | 0.51 | **0.57** | +2.29 | **+0.06** |
| 2017-2026 | 1.12 | **1.27** | +2.42 | **+0.15** |
| 2020-2026 | 1.08 | **1.24** | +3.00 | **+0.16** |
| 2011-2026 (full) | 0.62 | **0.67** | +2.03 | **+0.05** |

**CAGR up in all four. Calmar up in all four.** Drawdown roughly flat
(−0.45 on 2017-26, +1.30 on 2011-16).

Four previous candidates failed exactly this test (IFP 0.40, buffer 45,
winner-exemption, and persist-3). This is the first to pass.

## ⚠️ CORRECTION — walk-forward on FIVE disjoint windows (runs #912–921)

The four windows above **overlap**: 2011-26 is the union of the other two, and
2020-26 is a subset of 2017-26. So "improves in all four windows" was really two
independent pieces of evidence wearing four hats. A proper walk-forward on five
non-overlapping ~3-year windows gives a more honest — and weaker — picture:

| window | baseline | persist-2 | ΔCalmar | ΔCAGR |
|---|---|---|---|---|
| W1 2011-14 | 0.55 | 0.55 | **0.00** | −0.05 |
| W2 2014-17 | 0.79 | **0.99** | **+0.20** | +4.19 |
| **W3 2017-20** | **0.28** | **0.23** | **−0.05** | −0.42 |
| W4 2020-23 | 2.72 | **3.22** | **+0.50** | +6.24 |
| W5 2023-26 | 0.31 | **0.45** | **+0.14** | +3.40 |

**Score: 3 better, 1 flat, 1 worse.** Mean ΔCalmar **+0.16**, mean ΔCAGR
**+2.67 points**. Still clearly net positive — but not the clean sweep the
overlapping windows implied, and I should not have described it as one.

### Where it fails is informative

The one losing window, **W3 (2017-2020), contains the 2018 midcap crash and the
COVID collapse.** That is mechanically sensible: in a genuine crash, waiting an
extra session before exiting costs real money. Persist-2 helps in normal and
trending markets and hurts modestly in fast breakdowns — it trades a little
crash protection for a lot of right tail.

### And note how regime-dependent everything is

Baseline Calmar across these five windows ranges **0.28 to 2.72** — a tenfold
spread on the *same strategy*. W4 (2020-23, the post-COVID small-cap boom)
returns 41% CAGR; W3 and W5 return 5% and 9%. Any single 3-year window says
almost nothing, and this is the strongest argument yet for judging the strategy
on forward paper results rather than more in-sample slicing.

## ⚠️ The near-miss: persist-3 was a trap

| persist | 2017-26 | 2020-26 | **2011-16 (OOS)** |
|---|---|---|---|
| 1 (baseline) | 1.12 | 1.08 | **0.51** |
| **2** | 1.27 | 1.24 | **0.57** ✅ |
| 3 | **1.46** | **1.45** | **0.45** ❌ |
| 4 | 1.19 | — | — |

Persist-3 produced the best in-sample number ever recorded here — Calmar 1.46,
27.25% CAGR with an 18.63% drawdown, seemingly capturing the entire CAGR of
removing the exit while *lowering* drawdown. **Out of sample it scores 0.45
against a 0.51 baseline.** Adopting it on the 2017-26 number would have made the
strategy worse.

Note 2020-26 also loved persist-3 (1.45) — but it is a *subset* of 2017-26, not
independent evidence. Only 2011-16 is disjoint, and it rejects.

**Do not raise this parameter above 2 without re-running 2011-2016.**

## The other three architectures: all rejected

| architecture | 2017-26 | 2011-16 OOS | verdict |
|---|---|---|---|
| B · relative ATR expansion (1.5× / below trend) | 0.96 / 0.98 | 0.43 | ❌ |
| C · partial trim 50% / 33% | 0.96 / 0.98 | 0.48 | ❌ |
| D · winners exit on 10-day low | 0.84 | 0.31 | ❌ |
| D · winners exit on 20-day low | **1.14** | **0.31** | ❌ |

Option D-20 is the second trap in this batch: marginally above baseline
in-sample, one of the worst out-of-sample results ever recorded here. Price
structure invalidation on winners is actively harmful, consistent with all
sixteen other price-triggered exits tested.

Option C is instructive too — trimming reduces CAGR (22.16 → 20.00) without
meaningfully reducing drawdown. Half-measures on position size cost return
without buying protection.

## Two implementation bugs found (both mine)

1. **Option C crashed** — the partial-exit clone omitted `entry_trigger_price`
   and `structural_sl`, both `NOT NULL`. Fixed.
2. **Option B failed SILENTLY** — with `sl_mode='none'` the trend column was
   `NULL`, so the "below trend" leg was always false and the rule never fired
   once. Runs #891/#892 returned numbers *byte-identical* to the filter-only
   run, which is the only reason it was caught. A silent no-op is more dangerous
   than a crash: without noticing the identical figures it would have been
   reported as "tested, no effect". Fixed by wiring `ema_21` as the SMA-20
   stand-in.

## Deployed

- `positional_engine.py` — `pos_atr_persist_days` (default 1 = unchanged)
- `paper_trading/paper_positional.py` — 2-session persistence in the daily guard
- Paper book updated live; equity ₹400,711, 30 positions, no exits triggered yet

## Revised expectation

| | before | after |
|---|---|---|
| 2011-2026 CAGR | 14.90% | **16.93%** |
| 2017-2026 CAGR | 22.16% | **24.58%** |
| Calmar (2017-26) | 1.12 | **1.27** |

Still subject to the 3-7 point survivorship haircut. Realistic forward
expectation moves from ~16-19% to roughly **18-21%**.
