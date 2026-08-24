# Paper Trading — POSITIONAL Momentum (config #823)
**Started 2026-08-19 · Rs.4,00,000 notional · 30 positions live**

## Why this exists

Every backtest figure in this programme is survivorship-biased by an estimated
**3-7 CAGR points**, and that bias cannot be measured from our own data — the DB
contains **0 of 269** wipeout delistings. Forward paper trading is the only
survivorship-free evidence available, and it accrues from today.

## What is being tracked

Engine run **#823** (2017-2026: **22.16% CAGR / 19.74% MaxDD / Calmar 1.12**):

```
rank  = z(12-1) + z(6m) + z(3m) + z(6m/ATR) + z(-base_range_20d)
gates = IFP >= 0.40, close >= Rs.20, turnover >= Rs.8cr, ATR <= 5%, close > SMA200
book  = top 30, exit at rank >= 60 (concentric band), rebalance every 21 sessions
size  = inverse volatility, NO stop loss (measured harmful)
cost  = 0.32% per leg
```

**Expectation, stated up front:** roughly **16-19% CAGR** after a survivorship
haircut, against a Nifty 50 ETF at ~12.3%. If forward results land near the raw
22% that would be pleasant but surprising, and near 12% would be consistent with
the bias estimate rather than evidence of failure.

## Validation before trusting it

The paper selector is a standalone reimplementation, so it was checked against
the engine rather than assumed correct:

> Engine run #823 bought 21 names with fills dated 2026-08-04. **All 21 sit at
> rank ≤ 28** of the paper selector when ranking on **2026-08-03**.

That also surfaced a real detail: the engine **ranks on day D and fills at D+1
open**. The paper script now does the same, and defers if the next session isn't
published yet (Dhan releases a day's candle the following morning).

## Operation

```bash
cd /root/trade-execution-webhook
./venv/bin/python paper_trading/paper_positional.py --status     # book + kill criteria
./venv/bin/python paper_trading/paper_positional.py --mark       # daily MtM
./venv/bin/python paper_trading/paper_positional.py --rebalance  # honours 21-session cadence
```

Automated via two systemd timers (weekdays, after the market-data pipeline):

| timer | time (IST) | does |
|---|---|---|
| `paper-mark.timer` | 19:15 | daily mark-to-market |
| `paper-rebalance.timer` | 19:20 | rebalance if 21 sessions have passed |

## Pre-registered kill criteria

Registered **before any forward data existed**, so they cannot be rationalised
away later. A breach means stop and re-examine — not an automatic halt.

| # | criterion | rationale |
|---|---|---|
| 1 | MaxDD > 30% | worse than any backtest (24.05% full, 19.74% clean) |
| 2 | 6-month return < −20% | sustained loss beyond backtest experience |
| 3 | 12-month return < 0 while Nifty 50 > +10% | failing while the market works |
| 4 | < 15 positions for 3 consecutive rebalances | universe thinned; gates may be mis-specified |

Criterion 3 needs a manual Nifty check; the rest are evaluated automatically by
`--status`.

## Safety

- Reads `market_data` **read-only**; writes only to `paper_*` tables in
  `trading_platform`.
- **Never places an order.** No Dhan calls, no Telegram, no interaction with the
  live screener or webhook.
- Removable with `systemctl disable --now paper-mark.timer paper-rebalance.timer`
  and `DROP TABLE paper_positions, paper_equity, paper_rebalance;`

## Honest caveats

- Paper fills assume the next session's open plus 0.32% cost. Real fills on
  ~₹8-13k positions in mid-caps will differ, generally for the worse.
- The strategy is a mid/small-cap momentum book. Judge it against **Nifty Next
  50 (JUNIORBEES, 16.5% over 2020-26)** as much as Nifty 50 — beating a
  large-cap index with a small-cap book is partly beta, not alpha.
- Meaningful signal needs **12+ months**. Anything read from the first quarter is
  noise, in either direction.
