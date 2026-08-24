# CANSLIM on Our Data — Feasibility and Test
**2026-08-19 · Verdict: the earnings component adds +0.02 Calmar. Redundant.**

## 1. What is even implementable

| letter | requirement | status |
|---|---|---|
| **C** | quarterly EPS +25% YoY | ✅ computable from `earnings_fundamentals` |
| **A** | 3yr growth + **ROE ≥ 17%** | ⚠️ growth yes — **ROE impossible, no balance sheet** |
| **N** | new high / new product | ⚠️ `is_new_52w_high` yes — news, no |
| **S** | small float + volume surge | ⚠️ volume yes — **float impossible, no shares outstanding** |
| **L** | RS rating 80+ | ✅ this *is* our composite RS |
| **I** | institutional sponsorship | ⚠️ `ifp_score` proxy only |
| **M** | market uptrend | ✅ available (tested 3× — all destroyed value) |

**Our strategy is already ~4 of 7 CANSLIM.** L is the composite rank, I is the
IFP gate (our most robust component), N was tested (+0.08 harness, not adopted),
M was tested three ways and always lost. **The only untested letters are C and A.**

## 2. ⚠️ The window is far smaller than it looks

`earnings_fundamentals` spans 2010-2024 nominally. Actual per-row coverage after
a point-in-time `broadcast_date` join:

| period | rows with usable C and A |
|---|---|
| 2011-2018 | **0.0%** |
| 2019 | 2.0% |
| 2020-2023 | 43-58% |
| 2024 / 2025 / 2026 | 33% / 17% / 15% |

**Zero coverage before 2019.** So any CANSLIM test is effectively **2020-2023 —
about four years** — and coverage decays after that because `period_to` ends
2024-12-31. That alone makes the test weak regardless of outcome.

Point-in-time discipline: everything is keyed on `broadcast_date`, never
`period_to`. Median reporting lag is 43 days (95th percentile 91), so keying on
period end would have leaked six weeks of unknowable information per decision.

## 3. The headline result, and why it is not real

| config | universe | CAGR | MaxDD | Calmar |
|---|---|---|---|---|
| baseline 2011-26, all rows | 247 | 24.79 | 26.42 | 0.94 |
| C ≥ 25% | 93 | 31.44 | 30.55 | 1.03 |
| A ≥ 25% | 85 | 31.53 | 24.68 | 1.28 |
| **C ≥ 15% AND A ≥ 15%** | 69 | 30.81 | **23.02** | **1.34** |

0.94 → 1.34 looks like a major finding. It decomposes into three parts, only one
of which is about earnings:

| source | contribution |
|---|---|
| period effect (2011-26 → 2018-26 baseline) | **+0.12** |
| "has parseable fundamentals" (1.06 → 1.32) | **+0.26** |
| **the actual growth gates** (1.32 → **1.34**) | **+0.02** |

**O'Neil's C and A thresholds are worth +0.02 Calmar once period and data
availability are controlled for.**

## 4. Controls run before concluding

| control | result | reading |
|---|---|---|
| **coverage** — has-fund baseline vs gate | 1.32 vs 1.34 | the gate adds nothing |
| **concentration** — random 90/70/50 names | 0.43 / 0.24 / 0.17 | not merely shrinking the pool |
| **sign** — low-growth vs high-growth | 0.46 vs 1.34 | growth is *not* noise |
| **period** — 2018-26 no filter | 1.06 | part of the lift is just the era |

The concentration and sign controls matter: they rule out the lazy explanations.
Random cuts to the same universe size are catastrophic (0.17-0.43), and
selecting *low*-growth names costs 0.88 Calmar. Earnings growth genuinely
separates winners from losers.

## 5. So why does the gate add nothing?

Because **price momentum already contains it.**

Within the has-fundamentals universe, the momentum ranker's natural top-30 is
already populated by high-growth names — which is why filtering *to* high growth
changes almost nothing (1.32 → 1.34), while filtering *to* low growth wrecks the
book (0.46) by forcing it off the names it wants.

A company compounding EPS at 25%+ generally has the price trend to match, and
`z(12-1m) + z(6m) + z(3m)` picks that up months before the earnings are filed —
and without the 43-day reporting lag.

**This is the cleanest illustration yet of the pattern running through this whole
programme: the composite RS rank is a sufficient statistic for most of what other
frameworks add. CANSLIM's fundamental leg is not wrong, it is redundant.**

## 6. Recommendation

**Do not implement CANSLIM.** Two of seven letters are impossible on this data
(no balance sheet → no ROE; no shares outstanding → no float), the testable
letters are already in the strategy, and the one genuinely new component is worth
+0.02 Calmar on a four-year window.

If you want the CANSLIM *style* exposure, you already have it: L via composite
RS, I via the IFP gate, N via the 52-week-high proximity in the momentum legs.

## Caveats

- The +0.26 "has fundamentals" effect is not tradeable — you cannot buy "files
  parseable XBRL". It most likely encodes size, compliance and survivorship.
- 2020-2023 is four years and an exceptional bull period for Indian mid-caps.
- Survivorship applies with extra force here: CANSLIM targets high-growth names,
  which are disproportionately the ones that later fail — and we hold zero of
  the 269 wipeout delistings.
