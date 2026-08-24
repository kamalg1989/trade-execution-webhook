# Data Integrity & Survivorship — Quantified
**2026-08-19 · The price-mismatch defect is small. The survivorship defect is much worse than assumed.**

Two investigations. One closes a worry; the other opens a serious one.

---

## PART 1 — Price mismatch defect: bounded, and it works AGAINST us

The engine ranks on `stock_indicators.close` and fills on `ohlcv_data.close`,
which disagree >5% for 4.44% of selected positions. Measured impact:

| variant | CAGR | MaxDD | Calmar | 2011-15 | 2016-20 | 2021-26 |
|---|---|---|---|---|---|---|
| as-published | 24.65 | 26.42 | 0.93 | **16.89** | 25.61 | **31.35** |
| drop mismatched rows | 25.20 | 25.97 | 0.97 | 16.41 | 27.75 | 31.21 |
| returns from the fill series | 25.29 | 26.42 | 0.96 | 17.45 | 27.05 | 31.35 |
| **fully consistent (rank + settle on ohlcv)** | **27.12** | 27.84 | **0.97** | **22.95** | 26.43 | 27.77 |

**Headline conclusion: the defect costs us ~0.04 Calmar — it makes results
slightly PESSIMISTIC, not optimistic.** I raised the alarm on this yesterday;
having measured it, the alarm was larger than warranted for the aggregate.

**But it badly distorts the time profile.** Fully consistent pricing moves
2011-15 CAGR from 16.89% → **22.95%** (+6.1 pts) and 2021-26 from 31.35% →
27.77% (−3.6 pts). So any statement of the form "this strategy has been getting
better over time" is an artifact. Sub-period attribution from previous reports
should not be trusted; the 15-year aggregate and all within-window A/B
comparisons still stand.

**Fix priority: low-to-medium.** It is a correctness issue worth fixing, but it
is not distorting the decisions made so far.

---

## PART 2 — ⚠️ SURVIVORSHIP: the universe contains essentially NO dead stocks

I tried to bound this from our own data by charging a terminal loss whenever a
held name disappears. **The test could not fire, and that is the finding.**

### Our database has no delistings in it at all

| symbols whose price history ends >90 days before the dataset end | **6 of 3,262 (0.2%)** |
|---|---|
| symbols whose last trade is in 2026 | 3,259 |
| terminal-loss events triggered in a 15-year backtest | **0** |

Charging −20%, −50%, −80% or even **−100%** on vanishing holdings changes the
result by **exactly nothing** (24.65 / 26.42 / 0.93 in all five cases) — because
there are no vanishing holdings to charge. This is a **pure-survivor universe**,
as expected from a DB built off the Dhan scrip master (which lists *currently
tradeable* instruments) and backfilled.

### There is a second, compounding defect: the universe grows

| year | symbols with data | eligible under #799 gates |
|---|---|---|
| 2011 | 1,011 | **134** |
| 2013 | 1,053 | 160 |
| 2015 | 1,146 | 260 |
| 2018 | 1,539 | 387 |
| 2021 | 1,719 | 654 |
| 2024 | 2,377 | 1,036 |
| 2026 | 3,259 | 1,106 |

2,283 of 3,262 symbols first appear after July 2011. NSE had well over 1,500
listed equities in 2011; we hold 1,011. **So the 2011 universe is not "the market
in 2011" — it is "the subset of the 2011 market that was still alive in 2026."**
Early years are sampled from proven 15-year survivors and the eligible pool is
only 134 names, versus 1,106 today.

This matters specifically for momentum. A momentum screener buys names making new
highs; Indian microcaps that later collapsed into compulsory delisting frequently
had spectacular momentum immediately beforehand. Those are exactly the positions
this strategy would have taken, and **they are structurally absent from the data.**

### How large? Literature says: potentially crippling

| study | before | after including delistings |
|---|---|---|
| S&P 100 cross-sectional momentum | 26% CAGR | **12.2%** (−13.8 pts) |
| Nasdaq 100 momentum | 46% CAGR | **16.4%** (−29.6 pts) |
| momentum rotational (general) | >20% | **<8%**, Sharpe halved |
| NIFTY Smallcap 250 (India) | — | 82.5% removal rate; 16.1% delisted |

Those studies backfill *today's index membership*, which is the worst-case
variant and worse than ours — ours is "all currently-listed stocks" rather than
"current index members." So −13.8 points is an upper bound on the analogy, not an
estimate. But it establishes the order of magnitude, and **around 1,000 companies
were compulsorily delisted from BSE/NSE in 2016-18 alone.**

### The 2.5%/yr haircut used in the earlier audit has no empirical basis

I should be blunt: that figure was a guess, applied to make the numbers look
appropriately conservative. The evidence above suggests it may be low by a
multiple. **Run #799's 14.90% engine CAGR is an upper bound whose error term is
plausibly larger than every improvement this programme has searched for.**

That reframes the last two days of work: we have been hunting ±0.05 Calmar
effects while sitting on an unmeasured bias of plausibly several CAGR points.

---

## PART 3 — Can the data be obtained?

Yes, but it is a procurement task, not a computation:

1. **NSE official EOD/historical data** (paid) — `marketdata@nse.co.in`,
   +91-22-2659 8385. The authoritative source; would include securities that
   have since been delisted.
2. **NSE delisting lists** — the exchange publishes companies proposed for and
   completed delisting; usable to build the *symbol list* even if prices must
   come from elsewhere.
3. **Third-party aggregators** (Trade Brains, Nirmal Bang, AUM Securities
   publish delisted-company lists) — usable for the symbol universe.

The practical minimum viable step: obtain the **list of NSE symbols delisted or
suspended 2011-2026**, then check how many were *ever* in our eligible universe
before they vanished. Even without their prices, knowing the count tells us
whether the missing names are 5% or 40% of historical candidates — which converts
survivorship from unmeasurable to bounded.

---

## RECOMMENDATION — and a change of direction

1. **Stop searching for new alpha mechanisms.** Ten tested across two rounds,
   zero adopted. The marginal return is measurably zero, and the error bar on
   every result is larger than the effects being chased.
2. **Acquire the delisted symbol list** (cheap, possibly free) and size the gap.
   This is now the single highest-value action available: it either de-risks
   every existing number or invalidates them, and nothing else can be trusted
   until it is known.
3. **Treat 14.90% CAGR as an optimistic ceiling, not a forecast.** For live
   position sizing, planning against something materially lower would be prudent
   until survivorship is measured.
4. **Paper-trade #799 under the pre-registered kill criteria.** Forward
   out-of-sample data is the only survivorship-free evidence available, and it
   accrues whether or not we keep researching.
5. Price-mismatch fix: worth doing for correctness, but do not expect it to
   change any conclusion.

## Sources

- [Survivorship-free momentum & trend-following](https://quantifiedstrategies.substack.com/p/survivorship-free-momentum-and-trend)
- [Survivorship bias in backtesting](https://www.quantifiedstrategies.com/survivorship-bias-in-backtesting/)
- [Survivorship bias in momentum rotational backtests](https://www.priceactionlab.com/Blog/2019/11/survivorship-bias-in-backtests-of-momentum-rotational-trading-strategies/)
- [Survivorship bias in India's NIFTY Smallcap 250](https://papers.ssrn.com/sol3/Delivery.cfm/5833162.pdf?abstractid=5833162&mirid=1)
- [NSE paid EOD/historical data](https://www.nseindia.com/static/market-data/eod-historical-data-subscription)
- [NSE list of companies proposed to be delisted](https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted)
- [Number of delisted companies rising](https://www.business-standard.com/article/markets/number-of-delisted-companies-on-the-rise-bourses-may-see-more-exits-118033000878_1.html)
