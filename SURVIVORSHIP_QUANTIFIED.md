# Survivorship Bias — Quantified
**2026-08-19 · NSE delisted list cross-referenced against our universe**

Survivorship has moved from *unmeasurable* to *bounded*. The headline: **269 of
269 wipeout-type delistings are completely absent from our database**, and a
plausible central assumption puts the CAGR drag at **2.5–9.4 points**.

---

## 1. THE GAP, MEASURED

NSE's official list: **455 delisted symbols** (2002-2026), **398 inside our
backtest window** (2011-2026).

| category | count | in our DB? |
|---|---|---|
| **Compulsory delisting + liquidation ("wipeouts")** | **269** | **0 of 269** |
| Voluntary delisting (buyout, usually at a premium) | 111 | — |
| ITP / other | 18 | — |
| **total in-window** | **398** | **3 of 398** |

| | |
|---|---|
| our universe | 3,261 symbols |
| delisted names absent from it (in-window) | **395** |
| implied true historical universe | 3,656 |
| **share of the universe we never see** | **10.8%** |

**370 of the 398 were Main Board listings**, not SME/ITP — so this cannot be
dismissed as junk-tier noise. `symbols_meta` has 3,262 rows and zero inactive:
coverage of dead companies is exactly nil, not merely poor.

### An important softener, and an important sharpener

- **Softener:** 111 of the 398 were *voluntary* delistings, where holders are
  typically bought out at a premium. Those are not losses. **The loss-relevant
  count is 269, not 395.**
- **Sharpener:** this is a **lower bound**. The list covers *delisted* companies
  only. It excludes companies *suspended but still listed* — SEBI has referenced
  **~4,200 listed firms whose shares do not trade.** Those are equally invisible
  to us and are not counted above.

### The timing is the worst part

Wipeout-type delistings by formal date: **2016: 47 · 2017: 66 · 2018: 83** —
196 of 269 (73%) in three years. But **the formal delisting date lags the
economic death by years**; these companies were suspended and illiquid long
before. So the period in which our screener would actually have *bought* them is
roughly **2011-2015**.

That is precisely where our data is weakest on two other counts already
established: the panel holds only 976-1,146 symbols in 2011-2015 (vs 3,259 now),
and the `stock_indicators`/`ohlcv_data` price mismatch peaks at 12.5% in 2012.
**All three defects stack in the same window.**

---

## 2. CAGR IMPACT — SENSITIVITY (assumption-driven)

The gap size alone does not give the drag; that depends on how often a momentum
ranker would actually have *held* these names. So: replace a fraction `f` of each
month's 30 positions with a name mid-collapse. `f` is the assumption; everything
else is the real backtest.

Baseline (survivor universe): **24.91% CAGR / 26.42% DD / Calmar 0.94**

| f (share of book/month) | at −30%/mo | | at −60%/mo | |
|---|---|---|---|---|
| | CAGR | drag | CAGR | drag |
| 0 (baseline) | 24.91 | — | — | — |
| 0.5% | 22.38 | **−2.53** | 19.92 | −4.99 |
| 1.0% | 19.93 | **−4.98** | 15.23 | −9.68 |
| 2.0% | 15.50 | **−9.41** | 6.94 | −17.97 |
| 3.0% | 10.92 | −13.99 | −1.37 | −26.28 |
| 5.0% | 2.56 | −22.35 | −15.51 | −40.42 |

### What is a plausible `f`?

- Population rate would suggest ~12% (the missing names' share of the eligible
  pool) — but that is far too high, because a momentum ranker buys a name only
  while it is *rising*, and our gates (turnover ≥ ₹8cr, close ≥ ₹20, close >
  200-SMA, IFP ≥ 0.38) exclude most distressed microcaps outright.
- Against that: several of the missing names were large and liquid, with textbook
  momentum before collapse — **ABGSHIP** (ABG Shipyard), **AMTEKAUTO** (Amtek
  Auto), **ADHUNIK** (Adhunik Metaliks), **ASSAMCO**, **ANKURDRUGS**. These would
  comfortably have cleared our gates during their run-ups.
- If ~40-80 of the 269 wipeouts were eligible at some point and each occupied a
  slot for ~3 months across 186 months, `f` lands around **0.7-1.5%**.

**Central estimate: a drag of roughly 3-7 CAGR points**, i.e. run #799's 14.90%
engine CAGR is plausibly **8-12% in truth**. That straddles the published
momentum survivorship damage (−13.8 pts on S&P 100) and comfortably exceeds
every improvement this programme has searched for.

**The 2.5%/yr haircut used in the earlier audit was a guess of mine. This
analysis suggests it was roughly the right order of magnitude at the optimistic
end, and possibly 2-3× too small.**

---

## 3. WHAT THIS CHANGES

1. **Run #799 stands as the best *relative* configuration.** Every A/B comparison
   in this programme shares the bias in both arms, so the ablation conclusions
   (IFP gate helps, base-range-as-score helps, exit rules hurt) are unaffected.
2. **The absolute CAGR is not usable for planning.** For live position sizing,
   plan against something in the high single digits, not 14.9%.
3. **±0.05 Calmar mechanism hunting is not meaningful at this error level** —
   which is why ten consecutive negative results is not surprising and not worth
   extending.
4. **A full correction requires delisted price history**, which the free list does
   not include. That means a paid NSE EOD subscription
   (`marketdata@nse.co.in`, +91-22-2659 8385). Worth it only if you want a
   defensible absolute number; not needed to keep trading the strategy.

## 4. ARTEFACTS

- `delisted.xlsx` — the NSE source file
- `missing_delisted_symbols.csv` — **395 symbols we have no history for**
- `our_symbols.csv` — our 3,261-symbol universe
- `survivorship_crossref.py` — reusable cross-referencer (re-run when NSE updates)

## Caveats

- 3 of the 455 delisted symbols do appear in our DB; ticker reuse after delisting
  is possible, so a small number of those price series may belong to a *different*
  company that later took the same symbol. Not material at n=3.
- Symbol matching normalises case, whitespace and series suffixes. A company that
  was renamed before delisting could be missed, which would make the gap slightly
  larger than reported.
- The `f` sensitivity assumes a constant monthly loss rate rather than a modelled
  collapse path. It is a bound, not a simulation.
