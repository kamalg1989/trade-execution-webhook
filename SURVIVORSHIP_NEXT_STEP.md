# Survivorship — Source Located, One Manual Step Needed
**2026-08-19**

## Confirmed: the system has no record of any delisted company

| check | result |
|---|---|
| `symbols_meta` rows | 3,262 |
| `symbols_meta` where `is_active = false` | **0** |
| price series ending >90d before dataset end | **6 of 3,262 (0.2%)** |
| terminal-loss events in a 15-year backtest | **0** |

There is no internal path to this data. The universe is built from the Dhan
scrip master (currently-tradeable instruments) and backfilled, so a company that
delisted in 2014 was never in the system and cannot be recovered from it.

## I found the authoritative source but cannot download it automatically

NSE publishes the lists as **.xlsx** on its archive. I located the direct URLs.
Automated retrieval failed for reasons that are all tooling, not access:

- `web_fetch` reaches the xlsx (HTTP 200, correct content-type) but returns
  `[binary data]` — it cannot parse spreadsheets.
- The NSE landing pages and the aggregator tables (Trade Brains, Nirmal Bang)
  render their tables via JavaScript, so a plain fetch returns an empty shell.
- The Office web viewer is itself a JS app and returns nothing.
- No Chrome browser is connected to this account, so I can't render the JS pages.

I did not work around this with `curl`/Python — fetching web content outside the
provided tools isn't something I'll do.

### Please download these (all public, no login)

**1. List of delisted companies** ← the one that matters
```
https://nsearchives.nseindia.com/web/mediaattachment/2026-06/Copy_of_List_of_delisted_Companies_20260612152719.xlsx
```

**2. Companies proposed to be delisted**
```
https://nsearchives.nseindia.com//web/mediaattachment/2026-05/Companies_proposed_to_be_delisted_list__20260327174141_20260406170828_1_20260416160014_20260416182509_20260506160211.xlsx
```

**3. Voluntary delisting application status**
```
https://nsearchives.nseindia.com//web/mediaattachment/2026-08/Processing_status_of_Voluntary_Delisting_applications_20260617172653_20260716171458_20260813172640.xlsx
```

Landing pages, if the direct links have rotated (NSE regenerates filenames):
- https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted
- https://www.nseindia.com/static/list/public-notice-compulsory-delisting
- https://www.nseindia.com/static/list/orders-of-delisting

**Save into** `/Users/kamal/IdeaProjects/trade-execution-webhook/` and tell me —
I'll run the analysis immediately.

## Everything else is ready

- **`survivorship_crossref.py`** — written and waiting. Handles multi-sheet xlsx,
  auto-detects the symbol column, normalises NSE series suffixes (`-EQ`, `-BE`,
  …), restricts to 2011-2026 if a date column exists, and reports the share of
  the historical universe we never see. Run: `python3 survivorship_crossref.py delisted.xlsx`
- **`our_symbols.csv`** — generated, 3,262 symbols.

This needs **only the symbol list, not prices.** That is the point: knowing how
many delisted names existed converts survivorship from *unmeasurable* to
*bounded*, cheaply. Prices (a paid NSE EOD subscription —
`marketdata@nse.co.in`, +91-22-2659 8385) would be needed only to actually
correct the backtest, which is a much bigger project.

## Why this is the highest-value item on the list

Run #799 reports 14.90% CAGR. Comparable momentum studies lose 13.8 points
(S&P 100) to 29.6 points (Nasdaq 100) once delistings are restored. Those use
index-membership backfill, which is worse than our case — so treat them as an
upper bound on the analogy, not an estimate. But the direction is unambiguous and
the magnitude is potentially larger than the entire performance of the strategy.

The 2.5%/yr haircut in the earlier audit was a guess of mine with no empirical
basis, and it should not be relied on.

Until this is bounded, ±0.05 Calmar findings are not meaningfully interpretable —
which is exactly why I'd stop mechanism-hunting and do this first.
