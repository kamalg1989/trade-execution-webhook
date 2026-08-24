# Literature Strategy Sweep — 11 Published Strategies on the Positional Engine

**Date:** 2026-08-20 · **Runs:** #1035–#1045 (plus prior evidence cited below)
**Common base:** preset #909 config (composite_rs unless noted, top-30/buffer-60, 21d rebalance, ATR≤5% persist-2, IFP≥0.38, inverse-vol sizing, profit-only compounding to ₹2Cr, 0.20% slippage, full Dhan cost model), 2011-01-01 → 2026-08-16, NSE full universe with survivorship handling.
**Benchmarks:** #909 recommended = **16.93% CAGR / 25.4% MaxDD / Calmar 0.67** · #1010 relATR aggressive = **21.07 / 28.7 / 0.73**.

## Results (this sweep)

| # | Strategy (source) | Engine mapping | CAGR % | MaxDD % | Calmar |
|---|---|---|---|---|---|
| 1037 | Clenow vol-adjusted momentum — *Stocks on the Move* (Clenow 2015) | rank = z(mom12) + 2·z(mom12/ATR), inverse-vol sizing already on | **18.07** | 26.8 | **0.67** |
| 1036 | Intermediate momentum — Novy-Marx (2012), 12–7m horizon dominates | w_mom12=2, w_mom6=0.5, w_mom3=0 | 17.48 | 26.9 | 0.65 |
| 1040 | Vol-managed momentum — Barroso & Santa-Clara (2015), 25% target | pos_vol_target 25%, lb 126d | 16.99 | 25.4 | 0.67 |
| 1038 | Trend information ratio (Clenow slope×R² analog), 63d | pos_trend_ir_w=1, col 63 | 16.96 | 25.2 | 0.67 |
| 1039 | Frog-in-the-pan info discreteness — Da, Gurun & Warachka (2014) | pos_id_score_w=1 | 16.93 | 26.3 | 0.64 |
| 1044 | Dual momentum — Antonacci: absolute-momentum overlay + cash yield | regime MA200 + 2% band, 6% cash yield | 16.31 | 28.1 | 0.58 |
| 1042 | Concentrated momentum (fewer, stronger names) | top-15 / buffer-30 | 18.57 | 32.7 | 0.57 |
| 1043 | Diversified momentum | top-45 / buffer-90 | 15.07 | 24.7 | 0.61 |
| 1035 | Classic 12-1 momentum — Jegadeesh & Titman (1993) | w_mom12 only, all else 0 | 14.68 | 29.8 | 0.49 |
| 1041 | Low-volatility tilt (low-vol anomaly) | ATR ceiling tightened to 3.5% | 6.82 | 26.2 | 0.26 |
| 1045 | Minervini trend-template tightening (SEPA-style quality gates) | IFP≥0.5, base-tightness weight 2.0 | 4.83 | 36.3 | 0.13 |

## Prior evidence already in your run history (not re-run)

| Strategy (source) | Evidence | Result |
|---|---|---|
| Martin Luk relative-ATR exit (this week's port) | #1010 + split grid #1014–#1027 | 21.07 / 28.7 / **0.73** — current best |
| Barroso vol-targeting 12–20% | #811–#817 | 11–15% CAGR / 20–24% DD — cuts both |
| Connors RSI-2 mean reversion | #681 (RSI_REVERSION engine) | 6.9 / 53.3 — fails on daily Indian equities with delivery costs |
| Faber 10-month SMA timing | #705 (INDEX_TF JUNIORBEES 200d) | audited diversifier leg of the 30/70 blend |
| Donchian/box weekly breakout (Turtle-family) | #700/#703 (WEEKLY_BREAKOUT) | 9.5% static / 18.3% comp-capped, audited |
| 52-week-high momentum — George & Hwang (2004) | not mappable | needs a dist-from-52wk-high ranking column; noted as engine backlog |

## Read of the results

The composite you already run is the story: every attempt to replace it with a single published factor (12-1, low-vol, Minervini gates) loses badly, and every attempt to add one more academic overlay (frog-in-pan, vol-targeting, dual-momentum regime) lands within noise of the baseline. Your 4-factor composite + persist-2 ATR exit already harvests most of what this literature offers on daily Indian equity data.

Two findings have substance. Clenow-style vol-adjusted 12-month ranking (#1037) is the only ranking change that beat baseline CAGR meaningfully (+1.1pt for +1.4pt DD, Calmar-neutral) — consistent with Clenow's argument that momentum per unit of volatility, not raw momentum, is the tradeable signal. And the concentration axis behaves exactly as theory predicts: top-15 adds return and drawdown, top-45 removes both; top-30 sits at the Calmar optimum, confirming the preset's book size.

The two catastrophes are informative too. The 3.5% ATR ceiling (#1041) strangles the strategy — momentum needs volatile names, which is the same lesson the relATR work taught from the other direction. The Minervini-style gate tightening (#1045) starves the book of candidates in weak markets; his template is an entry discipline for discretionary swing trading, not a portfolio ranking.

## Recommendations

Keep #909 as recommended and #1010 as aggressive — nothing here displaces them. One combination is worth a single confirming run: **relATR 1.5/trim-33 exit + Clenow vadj-12 ranking** (the best exit found this week × the best ranking found this week). If it clears ~22% CAGR at ≤29% DD it becomes the new aggressive preset. Longer-term engine backlog: a dist-from-52-week-high ranking column (George & Hwang) is the one well-documented factor the engine cannot yet express.

## Sources

- [Jegadeesh & Titman momentum — 30-year review (Springer FMPM)](https://link.springer.com/article/10.1007/s11408-022-00417-8)
- [Novy-Marx: momentum horizon / intermediate momentum (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0378426614003252)
- [Clenow *Stocks on the Move* rules — Python replication (Teddy Koker)](https://teddykoker.com/2019/05/momentum-strategy-from-stocks-on-the-move-in-python/) · [Nifty-500 replication](https://utkaldesai.wordpress.com/2020/05/14/backtesting-andreas-clenows-stocks-on-the-move-on-nifty-500/) · [TuringTrader implementation](https://www.turingtrader.com/portfolios/clenow-stocks-on-the-move/)
- [George & Hwang: The 52-Week High and Momentum Investing (paper PDF)](https://www.bauer.uh.edu/tgeorge/papers/gh4-paper.pdf) · [International evidence (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0261560610001099)
- [Connors RSI-2 rules (QuantifiedStrategies)](https://www.quantifiedstrategies.com/rsi-2-strategy/)
- [Minervini trend template for Stage-2 stocks in India (sharpely)](https://sharpely.in/blogs/minervini-trend-template-for-stage2-stocks/) · [SEPA/VCP overview (FinancialTechWiz)](https://www.financialtechwiz.com/post/mark-minervini-trading-strategy/)
- Barroso & Santa-Clara (2015) "Momentum has its moments" — implemented in-engine (H3), prior runs #811–#817
- Da, Gurun & Warachka (2014) "Frog in the Pan" — implemented in-engine (H4 information discreteness)
- Antonacci *Dual Momentum* — absolute-momentum overlay mapped to the regime MA + cash-yield knobs
- Martin Luk systematic swing framework (user-supplied video breakdown) — relATR/trim port, runs #1008–#1034
