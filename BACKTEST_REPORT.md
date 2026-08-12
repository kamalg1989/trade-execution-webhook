# Backtest Programme — Consolidated Report

**Period covered:** every run from Run #1 to Run #464 (455 completed runs), plus
~1,100 additional in-process sweep backtests that never entered the run table.
**Compiled:** 2026-08-12
**Scope:** what we set out to do, what data exists, how the backtest works, every
experiment attempted, the per-year outcome of each, what worked, what failed, and
what we now believe.

---

## 1. What we are trying to do

### 1.1 The starting position

`screen_gpt.py` runs nightly, scans ~2,300 NSE EQ-series stocks, and produces
ranked swing-trade candidates. The top picks fire Telegram buy alerts. It works —
it has been trading real money — but nobody had ever measured it. The questions
that started this programme were:

1. **Does the quant funnel actually make money**, over a long enough period and
   across enough market regimes to be believable?
2. **Which of its ~30 tunable parameters matter**, and which are cargo cult?
3. **Can drawdown be reduced** without giving up the returns?

The unstated goal, which became the actual goal, is the one Kamal stated directly
partway through: *"I need consistent returns with less drawdowns."*

### 1.2 What "consistent" turned out to mean

The single most important discovery of the whole programme is that **the year
matters far more than the configuration**. Across the 11-year campaign, the
spread between the best and worst *config* in a given year was ~₹15k, while the
spread between the best and worst *year* for a given config was ~₹104k — roughly
**7x**. Every config wins in 2023 and loses in 2018. That reframed the objective
from "find the best settings" to "find something whose bad years are survivable."

---

## 2. The data

Everything lives in Postgres on the VPS (165.232.187.97), database `market_data`.

| Table | Rows | Symbols | Coverage | What it is |
|---|---|---|---|---|
| `ohlcv_data` | 6,077,108 | 3,261 | 2010-01-04 → 2026-08-11 | Daily OHLCV, the raw price history everything is built on |
| `stock_indicators` | 5,508,656 | 3,261 | 2011-07-18 → 2026-08-11 | Precomputed per-day indicators: SMA50/200, EMA10/21/50, distance-from-MA %, `pct_chg_3m/6m/1y`, `turnover_1m_avg_cr`, ATR, IFP score |
| `earnings_filings` | 146,920 | 2,706 | 2011-01-06 → 2026-08-06 | Every NSE results filing with its **broadcast date** and XBRL URL — harvested specifically for this programme |
| `earnings_fundamentals` | 80,414 | 2,378 | 2011-01-11 → 2026-08-06 | Parsed XBRL: revenue, PAT, EPS per filing. **Backfill is now 100% complete** (75,698 parsed OK, 4,716 failed) |
| `backtest_runs` | 455 | — | 2026-08-08 → 2026-08-12 | One row per run, with the full config that produced it |
| `backtest_trades` | 69,117 | 998 | 2016-01-01 → 2026-08-07 | Every simulated trade: entry, exit, reason, R-multiple, gross and net P&L |
| `backtest_stage2_signals_cache` | 286,811 | — | — | Config-hash-keyed cache of Stage 2 signal computation |

### 2.1 Point-in-time correctness

The fundamentals table carries `broadcast_date` on every row, and the backtest may
only read rows where `broadcast_date <= simulated date`. This matters more than it
sounds: a Q3 result published in February is *not knowable* in January, and a
backtest that reads it is measuring clairvoyance. This is the one form of
look-ahead bias that is both easy to introduce and invisible in the output.

### 2.2 Known data gaps

- **Banks parse with null profit** — they use a different XBRL taxonomy. Roughly
  4,716 filings failed to parse, mostly banks and pre-2018 filings whose XBRL URL
  is a placeholder (`/-`) rather than a real document.
- **Earnings calendar is thin after 2024** — 3,958 filings in 2025, 26 in 2026.
  Any earnings-based rule tested on 2025/26 measures a no-op, which is why the
  earnings campaign windows deliberately stop at 2024.
- **No survivorship-bias correction.** `ohlcv_data` contains the symbols that
  exist today. Companies delisted before 2026 are largely absent, which flatters
  every result here to an unknown degree. This is the largest un-quantified risk
  in the entire programme.

---

## 3. How the backtest works

### 3.1 The pipeline

The breakout backtest replays each trading day in order and runs the same four
stages the live screener runs:

- **Stage 1 — survivor gates (SQL).** Liquidity, base range, volume multiple,
  prior upmove, giveback, volume dry-up, distance from high, IFP score. Reduces
  ~2,300 symbols to ~70 survivors per day.
- **Stage 2 — base classification + entry technique (Python, `screen_gpt.py`).**
  Identifies the base stage, its width and bounce quality, and detects the entry
  trigger (trend bar / pin bar / pullback / breakout-retest). The expensive stage.
- **Stage 3 — position sizing.** Risk per trade and max capital per trade against
  the structural stop.
- **Stage 4 — ranking.** Order the day's candidates; take the top N.

An order placed on day *D* fills at day *D+1*'s open, plus slippage. Exits follow
the configured ladder (breakeven at +1R, half-book at +2R, EMA21 trail, safety
stop) and are checked daily.

### 3.2 The cost model

Modelled on Dhan equity delivery:

| Component | Rate |
|---|---|
| Slippage | 0.10% per fill (both legs) |
| STT | 0.10% both legs |
| Stamp duty | 0.015% buy only |
| Exchange charges | 0.003% both legs |
| DP charge | ₹14.75 per sell |
| Brokerage | ₹0 |

**Round-trip cost ≈ 0.52% of position value.** This number ends up being the
protagonist of the entire report — see §6.1.

### 3.3 Why it got fast enough to do this many runs

The first full-year run took roughly an hour. Three fixes brought it to minutes:

1. **Config-hash-keyed Stage 2 cache.** The cache key is a SHA-256 of the nine
   resolved Stage 2 constants, so changing a Stage 2 parameter correctly misses
   the cache while re-running the same config is nearly free. An earlier
   symbol+date-only key would have silently served results computed under
   *different settings* — a bug that produces plausible numbers and no error.
2. **Tick-size cache warmed once per run.** `load_tick_sizes()` downloads a ~10k
   row CSV; under the thread pool several workers each triggered their own
   download. Priming it single-threaded before the pool starts fixed it.
3. **`DEBUG=False`** — the screener prints several lines per symbol per day.

Stage 2 overrides are applied by monkeypatching the in-memory `screen_gpt` module.
This is safe only because every run executes in its own throwaway subprocess; it
never touches `screen_gpt.py` on disk or the live screener.

### 3.4 The methodology used to avoid fooling ourselves

This became necessary after several early "edges" evaporated. The standing rules:

- **Multi-window validation.** 11 independent one-year windows (2016–2026ytd)
  spanning very different regimes — measured average % of stocks above their
  200SMA ranges from ~25% in 2019 to ~85% in 2021.
- **Split-sample (FIT/TEST).** Rank configs on 2016-20, then on 2021-26, and
  compute the Spearman rank correlation. Near zero means the ranking is noise and
  **no config should be adopted regardless of its total**.
- **Plateaus, not peaks.** A real parameter effect is smooth — neighbours of a
  good setting are also good. An isolated spike surrounded by poor neighbours is
  luck. Per-axis marginals are printed for exactly this reason.
- **Judge on worst year and drawdown**, not on total.
- **Count the trades behind a claim.** A +₹20k improvement that comes from 24
  trades in one year is not an edge.

---

## 4. Experiment log — chronological

### Phase 1 — Stage 1 gate sweep (16 runs, single window)

One-at-a-time loosen/tighten of each of the eight survivor gates.

| Variant | Trades | Win% | Net P&L | avg R | maxDD |
|---|---|---|---|---|---|
| g3-volmult-loosen-0.6x | 132 | 37.9 | 22k | 0.12 | 22k |
| g8-ifp-tighten-0.35 | 137 | 36.5 | 20k | 0.16 | 23k |
| g8-ifp-loosen-0.15 | 138 | 36.2 | 19k | 0.15 | 25k |
| g6-dryup-tighten-1.0x | 136 | 39.0 | 18k | 0.12 | 24k |
| g6-dryup-loosen-1.6x | 137 | 35.0 | 17k | 0.11 | 24k |
| g1-turnover-tighten-15cr | 134 | 35.8 | 15k | 0.15 | 31k |
| g4-upmove-tighten-25pct | 135 | 37.8 | 15k | 0.05 | 23k |
| g4-upmove-loosen-10pct | 139 | 34.5 | 13k | 0.10 | 30k |
| g7-dist-loosen-8pct | 133 | 32.3 | 10k | 0.05 | 25k |
| g5-giveback-loosen-40pct | 144 | 31.9 | 10k | 0.03 | 32k |
| g5-giveback-tighten-20pct | 134 | 34.3 | 9k | 0.06 | 29k |
| g3-volmult-tighten-1.0x | 143 | 35.7 | 7k | 0.07 | 32k |
| g7-dist-tighten-2pct | 146 | 34.2 | 7k | 0.08 | 28k |
| g2-baserange-tighten-15pct | 139 | 36.0 | -4k | -0.07 | 31k |
| g1-turnover-loosen-5cr | 139 | 30.9 | -4k | -0.07 | 30k |
| g2-baserange-loosen-25pct | 127 | 30.7 | -13k | -0.11 | 34k |

**Read:** almost every variant lands between ₹7k and ₹22k on ~135 trades. Note
that *both* directions of the IFP gate improved on baseline, and both directions
of vol-multiple appear near the top and near the bottom. That pattern is the
signature of noise, not of a tunable parameter. **Nothing here was adopted.**

### Phase 2 — Stage 2 sweep (15 runs, single window)

| Variant | Trades | Win% | Net P&L | avg R | maxDD |
|---|---|---|---|---|---|
| **s2-basemax-tighten-2** | **129** | **41.1** | **39k** | **0.30** | **25k** |
| s2-breakoutretest-trigger-ON | 135 | 37.0 | 24k | 0.20 | 21k |
| s2-pullback-trigger-ON | 132 | 36.4 | 23k | 0.19 | 20k |
| s2-basewidth-tighten-15 | 142 | 38.0 | 23k | 0.22 | 25k |
| s2-bounce-tighten-20 | 141 | 36.2 | 22k | 0.17 | 26k |
| s2-basewidth-loosen-6 | 141 | 36.9 | 21k | 0.18 | 26k |
| s2-barrange-tighten-1.0 | 139 | 36.7 | 20k | 0.13 | 25k |
| s2-pinbody-loosen-0.45 | 136 | 36.8 | 20k | 0.16 | 24k |
| s2-pinwick-tighten-0.65 | 138 | 36.2 | 20k | 0.17 | 25k |
| s2-bounce-loosen-5 | 134 | 36.6 | 19k | 0.14 | 22k |
| s2-pinwick-loosen-0.45 | 138 | 36.2 | 19k | 0.15 | 25k |
| s2-pinbody-tighten-0.25 | 138 | 36.2 | 19k | 0.15 | 25k |
| s2-barrange-loosen-0.25 | 138 | 36.2 | 19k | 0.15 | 25k |
| s2-trendbar-tighten-0.80 | 140 | 36.4 | 18k | 0.13 | 29k |
| s2-trendbar-loosen-0.60 | 138 | 35.5 | 16k | 0.13 | 25k |

**Read:** `base_stage_max_allowed = 2` is the one genuine standout — ₹39k vs a
pack clustered at ₹16–24k, with the best win rate and the best avg R. It means
*only trade stocks in an early-stage base* (stage 1 or 2), rejecting late-stage
bases. This is the single most durable finding of the whole programme and it
survived every later re-test. Re-validation at basemax 1/3/4 confirmed 2 is the
plateau, not a spike.

### Phase 3 — Market-breadth entry filter (16 runs, 2 windows)

Hypothesis: don't take breakout entries when market breadth is weak.

| Variant | 2025 P&L | 2025 DD | 2026 P&L | 2026 DD | Trades |
|---|---|---|---|---|---|
| lvl30-only | -243 | 17k | 22k | 23k | 130 |
| lvl35+rising | 76 | 20k | 20k | 10k | 94 |
| lvl35+rising+chandelier | 413 | 20k | 20k | 10k | 94 |
| lvl35+rising+fbo | 2k | 14k | 7k | 12k | 108 |
| lvl35+rising+minpos25k | -3k | 13k | -731 | 5k | 46 |
| lvl35+rising+top3 | -1k | 24k | 11k | 19k | 131 |
| lvl40+rising | 1k | 20k | 28k | 12k | 115 |
| rising-only | -9k | 20k | 30k | 12k | 159 |

This looked like a win at the time and was adopted as part of the "best known"
config. **It was later substantially invalidated** — see Phase 7.

### Phase 4 — VCP contraction gate (8 runs, 2 windows)

| Variant | 2025 P&L | 2025 DD | 2026 P&L | 2026 DD | Trades |
|---|---|---|---|---|---|
| contraction0.6 | -3k | 20k | -9k | 35k | 98 |
| contraction0.7 | 22k | 22k | 21k | 23k | 118 |
| contraction0.7-noBreadth | 5k | 17k | -44 | 66k | 269 |
| contraction0.85 | 1k | 31k | 27k | 22k | 126 |

0.7 looked excellent on both windows. **Also later invalidated** — Phase 7.

### Phase 5 — Position sizing and capital (16 runs, 2 windows)

| Variant | 2025 P&L | 2025 DD | 2026 P&L | 2026 DD | Trades |
|---|---|---|---|---|---|
| capital10L+risk0.5pct | 25k | 69k | 152k | 38k | 114 |
| capital10L | 10k | 45k | 74k | 28k | 115 |
| risk1.0pct | 11k | 31k | 71k | 18k | 114 |
| risk0.5pct+cap15 | 7k | 35k | 57k | 22k | 115 |
| risk0.5pct | 8k | 28k | 56k | 19k | 115 |
| best-sl8pct | 2k | 20k | 27k | 12k | 115 |
| best-gate-baserange-15 | -8k | 22k | 908 | 13k | 118 |

**Read:** larger capital and larger risk-per-trade scale *both* P&L and drawdown
almost proportionally. There is no free lunch here — the ₹10L result is not a
better strategy, it is the same strategy with a bigger bet. The one real insight:
fixed per-trade costs (₹14.75 DP + slippage floor) hurt small positions
disproportionately, so **larger positions genuinely do improve cost efficiency** —
but only up to the point where a single position's risk becomes unacceptable.

### Phase 6 — Trailing mechanism (6 runs, 2 windows)

| Variant | 2025 P&L | 2025 DD | 2026 P&L | 2026 DD | Trades |
|---|---|---|---|---|---|
| EMA21+Rladder (ref) | 22k | 22k | 21k | 23k | 118 |
| Rladder-only | 32k | 11k | 1k | 34k | 111 |
| Rladder-trailfull | 36k | 15k | 17k | 39k | 111 |

**Read:** the R-ladder wins 2025 decisively and loses 2026 decisively. Exactly the
regime-dependence pattern that later dominated everything. No adoption.

### Phase 7 — The 11-year campaigns (265 runs)

This is where the programme changed direction. Everything above was tuned on 2025
and 2026 — which, it turned out, are the **same low-breadth regime**. The campaign
ran every candidate config across 11 independent one-year windows.

#### Campaign v1 — the core config set

| Config | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** | +ve | Trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A-production | -3k | 60k | -46k | -21k | 48k | 67k | -23k | 137k | -253 | -47k | 31k | **202k** | 5/11 | 2914 |
| B-basemax2 | -2k | 31k | -36k | -9k | 37k | 72k | -36k | 92k | -3k | -33k | 56k | **169k** | 5/11 | 2230 |
| C-basemax2+rising | -5k | 15k | -13k | -12k | 21k | 36k | -3k | 81k | 3k | -24k | 35k | **134k** | 6/11 | 1299 |
| D-basemax2+vcp | -8k | 7k | -34k | -15k | 5k | 86k | -32k | 67k | -23k | 25k | 13k | **90k** | 6/11 | 2478 |
| E-basemax2+rising+vcp | -22k | 6k | -17k | 3k | 9k | 59k | -15k | 59k | -16k | -788 | 22k | **87k** | 6/11 | 1523 |
| F-full+risk1 | -28k | 21k | -39k | -8k | 14k | 113k | -13k | 161k | -16k | -9k | 47k | **244k** | 5/11 | 1520 |
| G-full+lvl40cap | -6k | 0 | -42k | -8k | 27k | **0** | 28k | 8k | **0** | 25k | 43k | **76k** | 5/11 | 370 |

**Three things to notice.**

1. **The columns move together.** Every config loses in 2018 and 2025; every one
   wins in 2023. Config choice moves the number by ~₹15k; the year moves it by
   ~₹104k.
2. **G blocked 100% of days in 2021 and 2024** (₹0, both bull years). The absolute
   40%-breadth cap that looked so good on 2025/26 simply *stops trading* in a bull
   market. This was the moment the "validated edge" collapsed.
3. **VCP contraction (D, E) is among the worst configs over 11 years** despite
   looking excellent on the two tuning windows.

#### Campaign v2 — minimum-risk and time stops

| Config | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** | +ve |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| H-C+minrisk3 | -14k | 6k | -20k | -20k | 14k | 37k | 5k | 83k | 3k | -21k | 35k | **108k** | 7/11 |
| I-C+minrisk4 | -20k | 4k | -29k | -20k | 19k | 34k | 7k | 82k | -2k | -21k | 28k | **81k** | 6/11 |
| J-C+timestop12 | -12k | 18k | -17k | -7k | 24k | 27k | -4k | 72k | -4k | -17k | 31k | **112k** | 5/11 |
| K-C+timestop20 | -11k | 15k | -11k | -10k | 21k | 32k | -933 | 77k | 890 | -22k | 35k | **125k** | 6/11 |
| L-C+minrisk3+timestop15 | -18k | 2k | -19k | -16k | 10k | 30k | 6k | 80k | 2k | -19k | 36k | **93k** | 7/11 |

**Read:** every variant is *worse* than the C reference (₹134k). Minimum-risk
filters and time stops both cost money. No adoption.

#### Campaign v3 — earnings rules (2016-2024 only, by design)

| Config | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | **Total** | +ve |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C-ref | -5k | 15k | -13k | -12k | 21k | 36k | -3k | 81k | 3k | **123k** | 5/9 |
| **M-C+noEntry3d** | -12k | 18k | -12k | -15k | 37k | 41k | 9k | 78k | 217 | **146k** | 6/9 |
| N-C+exit2d | -7k | 16k | -14k | -6k | 15k | 20k | -8k | 78k | -6k | **90k** | 4/9 |
| O-C+noEntry3d+exit2d | -13k | 21k | -11k | -9k | 29k | 28k | -377 | 68k | -12k | **100k** | 4/9 |
| P-C+noEntry10d (leak probe) | -13k | 557 | -16k | -17k | 27k | 42k | 10k | 72k | -13k | **92k** | 5/9 |

**M looked like a win** (+₹23k over reference): don't enter within 3 days before
an earnings announcement. **It did not survive scrutiny.** Digging into where the
gain came from: 94% of it came from just **24 trades in 2020-21**. An "edge"
concentrated in 24 trades in one regime is not an edge. The 10-day leak probe (P)
also behaved inconsistently, which is what you would expect if the effect were
noise rather than a real earnings-risk premium.

#### Campaign v4 — alternate entry techniques

| Config | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Q-C+pullback | -4k | 9k | -4k | -11k | 24k | 34k | -348 | 63k | -6k | -23k | 34k | **116k** |
| R-C+retest | -14k | 14k | -11k | -12k | 24k | 44k | -4k | 82k | -3k | -25k | 40k | **135k** |
| S-C+both | -8k | 10k | -6k | -12k | 27k | 34k | -2k | 67k | -7k | -23k | 36k | **116k** |

**Read:** R (₹135k) vs reference C (₹134k). Within noise. No adoption.

#### Campaign v5 — regime state machine ("sit out bad regimes")

The most conceptually promising idea: use a confirmed index-vs-MA state machine to
stop trading entirely in downtrends.

| Config | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** | +ve |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| T-B+regime50/3-block | 10k | 14k | -39k | -25k | 28k | 19k | -20k | 74k | 1k | -11k | 46k | **97k** | 7/11 |
| U-B+regime50/5-block | 10k | 18k | -39k | -18k | 19k | 25k | -20k | 63k | -1k | -6k | 40k | **91k** | 6/11 |
| V-B+regime50/3-half | 7k | 10k | -27k | -15k | 20k | 55k | -36k | 80k | 225 | -33k | 46k | **108k** | 7/11 |
| W-B+regime100/3-block | -11k | 23k | -21k | -27k | 38k | 49k | -12k | 95k | -8k | -19k | 40k | **147k** | 5/11 |
| X-C+regime50/3-block | 1k | 19k | -23k | -14k | 16k | 18k | -10k | 73k | -2k | -11k | 29k | **96k** | 6/11 |

**Read — and this one is genuinely counter-intuitive.** The regime filter made
**2018 and 2019 worse**, which are precisely the years it was supposed to save.
The reason: a confirmed regime signal turns off *after* the drawdown has started
and turns back on *after* the recovery has started, so it systematically sells the
bottom and buys back higher. It converts a drawdown into a realised loss.
Compared against baseline B (₹169k), every regime variant except W is worse, and
W's edge is inside noise. **Not adopted.**

### Phase 8 — CAN SLIM / fundamentals infrastructure

To test whether earnings acceleration filters help, we needed point-in-time
fundamentals that simply did not exist anywhere purchasable at a sane price. So we
built them:

- `harvest_earnings.py` — harvested **146,920 NSE filings** (2011–2026) with
  broadcast dates and XBRL URLs.
- `backfill_fundamentals.py` — parses the XBRL for revenue/PAT/EPS, sharded four
  ways by `id % shards`. **Now 100% complete: 80,414 filings processed, 75,698
  parsed OK, 4,716 failed.**

Two bugs worth recording, because both produced *plausible output with no error*:

1. The XBRL parser returned "no data" for every single file. Cause: the namespace
   prefix is `in-bse-fin`, which contains hyphens, and the regex only accepted
   `[A-Za-z0-9_]+:`. Caught only by opening a sample file by hand.
2. Pre-2018 filings have a placeholder XBRL URL (`/-`) rather than a document.
   Fixed by filtering to `xbrl_url LIKE '%.xml'`.

**Status: infrastructure complete and usable for ~2019-2024; the C (current
earnings) and A (annual earnings) tests of CAN SLIM have not yet been run.**

### Phase 9 — Mean reversion (a deliberate diversifier)

The idea was not that mean reversion is profitable, but that it might be
*uncorrelated* with the breakout book, so the pair would be smoother than either.

| Variant | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MR-s8d5 (avg hold 3.8d) | -69k | 58k | 32k | -36k | -29k | 39k | -40k | 53k | -52k | -21k | -22k | **-86k** |
| MR-s4d3 (avg hold 2.5d) | -164k | 58k | -183k | -116k | -144k | 34k | -146k | -16k | -102k | -215k | -58k | **-1,053k** |

**Killed on two counts.** It loses money outright, and the correlation with the
breakout book was **+0.53** — it needed to be negative to serve its purpose. The
tighter variant (s4d3, 6,461 trades) is a pure demonstration of the cost thesis:
more trades, shorter holds, catastrophic losses.

### Phase 10 — Positional momentum (the breakthrough)

A completely different strategy shape: cross-sectional momentum rotation. Rank the
liquid universe above its 200SMA by momentum, hold the top N equal-weighted,
rebalance every N sessions, sell only when a name falls outside a buffer rank
(hysteresis to prevent churn).

First run, `POS-6m-m21-t10`:

| Year | Trades | Win% | Realized | Unrealized | **Total** | avg hold |
|---|---|---|---|---|---|---|
| 2016 | 45 | 33.3 | -44k | -8k | **-52k** | 66d |
| 2017 | 43 | 48.8 | 81k | 298k | **379k** | 67d |
| 2018 | 46 | 28.3 | -149k | 14k | **-135k** | 69d |
| 2019 | 48 | 31.2 | -110k | 115k | **5k** | 59d |
| 2020 | 38 | 44.7 | -44k | 424k | **380k** | 64d |
| 2021 | 48 | 33.3 | 130k | 117k | **247k** | 66d |
| 2022 | 42 | 33.3 | -118k | -12k | **-130k** | 73d |
| 2023 | 45 | 40.0 | 147k | 17k | **164k** | 70d |
| 2024 | 50 | 42.0 | 20k | 58k | **77k** | 58d |
| 2025 | 49 | 30.6 | -136k | 84k | **-52k** | 66d |
| 2026ytd | 21 | 38.1 | 7k | 128k | **135k** | 67d |
| **ALL** | | | | | **1,019k** | |

**₹1,019k vs ₹134k for the best breakout config.** Roughly 43 trades/year held
~66 days, against ~118 trades/year held ~14 days.

#### Positional round 1 — rotation parameter sweep (48 configs × 11 windows)

Top 15 by total:

| momentum / rebalance / top-N | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** | +ve | maxDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 6m / 63d / top5 | -125k | 301k | -80k | 105k | 485k | 315k | -55k | 153k | 28k | 59k | 190k | **1376k** | 8/11 | 50% |
| 1y / 63d / top15 | -145k | 420k | -135k | 110k | 236k | 326k | -90k | 301k | 137k | -103k | 93k | **1149k** | 7/11 | 49% |
| 1y / 63d / top5 | -157k | 291k | -35k | -42k | 449k | 388k | -177k | 417k | -18k | -150k | 151k | **1117k** | 5/11 | 60% |
| 1y / 42d / top15 | -167k | 430k | -124k | 57k | 284k | 307k | -93k | 352k | 100k | -124k | 88k | **1109k** | 7/11 | 49% |
| 6m / 63d / top15 | -91k | 382k | -105k | 98k | 257k | 219k | -107k | 316k | 47k | -83k | 69k | **1002k** | 7/11 | 46% |
| 1y / 63d / top10 | -180k | 453k | -137k | 14k | 254k | 363k | -145k | 365k | 64k | -170k | 119k | **1000k** | 7/11 | 52% |
| 1y / 42d / top20 | -147k | 432k | -129k | 40k | 243k | 300k | -106k | 323k | 66k | -109k | 76k | **989k** | 7/11 | 47% |
| 6m / 63d / top10 | -80k | 293k | -116k | 142k | 338k | 225k | -86k | 252k | 12k | -64k | 62k | **977k** | 7/11 | 49% |
| 1y / 21d / top20 | -123k | 382k | -160k | 24k | 237k | 317k | -82k | 317k | 128k | -122k | 57k | **975k** | 7/11 | 50% |
| **6m / 63d / top20** | -68k | 376k | -116k | 83k | 236k | 183k | -62k | 293k | 64k | -88k | 69k | **969k** | 7/11 | **42%** |

**The overfitting trap, caught live.** The best config on FIT (2016-20) was
`3m / 42d / top5`. On TEST (2021-26) it ranked **48th of 48**, at −₹441k. Had we
adopted the grid maximum, we would have adopted the single worst forward config in
the entire sweep. This is the clearest demonstration in the programme of why the
split-sample rule exists.

**Chosen: `6m / 63d / top20`** — not the maximum (₹969k vs ₹1,376k for top5), but
it sits in the middle of a smooth plateau and has the lowest drawdown of the group
(42% vs 50-60%). top5 concentrates the whole book in five names; its ₹1,376k is
five bets going right.

### Phase 11 — The positional stop-loss study (three rounds)

**Round 0 finding:** the positional book had **no stop-loss at all**. The only
exit was at rebalance. That entirely explained its ~42-45% drawdown.

#### Round 1+2 — stop type ladder (26 configs × 11 windows)

| momentum / stop | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** | maxDD | Trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **6m / fixed 15%** | -71k | 359k | -88k | 39k | 353k | 162k | -58k | 261k | 59k | -42k | 59k | **1034k** | **33%** | 516 |
| 6m / fixed 20% | -86k | 390k | -99k | 60k | 339k | 155k | -80k | 276k | 68k | -70k | 62k | **1015k** | 36% | 472 |
| 6m / fixed 25% | -92k | 387k | -104k | 69k | 328k | 178k | -96k | 285k | 70k | -83k | 62k | **1005k** | 40% | 448 |
| 6m / SMA200 | -102k | 371k | -86k | 46k | 325k | 176k | -81k | 290k | 62k | -89k | 62k | **973k** | 40% | 432 |
| 6m / **none** | -68k | 376k | -116k | 83k | 236k | 183k | -62k | 293k | 64k | -88k | 69k | **969k** | 42% | 407 |
| 6m / fixed 10% | -55k | 294k | -82k | 48k | 366k | 112k | -43k | 242k | 23k | -44k | 78k | **940k** | 31% | 571 |
| 6m / trail 25% | -84k | 347k | -122k | 54k | 284k | 186k | -95k | 265k | 46k | -70k | 60k | **872k** | 41% | 532 |
| 6m / trail 20% | -72k | 272k | -80k | 37k | 257k | 183k | -65k | 232k | 36k | -67k | 47k | **780k** | 32% | 598 |
| 6m / trail 15% | -51k | 249k | -54k | 27k | 235k | 147k | -24k | 156k | 20k | -62k | 67k | **709k** | 32% | 688 |
| 6m / EMA50 | -59k | 278k | -29k | -8k | 108k | 122k | -43k | 159k | -22k | -47k | 71k | **529k** | 37% | 716 |
| 6m / SMA50 | -68k | 203k | -31k | -7k | 90k | 117k | -60k | 152k | -17k | -50k | 77k | **407k** | 36% | 741 |
| 6m / EMA21 | -20k | 92k | -10k | 16k | 92k | 86k | -2k | 77k | -4k | -29k | 17k | **315k** | **25%** | 834 |
| 1y / EMA21 | -65k | 66k | -18k | -11k | 105k | 118k | 18k | 88k | -934 | -27k | 31k | **304k** | **17%** | 828 |

**Two clean findings.**

1. **A fixed 15% stop is a free win** — higher total (₹969k → ₹1,034k) *and*
   lower drawdown (42% → 33%) *and* better worst year (−₹116k → −₹88k). Risk
   controls almost never improve return; this one does, because it prevents a few
   catastrophic single-name losses that the rebalance-only rule rode all the way
   down. The fixed-% axis is a smooth plateau (10/15/20/25 all land ₹940k–₹1,034k).
2. **The MA stops are monotonic in speed, and faster is strictly worse.** EMA21
   (fastest) → SMA50 → EMA50 → SMA200 (slowest) → none, in exactly ascending order
   of return. And it holds in FIT and TEST *independently* — EMA21 is worst in
   both halves. That is real structure.

**The exception worth remembering:** EMA21 produces the **lowest drawdown in the
entire programme — 17-25%, with a worst year of only −₹29k.** It is the best risk
profile ever measured here. It just costs two-thirds of the return.

#### Round 3 — can EMA21 keep the low drawdown without the churn? (21 configs × 11 windows)

The diagnosis: EMA21 doubled the trade count (407 → 834). **An important
correction was made here.** The initial hypothesis was "the same names are being
round-tripped." Checking the actual trade log disproved it: for EMA21 in 2022 there
were 80 trades across **71 distinct names — only 1.13 round-trips per name**. The
real mechanism is that **average holding period collapses from ~66 days to 18**, so
every rebalance finds empty slots and fills them with *different* names.

| variant | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 | **Total** | +ve | maxDD | Trades |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ref-fixed15 | -71k | 359k | -88k | 39k | 353k | 162k | -58k | 261k | 59k | -42k | 59k | **1034k** | 7/11 | 33% | 516 |
| ref-none | -68k | 376k | -116k | 83k | 236k | 183k | -62k | 293k | 64k | -88k | 69k | **969k** | 7/11 | 42% | 407 |
| fixed15+ext15 | -61k | 212k | -95k | 17k | 334k | 233k | -40k | 266k | 37k | -93k | 91k | **899k** | 7/11 | 35% | 522 |
| **ema21-c3+buf4** | -66k | 312k | -62k | 37k | 273k | 154k | -51k | 202k | 28k | -72k | 61k | **816k** | 7/11 | **32%** | 653 |
| ema21-arm20+buf4 | -79k | 247k | -120k | 67k | 207k | 192k | -67k | 255k | 38k | -60k | 60k | **741k** | 7/11 | 42% | 530 |
| ema21-arm20 | -72k | 239k | -109k | 67k | 194k | 201k | -54k | 225k | 52k | -49k | 39k | **733k** | 7/11 | 40% | 565 |
| ema21-c3+arm20 | -89k | 270k | -110k | 61k | 139k | 202k | -67k | 248k | 54k | -59k | 65k | **713k** | 7/11 | 41% | 544 |
| sma50-arm20 | -74k | 255k | -106k | 57k | 147k | 180k | -84k | 250k | 42k | -58k | 64k | **674k** | 7/11 | 44% | 514 |
| ema21-buf6 | -35k | 231k | -60k | -1k | 252k | 134k | -32k | 182k | -32k | -35k | 61k | **664k** | 5/11 | 35% | 698 |
| sma50-c3+buf4 | -102k | 303k | -77k | 28k | 195k | 148k | -62k | 245k | 1k | -82k | 55k | **652k** | 7/11 | 38% | 622 |
| ema21-c3+buf4+nore1 | -63k | 314k | -40k | 32k | 218k | 84k | -42k | 142k | 26k | -71k | 39k | **639k** | 7/11 | 32% | 658 |
| ema21-arm10 | -80k | 156k | -50k | 49k | 188k | 156k | -47k | 169k | 32k | -71k | 8k | **511k** | 7/11 | 38% | 639 |
| ema21-confirm5 | -82k | 177k | -18k | -2k | 186k | 134k | -48k | 160k | -19k | -48k | 67k | **506k** | 5/11 | 37% | 756 |
| ema21-buf4 | -59k | 155k | -27k | -488 | 173k | 124k | -14k | 130k | -31k | -40k | 55k | **465k** | 5/11 | 34% | 762 |
| ema21-confirm2 | -54k | 114k | 2k | 9k | 112k | 146k | -20k | 101k | 8k | -33k | 55k | **440k** | **8/11** | 28% | 806 |
| ema21-buf2 | -43k | 121k | -11k | 7k | 111k | 147k | -15k | 115k | -397 | -28k | 25k | **429k** | 6/11 | 27% | 804 |
| ema21-confirm3 | -69k | 128k | -7k | -1k | 156k | 148k | -34k | 100k | -8k | -57k | 54k | **409k** | 5/11 | 31% | 791 |
| ref-ema21 | -20k | 92k | -10k | 16k | 92k | 86k | -2k | 77k | -4k | -29k | 17k | **315k** | 6/11 | 25% | 834 |
| ema21+ext15 | -11k | 26k | -13k | -4k | 68k | 66k | 19k | 78k | -7k | -67k | 32k | **188k** | 6/11 | 20% | 833 |
| ema21-noreentry1 | -22k | 82k | -3k | 15k | 24k | 14k | -12k | 43k | -2k | -32k | 10k | **116k** | 6/11 | 25% | 831 |
| ema21-noreentry2 | -22k | 72k | -8k | 16k | 36k | 13k | -7k | 34k | -2k | -32k | 10k | **107k** | 6/11 | 25% | 833 |

**Findings:**

- **Confirmation days and small buffers barely reduce trades at all** (834 → 791
  for confirm-3; 834 → 804 for buffer-2%) while raising returns modestly. They
  reduce *how often* the stop fires, but the surviving stop-outs still shorten the
  holding period, so the slot-recycling continues.
- **The re-entry block is actively catastrophic** (₹315k → ₹116k) — exactly as
  predicted once we knew only 1.13 trips/name occur. It blocks re-buying the few
  genuinely good names while doing nothing about the churn.
- **The entry-extension filter also fails** (₹188k). Confirms that entry quality
  was never the problem.
- **Best combination: `ema21-c3+buf4`** — ₹816k at 32% drawdown on 653 trades.
  That is ~2.6x EMA21's raw return at essentially the same drawdown. But it is
  still worse than plain fixed-15% (₹1,034k at 33%) on **both** axes.

**Answer to the question "can we get EMA21's drawdown at fixed-15%'s trade
count?" — No.** The lowest trade count among all EMA21 variants is 530
(`arm20+buf4`) and it costs 28% of the return to get there. The trade count and
the drawdown are the same variable: EMA21 gets its low drawdown *by* exiting
early and often. Removing the churn removes the protection.

---

## 5. Consolidated scoreboard

| Strategy / config | Total (11 yrs, ₹4L) | +ve years | Worst year | maxDD | Trades |
|---|---|---|---|---|---|
| **Positional 6m/63d/top20 + fixed 15% stop** | **₹1,034k** | 7/11 | −₹88k | 33% | 516 |
| Positional 6m/63d/top20, no stop | ₹969k | 7/11 | −₹116k | 42% | 407 |
| Positional + EMA21 c3+buf4 | ₹816k | 7/11 | −₹72k | 32% | 653 |
| Positional + EMA21 (raw) | ₹315k | 6/11 | −₹29k | **17-25%** | 834 |
| Breakout F (full + risk 1%) | ₹244k | 5/11 | −₹39k | ~14% | 1,520 |
| Breakout A (production today) | ₹202k | 5/11 | −₹47k | ~17% | 2,914 |
| Breakout B (basemax 2) | ₹169k | 5/11 | −₹36k | ~13% | 2,230 |
| Breakout C (basemax 2 + rising) | ₹134k | 6/11 | −₹24k | ~7% | 1,299 |
| Mean reversion s8d5 | −₹86k | 5/11 | −₹69k | — | 1,489 |
| Mean reversion s4d3 | −₹1,053k | 2/11 | −₹215k | — | 6,461 |

---

## 6. What we learned

### 6.1 The cost thesis — the central finding

Diagnosed from campaign v1: average **gross** move per breakout trade is
**+0.704%**, against a round-trip cost of **0.522%**. Costs consume **74% of the
gross edge**, leaving **+0.18% net per trade**.

Look at what that does to config C over 11 years: **₹134k of profit against ₹153k
of cost drag.** The strategy generated more in broker/tax/slippage friction than
it kept. Config A (production) is worse: ₹202k profit against ₹298k of costs, at
**₹102 of cost per trade × 2,914 trades**.

This single fact explains almost every result in this report:

- Why mean reversion at 2.5-day holds lost ₹1,053k.
- Why every fast stop loses money — it multiplies trades.
- Why positional momentum wins: **~47 trades/year on ~₹20k positions ≈ 1.2% of
  capital in annual friction, versus ~3.5% for breakout C and ~6.8% for
  production.** Same market, same data, one third to one fifth the toll.

**The most reliable way to make more money here has been to trade less.**

### 6.2 Regime dominates configuration

Year explains ~7x more variance than config. All configs win or lose together.
This kills the "one optimal setting" framing and is why attempts to *detect* the
regime and act on it (campaign v5) were tried at all.

### 6.3 But regime *detection* did not work

Every version of "sit out bad regimes" made the bad years worse, because a
confirmed signal turns off after the drawdown starts and back on after the
recovery starts — converting unrealised drawdown into realised loss. The lesson:
knowing that regime matters is not the same as being able to trade it.

### 6.4 Seven ideas killed by proper validation

Recording these because the failures are the substance of the programme:

| Idea | Why it looked good | Why it died |
|---|---|---|
| Absolute breadth cap (40%) | Best on 2025 + 2026 | Blocked **100% of days** in 2021 and 2024 |
| VCP contraction gate | Strong on both tuning windows | Among the **worst** configs over 11 years |
| Earnings rule M (no entry 3d before) | +₹23k over reference | **94% of the gain from 24 trades** in 2020-21 |
| Entry technique (pullback / retest) | Plausible mechanism | Within noise (₹135k vs ₹134k) |
| RS-ranking variant | Standard momentum practice | Non-monotonic across the axis |
| Regime state machine | The most compelling idea | Made 2018/2019 **worse** |
| Mean-reversion diversifier | Meant to be uncorrelated | **+0.53** correlation, and lost money |
| Re-entry block (round 3) | Should reduce churn | Churn wasn't re-entry; ₹315k → ₹116k |

Both tuning windows (2025, 2026) turned out to be the **same low-breadth regime**.
Everything tuned on them was tuned on one regime and presented as general.

### 6.5 What actually survived

Only three things have survived every re-test:

1. **`base_stage_max_allowed = 2`** — trade only early-stage bases. Consistent
   across sweeps, campaigns and re-validation.
2. **Positional momentum as a strategy shape** — the low-turnover structure beats
   the high-turnover one by ~7x, primarily on cost.
3. **A fixed 15% stop on the positional book** — improves return *and* drawdown,
   sits on a smooth plateau, verified across 11 windows.

### 6.6 Honest caveats

- **Spearman FIT→TEST is only +0.23** on the stop grid. Fixed-15% ranked #1 on
  FIT but **12th of 26 on TEST**. Treat "15% is *the* optimum" as unproven; what
  is well supported is the *ordering* (fixed ≥ none > trailing > fast MA).
- **Survivorship bias is uncorrected** and flatters everything by an unknown
  amount. This is the biggest open risk.
- **The positional book runs ~100% deployed** with measured calendar years of
  +95% and −34%. A 33% maxDD is a very different proposition from the breakout
  book's ~7%, and no amount of backtest total return changes what a 33% drawdown
  feels like in real money.
- **Sample size.** 11 years is 11 independent observations of "a year." That is
  not many, and 7/11 positive years has a wide confidence interval.

---

## 7. Current state

### 7.1 Shipped and live

- Config-hash-keyed Stage 2 cache; run times from ~1 hour to minutes.
- Positional momentum strategy fully wired into the run pipeline (`sql/020`), so
  it appears in the normal UI — run list, trade log, equity curve, realized /
  unrealized / total P&L.
- Positional stop-loss (`sql/021`): `pos_sl_mode` ∈ {none, fixed, trail, ema21,
  sma50, ema50, sma200} + `pos_sl_pct`, **checked every session**, exposed in the
  Backtest UI with the full ladder. Exits log as `SL_FIXED` / `SL_EMA21` / … so
  the trade log distinguishes "stopped out" from "rotated out".
- Backtest UI cleaned up: presets (Best known / Positional / Positional no-stop /
  Production today), year shown in the window column, realized-unrealized-total
  P&L columns, sort / filter / search.
- Earnings + fundamentals infrastructure complete (146,920 filings, 80,414 parsed).

### 7.2 Not done

- The 110-run API job that populates every stop variant into the UI run list was
  **paused** (it was contending with the sweeps for the box's 2 vCPUs). It is
  resumable and skips completed runs.
- CAN SLIM C and A tests have not been run, though the data now exists.
- The fixed-15% stop is validated but **has not been proposed for the live
  screener** — the positional book is not what `screen_gpt.py` trades.

---

## 8. Open questions, in priority order

1. **Quantify survivorship bias.** Every number above is suspect until we know
   how much delisted-stock absence inflates it. This outranks any further tuning.
2. **Should the positional book be traded at all, and at what size?** It is
   ~7x the breakout book's return with ~5x its drawdown. That is a portfolio
   allocation question, not a backtest question.
3. **Run the CAN SLIM C/A tests** — the fundamentals are finally there.
4. **Test the two books together.** The breakout book has ~7% maxDD and the
   positional ~33%. If their correlation is low, a blend could be better than
   either. This has never been measured.
5. **A faster rebalance paired with a fast stop.** Round 3's cash-until-rebalance
   rule penalises fast stops heavily — a 63-day wait in cash after a stop-out. A
   21-day rebalance with an EMA21 stop is a genuinely different, untested
   strategy, and it is the one configuration that might capture EMA21's 17-25%
   drawdown without the return collapse.

---

---

## 9. The continuous portfolio test (2026-08-12)

### 9.1 Why the earlier numbers were the wrong measurement

Everything in §1–8 came from **eleven independent one-year backtests summed
together**. That is not a portfolio. It resets capital to ₹4L every January,
throws away open positions at each year end, and therefore cannot compound and
cannot express a drawdown that spans a year boundary. A book that falls 40% in
December and recovers in January appears as "bad year / good year" instead of as
one 40% drawdown — which is what it actually was.

`portfolio_engine.py` runs **one continuous simulation, 2016 → 2026**: capital
carried forward, positions held across year ends, daily mark-to-market, costs and
caps applied at portfolio level, cash held explicitly. Metrics describe the
*path*, not just the destination. The headline is the **Martin ratio (CAGR ÷
ulcer index)** rather than CAGR/maxDD, because the ulcer index accounts for how
*long* the book is underwater, not only how deep the single worst hole got.

### 9.2 Results — all controls, full period, ₹4L start

| Config | CAGR% | maxDD% | Ulcer | Worst 12m% | **Martin** | Turn/yr | Final |
|---|---|---|---|---|---|---|---|
| No stop (true baseline) | 19.91 | **57.1** | 22.22 | −37.9 | 0.90 | 2.39 | ₹26.5L |
| **Stop 15% only** | 19.51 | 39.3 | 18.99 | −30.7 | 1.03 | 2.60 | ₹25.6L |
| **+ mild vol scaling (floor 75%)** | **20.04** | 37.9 | 18.53 | −29.8 | **1.08** | 2.48 | ₹26.8L |
| **+ top-30 instead of top-20** | 16.73 | 33.8 | **15.30** | −26.5 | **1.09** | 2.53 | ₹20.0L |
| + sector cap 3/sector | 19.14 | 39.1 | 18.46 | −31.3 | 1.04 | 2.58 | ₹24.8L |
| + gentle vol scaling (floor 60%) | 17.76 | 38.2 | 19.11 | −30.1 | 0.93 | 2.38 | ₹22.0L |
| + sector cap 2/sector | 16.95 | 38.3 | 18.86 | −31.0 | 0.90 | 2.66 | ₹20.4L |
| + vol scaling, absolute bands | 15.92 | 41.1 | 18.33 | −27.0 | 0.87 | 2.22 | ₹18.6L |
| + vol scaling, percentile (25% floor) | 14.10 | 39.3 | 18.50 | −26.7 | 0.76 | 2.07 | ₹15.8L |
| + DD throttle at −10% | 11.34 | 35.9 | 19.91 | −27.9 | 0.57 | 1.87 | ₹12.2L |
| + DD throttle at −20% | 11.17 | 36.6 | 19.89 | −28.6 | 0.56 | 2.01 | ₹12.1L |
| **FULL STACK (recommended set)** | 9.15 | **28.5** | 14.56 | **−19.7** | 0.63 | 1.45 | ₹10.0L |
| Full stack, gentle vol | 12.93 | 29.3 | 15.70 | −20.9 | 0.82 | 1.49 | ₹14.2L |
| *Strict sector — see §9.5* | *28.14* | *32.4* | *13.22* | *−27.8* | *2.13* | *2.22* | *₹52.9L* |
| *Full stack, strict sector — see §9.5* | *21.14* | *22.6* | *9.77* | *−17.7* | *2.16* | *1.35* | *₹29.5L* |

### 9.3 What survived, and what did not

**The stop pays for itself — confirmed, and more clearly than before.** It costs
**0.4pp of CAGR** (19.91 → 19.51) and removes **18pp of drawdown** (57.1% → 39.3%).
Martin 0.90 → 1.03. In the old annual framework this looked like a modest gain;
measured on a continuous equity curve it is the single best trade available.

**Volatility scaling largely failed.** The percentile version cost **5.4pp of
CAGR and delivered zero drawdown reduction** (39.3% → 39.3%). Only the *mildest*
version — never cutting below 75% — helped at all, and only marginally
(Martin 1.03 → 1.08). The graded design did not fix the binary version's problem;
it diluted it.

**The drawdown throttle failed at every threshold tested.** At −10% Martin falls
to 0.57; at −20%, 0.56. The mechanism is visible in the calendar returns: it turns
**2017 from +91.6% into +43.0%**. It throttles into recoveries. Note especially
that the **ulcer index got *worse*** (18.99 → 19.89) — the throttle makes the book
spend *longer* underwater, because it is not participating in the rebound. This is
the same late-and-wrong pathology that killed the binary regime filter in campaign
v5, and grading it did not cure it.

**Sector caps at 2/sector cost more than they buy** (Martin 0.90); at 3/sector
they are roughly neutral (1.04). Caveat in §9.5.

**The full recommended stack achieves the stated goal and fails the trade-off
test.** It does exactly what it was designed to do — maxDD 28.5%, worst 12-month
−19.7%, turnover down to 1.45/yr. But CAGR falls to 9.15% and Martin to 0.63,
**worse than applying no controls at all**. It buys survivability at a price that
exceeds what it is worth.

### 9.4 The control that worked was not on the list — but it is a hypothesis, not a result

**Holding 30 names instead of 20** produced the best unbiased risk-adjusted
outcome in this test: CAGR 19.51 → 16.73 (−2.8pp), but maxDD 39.3% → 33.8%,
ulcer 18.99 → **15.30** (the largest single improvement in the table), and worst
12-month −30.7% → −26.5%. Martin **1.09**, the highest of any unbiased config.

**This is one post-hoc cell and must not be treated as the production setting
yet.** Every idea this project has killed looked exactly like this at exactly
this stage — the absolute breadth cap, the VCP gate, earnings rule M — and each
died as soon as its neighbouring values and its out-of-sample behaviour were
checked. Until top-20/25/30/35/40 trace a plateau rather than a spike, and until
the ranking survives a FIT/TEST split, top-30 is a **hypothesis under test**
(see §9.10).

That is a meaningful result for the original hypothesis. The thesis was *"use
position sizing, diversification, cash and portfolio limits to control the return
path."* Split into its parts: **diversification worked; cash did not.** Cutting
exposure to cash reduced returns roughly in proportion to the risk it removed, or
worse. Spreading the same full exposure across more names reduced risk while
keeping most of the return — because it attacks single-name blowups, which is what
actually drives this book's drawdowns, rather than attacking market beta, which is
what pays it.

### 9.5 A result that looks spectacular and should be discarded

The two strict-sector configs (universe restricted to symbols with a known sector,
i.e. **current** NSE index constituents) produce the best numbers ever measured in
this project: 28.14% CAGR at 32.4% maxDD, and 21.14% CAGR at **22.6%** maxDD with
Martin 2.16.

**These are almost certainly survivorship artifacts and must not be acted on.**
`symbols_meta.sector` is populated only for today's NIFTY500 / MICROCAP250
members. Filtering the 2016 universe by "is in an NSE index in 2026" is selecting
the winners with a decade of hindsight. The result is reported only because
omitting it would be worse — and because it sets an upper bound on how much of
this programme's apparent edge could be survivorship-driven. That bound is
uncomfortably large.

### 9.6 Walk-forward on the stop (stop as the only control)

| Stop | FIT CAGR% | FIT maxDD% | FIT Martin | TEST CAGR% | TEST maxDD% | TEST Martin | TEST worst 12m% |
|---|---|---|---|---|---|---|---|
| 10% | 19.04 | **29.5** | **1.25** | 9.06 | 34.4 | 0.46 | −29.0 |
| 15% | **23.48** | 39.3 | 1.10 | 13.45 | 38.1 | 0.64 | −32.4 |
| 20% | 21.11 | 41.8 | 0.91 | **14.60** | 42.8 | **0.66** | −37.2 |

The in-sample ordering (15 > 10 > 20 on Martin) does **not** reproduce out of
sample (20 ≈ 15 > 10). What *is* consistent: **10% is the worst on TEST on both
CAGR and Martin**, and 15% and 20% are close on every measure. So the supported
conclusion is the range, not the point — **15–20%, with 10% rejected**. This is
exactly the "moderate fixed stop" finding and nothing more precise than that is
justified. The first walk-forward run was discarded because it evaluated the stop
*inside* the full control stack, which this test then showed to be net negative.

### 9.7 A bug worth recording

The first version of the matrix shipped **sector caps ON by default**. The "no
caps" baseline therefore silently had them, and the "+sectorcaps" variant came out
byte-identical to it — which read as the clean finding *"sector caps make no
difference."* They make a large difference: a mid-2018 top-20 held **nine names in
a single sector**. The bug produced a plausible number, no error, and a wrong
conclusion. Defaults are now inert; a control whose default is active cannot be
measured against a baseline.

### 9.8 Where this leaves the strategy

**Best current candidate, on the evidence:**

```
Core:      Positional 6m momentum / 63-session rebalance / buffer rank
Breadth:   top 30-40. NOT top-30 specifically - that FAILED split-sample
           validation (§9.10). What survived is the DIRECTION: out of sample,
           every risk metric improved monotonically as top-N rose, and was
           still improving at 40. Pick on CAGR you will pay, not on a maximum.
Stop:      Fixed 15% (supported RANGE 15-20%; 10% rejected. Not "15 is optimal")
Exposure:  Full, or mild vol scaling with a 75% floor. NOT the 25% ladder.
Sector:    3 per sector — TENTATIVE, roughly neutral rather than proven
Throttle:  None — it fails at every threshold tested
```

**Observed in this backtest: ~17% CAGR, ~34% maxDD, worst 12-month ~−26%**, or
₹20L from ₹4L over the measured decade.

The word is *observed*, not *expected*. **Survivorship bias is unresolved**, and
§9.5 shows it can move results dramatically in both directions — the
survivorship-contaminated variant posted a 28% CAGR *and* a materially lower
drawdown. Until that is quantified, these figures describe what happened to this
dataset, not what a live book should anticipate.

**What this does not do is make the book comfortable.** A 34% drawdown and a
−26% worst year remain. The honest conclusion of this test is that the original
diagnosis was right — you cannot engineer consistency out of a long-only momentum
book — but that the *proposed remedy* mostly does not work either. Volatility
scaling and drawdown throttles are late by construction: they reduce exposure
after the loss and restore it after the recovery. Only two things reduced pain at
an acceptable price: **a moderate per-stock stop, and more names.**

If a ~34% drawdown is unacceptable, the remaining lever is not inside this
strategy — it is **allocation**. Running the positional book at half size against
cash or a bond sleeve is mathematically equivalent to the exposure-scaling that
failed here, except it is decided once rather than timed badly. That is a
portfolio decision, and it is the honest answer to "how do I make this
survivable."

### 9.10 The top-N plateau + split-sample test — top-30 FAILED validation

Run as 30 continuous simulations (5 top-N values × 2 stops × full/FIT/TEST).

**Full period 2016–2026, stop 15%:**

| top-N | CAGR% | maxDD% | Ulcer | Worst 12m% | Martin | Trades |
|---|---|---|---|---|---|---|
| 20 | 19.51 | 39.3 | 18.99 | −30.7 | 1.03 | 530 |
| 25 | 17.67 | 35.9 | 16.94 | −28.0 | 1.04 | 655 |
| **30** | 16.73 | **33.8** | **15.30** | −26.5 | **1.09** | 765 |
| 35 | 15.66 | 37.5 | 16.60 | −28.6 | 0.94 | 902 |
| 40 | 16.42 | 37.8 | 15.98 | −28.2 | 1.03 | 1005 |

At stop 20% the top-N effect essentially vanishes (Martin 0.91 / 0.82 / 0.91 /
0.86 / 0.92 — flat). So even in-sample, top-30's advantage exists only at one
stop level and is non-monotonic in its neighbours: 35 drops *below* 20.

**Split sample:**

| Stop | top-N | FIT CAGR% | FIT maxDD% | FIT Martin | TEST CAGR% | TEST maxDD% | TEST Martin | TEST worst 12m% |
|---|---|---|---|---|---|---|---|---|
| 15 | 20 | 23.48 | 39.3 | 1.10 | 13.45 | 38.1 | 0.64 | −32.4 |
| 15 | 25 | 20.00 | 35.9 | 1.09 | 14.27 | 36.7 | 0.75 | −29.6 |
| 15 | **30** | 19.69 | 33.8 | **1.16** | 14.72 | 36.6 | 0.86 | −28.3 |
| 15 | 35 | 17.21 | 37.5 | 0.91 | 14.15 | 34.3 | 0.87 | −25.4 |
| 15 | 40 | 16.64 | 37.8 | 0.90 | **14.91** | **32.9** | **0.97** | **−24.7** |
| 20 | 30 | 18.44 | 38.2 | 0.94 | 16.99 | 38.5 | **0.98** | −31.8 |

**Spearman rank correlation (Martin), FIT vs TEST: −0.39 — inverted.**
Best on FIT was `stop15/top30`; **its TEST rank was 6th of 10**.

**Verdict: top-30 as a specific setting is not validated and must not be
adopted.** It is the FIT maximum that degrades out of sample — the exact
signature that killed the breadth cap, the VCP gate and earnings rule M, and the
same signature as the positional rotation sweep where the FIT-best config ranked
48th of 48 on TEST. Wiring top-30 into production on the strength of §9.4 would
have repeated the programme's most-repeated mistake.

**But the underlying direction does survive, on the risk axis specifically.**
Read *within* a stop level, every out-of-sample risk measure improves
monotonically as top-N rises:

- TEST Martin: 0.64 → 0.75 → 0.86 → 0.87 → **0.97**
- TEST maxDD: 38.1 → 36.7 → 36.6 → 34.3 → **32.9**
- TEST worst 12m: −32.4 → −29.6 → −28.3 → −25.4 → **−24.7**

No bumps, no reversals. The negative overall Spearman comes from FIT and TEST
*disagreeing about direction* — in 2016-2020 fewer names scored better, in
2021-2026 more names scored better — not from the relationship being random.

So the honest statement is: **"more names lowers drawdown" has out-of-sample
support; "30 is the right number" does not.** On TEST the metrics were still
improving at 40, which is the edge of what was tested. No single top-N should be
hard-coded; the defensible zone is 30–40, and it should be chosen on how much
CAGR you are willing to pay rather than on a backtest maximum.

### 9.11 Survivorship bias — measured, and bounded

**The bias exists, and is close to total.** Of 3,261 symbols in `ohlcv_data`,
**7 have a price series that ends early — 0.21%.** The eligible pool grows
monotonically and never shrinks:

```
2016:1249  2017:1388  2018:1563  2019:1753  2020:1814  2021:1963
2022:2126  2023:2379  2024:2743  2025:3091  2026:3258
```

A real universe loses companies every year to compulsory delisting, prolonged
suspension, merger and liquidation. This one only gains them. **It is not a
universe; it is a list of survivors.** For scale: NSE had roughly 1,800 listed
companies in 2016 against our 2016 pool of 1,249, and 132 NSE companies had been
suspended for over seven years as of that date.

The consequence is specific: **the backtest cannot buy a stock that later goes to
zero, because such stocks are absent from the table.** Every figure in this
report is an upper bound.

**Why this is a bound, not a correction.** Fixing it properly requires
point-in-time universe snapshots — the tradeable NSE symbol list on each
historical date, with delisting dates and final prices. That is not in the
database and is not freely available. What *can* be done rigorously is to inject
the missing losses across a range of rates and watch how fast the result
degrades.

**Hazard calibration.** NSE compulsory delistings run ~5–50/year against
~1,800–2,700 listed companies: **~0.3–2.7% a year**. Applied unconditionally this
*overstates* the harm here, because delisting candidates are overwhelmingly
illiquid and falling — they fail the ₹5cr turnover gate and the `close > SMA200`
filter long before the book could buy them. But the channel that *does* hit a
momentum book is real: a stock that runs on manipulation, passes every filter,
then gets suspended. Erring pessimistic is the right direction for a bound.

**Results** (top-35, stop 15%, mean of 5 seeds; the *whole distribution* matters
on a rare event, so the seed spread is shown):

| Hazard/yr | Recovery | CAGR% | (min–max) | maxDD% | Ulcer | Martin | Blow-ups |
|---|---|---|---|---|---|---|---|
| 0.0% | — | 15.66 | — | 37.5 | 16.60 | 0.94 | 0 |
| 0.5% | 0% | 15.09 | 13.8–15.7 | 37.5 | 16.72 | 0.90 | 1.6 |
| 1.0% | 0% | 14.88 | 13.2–15.7 | 37.8 | 16.82 | 0.89 | 2.2 |
| **2.0%** | **0%** | **13.66** | 11.5–14.9 | 39.4 | 17.51 | 0.78 | 4.8 |
| 4.0% | 0% | 11.24 | 8.1–13.0 | 40.5 | 18.86 | 0.60 | 10.4 |
| 0.5% | 30% | 15.36 | 14.7–15.7 | 37.4 | 16.62 | 0.92 | 1.6 |
| 1.0% | 30% | 15.10 | 13.6–15.7 | 38.1 | 16.84 | 0.90 | 2.2 |
| 2.0% | 30% | 14.43 | 13.2–15.5 | 38.4 | 17.12 | 0.84 | 4.8 |
| 4.0% | 30% | 12.84 | 11.9–13.7 | 39.2 | 17.81 | 0.72 | 10.4 |

**How to state this result — the wording matters and an earlier draft got it
wrong.** This is **a deliberately simplified random-loss stress test. It is NOT a
correction for survivorship bias, and it is not a bound.** The correct phrasing
is:

> *Under a deliberately simplified random-loss stress test, CAGR falls by roughly
> 1–4pp. This is not a correction for survivorship bias; actual results could be
> materially worse.*

Calling it a "bound" or quoting a precise "2–4pp discount" would imply the true
effect is contained within these numbers. It is not, for three structural
reasons:

1. **The hazard is independent and uniform.** Real blow-ups cluster — in time
   (2018 NBFC crisis, 2019 promoter-pledge unwinds) and by sector. Clustered
   losses hit a concentrated book far harder than independent ones, so the
   drawdowns here are optimistic *even at a given hazard rate*.
2. **Universe composition is untouched.** This injects losses into stocks that
   *were bought*. It cannot restore the ~550 companies missing from the 2016
   pool, whose absence also changed **which stocks ranked top-N in the first
   place** — a first-order effect on selection that no injection model reaches.
3. **The true rate is unknown, not merely uncertain.** Only the *shape* of the
   degradation is established.

**Ordering fix (2026-08-12).** The first version ran the normal stop check
*before* injecting the delisting, which let the book escape most blow-ups at a
controlled −15% loss. That is not how a fraud halt or suspension behaves: it gaps
straight through the stop, with no session at which the position could have been
exited first. Running the stop first understated the harm and defeated the point
of a stress test. **The shock is now applied before the stop check**, and §9.14
supersedes the table above.

**Practical reading: treat the headline CAGR as optimistic by a few percentage
points, and treat drawdown as a floor rather than an estimate.**

### 9.12 Final bounded diversification test — the rule fired, and it conflicts with the data

Six top-N values × two stops × three windows, with the **decision rule fixed
before the run**: adopt a top-N only if a contiguous plateau appears in the upper
half of *both* stop columns on TEST, then take its middle; otherwise keep top-20.

**TEST (2021–2026) Martin by top-N:**

| Stop | 20 | 30 | 35 | 40 | 45 | 50 |
|---|---|---|---|---|---|---|
| 15% | 0.64 | 0.86 | 0.87 | 0.97 | 1.02 | 1.02 |
| 20% | 0.66 | 0.98 | 0.94 | 0.90 | 0.94 | 1.06 |

Upper half of both columns intersects at `{50}` only — not contiguous, not three
wide. **The rule says: keep top-20.**

**The rule stands. Top-20 remains the frozen baseline.**

An earlier draft argued the rule should be overridden because top-20 is the
*worst* TEST value in both stop columns. That reasoning is not admissible:
**Martin was pre-registered as the selection criterion, and a pre-registered rule
cannot be discarded after seeing the data it was written to adjudicate.** Doing
so converts it from a safeguard into a formality. What the rule's outcome means
is precisely and only this: *do not select a specific top-N yet.*

A separate, legitimate statement can be made about risk, because it does not
involve selecting a value:

> Increasing holdings from 20 toward 45–50 consistently reduced out-of-sample
> drawdown, at a cost of roughly 2–3pp of CAGR.

That is a valid **risk trade-off**, not proof that top-45 is better overall. The
supporting evidence — both columns essentially monotone on the risk axis:

| Stop | Metric | 20 | 30 | 35 | 40 | 45 | 50 |
|---|---|---|---|---|---|---|---|
| 15% | TEST maxDD% | 38.1 | 36.6 | 34.3 | 32.9 | **30.0** | 30.6 |
| 15% | TEST worst 12m% | −32.4 | −28.3 | −25.4 | −24.7 | −21.4 | **−21.3** |
| 20% | TEST maxDD% | 42.8 | 38.5 | 36.8 | 36.0 | **33.2** | 33.6 |
| 20% | TEST worst 12m% | −37.2 | −31.8 | −28.9 | −29.8 | −26.0 | **−21.3** |

Four series, all monotone but for single-step wobbles at the last point. **This is
the cleanest out-of-sample relationship found anywhere in the programme.**

**Conclusion, in two parts of differing strength:**

- **Supported:** more names consistently reduced out-of-sample drawdown and worst
  12-month loss, at a cost of ~2–3pp CAGR. Going 20 → 45 traded roughly **2–3pp
  of CAGR for about 8pp of max drawdown**.
- **Not supported:** any specific top-N, including whether 45 is *better overall*
  than 20. The pre-registered criterion did not separate them.

**Frozen decision: top-20 stays as the baseline.** Top-35 or top-45 may be chosen
instead *only* as an explicit, pre-registered statement that lower drawdown is
preferred over return — recorded before paper trading begins, not selected
afterwards from the results.

### 9.13 The breakout sleeve — remove it

Tested as an annual-return blend against the correct null: **the same weight in
plain cash.**

Annual-return correlation, breakout vs positional: **+0.57** (config C), **+0.51**
(config F). It loses in the same years the positional book loses — 2016, 2018,
2019, 2022, 2025. That is the wrong sign for a diversifier.

| Allocation | Mean% | Stdev% | Worst yr% | **Mean/Stdev** |
|---|---|---|---|---|
| 100% positional | 23.2 | 44.4 | −33.7 | 0.522 |
| 90% positional + 10% **breakout** | 21.1 | 40.4 | −30.7 | 0.524 |
| 90% positional + 10% **cash** *(null)* | 20.8 | 40.0 | −30.4 | 0.522 |
| 80% positional + 20% **breakout** | 19.1 | 36.3 | −27.6 | 0.526 |
| 80% positional + 20% **cash** *(null)* | 18.5 | 35.5 | −27.0 | 0.522 |

**The breakout rows and the cash rows are indistinguishable** — mean/stdev of
0.524–0.526 versus 0.522. The sleeve adds return only in exact proportion to the
risk it adds. It is not diversifying; it is a second correlated bet held in
smaller size, with its own execution cost and operational complexity.

**Decision, stated provisionally because the evidence is weak:**

> Do not allocate to breakout in the live candidate unless a future
> shared-capital continuous test demonstrates an improvement over the equivalent
> cash allocation.

This is **not** a demonstration that breakout is useless. It rests on **11 annual
observations** from the annual-reset harness, not a shared-capital continuous
simulation — 11 points is a weak correlation estimate and the blend is
reconstructed arithmetically rather than simulated. What it does establish is
that the sleeve has not cleared a low bar: beating cash. Absent that, it should
not be carried into a live allocation, and the burden of proof sits with a proper
shared-capital test that has not been run.

### 9.15 Drawdown percentiles — the correct stress-sizing reference (2,000 paths/cell)

**Supersedes the "worst drawdown" figures in §9.14 as a planning input.**
`max()` over N paths is an **order statistic**: it is non-decreasing in N *by
construction*, so it reports the path count as much as the strategy and can never
be a stable estimate. It is retained below only as an illustrative disaster
scenario.

| Hazard/yr | Rec. | CAGR p50 | CAGR p5 | DD p50 | DD p90 | **DD p95** | DD p99 | DD *(sampled worst)* | w12m p5 |
|---|---|---|---|---|---|---|---|---|---|
| 0.0% | — | 19.51 | 19.51 | 39.3 | 39.3 | **39.3** | 39.3 | *39.3* | −30.7 |
| 0.5% | 0% | 19.37 | 17.40 | 39.3 | 42.2 | **43.0** | 46.3 | *50.8* | −34.0 |
| 1.0% | 0% | 18.79 | 16.02 | 39.3 | 43.3 | **45.3** | 48.7 | *54.5* | −35.0 |
| **2.0%** | 0% | 17.79 | 14.21 | 40.1 | 45.5 | **47.6** | 50.8 | *60.3* | −37.8 |
| 4.0% | 0% | 15.90 | 10.69 | 42.6 | 49.0 | **51.8** | 57.0 | *65.1* | −40.6 |
| 0.5% | 30% | 19.37 | 16.77 | 39.3 | 41.5 | **43.0** | 44.8 | *46.7* | −32.8 |
| 1.0% | 30% | 19.06 | 15.82 | 39.3 | 43.0 | **44.2** | 45.8 | *49.1* | −33.8 |
| 2.0% | 30% | 18.25 | 14.84 | 39.3 | 44.2 | **44.9** | 47.1 | *50.6* | −35.5 |
| 4.0% | 30% | 16.86 | 12.53 | 41.5 | 46.1 | **47.4** | 50.8 | *56.7* | −37.0 |

**43–48% is a MODEL-CONDITIONAL p95, not "the" sizing figure.** Across the
empirical hazard range, DD p95 lands at 43–48% — worse than the 39.3% observed
with no stress, well short of the sampled extremes.

The wording matters. This is a p95 **conditional on** a deliberately conservative
but **unvalidated** 0.5–2%/yr hazard applied to held names, with losses assumed
independent. It is **not** a statistical 95% confidence statement about live
performance, and nothing here estimates the probability that the hazard
assumption itself is right. Read it as a **planning stress range** — a defensible
number to size against — rather than as a measured property of the strategy.

*A note on the order-statistic point, verified empirically:* doubling from 1,000
to 2,000 paths left every sampled worst **unchanged** (50.8 / 54.5 / 60.3 / 65.1)
— the seeds are nested, and the maximum happened to fall in the first thousand
each time. That is luck of the draw, not stability: the statistic can only rise
or stay flat as N grows, never fall, which is precisely why it cannot serve as a
forecast. The p95 figures, by contrast, were stable across both runs.

Raw per-path values are written to `mc2_raw.json`, so any other percentile can be
recovered without re-running.

### 9.14 Monte Carlo stress, corrected ordering — 1,000 paths per cell

Supersedes §9.11's table. Two changes: **the delisting shock is applied *before*
the stop check** (a suspension gaps through a stop; there is no session at which
the position could have exited at −15% first), and results are reported by
**percentile** rather than mean, on the frozen top-20 baseline.

The engine was refactored into `load_market_data()` + a pure `simulate()` to make
this affordable — one simulation went from ~50s to **0.10s**, a ~500× speedup, and
the refactor reproduces the pre-refactor figures **exactly** (CAGR 19.51, maxDD
39.3, ulcer 18.99, Martin 1.03, 530 trades) with all 12 invariant tests passing.

| Hazard/yr | Recovery | CAGR p50 | **CAGR p5** | maxDD p50 | **maxDD WORST** | worst-12m p5 | Blow-ups |
|---|---|---|---|---|---|---|---|
| 0.0% | — | 19.51 | 19.51 | 39.3 | 39.3 | −30.7 | 0.0 |
| 0.5% | 0% | 19.33 | 17.42 | 39.3 | **50.8** | −34.1 | 0.7 |
| 1.0% | 0% | 18.82 | 16.03 | 39.3 | **54.5** | −35.1 | 1.4 |
| **2.0%** | **0%** | **17.82** | **14.21** | 39.9 | **60.3** | −37.5 | 2.7 |
| 4.0% | 0% | 15.90 | 10.40 | 42.6 | **65.1** | −40.4 | 5.5 |
| 0.5% | 30% | 19.37 | 16.77 | 39.3 | 46.7 | −32.9 | 0.7 |
| 1.0% | 30% | 19.07 | 15.72 | 39.3 | 49.1 | −33.8 | 1.4 |
| 2.0% | 30% | 18.28 | 14.88 | 39.3 | 50.0 | −35.4 | 2.7 |
| 4.0% | 30% | 16.85 | 12.77 | 41.4 | 54.3 | −37.3 | 5.5 |

**The percentiles change the conclusion, and the earlier mean-based table was
misleading.** The *median* path barely moves — 19.51 → 17.82 at a 2% hazard, less
than 2pp. But **CAGR p5 falls to 14.21%**, a 5.3pp hit in the bad case, and the
drawdown tail is far heavier than the median suggests.

A mean would have reported "small effect." The dispersion comes from how many
independent shocks land, and on which positions — a handful of blow-ups is enough
to take a 39% drawdown well past 50%, and the median is silent about it.

**Two things this model does NOT say**, both of which an earlier draft got wrong:

- It contains **no clustering at all** — shocks are independent per position per
  day. An earlier draft wrote "most paths never hit that cluster", which
  described a mechanism that is not in the model. The correct statement is: *the
  model omits the temporal and sector clustering that could produce worse
  drawdowns than its independent-loss paths.*
- **The sampled worst drawdown is not a forecast.** `max()` over N paths is an
  order statistic: it rises mechanically with N, so "worst across 1,000 paths"
  reports the path count as much as the strategy. See §9.15 — **p95 is the
  stress-sizing reference**; the sampled worst is an illustrative disaster
  scenario only.

Stating it as agreed:

> *Under a deliberately simplified random-loss stress test, CAGR falls by roughly
> 1–4pp (median to 5th percentile across the empirical hazard range). This is not
> a correction for survivorship bias; actual drawdown could be materially worse.*

And it genuinely could: the hazard here is **independent**, while real blow-ups
cluster in time and sector. A clustered version of this test would show worse
tails than 60%.

---

## 10. FROZEN CONFIGURATION

Research is closed. No further filters, AI rules, regime signals, exit variants
or parameter sweeps. The following is fixed for the paper-trading period.

```
Strategy      Positional momentum, long only, NSE equities
Ranking       pct_chg_6m, descending
Universe      turnover_1m_avg_cr >= 5, close > SMA200
Rebalance     every 63 sessions
Holdings      top 20            <- frozen baseline; the pre-registered rule did
                                   NOT select a different value (§9.12)
Buffer        rank 40 (2 x top-N)
Stop          fixed 15%, checked daily, exit at the close that breaches
Exposure      100%. No volatility scaling.
Regime        None.
Throttle      None.
Sector caps   None (data covers only ~55% of traded names).
Breakout      NOT allocated (§9.13, provisional).
Sizing        equal weight, current equity / 20, recomputed each rebalance
Costs         0.10% slippage/fill, STT 0.10% both legs, stamp 0.015% buy,
              exchange 0.003%, DP Rs.14.75/sell, brokerage 0
```

**Observed on this data (not expected forward):** CAGR 19.5%, maxDD 39.3%,
ulcer 18.99, worst 12-month −30.7%, ~50 trades/yr, turnover 2.6x/yr.

**Under stress (§9.15):** CAGR p50 17.8–19.4%, CAGR p5 14–17%, and
**drawdown p95 of 43–48%** across the empirical hazard range. The p95 is the
sizing reference; the sampled worst (50–65%) is an illustrative disaster
scenario, not a forecast.

### 10.1 The one permitted variation, if pre-registered

Top-35 or top-45 may be substituted **only** as an explicit prior statement that
lower drawdown is preferred over return — written down *before* paper trading
starts. Out of sample, 20 → 45 traded roughly 2–3pp of CAGR for ~8pp of max
drawdown. Choosing it *after* seeing paper-trade results would be the same error
this programme has already made twice.

### 10.2 Paper-trading protocol

Minimum 6 months, target 12. **No setting changes during the test** except for a
documented operational failure.

Log every rebalance:

| Field | Why |
|---|---|
| Intended symbol, qty, limit | The backtest's decision |
| Actual fill price and time | Realised slippage vs the assumed 0.10% |
| Orders not filled / partial | The backtest assumes every order fills |
| All charges, itemised | Verify the 0.52% round-trip model |
| Stop fills, and the gap through the stop | The backtest exits at the close |
| Any suspension/halt in a held name | **The one input the backtest can never supply** |
| Deviation from backtest signal | Data or timing differences |

The last two matter most. Suspensions in live holdings are the only *empirical*
evidence about the survivorship channel that §9.14 can only model, and gap-through
behaviour on stops is where a −15% stop becomes a −30% loss in reality.

### 10.3 Allocation

Sizing is now the decision, not signals. **Size against the larger of the two
relevant drawdown figures**, never against the median CAGR:

- **observed** max drawdown: **39.3%** (no stress)
- **stress p95** max drawdown: **~48%** (§9.15, 2%/yr hazard, total loss)

Using ~48% as the planning denominator — a **model-conditional** stress figure,
not a confidence interval:

| Max drawdown you can hold through | Allocation to the strategy |
|---|---|
| ~15% | ~30% |
| ~20% | ~40% |
| ~25% | ~50% |
| ~30% | ~60% |
| ~48% | fully invested |

**These are rough linear translations, not guarantees.** They also assume the
drawdown scales linearly with allocation, which is true only because the
remainder sits in cash — the moment the balance is in another risky asset, the
arithmetic no longer holds.

The remainder belongs in cash or low-risk assets, which per §9.13 is also
strictly better than the breakout sleeve on the evidence available.

**Two reasons the true figure may exceed 48%:** the stress model contains no
temporal or sector clustering (real blow-ups cluster, and clustered losses hit a
20-name book harder), and stops are assumed to fill at the breaching close rather
than gapping through. Both push the same direction.

### 9.16 Production's SELECTION inside the low-turnover wrapper

The cost thesis (§6.1) concluded the breakout book failed on **turnover**, not on
stock picking. That was an inference and was never tested directly — production's
selection had only ever run inside production's high-turnover wrapper, so "bad
picks" and "too many trades" were confounded.

This separates them. Production's own funnel (Stage 1 SQL gates, Stage 2
base/trigger classification, ranked `-ifp_score` then `base_range_pct`, exactly
as `screen_gpt.rank_candidates()` orders it) fills a **top-20 book rebalanced
every 63 sessions**, with the same stop, costs and mechanics as the frozen
momentum strategy. Only the ranking differs.

| Window | Selection | CAGR% | maxDD% | Ulcer | Worst 12m% | **Martin** | Deployed |
|---|---|---|---|---|---|---|---|
| **FULL** | momentum + 15% | **19.51** | 39.3 | 18.99 | −30.7 | 1.03 | 66.0% |
| **FULL** | **breakout + 15%** | 13.76 | **18.9** | **7.72** | **−15.3** | **1.78** | 64.2% |
| FULL | breakout + 2× structural | 11.42 | 14.1 | 6.15 | −11.0 | 1.86 | 46.0% |
| FULL | breakout, no stop | 14.05 | 22.4 | 9.61 | −19.0 | 1.46 | 70.6% |
| FULL | breakout, top 10 | 15.73 | 29.7 | 13.59 | −26.0 | 1.16 | 74.6% |
| **FIT** | momentum + 15% | **23.48** | 39.3 | 21.26 | −30.7 | 1.10 | 66.1% |
| **FIT** | breakout + 15% | 9.41 | **18.1** | 8.05 | −15.3 | **1.17** | 53.0% |
| **TEST** | momentum + 15% | 13.45 | 38.1 | 21.11 | −32.4 | 0.64 | 64.5% |
| **TEST** | **breakout + 15%** | **13.89** | **27.9** | **11.48** | **−16.9** | **1.21** | 68.9% |
| TEST | breakout, no stop | 15.78 | 29.9 | 11.47 | −18.8 | **1.38** | 78.1% |

**The cost thesis is confirmed, and more strongly than expected.** The same
selection that made ₹202k against ₹298k of costs in its native high-turnover
wrapper produces **the best risk-adjusted result measured anywhere in this
programme** when the turnover is removed — Martin 1.78 against the frozen
strategy's 1.03, with **half the drawdown (18.9% vs 39.3%)** and a worst year of
−15.3% against −30.7%.

**It is not a cash artifact.** The obvious objection is that a book which cannot
fill 20 slots is simply holding cash. Deployment is **64.2% vs 66.0%** — the two
books are equally invested. (The 2× structural variant *is* a cash artifact at
46% deployed, and is discounted accordingly.)

**And unusually for this project, it holds in both halves.** Martin: FIT 1.17 vs
1.10, TEST 1.21 vs 0.64. This is **the first change tested that beats the
incumbent on risk-adjusted return in FIT and TEST independently** — every prior
candidate won one and lost the other.

**Three things that keep this a candidate rather than a conclusion:**

1. **It costs return.** Over the full window CAGR falls 19.5% → 13.8%. The win is
   entirely risk-adjusted. If the objective is maximum growth, momentum still
   wins; if it is a survivable path, this is materially better.
2. **The FIT margin is thin** (1.17 vs 1.10) and driven by drawdown, not return —
   in 2016-2020 momentum earned 23.5% CAGR against breakout's 9.4%. Only on TEST
   does breakout win on *both* axes.
3. **One experiment.** Every dead idea in this report looked good at exactly this
   stage. It has not had a top-N sweep, a stop sweep, or a Monte Carlo stress.

**The frozen configuration does not change.** Unfreezing on a single result is
the mistake this programme has made repeatedly. This becomes the **first item for
the next research round**, to be run *after* paper trading of the frozen config is
underway — not instead of it.

*Caveat on what was tested: production waits for an intraday entry trigger and
sizes by risk; this buys the ranked names at the next open in equal weight. What
is measured is whether the funnel's choice of STOCKS carries information, not
whether its entry mechanics do.*

### 9.17 Entry v2 (buy-point gate) — REJECTED, and the cleanest kill in the programme

The decks separate *where* in the base you are (pullback / reverse H&S / high
breakout / breakout retest) from *whether* today's bar is actionable (hammer /
HH-HL / inside bar / trend bar). Production only ever asks the second. Entry v2
required both.

**Three rounds of inspection ran before any backtest**, and each found a real
defect — which is what makes the final negative result trustworthy rather than a
suspected bug:

| Round | Method | Finding |
|---|---|---|
| 1 | Count the pass rate | `HIGH_BREAKOUT` fired on 78% of survivors — Stage 1 already gates on 5% near-high, so it re-tested an upstream filter. Gate passed 73.6%. |
| 2 | Print bars and read them | `BREAKOUT_RETEST` labelled JINDALSTEL a retest while price sat **5% above** the level and climbing — the band allowed the low to be *above* the breakout level. |
| 3 | Measure firing persistence | `REVERSE_HS` fired **100 times from 20 real setups** (5.00/setup, one running 12 consecutive days) — the neckline test was a *state*, not an *event*. |

Each fix **removed a tolerance** rather than adding a threshold, and each was
driven by redundancy, visual inspection or measurement — never by a return
figure, since none existed. The gate ended at **38.3%** pass rate from 73.6%,
with all four detectors verified to fire on genuine patterns.

**Then it was tested, and it fails everywhere.**

| Case | Trades | Win% | Total | maxDD | Return/DD |
|---|---|---|---|---|---|
| 0 baseline, daily | 3,784 | 37.6 | ₹193,635 | ₹121,158 | 1.60 |
| 1 + base ladder v2 | 3,788 | 37.5 | ₹159,091 | ₹110,007 | 1.45 |
| 2 **+ buy-point gate** | 4,238 | 36.2 | **₹101,807** | ₹156,984 | **0.65** |
| 3 + ladder AND gate | 4,235 | 36.1 | ₹80,618 | ₹139,919 | 0.58 |
| 4 + both + weekly | 1,250 | 36.7 | ₹37,447 | ₹58,655 | 0.64 |
| **5 weekly cadence only** | **1,313** | **38.6** | ₹99,339 | **₹51,613** | **1.92** |

- **Daily: ₹194k → ₹102k. Weekly: ₹99k → ₹37k.** Worse in both, and *more* harmful
  under weekly (−62% vs −47%). No regime dependence to hide behind.
- **Drawdown got worse too** (₹121k → ₹157k), so it is not even a
  return-for-safety trade.
- **The base ladder v2 also failed**: −18% return for −9% drawdown, worse than
  proportional.

**The diagnostic detail.** Trade count went *up* (3,784 → 4,238) despite the gate
cutting candidates by 62%. The 3-pick cap binds either way, so filtering changes
*which* three are taken, not how many — and the gate systematically favours
stocks **at breakout points**, whose buy-stop entries trigger more reliably. More
fills, at worse prices, with a lower win rate. The gate selects stocks that are
already extended, close to the opposite of the deck's intent.

**Why this kill is the cleanest yet.** The detectors provably fire on real
pullbacks, breakouts and head-and-shoulders — verified by eye and by persistence
measurement before any P&L existed. So this is not "the code was broken". It is
the harder finding: **the patterns are genuine and they still do not predict.**

**What survived instead: weekly cadence.** Best return-per-drawdown of all six
(1.92 vs 1.60), highest win rate (38.6%), and **less than half the drawdown** —
51% of the return for 43% of the pain.

### 9.18 Gate strictness cannot control trade count

A recurring intuition worth settling with numbers: *"stricter gates → fewer,
better stocks → fewer trades."* The first two hold. The third does not.

Measured over 2019-2024: the funnel yields **22.7 candidates/day (median 18)**,
and only **4.5% of days have fewer than 3**. With `max_picks_per_track = 3` the
cap binds on ~95% of days, so shrinking the pool changes *which* three are taken,
not how many. After the entry-v2 gate (×0.38) still only ~20% of days fall below
3 — a trade-count reduction of roughly **10%**, not the 60% the pool reduction
suggests.

To reach ~50 trades/year on a daily scan the gate would need to reject **~99%**
of survivors. At that point the survivors are not the *best* stocks but whatever
cleared an arbitrary threshold stack — and the ranking, which already sorts by
IFP and base tightness and picks the top 3 of ~23, is a strictly better selector.

**Trade count is `picks per scan × scans per year`.** Gates are for quality;
cadence and pick-count are for quantity. Conflating them uses the wrong tool and
gets neither.

### 9.9 Still open

- **Survivorship bias remains unquantified**, and §9.5 suggests it could be large.
  This now clearly outranks any further strategy work.
- **The breakout sleeve has not been tested inside this framework.** The stated
  condition was "a 10–20% sleeve only if the combined backtest improves drawdown."
  The two books can now share capital in one engine; the correlation has never
  been measured.
- ~~Top-30 was one data point, not a sweep.~~ **Done — and it failed.** See
  §9.10. The direction survived; the specific value did not.
- **Test top-N beyond 40.** Every out-of-sample risk metric was still improving
  at the edge of the tested range, so the useful zone may not have been reached.
- **`portfolio_engine.py` now has invariant tests** (`test_portfolio_engine.py`,
  12 passing) covering the daily `cash + marked holdings == equity` identity,
  compounding position sizing, positions surviving year ends, cost ordering and
  single application, stop fills at or below threshold, and unknown-sector
  stocks not being silently excluded. They assert accounting identities rather
  than pinning returns — a test that pins CAGR is a regression detector for the
  data, not a correctness check for the engine.

  Two things worth recording about those tests:

  **The compounding test was originally worthless.** It doubled the starting
  capital and checked that final equity roughly doubled. An engine that wrongly
  sized every slot from the *initial* capital scales just as linearly and would
  have passed — linear scaling follows from both the correct and the incorrect
  rule, so it cannot distinguish them. It now asserts
  `slot == equity / top_n` **at each rebalance**, via a dedicated audit hook,
  and additionally requires that equity actually moved >2% from its start
  (otherwise the assertion is trivially satisfied by a book that never changed
  value).

  **That replacement was verified by mutation.** Changing the engine to
  `slot = cap0 / top_n` makes the test fail with
  `slot 20000.00 != equity/top_n 18472.91`, and reverting makes it pass. A test
  that has never been observed to fail is an assumption, not a check.

- **The API/UI path reproducing the sweep numbers is not independent
  confirmation.** `portfolio_run.py` calls the same `run_portfolio()` as the
  standalone sweeps. The agreement validates configuration handling,
  persistence and plumbing — it does **not** independently verify CAGR or
  drawdown, because there is only one implementation of those calculations.

---

*Every figure in this report is generated from the raw run data by
`custom-screener/backend/backtest/report/tables.py` and the portfolio sweep logs;
none is transcribed by hand.*
