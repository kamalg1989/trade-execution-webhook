# Clean-Window Re-Runs & Honest Benchmarking (runs #818–823)
**2026-08-19 · The best news of this whole programme, and one clear "don't do that".**

Three results that matter:

1. **The strategy's core findings replicate on trustworthy data** — and look
   considerably better than the contaminated 15-year window suggested.
2. **Against a real, investable index it does generate alpha** — which answers
   the "are we even beating the index?" question properly for the first time.
3. **I stopped the price-mismatch fix.** It is a 2.1 GB TimescaleDB hypertable
   feeding the live screener; the risk is out of all proportion to +0.04 Calmar.

---

## 1. CLEAN-WINDOW RESULTS

All three data defects concentrate in 2011-2015 (price mismatch 9.5-12.5% of
eligible rows vs 0.3-0.9% post-2020; panel 976-1,146 symbols vs 3,259; the 269
wipeout delistings would have been bought there). So shorter, later windows are
materially more trustworthy.

| run | window | config | CAGR | MtM MaxDD | Calmar |
|---|---|---|---|---|---|
| #799 | 2011-26 | IFP 0.38 | 14.90 | 24.05 | 0.62 |
| #818 | 2017-26 | IFP 0.38 | 19.92 | 20.65 | 0.96 |
| **#823** | **2017-26** | **IFP 0.40** | **22.16** | **19.74** | **1.12** |
| #819 | 2020-26 | IFP 0.38 | 22.81 | 21.02 | 1.09 |

**Calmar improves monotonically as contamination falls: 0.62 → 0.96 → 1.09.**

### The ablations replicate — this is the important part

| removed | 2011-26 | 2017-26 | 2020-26 |
|---|---|---|---|
| **IFP gate** | −0.12 | **−0.12** | **−0.16** |
| **base-range score** | −0.06 | **−0.26** | — |

Both confirmed positive in every window. The IFP gate's contribution is
remarkably stable (−0.12 / −0.12 / −0.16). Base-range-as-score is **far more
valuable on clean data** (−0.26) than the full window implied (−0.06).

### The IFP threshold flips back to 0.40

| window | IFP 0.38 | IFP 0.40 |
|---|---|---|
| 2011-26 | **0.62** | 0.59 |
| 2017-26 | 0.96 | **1.12** |

The earlier "engine prefers 0.38" conclusion was an artifact of the contaminated
window. On clean data the engine agrees with the harness: **0.40 is correct.**
That is a small, concrete correction to the recommended production config.

---

## 2. BENCHMARKING — done honestly this time

A higher CAGR on a later window could just be regime; 2020-26 was extraordinary
for Indian mid/small caps. So benchmark it.

| benchmark | 2020-2026 CAGR | survivorship-free? | investable? |
|---|---|---|---|
| **NIFTYBEES** (Nifty 50 ETF) | **12.27%** | yes | yes |
| SETFNIF50 (Nifty 50 ETF) | 11.81% | yes | yes |
| **JUNIORBEES** (Nifty Next 50 ETF) | **16.53%** | yes | yes |
| SYNTH_EQW (equal-weight our universe) | 31.46% | **NO** | **no** |
| **strategy (#819)** | **22.81%** | no | yes |

### Two comparisons, two very different stories

**Against SYNTH_EQW (31.46%) the strategy loses badly.** But that comparison is
invalid: SYNTH_EQW is an equal-weight index of *our surviving universe*, so it is
inflated by exactly the survivorship bias we just quantified — worse than the
strategy is, because it holds every eventual 100-bagger from day one. It also
charges zero costs and cannot actually be bought. **Discard it as a yardstick.**

**Against real ETFs the strategy generates genuine alpha:**

| | strategy | vs Nifty 50 | vs Nifty Next 50 |
|---|---|---|---|
| raw (2020-26) | 22.81% | **+10.5 pts** | **+6.3 pts** |
| less survivorship (−3 to −7) | 16-20% | **+4 to +8 pts** | −0.5 to +3 pts |

**So: clearly ahead of Nifty 50 even after a conservative survivorship haircut;
roughly level with Nifty Next 50.** That is a real but modest edge — not the
"momentum machine" the raw 15-year harness numbers implied, and not the failure
the SYNTH_EQW comparison implied.

This is the direct answer to the question raised earlier in this programme
("all these are lesser than nifty index CAGR, right?"). **No — the strategy beats
the Nifty 50 ETF on a like-for-like basis.** The confusion came from comparing
against a survivor-biased internal proxy instead of a buyable index.

---

## 3. ⚠️ WHY I STOPPED THE PRICE-MISMATCH FIX

You approved this, and I'd normally just execute. But the situation is materially
different from what I described when I proposed it, so it needs a second look.

What I found on inspection:

- `stock_indicators` is a **TimescaleDB hypertable with 185 chunks, 2.1 GB**, on a
  box with **1.9 GB total RAM**, read by the live screener every evening.
- My first backup attempt produced a **4.6 KB file for 5.5 M rows** — `pg_dump -t
  stock_indicators` captured only the empty parent. Had I not checked the file
  size, I would have "backed up" nothing and then rewritten the table.
- A correct backup needs full-database or Timescale-aware dumping, and the
  recompute would rewrite 5.5 M rows across 185 chunks.

Against that: the **measured benefit is +0.04 Calmar.**

**Recommendation: don't.** The clean-window runs above achieve the same objective
— a trustworthy number — with zero production risk, because post-2020 the
mismatch is already under 1%. Use **#823 (2017-26) or #819 (2020-26) as the
reference result** and treat the 15-year figure as a contaminated lower bound.

What I'd do instead, both cheap and safe:

1. Add a **reconciliation check** to `daily_pipeline.sh`: flag any symbol whose
   `stock_indicators.close` diverges >1% from `ohlcv_data.close` for the same
   date. Catches recurrence without rewriting history.
2. If the historical fix is ever wanted, do it as a **deliberate maintenance
   window** with a verified full-DB dump, chunked by year, off-hours — not as a
   research side-quest.

---

## 4. REVISED RECOMMENDED CONFIG

```python
strategy               = "POSITIONAL"
pos_momentum           = "composite_rs"
pos_min_ifp_score      = 0.40      # CHANGED from 0.38 — 0.38 was a contaminated-window artifact
pos_base_range_score_w = 1.0       # worth MORE than previously measured (-0.26 on clean data)
pos_min_close          = 20.0
pos_min_turnover_cr    = 8.0       # do NOT raise
pos_atr_max_pct        = 5.0
pos_size_mode          = "inverse_vol"
pos_top_n = 30 ; pos_buffer_n = 60 ; pos_rebalance_days = 21
pos_sl_mode            = "none"
compounding_enabled    = True
# reference result (#823, 2017-2026): 22.16% CAGR / 19.74% MaxDD / Calmar 1.12
# expect ~16-19% after a survivorship haircut; benchmark Nifty 50 ETF ~12.3%
```

## 5. WHAT'S LEFT

- **Paper trading** (#799/#823 config) — the only survivorship-free evidence, and
  it accrues from today. Now the highest-value remaining action.
- Reconciliation check in the daily pipeline (cheap, safe).
- Paid NSE EOD delisted history — only if you want a defensible absolute number.

## Caveats

- 2020-26 is 6.6 years and a strong smallcap regime; #823's 9.6-year window is
  the better reference.
- ETF proxies only start 2019, so no like-for-like benchmark exists for 2011-2018.
- The strategy retains survivorship bias; the benchmarks do not. Every
  strategy-vs-ETF gap above should be read net of the 3-7 point haircut.
