# Literature Round 2 — Seven New Mechanisms Tested
**2026-08-19 · No adoptable alpha. Two bugs found, one of them in production data.**

Searched current academic and practitioner literature for mechanisms never tried
here, built seven of them, and screened all seven. **All seven are negative.**

The valuable output of this round is not a strategy improvement — it is (a) an
89-day look-ahead bug I wrote and caught, and (b) a **production data-integrity
defect that affects every backtest number this programme has produced.**

---

## 1. RESULTS — all seven negative

Baseline throughout: sg2 winner, 24.40% CAGR / 25.92% MaxDD / **Calmar 0.94**.

| # | Mechanism | Source | Best as SCORE | Best as FILTER |
|---|---|---|---|---|
| 1 | Trend quality, slope×R² | Clenow, *Stocks on the Move* | **0.94** (parity) | — |
| 2 | Trend linearity R² alone | Gettleman/Marks regression form | 0.86 | 0.85 |
| 3 | Momentum acceleration Δ6m | Gettleman & Marks (2005) | 0.84 | — |
| 4 | Turnover froth (rel. to own norm) | Lee & Swaminathan (2000), *JF* | 0.87 | **0.42** |
| 5 | On-balance-volume slope | practitioner standard | 0.89 | — |
| 6 | Up/down volume ratio | practitioner standard | 0.90 | 0.74 |
| 7 | Momentum gap / crowding | Huang (2018) | **not tested — see §4** | — |

Nothing beats 0.94. The best case is parity.

### Notable specifics

- **Acceleration** (reported +6.15% spread in US data) gives 0.84 here, and the
  *sign check* also gives 0.76 — so it is diluting the composite rather than
  carrying inverted information. Momentum acceleration does not survive on NSE
  data once 12-1/6m/3m momentum is already in the score.
- **Lee & Swaminathan turnover froth** was the most promising lead (an India
  backtest reported 14.01%→17.95% CAGR from an "anti-speculation" turnover
  filter). It fails both ways here: as a penalty 0.85–0.87, and as a **filter it
  is destructive (0.52 at ≤2.0×, 0.42 at ≤1.5×)**. Notably the *reward* direction
  (0.87) slightly beat the *penalty* direction (0.85) — the opposite of the
  published sign. This is the illiquidity premium re-appearing: on NSE, anything
  that screens out actively-traded names removes return.
- **Trend R² as a filter destroys the edge** (0.67–0.85) while being roughly
  neutral as a score — the third time this programme has seen that exact
  asymmetry (base tightness, IFP inverted, now R²).

---

## 2. THE BUG I WROTE — an 89-day look-ahead

My first Clenow implementation produced:

| config | CAGR | MaxDD | Calmar |
|---|---|---|---|
| baseline | 24.40 | 25.92 | 0.94 |
| clenow_90 w=1.5 | **31.83** | 25.69 | **1.24** |
| clenow_90 **alone** | **55.84** | 32.44 | **1.72** |

CAGR +7.4 points with drawdown flat, and 55% CAGR standalone. I did not report
this as a finding, because the shape was wrong: monotone improvement with factor
weight and no drawdown cost is what look-ahead looks like, not what alpha looks
like. Three checks confirmed it:

1. **Lag test — decisive.** Lagging the feature by one month made it *better*
   (1.24 → 1.49), and by two months better still (1.50). **Stale information
   cannot beat fresh information.** That is proof of a bug, not evidence of a
   signal.
2. **Magnitude implausibility.** No long-only NSE momentum book returns 55% CAGR
   over 15 years.
3. **Decomposition.** "Slope alone" gave 40% CAGR — the leak, not the R².

**Root cause:** I computed the rolling regression via `np.convolve` and sliced
the output at `[win-1 : len+win-1]`. With `w = reversed(t_dev)`, `conv[k]` is the
window *ending* at index `k`, so the correct slice is `[0:len]`. My slice took
the window ending **89 sessions in the future.**

**After the fix** — and after validating the vectorised regression against
brute-force `np.polyfit` on 193 random samples (0 mismatches) — the entire
effect vanishes:

| config | before fix | after fix |
|---|---|---|
| clenow_90 w=1.0 | 1.14 | **0.91** |
| clenow_90 w=1.5 | 1.24 | 0.91 |
| clenow_90 alone | 1.72 | **0.53** |
| lag test | lagging *helped* | lagging **hurts** ✓ |

The lag test is now a standing guard in the harness. Any future feature where
lagging improves the result has a leak.

---

## 3. ⚠️ PRODUCTION DATA DEFECT — `stock_indicators` and `ohlcv_data` disagree

Found while verifying the above. **These two tables report different closes for
the same (symbol, date).**

| scope | rows | close mismatch >5% |
|---|---|---|
| all panel rows | 281,501 | 3.49% |
| eligible universe (#799 gates) | 45,297 | 2.25% |
| **rows reaching an actual top-30 book** | 5,423 | **4.44%** |

**Why this matters:** the engine **ranks on `stock_indicators.close`** but
**fills on `ohlcv_data.close`**. For 4.4% of positions actually selected, the
price used to pick the stock and the price used to trade it differ by >5%.

It is **strongly time-concentrated**, and worst exactly where the backtest has
the least data:

| year | 2011 | 2012 | 2013 | 2015 | 2018 | 2021 | 2024 | 2026 |
|---|---|---|---|---|---|---|---|---|
| mismatch % | 9.5 | **12.5** | 9.7 | 10.3 | 5.1 | 0.5 | 0.4 | 0.3 |

**And it is not confined to microcaps** — the affected eligible names are liquid,
genuinely-tradeable stocks: ZEEL, DISHTV, HEXT, TATACOMM, TATACHEM, YESBANK,
IDEA, CGPOWER, IEX, TRIVENI (44 distinct symbols in the eligible set).

**Likely cause:** `ohlcv_data` has been re-ingested/corporate-action-adjusted at
some point, while historical `stock_indicators` rows were computed from the
prices as they stood then and never recomputed. Observed ratios are scattered
(0.70, 0.09, 1.41, 2.30 …) rather than clean 2:1 / 10:1 split ratios, consistent
with cumulative adjustment drift rather than a single split.

**Consequence for this programme:** every CAGR/drawdown figure produced here
carries an additional error term concentrated in 2011–2015. It does not
invalidate *relative* comparisons made within the same window (both arms of every
A/B share the defect), which is why the ablation conclusions still stand. But the
absolute 15-year numbers are less trustworthy than assumed, on top of the known
survivorship bias.

**Recommended fix (not applied — it touches BAU):** recompute
`stock_indicators` from the current `ohlcv_data` for historical dates, or add a
reconciliation check to `custom-screener-compute.timer` that flags divergence.
I have deliberately not run this: it rewrites a production table the live
screener depends on, and it would change how every historical run reproduces.

---

## 4. WHAT I DELIBERATELY DID NOT TEST

**Momentum gap / crowding** (Huang 2018 — the momentum gap negatively predicts
momentum profits; invest only when it is below the 80th percentile). Skipped on
purpose: it is a **factor-timing rule**, and factor-breadth timing plus three
regime-shield variants are already on the excluded list as empirically
destructive here. Testing it would be re-running a settled negative in new
clothing.

---

## 5. HONEST ASSESSMENT

Round 2 found no alpha. Combined with H1/H3/H4, that is **ten mechanisms tested
across two rounds with zero adoptable improvements.** Run #799 (14.90% CAGR /
24.05% MaxDD / Calmar 0.62) remains the best engine configuration.

The pattern across everything tested is now quite consistent, and I think it is
the real finding: **this strategy's edge lives almost entirely in the
eligibility gate (IFP ≥ 0.38) and the 5-factor composite rank.** Every attempt to
add information — exit rules, exposure scaling, correlation constraints,
alternative momentum estimators, volume/trend-quality overlays — either does
nothing or subtracts. That is what a reasonably efficient, already-well-specified
strategy looks like.

Where I would look next, in order of expected value:

1. **Fix the data defect** (§3). This is the only item here with a known,
   quantified impact on the numbers, and it is cheap.
2. **Resolve survivorship bias** — acquire delisted-symbol history. Still the
   largest unquantified error in every result, and it flatters momentum
   specifically. No new mechanism can be trusted at the ±0.05 Calmar level while
   an unmeasured bias of plausibly larger size sits underneath.
3. **Paper-trade #799 under the kill-criteria protocol** rather than continuing
   to search. Ten consecutive negatives is reasonable evidence that further
   in-sample search has low expected value.

I would push back on continuing to hunt for indicators. The marginal return has
been zero across ten well-motivated, literature-grounded attempts, while two real
bugs surfaced in a single afternoon of verification work. The evidence suggests
effort is better spent on data quality and out-of-sample validation than on
finding an eleventh mechanism.

## Sources

- Lee & Swaminathan, "Price Momentum and Trading Volume", *Journal of Finance* (2000)
- Gettleman & Marks, "Acceleration Strategies" (2005)
- Huang, "The Momentum Gap and Return Predictability" (2018)
- Clenow, *Stocks on the Move* (regression-slope × R² trend quality)
- BacktestIndia 18-year NSE factor backtests; MomentumLAB 10-year India study
