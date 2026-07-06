# ==============================================================
# tick_utils.py — Shared tick-size + instrument-master utilities
#
# Build step 1 of the Trade Setup Enhancement (design doc §8).
#
# Responsibilities:
#   1. The four tick functions, moved VERBATIM from the working
#      screener / entry_engine:
#         - convert_tick_multiplier_to_decimal
#         - load_tick_sizes
#         - get_tick_size
#         - round_to_tick
#   2. A disk-cache layer around the Dhan instrument-master CSV:
#         - cache file at project root
#         - daily freshness (re-download if not modified today, IST)
#         - stale-cache fallback if download fails
#         - ONE download serves BOTH tick sizes AND security IDs
#
# No dependencies on other new modules (per build order §16).
# Stdlib + pandas + requests only.
# ==============================================================

import os
import time
import pandas as pd
import requests
from datetime import datetime, timezone, timedelta

# --------------------------------------------------------------
# Paths & constants
# --------------------------------------------------------------
# Scripts live in /root/trade-execution-webhook/Webhook-app/.
# Project root is one level up: /root/trade-execution-webhook/.
# The design doc (§8) says the cache file lives at "project root".
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_THIS_DIR)
CACHE_FILE = os.path.join(PROJECT_ROOT, "api-scrip-master.csv")

DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

IST = timezone(timedelta(hours=5, minutes=30))

# In-process caches (cross-call short-circuit within a single run).
# The disk file is the cross-RUN cache; these globals are the
# in-PROCESS cache, mirroring the original scripts' behavior.
TICK_SIZE_CACHE = {}
SECURITY_MAP_CACHE = {}
_INSTRUMENT_DF = None  # raw DataFrame, read once per process


# --------------------------------------------------------------
# Disk-cache layer for the instrument-master CSV
# --------------------------------------------------------------
def _cache_is_fresh(path=CACHE_FILE):
    """
    Daily freshness check (design doc §8: "modified today").
    Fresh == cache file exists AND its mtime falls on today's
    calendar date in IST. NOT a rolling 24h window — this matches
    the doc wording and the IST-anchored rest of the codebase.
    """
    try:
        if not os.path.exists(path):
            return False
        mtime = os.path.getmtime(path)
        mtime_date_ist = datetime.fromtimestamp(mtime, IST).date()
        today_ist = datetime.now(IST).date()
        return mtime_date_ist == today_ist
    except Exception:
        return False


def _download_scrip_master(path=CACHE_FILE):
    """
    Download the Dhan instrument-master CSV and write it to `path`.
    Returns a DataFrame on success, or None on failure (caller
    decides whether to fall back to a stale cache).
    """
    try:
        print("📥 Downloading Dhan instrument master CSV...")
        df = pd.read_csv(DHAN_SCRIP_MASTER_URL, low_memory=False)
        # Write atomically: temp file then rename, so a crash mid-write
        # never leaves a corrupt cache behind.
        tmp_path = f"{path}.tmp"
        df.to_csv(tmp_path, index=False)
        os.replace(tmp_path, path)
        print(f"✅ Instrument master cached → {path} ({len(df)} rows)")
        return df
    except Exception as e:
        print(f"❌ Failed to download instrument master: {e}")
        return None


def _read_cached_csv(path=CACHE_FILE):
    """Read the on-disk cache into a DataFrame, or None if unreadable."""
    try:
        if os.path.exists(path):
            df = pd.read_csv(path, low_memory=False)
            return df
    except Exception as e:
        print(f"❌ Failed to read cached instrument master: {e}")
    return None


def load_instrument_master(force_refresh=False):
    """
    Return the instrument-master DataFrame, using the disk cache.

    Policy (design doc §8):
      - If cache is fresh (modified today) and not forced → read disk.
      - Else attempt re-download and rewrite the cache.
      - If download FAILS but ANY cache exists (even stale) → use it
        (a day-old tick size is almost certainly correct; a working
        stop beats no stop).
      - Hard-fail (return None) ONLY when there is no cache at all.

    Caches the DataFrame in a module global so repeat calls within a
    single process don't re-read disk.
    """
    global _INSTRUMENT_DF

    if _INSTRUMENT_DF is not None and not force_refresh:
        return _INSTRUMENT_DF

    # 1. Fresh cache on disk → just read it.
    if not force_refresh and _cache_is_fresh():
        df = _read_cached_csv()
        if df is not None:
            print(f"✅ Using fresh cached instrument master ({len(df)} rows)")
            _INSTRUMENT_DF = df
            return _INSTRUMENT_DF

    # 2. Stale or missing or forced → try to download.
    df = _download_scrip_master()
    if df is not None:
        _INSTRUMENT_DF = df
        return _INSTRUMENT_DF

    # 3. Download failed → fall back to any cache we have, even stale.
    stale = _read_cached_csv()
    if stale is not None:
        print("⚠️  Download failed — falling back to STALE cached instrument master")
        _INSTRUMENT_DF = stale
        return _INSTRUMENT_DF

    # 4. Nothing at all → hard fail.
    print("❌ No instrument master available (download failed, no cache on disk)")
    return None


# ==============================================================
# TICK SIZE FETCHING — functions moved verbatim, CSV source
# now routed through the disk cache via load_instrument_master().
# ==============================================================
def convert_tick_multiplier_to_decimal(tick_multiplier):
    """
    Convert SEM_TICK_SIZE multiplier to actual decimal tick value.
    SEM_TICK_SIZE is stored as: 1→0.01, 5→0.05, 10→0.10, etc.
    Formula: decimal_tick = tick_multiplier / 100

    Examples:
    - tick_multiplier=1 → 0.01
    - tick_multiplier=5 → 0.05
    - tick_multiplier=10 → 0.10
    - tick_multiplier=50 → 0.50
    """
    try:
        multiplier = float(tick_multiplier)
        if multiplier <= 0:
            return 0.05  # fallback default
        decimal_tick = multiplier / 100.0
        return round(decimal_tick, 4)
    except (ValueError, TypeError):
        return 0.05  # fallback default


def load_tick_sizes():
    """
    Load tick sizes from Dhan instrument master CSV.
    Converts SEM_TICK_SIZE multiplier to actual decimal values.
    Returns dict: {symbol: tick_size_decimal}
    Caches result globally to avoid repeated downloads.
    """
    global TICK_SIZE_CACHE

    if TICK_SIZE_CACHE:
        dbg(f"✅ Using cached tick sizes ({len(TICK_SIZE_CACHE)} symbols)")
        return TICK_SIZE_CACHE

    try:
        print("📥 Loading tick sizes from Dhan instrument master...")
        df = load_instrument_master()
        if df is None:
            print(f"⚠️  Falling back to default tick=0.05 for all symbols")
            return {}

        # Filter for NSE equities only
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]

        # Build cache: symbol → tick size (converted to decimal)
        for _, row in df.iterrows():
            symbol = str(row.get('SEM_TRADING_SYMBOL', '')).strip().upper()
            tick_multiplier = row.get('SEM_TICK_SIZE', 5)  # default 5 → 0.05

            # Convert multiplier to decimal tick value
            tick_decimal = convert_tick_multiplier_to_decimal(tick_multiplier)

            if symbol:
                TICK_SIZE_CACHE[symbol] = tick_decimal

        print(f"✅ Loaded tick sizes for {len(TICK_SIZE_CACHE)} NSE equity symbols")
        dbg(f"   Sample: {list(TICK_SIZE_CACHE.items())[:5]}")

        return TICK_SIZE_CACHE

    except Exception as e:
        print(f"❌ Failed to load tick sizes from CSV: {e}")
        print(f"⚠️  Falling back to default tick=0.05 for all symbols")
        return {}


def get_tick_size(symbol):
    """
    Get tick size for a symbol (already in decimal form).
    symbol: e.g., "ONGC" (without .NS) or "ONGC.NS"
    Returns: float tick size in decimal form (e.g., 0.01, 0.05, 0.10)
    """
    global TICK_SIZE_CACHE

    # Load if not already cached
    if not TICK_SIZE_CACHE:
        load_tick_sizes()

    symbol_clean = symbol.replace(".NS", "").strip().upper()

    # Return from cache, or default to 0.05
    tick = TICK_SIZE_CACHE.get(symbol_clean, 0.05)

    if tick == 0.05:
        dbg(f"   [{symbol}] Tick size: ₹{tick:.4f} (from Dhan CSV or default)")
    else:
        dbg(f"   [{symbol}] Tick size: ₹{tick:.4f} (from Dhan CSV)")

    return tick


def round_to_tick(price, tick, mode="up"):
    """
    Round price to nearest tick.

    Args:
        price (float): Price to round
        tick (float): Tick size in decimal form (e.g., 0.05, 0.01, 0.10)
        mode (str): "up" for entry (buy above signal),
                    "down" for SL (sell below signal),
                    "nearest" for standard rounding

    Returns:
        float: Price rounded to tick precision

    Examples:
        round_to_tick(100.47, 0.05, mode="up") → 100.50
        round_to_tick(100.47, 0.05, mode="down") → 100.45
        round_to_tick(100.47, 0.01, mode="up") → 100.47
    """
    import math
    if tick <= 0:
        return round(price, 4)

    # Calculate number of steps: price / tick
    steps = price / tick

    if mode == "up":
        # Ceiling: round up to next tick
        rounded_price = math.ceil(steps) * tick
    elif mode == "down":
        # Floor: round down to previous tick
        rounded_price = math.floor(steps) * tick
    else:  # mode == "nearest" or any other
        # Standard rounding: round to nearest tick
        rounded_price = round(steps) * tick

    # Return with 4 decimal precision (enough for 0.01 tick size)
    return round(rounded_price, 4)


# ==============================================================
# SECURITY-ID MAP — second consumer of the same cached download
# (matches the screener's fetch() behavior: NSE filter only,
#  keys in "SYMBOL.NS" form, values are string security IDs).
# ==============================================================
def load_security_ids():
    """
    Build the security-ID map from the same cached instrument master.
    Returns dict: {"SYMBOL.NS": "security_id_str"}.

    Filtering matches the screener's fetch() exactly: NSE exchange
    only (SEM_EXM_EXCH_ID == 'NSE'), NO segment filter — so each
    consumer gets byte-for-byte what it gets today.
    Caches globally to avoid repeated work within a process.
    """
    global SECURITY_MAP_CACHE

    if SECURITY_MAP_CACHE:
        dbg(f"✅ Using cached security IDs ({len(SECURITY_MAP_CACHE)} symbols)")
        return SECURITY_MAP_CACHE

    try:
        df = load_instrument_master()
        if df is None:
            print("⚠️  No instrument master — security-ID map empty")
            return {}

        df_map = df[df["SEM_EXM_EXCH_ID"] == "NSE"]
        SECURITY_MAP_CACHE = {
            f"{row['SEM_TRADING_SYMBOL']}.NS": str(row["SEM_SMST_SECURITY_ID"])
            for _, row in df_map.iterrows()
        }
        print(f"✅ Loaded {len(SECURITY_MAP_CACHE)} instruments (security IDs)")
        return SECURITY_MAP_CACHE

    except Exception as e:
        print(f"❌ Failed to build security-ID map: {e}")
        return {}


def get_security_id(symbol):
    """
    Get the Dhan security ID for a symbol.
    Accepts "ONGC" or "ONGC.NS". Returns the string ID, or None
    if not found.
    """
    global SECURITY_MAP_CACHE

    if not SECURITY_MAP_CACHE:
        load_security_ids()

    symbol_clean = symbol.replace(".NS", "").strip().upper()
    key = f"{symbol_clean}.NS"
    return SECURITY_MAP_CACHE.get(key)


# --------------------------------------------------------------
# Local dbg() so the verbatim functions keep their logging without
# importing anything from the screener. Mirrors the screener's dbg.
# --------------------------------------------------------------
DEBUG = True

def dbg(msg):
    if DEBUG:
        print(msg)


# --------------------------------------------------------------
# Manual smoke test: `python tick_utils.py`
# --------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("tick_utils.py smoke test")
    print("=" * 60)

    # Pure-logic checks (no network needed)
    assert convert_tick_multiplier_to_decimal(1) == 0.01
    assert convert_tick_multiplier_to_decimal(5) == 0.05
    assert convert_tick_multiplier_to_decimal(10) == 0.10
    assert convert_tick_multiplier_to_decimal(50) == 0.50
    assert convert_tick_multiplier_to_decimal(0) == 0.05      # fallback
    assert convert_tick_multiplier_to_decimal("x") == 0.05    # fallback
    print("✅ convert_tick_multiplier_to_decimal OK")

    assert round_to_tick(100.47, 0.05, mode="up") == 100.50
    assert round_to_tick(100.47, 0.05, mode="down") == 100.45
    assert round_to_tick(100.47, 0.01, mode="up") == 100.47
    assert round_to_tick(100.47, 0.05, mode="nearest") == 100.45
    print("✅ round_to_tick OK")

    # Network-dependent checks (build/serve from cache)
    print("\nLoading instrument master (download or cache)...")
    ticks = load_tick_sizes()
    print(f"   tick symbols: {len(ticks)}")
    secs = load_security_ids()
    print(f"   security IDs: {len(secs)}")

    if ticks:
        sample = list(ticks.keys())[0]
        print(f"   get_tick_size({sample!r}) = {get_tick_size(sample)}")
        print(f"   get_tick_size('{sample}.NS') = {get_tick_size(sample + '.NS')}")
    if secs:
        sample = list(secs.keys())[0].replace(".NS", "")
        print(f"   get_security_id({sample!r}) = {get_security_id(sample)}")

    print("\n✅ smoke test complete")