#!/usr/bin/env python3
"""Cross-reference NSE delisted companies against our backtest universe.

WHY: our DB is a pure-survivor universe (symbols_meta: 3,262 rows, 0 inactive;
only 6 of 3,262 price series end before the dataset end). Every CAGR figure in
this programme is therefore an upper bound of unknown size. This script converts
"unknown" into "bounded" using only the delisted SYMBOL LIST -- no prices needed.

USAGE
  1. Download NSE's delisted-companies list (see DELISTED_DATA_SOURCES below)
     into this folder, e.g. delisted.xlsx / delisted.csv
  2. python3 survivorship_crossref.py delisted.xlsx

OUTPUT
  - how many delisted NSE symbols exist for the backtest window
  - how many are ABSENT from our DB (= the invisible universe)
  - the implied share of historical candidates we never evaluated, by year

DELISTED_DATA_SOURCES (found 2026-08-19, NSE archives — all public):
  List of delisted companies:
    https://nsearchives.nseindia.com/web/mediaattachment/2026-06/Copy_of_List_of_delisted_Companies_20260612152719.xlsx
  Companies proposed to be delisted:
    https://nsearchives.nseindia.com//web/mediaattachment/2026-05/Companies_proposed_to_be_delisted_list__20260327174141_20260406170828_1_20260416160014_20260416182509_20260506160211.xlsx
  Voluntary delisting application status:
    https://nsearchives.nseindia.com//web/mediaattachment/2026-08/Processing_status_of_Voluntary_Delisting_applications_20260617172653_20260716171458_20260813172640.xlsx
  Landing pages:
    https://www.nseindia.com/static/list/list-of-companies-proposed-to-be-delisted
    https://www.nseindia.com/static/list/public-notice-compulsory-delisting
"""
import sys
import re
import pandas as pd

OUR_SYMBOLS_SQL = """
-- run on the VPS to regenerate our_symbols.csv:
--   sudo -u postgres psql -d market_data -c "\\copy (
--     SELECT DISTINCT symbol FROM ohlcv_data
--   ) TO STDOUT WITH CSV HEADER" > our_symbols.csv
"""


def norm(s: str) -> str:
    """NSE symbols vary by suffix/case/whitespace across sources."""
    if not isinstance(s, str):
        return ""
    s = s.strip().upper()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"(-|_)?(EQ|BE|BZ|SM|ST)$", "", s)   # series suffixes
    return s


def load_any(path: str) -> pd.DataFrame:
    if path.lower().endswith((".xlsx", ".xls")):
        # try every sheet, keep the one that looks like a symbol table
        sheets = pd.read_excel(path, sheet_name=None, dtype=str)
        best, best_score = None, -1
        for name, df in sheets.items():
            cols = [str(c).lower() for c in df.columns]
            score = sum(("symbol" in c) or ("nse" in c) or ("name" in c) for c in cols)
            score += len(df) / 10000.0
            if score > best_score:
                best, best_score = df, score
        return best if best is not None else pd.DataFrame()
    return pd.read_csv(path, dtype=str)


def pick_symbol_col(df: pd.DataFrame) -> str:
    for c in df.columns:
        if "symbol" in str(c).lower():
            return c
    # fall back: the column whose values look most like tickers
    best, best_frac = df.columns[0], -1.0
    for c in df.columns:
        v = df[c].dropna().astype(str).head(400)
        if not len(v):
            continue
        frac = (v.str.strip().str.match(r"^[A-Za-z0-9&\-]{2,20}$")).mean()
        if frac > best_frac:
            best, best_frac = c, frac
    return best


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nERROR: pass the downloaded delisted list as the first argument.")
        raise SystemExit(1)

    dl = load_any(sys.argv[1])
    if dl.empty:
        raise SystemExit("could not parse the delisted file")
    scol = pick_symbol_col(dl)
    print(f"delisted file: {len(dl):,} rows; using column {scol!r}")

    dsyms = {norm(x) for x in dl[scol].dropna()}
    dsyms.discard("")
    print(f"distinct delisted symbols: {len(dsyms):,}")

    # date column, if present, lets us restrict to the backtest window
    dcol = next((c for c in dl.columns
                 if any(k in str(c).lower() for k in ("date", "w.e.f", "effective"))), None)
    if dcol:
        dt = pd.to_datetime(dl[dcol], errors="coerce", dayfirst=True)
        win = dl[(dt >= "2011-01-01") & (dt <= "2026-08-31")]
        wsyms = {norm(x) for x in win[scol].dropna()} - {""}
        print(f"delisted WITHIN 2011-2026 (col {dcol!r}): {len(wsyms):,}")
    else:
        wsyms = dsyms
        print("no usable date column — treating all rows as in-window")

    try:
        ours = pd.read_csv("our_symbols.csv", dtype=str)
        osyms = {norm(x) for x in ours.iloc[:, 0].dropna()} - {""}
    except FileNotFoundError:
        print("\nour_symbols.csv not found. Generate it with:" + OUR_SYMBOLS_SQL)
        raise SystemExit(1)
    print(f"our universe: {len(osyms):,} symbols")

    missing = wsyms - osyms
    present = wsyms & osyms
    print("\n" + "=" * 72)
    print("SURVIVORSHIP GAP")
    print("=" * 72)
    print(f"  delisted symbols we DO have price history for : {len(present):,}")
    print(f"  delisted symbols COMPLETELY ABSENT from our DB: {len(missing):,}")
    denom = len(osyms) + len(missing)
    print(f"  implied true historical universe              : {denom:,}")
    print(f"  share of the universe we never see            : "
          f"{len(missing)/denom*100:.1f}%")
    print("\nInterpretation: a momentum screener buys new highs, and companies that")
    print("later delisted often peaked spectacularly first. The share above is the")
    print("fraction of candidates that could never have been selected in any")
    print("backtest here -- a lower bound on the survivorship distortion.")

    pd.Series(sorted(missing)).to_csv("missing_delisted_symbols.csv",
                                      index=False, header=["symbol"])
    print("\nwrote missing_delisted_symbols.csv")


if __name__ == "__main__":
    main()
