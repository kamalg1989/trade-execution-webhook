# Why regime-switching between these configs cannot work

Tested the hypothesis "a different approach works each year, so we should pick
the approach to match the market". The hypothesis is sound in general — regime-
dependent strategy selection is real — but it fails here for a specific,
measurable reason.

## 1. The year dominates the config, ~7 to 1

Across 15 configs x 11 one-year windows (165 backtests):

    spread of YEAR means (best year  - worst year ):  Rs 104k
    spread of CONFIG means (best cfg - worst cfg  ):  Rs  15k

Which year it is explains about seven times more of the outcome than which
configuration is run. Every config wins together and loses together:

    2018: best -4k,  median -19k, worst -46k   <- everything loses
    2023: best 161k, median  80k, worst   8k   <- everything wins

These 15 configs are not different strategies. They are the same breakout
strategy with different knobs, so they are all long the same underlying bet and
are highly correlated. Switching among them changes the noise, not the bet.

## 2. Regime does not predict which config wins

Per-year winner, against that year's breadth (% of stocks above 200SMA):

    2016 (57)  B-basemax2            2022 (50)  G-full+lvl40cap
    2017 (70)  A-production          2023 (65)  F-full+risk1
    2018 (34)  Q-C+pullback          2024 (70)  H-C+minrisk3
    2019 (25)  E-basemax2+rising+vcp 2025 (34)  D-basemax2+vcp
    2020 (47)  A-production          2026 (32)  B-basemax2
    2021 (85)  F-full+risk1

Years with near-identical regimes pick different winners:
  * breadth ~32-34: 2018 -> pullback, 2025 -> vcp, 2026 -> basemax2
  * breadth  70   : 2017 -> production, 2024 -> minrisk3

The winner is effectively random with respect to regime. Fitting a
regime -> config mapping on this would be fitting noise, and with 15 configs x
11 years SOME config necessarily wins each year by chance — which is exactly
what "a different approach worked each year" looks like when nothing is
actually adapting.

## 3. What would actually be needed

The insight is right at a deeper level: one setup should not always apply. But
acting on it requires strategies that are UNCORRELATED, not parameter variants
of one strategy. The losing years (2016, 2018, 2019, 2022, 2025) are precisely
the years breakouts do not follow through — no breakout parameter set escapes
that, because the premise itself is what fails.

Genuine options, in increasing order of work:
  a. Sit out bad regimes entirely (cash is a position). Cheap to test, but
     needs a regime signal known IN ADVANCE — contemporaneous full-year breadth
     is look-ahead and must not be used.
  b. Add a genuinely different, low-correlation strategy for those regimes
     (e.g. mean-reversion), and allocate between them.
  c. Accept the strategy's real distribution: ~6 of 11 years positive.

What will NOT work is another parameter sweep. At ~250 backtests, roughly a
dozen results look "significant" at p=0.05 by chance alone; the search itself
is now the dominant source of false positives.

---

# Option 1 result: the regime state machine does NOT work

Campaign v5, against B (basemax=2 alone: 169.3k, 5/11 yrs, worst -36.4k,
avgDD 27.8k, 2,230 trades):

    W-B+regime100/3-block  146.8k  5/11  worst -27.0k  avgDD 17.1k  1270 trades
    V-B+regime50/3-half    107.9k  7/11  worst -35.5k  avgDD 21.7k  1800
    T-B+regime50/3-block    97.0k  7/11  worst -39.2k  avgDD 17.0k  1299
    U-B+regime50/5-block    91.3k  6/11  worst -39.0k  avgDD 17.0k  1308

## It fails on its own terms

Per-year, T vs B in exactly the years it was built to protect:

    2018  -36.4k -> -39.2k   WORSE
    2019   -8.8k -> -25.2k   MUCH WORSE
    2022  -35.8k -> -19.8k   better
    2025  -33.1k -> -10.5k   better

while giving up large amounts in the good years:

    2021  +71.8k -> +18.6k
    2017  +31.0k -> +14.4k
    2023  +92.3k -> +74.0k

It made the two worst years WORSE. A signal that cannot flag 2018 and 2019 in
advance is not identifying regimes — it is just trading less.

## The decisive comparison

    T (state machine)      1,299 trades -> 97.0k, 7/11 yrs, avgDD 17.0k
    C (naive daily gate)   1,299 trades -> 134.4k, 6/11 yrs, avgDD 15.5k

At IDENTICAL trade count the simple daily breadth filter beats the hysteresis
state machine on total P&L and on drawdown. The confirmation logic — the whole
premise of the design — adds nothing. What looks like a defensive benefit
(7/11 years, lower drawdown) is just the mechanical consequence of taking 42%
fewer trades, not of taking better ones.

## What this means for Option 2

If bad regimes are not identifiable in advance, then an allocation layer that
SWITCHES between two strategies fails for the same reason — the switch would be
wrong exactly when it matters. A second strategy would have to run ALWAYS-ON
alongside the first, with diversification doing the work rather than timing.
That is a materially different (and more honest) design than "detect regime,
pick strategy".

---

# Positional sweep result: partially real, but the drawdown disqualifies it

48 configs (3 momentum x 4 rebalance x 4 top_n) x 11 windows = 528 backtests,
with true daily mark-to-market drawdown.

## 1. Does the ranking transfer?  Spearman FIT->TEST = +0.39

Better than noise, weaker than solid. And the top-5-on-FIT table shows exactly
why a single "sweet spot" must not be trusted:

    config                      FIT     TEST   TEST rank
    (pct_chg_3m, 42, 5)        709k    -441k     48/48    <- BEST on fit, WORST on test
    (pct_chg_6m, 63, 5)        686k     690k      1/48
    (pct_chg_6m, 10, 5)        656k     275k     31/48
    (pct_chg_6m, 63, 10)       576k     401k     22/48
    (pct_chg_6m, 63, 15)       541k     461k     14/48

Picking the single best config on 2016-20 would have selected the WORST config
of all 48 on 2021-26, losing Rs 441k. That is the overfitting trap firing live.

## 2. But the AXIS effects are real, and that is the useful finding

The marginals are smooth plateaus, not spikes:

    momentum:   1y 915k | 6m 862k | 3m 309k      <- 3m clearly broken
    top_n:      5 637k | 10 673k | 15 719k | 20 753k   <- monotonic, more = better
    rebalance:  10 709k | 21 672k | 42 596k | 63 805k  <- flat-ish, weakest axis

So the transferable knowledge is NOT a magic triple. It is:
  * use 6-month or 12-month momentum, never 3-month
  * hold MORE names (20 > 15 > 10 > 5) — diversification, not concentration
  * rebalance frequency barely matters, so pick the cheapest (least turnover)

Those are exactly the conclusions that survive being averaged over everything
else, which is what makes them believable where the single best cell is not.

## 3. The disqualifying number: maxDD 42-50%

Every robust config draws down 42-50% peak-to-trough, with worst YEARS of
-Rs 100k to -Rs 152k on Rs 400k (-25% to -38%). The breakout book's worst year
was -Rs 23.5k (-6%) with ~15k average drawdown.

This is not a better strategy. It is a much higher-octane one: bigger returns,
and losses roughly 6x deeper. Whether that is acceptable is a risk-tolerance
decision, not a backtest result — and it should be made knowing that a ~45%
drawdown is the normal case here, not the tail.

Note also (pct_chg_6m, 63, 5) tops both total AND yrs-positive at 8/11, but it
holds only 5 names and trades 9 times a year — with n that small, its 1376k is
far more likely to be luck than the plateau-supported settings above.
