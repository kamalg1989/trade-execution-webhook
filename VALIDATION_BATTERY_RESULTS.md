# Validation Battery — runs #818–848
**2026-08-19 · ALL 31 COMPLETE.**

## VERDICT: change nothing except reverting IFP to 0.38

Two candidate improvements were found on 2017-26. **Neither replicated on
2020-26.** Both are rejected. The config that survives is the one we started
with, minus a false-precision tweak.

Read every sweep by its **neighbourhood minimum** — the worst of {value, its two
neighbours}. A parameter whose headline number is high but whose neighbours are
poor is a knife edge, not an optimum, and will not survive live.

---

## 1. IFP GATE — the 0.40 claim is dead

| threshold | 2017-26 Calmar | 2020-26 Calmar |
|---|---|---|
| none (no gate) | 0.84 | 0.93 |
| 0.34 | 0.91 | — |
| 0.36 | 0.91 | — |
| 0.38 | 0.96 | **1.09** |
| **0.40** | **1.12** | 1.08 |
| 0.42 | 0.94 | — |
| 0.44 | 0.65 | — |

**Two conclusions.**

1. **The gate itself is real and valuable.** Every threshold from 0.34 to 0.42
   beats no gate at all (0.91–1.12 vs 0.84), and it replicates on 2020-26
   (1.08–1.09 vs 0.93). Keep the gate.
2. **The exact threshold is not determined, and 0.40 is not special.** On
   2020-26, 0.38 and 0.40 are a dead heat (1.09 vs 1.08). The 1.12 on 2017-26 is
   a favourable cell inside a 0.34–0.42 plateau, not a real optimum.

**I was wrong to recommend switching to 0.40.** I called 0.38 "a
contaminated-window artifact" on the strength of one pairwise comparison; the
sweep does not support that. Either value is fine — **revert to 0.38**, which
wins on 2 of 3 windows and has the longer history behind it.

**And do not quote 1.12 as the strategy's Calmar.** The honest expectation for
the IFP gate anywhere in its plateau is **~0.95**.

---

## 2. EXIT BAND — looked excellent, then failed replication

`pos_buffer_n` is the rank at which a holding is sold. Current setting is 60.

**On 2017-26 the whole 30–50 region beats 60, convincingly:**

| buffer | CAGR | MaxDD | Calmar | neighbourhood min |
|---|---|---|---|---|
| 30 (no band) | 20.78 | 17.95 | 1.16 | — |
| 35 | 20.91 | 17.84 | 1.17 | 1.15 |
| 40 | 20.58 | 17.86 | 1.15 | 1.15 |
| **45** | 21.77 | **17.46** | 1.25 | **1.15** |
| **50** | 22.42 | 17.55 | **1.28** | 1.12 |
| 60 (current) | 22.16 | 19.74 | 1.12 | 1.01 |
| 90 | 21.82 | 21.56 | 1.01 | — |

A broad elevated plateau, not a spike — 2.2 points less drawdown at equal CAGR.
It also held at IFP 0.38 (buffer 45 → 1.10 vs buffer 60 → 0.96) and under 2×
cost (0.85 vs 0.80). Everything about it looked real.

**Then the replication test:**

| window | buffer 60 | buffer 45 | buffer 40 |
|---|---|---|---|
| 2017-26 | 1.12 | **1.25** | 1.15 |
| **2020-26** | **1.08** | **1.09** | **1.02** |

**On 2020-26 the band change is worth +0.01 — nothing.** And buffer 40 is
actively worse (1.02 vs 1.08).

Since 2017-26 minus 2020-26 leaves 2017-2019, the entire benefit is concentrated
in that stretch — which contains the 2018 Indian midcap crash. The plausible
mechanism is real (a tighter band exits deteriorating names faster in a crash),
but **it rests on a single market episode.** That is not enough to change a
production parameter.

**REJECTED.** Keep buffer 60. Re-examine if forward data shows a 2018-style
midcap unwind.

---

## 3. ROBUSTNESS OF EVERY OTHER AXIS (2017-26, IFP 0.40)

| axis | values → Calmar | verdict |
|---|---|---|
| **portfolio size N** | 20 → 0.73 · **30 → 1.12** · 40 → 1.00 | N=30 confirmed |
| **ATR ceiling** | 4.5 → 0.78 · **5.0 → 1.12** · 5.5 → 0.87 | ⚠️ **knife edge** |
| **base-range weight** | 0.0 → 0.70 · 0.5 → 0.91 · 1.0 → 1.12 · 1.5 → 1.16 | monotone, robust |
| **turnover floor** | 5 → 1.14 · **8 → 1.12** · 12 → 1.08 | flat; do not raise |
| **cost 2×** (slip 0.45%) | 1.12 → **0.80** | degrades gracefully |

### ⚠️ The ATR ceiling is the weakest part of the strategy

4.5 → 0.78, 5.0 → 1.12, 5.5 → 0.87. **Neighbourhood minimum 0.78.** A ±0.5
change in a single parameter costs 0.25–0.34 Calmar. That is not a plateau; it
is a spike, and it is the most overfit-looking parameter in the configuration.

It was originally chosen on the full 2011-26 window (where 5.0 beat 5.5, 0.59 vs
0.53) so it is not *purely* fitted to this window — but the sharpness is a real
concern and I had not tested 4.5 before today. **Treat 5.0 as provisional and
expect live results to behave more like the 0.78–0.87 neighbours than the 1.12
peak.**

### Everything else is well-behaved

N=30 sits on a proper interior optimum. Base-range weight is monotone (and 1.5
slightly beats 1.0 — worth testing 2.0). The turnover floor is flat between 5
and 12, confirming the illiquidity premium finding without the cliff seen on the
full window. At 2× modelled friction the strategy still returns 18.79% at
Calmar 0.80 — it is not living on thin execution assumptions.

---

## 4. REVISED CONFIG

```python
pos_min_ifp_score      = 0.38     # REVERTED from 0.40 — sweep shows no real difference
pos_top_n              = 30       # confirmed interior optimum
pos_buffer_n           = 60       # UNCHANGED — 45 failed 2020-26 replication
pos_atr_max_pct        = 5.0      # PROVISIONAL — knife edge, neighbours 0.78/0.87
pos_base_range_score_w = 1.0      # robust; 1.5 marginally better
pos_min_turnover_cr    = 8.0      # flat 5-12; do not raise further
pos_size_mode          = "inverse_vol"
pos_sl_mode            = "none"
```

**Honest expected Calmar: ~0.95, not 1.12.** And that is before the 3–7 point
survivorship haircut on CAGR.

## 4b. TRUE OUT-OF-SAMPLE TEST — 2011-2016 (runs #849–854)

**Correcting my own error first:** I described 2020-26 as a "held-out window".
It is not — it is a *subset* of 2017-26, so failing there only localises an
effect to 2017-2019; it is not independent replication. The only genuinely
disjoint window we hold is **2011-2016**.

Caveat: it is also the most contaminated (price mismatch 9.5-12.5%, ~1,000
symbol universe, wipeout delistings invisible). Independent but noisy.

| config on 2011-2016 | CAGR | MaxDD | Calmar | vs baseline |
|---|---|---|---|---|
| baseline (IFP .38, buf 60, brw 1.0) | 12.19 | 24.05 | **0.51** | — |
| IFP 0.40 | 12.42 | 22.92 | 0.54 | +0.03 |
| buffer 45 | 11.90 | 22.87 | 0.52 | +0.01 |
| buffer 50 | 11.76 | 23.10 | 0.51 | 0.00 |
| **no IFP gate** | 12.03 | 29.09 | **0.41** | **−0.10** |
| **no base-range score** | 13.60 | 25.13 | **0.54** | **+0.03** |

### ✅ The IFP gate is confirmed in all four windows

| window | cost of removing the gate |
|---|---|
| 2011-2016 (out-of-sample) | **−0.10** |
| 2011-2026 | −0.12 |
| 2017-2026 | −0.12 |
| 2020-2026 | −0.16 |

Four windows, one of them genuinely disjoint, all agreeing within a narrow band.
**This is the strategy's most robust component and the finding I have most
confidence in.**

### ❌ The base-range score does NOT survive out-of-sample

| window | cost of removing it |
|---|---|
| 2011-2016 (out-of-sample) | **+0.03 — removing it HELPS** |
| 2011-2026 | −0.06 |
| 2017-2026 | −0.26 |

On the disjoint window, dropping the factor *raises* CAGR from 12.19% to 13.60%.
Its entire value is concentrated in 2017-2026.

**This corrects something I told you earlier.** I called base-range-as-score
"the second-biggest contributor" and "robust, monotone" — but the monotone
weight sweep (0.0→0.70, 0.5→0.91, 1.0→1.12, 1.5→1.16) was run *entirely on
2017-26*. A clean gradient inside one window says nothing about whether the
factor generalises, and here it does not.

**Status: keep it (it is not harmful on average across windows), but it is a
window-dependent factor, not a structural one.** Do not count it as part of the
strategy's core evidence.

### Both rejected candidates stay rejected

IFP 0.40 (+0.03) and buffer 45 (+0.01) are as flat out-of-sample as they were on
2020-26. Rejection confirmed on independent data.

### And the level is much lower in 2011-2016

Calmar ~0.51 versus ~0.96 (2017-26) and ~1.08 (2020-26). Either the regime was
genuinely harder (2011 correction, 2013 taper, 2015-16 selloff) or the
contamination is depressing it — both are concentrated in exactly this window,
and the two cannot be separated with the data we have.

---

## 5. THE META-FINDING — single-window tuning is unreliable

Two independent "improvements" were found by optimising on 2017-26:

| candidate | 2017-26 gain | 2020-26 gain |
|---|---|---|
| IFP 0.38 → 0.40 | **+0.16** | −0.01 |
| buffer 60 → 45 | **+0.13** | +0.01 |

**Both vanished on the held-out window.** Two for two. That is not coincidence —
it is what a 9.6-year sample with ~115 rebalances and 3-4 independent regimes
does when you tune multiple parameters against it.

Practical rule going forward: **no parameter change is adopted on a single
window.** Fit on 2017-26, confirm on 2020-26, and require both. Anything that
only appears in one is treated as noise regardless of how clean the sweep looks.

Notably, the component that DOES replicate everywhere is the one never tuned
finely — **the IFP gate's existence** (−0.10 to −0.16 across four windows). The
base-range score, which I previously listed alongside it, does not survive the
out-of-sample test (§4b). N=30 and no-stop-loss remain confirmed but were only
tested on 2017-26.

**What the strategy actually rests on, in order of evidential strength:**

1. **The IFP gate** — four windows, one disjoint, all agree. Solid.
2. **The composite momentum rank itself** — the base signal; never in doubt.
3. **N=30, no stop loss, inverse-vol sizing** — confirmed, but single-window.
4. **The base-range score** — window-dependent. Keep, but do not rely on.

Everything else tested across this programme is noise.

## 6. WHAT THIS MEANS FOR THE PAPER BOOK

It is running IFP 0.40, buffer 60. Given 0.38 and 0.40 are indistinguishable,
**I would not disturb the book to change it** — restarting would discard the
forward record for no measurable gain. If the band change replicates, that is
worth a clean restart, since 2.3 drawdown points is material and the book is
only one day old.
