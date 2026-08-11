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
