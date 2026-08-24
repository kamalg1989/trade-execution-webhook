# Audited Final Numbers — Post-Remediation Backtest Matrix (2026-08-17)

Every audit condition is now implemented **in engine code** and re-run, not
adjusted analytically (except the survivorship haircut, which cannot be
simulated without delisted data):

| Audit fix | Implementation | Runs |
|---|---|---|
| V2 Mark-to-market equity | Positions marked at `ohlcv_weekly` Friday closes every week | all curves below |
| V4 Stressed-exit slippage | `exit_slippage_pct=0.30` on every sell leg (buys stay 0.10%) | 700–705 |
| V5 ADV liquidity cap | `adv_position_cap_pct=2.0` hard-coded in `PositionSizer` | 700–705 |
| V5 Compounding ceiling | `compounding_max_capital=₹20L` sizing cap | 701, 703 |
| V1 Survivorship | −2.5 CAGR pts/yr geometric haircut on WB books (ETFs exempt — they can't delist out of the sample) | Section 1/3 |
| V3 Tradeable diversifier | INDEX_TF strictly on NIFTYBEES / JUNIORBEES, 2019–2026 | 704, 705 |

## Guard effectiveness (the code works)

| Book | Max %ADV | Trades >10% ADV |
|---|---|---|
| #14 compounded, unguarded (old #691) | 49.58% | 20 |
| #14 comp capped+ADV-capped (#701) | **3.21%** | **0** |
| #15 comp capped+ADV-capped (#703) | **3.21%** | **0** |

(Residual >2% tail exists because the cap sizes on signal-date ADV while the
fill lands a week later on slightly different ADV — bounded and acceptable.)

---

## Section 1 — 15-year WB books, fully audited (MtM + slippage + ADV cap + haircut)

| Book | Run | CAGR% | True MaxDD% | Underwater | Calmar | Sharpe | Final equity |
|---|---|---|---|---|---|---|---|
| #14 static | 700 | **9.47** | 36.01 | **63.0 mo** | 0.26 | 0.26 | ₹15.5L |
| #14 comp capped @20L | 701 | **13.47** | 51.80 | 39.8 mo | 0.26 | 0.39 | ₹26.5L |
| #15 static | 702 | 10.86 | 42.49 | 37.7 mo | 0.26 | 0.31 | ₹18.7L |
| **#15 comp capped @20L** | 703 | **18.33** | 49.36 | 39.8 mo | **0.37** | **0.46** | ₹49.5L |

**The realized-only curves were hiding roughly half the risk.** Preset #14's
advertised 15.91% MaxDD is **36.0%** marked-to-market (pre-haircut MtM: 35.98%
vs realized 16.78% — +17.8pts). The audit's "MaxDD figures are lower bounds"
warning is now quantified: understatement ranged +12.5 to +24.7pts across books.

The haircut also exposes a fragility in #14 static: at 9.47% CAGR its worst
underwater spell stretches to 63 months, because the thin margin over
zero-growth years no longer clears old peaks quickly.

## Section 2 — Tradeable INDEX_TF (2019–2026, compounded, 0.30% exit slippage)

| ETF | Run | CAGR% | MaxDD% | Underwater | Calmar | Sharpe | Trades |
|---|---|---|---|---|---|---|---|
| NIFTYBEES ma200 | 704 | 10.26 | **5.33** | 31.1 mo | **1.93** | 0.27 | 21 |
| JUNIORBEES ma200 | 705 | **11.98** | 10.05 | 32.8 mo | 1.19 | 0.31 | 18 |

The synthetic proxy's 19.17% is confirmed **not tradeable**. The honest,
buyable diversifier delivers 10–12% at very shallow drawdowns — its value in
the blend is drawdown-depth compression, not return.

## Section 3 — FINAL HONEST BLENDS (2020-03 → 2026-08 common window; includes the COVID crash)

Best pairing is **#14 comp-capped × JUNIORBEES** (ρ = −0.059):

| Allocation | CAGR% | MaxDD% | Underwater | Calmar | Sharpe |
|---|---|---|---|---|---|
| 100% WB (#14 comp-capped) | 17.98 | 44.82 | 25.3 mo | 0.40 | 0.46 |
| 100% ITF (JUNIORBEES) | 12.99 | 10.05 | 32.8 mo | 1.29 | 0.31 |
| **60/40 WB/ITF** | **16.12** | 33.93 | **19.6 mo** | 0.48 | 0.42 |
| **30/70 WB/ITF** | **14.61** | **21.21** | 21.0 mo | **0.69** | 0.42 |

(#15-based and NIFTYBEES-based variants run 0.5–1.5 CAGR pts lower or carry
5pts more DD; full grid in `/tmp/qr/final_audited.py` output.)

Notable: unlike the pre-audit blends, these **do** shorten underwater duration
(25.3 → 19.6 mo at 60/40) — because this window contains an actual crash
(Mar-2020), where JUNIORBEES's shallow-drawdown profile earns its keep, rather
than the slow 2018-19 grind where averaging couldn't help.

---

## Bottom line — what survived the audit

| Claim (pre-audit) | Audited reality |
|---|---|
| "16.6% CAGR at 15.8% MaxDD" (60/40) | **16.1% at ~34% MaxDD** (60/40) — the return survived; the risk number did not |
| "17.8% at 17.75% DD" (30/70 variant) | **14.6% at 21.2% DD** (30/70) |
| Compounding reaches 21.8% | 18.3% capped (#15) / 13.5% (#14), with zero liquidity breaches |
| MaxDD ~16% | True MtM MaxDD 36–52% on WB books; only heavy ITF weighting brings blends near 20% |

- **Deployable recommendation:** `30/70 #14-comp-capped / JUNIORBEES-TF` —
  **~14.6% CAGR, ~21% true MaxDD, ~21 months max underwater, Calmar 0.69**,
  zero ADV breaches, all guards in code, every instrument buyable today.
- The aggressive alternative (60/40): ~16.1% CAGR but ~34% true MaxDD — only
  for an operator who has genuinely priced in a one-third account decline.
- All figures include: 0.30% stressed-exit slippage, 2% ADV cap, ₹20L
  compounding ceiling, weekly mark-to-market, and a −2.5%/yr survivorship
  haircut on the stock book. These are the numbers I would defend in a review.

**Remaining open item (unchanged from audit):** true delisted-stock data would
replace the flat haircut with measurement; and the blend window is 6.4 years —
the 15-year blend record cannot exist because the tradeable ETFs don't go back
that far. Both are data limitations, not modelling choices.
