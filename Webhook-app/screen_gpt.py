# ==============================================
# 🚀 OHM SYSTEM — P2.2 (FIXED TICK SIZE)
# Builds on P0 + P1 + P2 + P2.1 + P2.2
#
# P2.2 FIXED changes:
#   - TICK SIZE now fetched from Dhan CSV (SEM_TICK_SIZE column)
#   - Removed hardcoded TICK_SIZE_BANDS and TICK_SIZE_OVERRIDES
#   - Cache TICK_SIZES globally after first load
# ==============================================

import os
import json
import time
import requests
import pandas as pd
import yfinance as yf
import mplfinance as mpf
import matplotlib.pyplot as plt
from datetime import datetime
from openai import OpenAI
from matplotlib.patches import Patch

from reportlab.platypus import SimpleDocTemplate, Image, Spacer
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader

import sqlite3
import math

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

# ==========================
# GENERAL CONFIG
# ==========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL = int(os.getenv("CAPITAL") or "400000")

DEBUG = True  # master toggle for verbose logs

# When True, skips Telegram sends (useful for tuning). Set via env var.
DRY_RUN = os.getenv("OHM_DRY_RUN", "false").lower() in ("true", "1", "yes")

def dbg(msg):
    if DEBUG:
        print(msg)


# ==========================
# TIMING HELPER
# ==========================
class StageTimer:
    """Tracks cumulative time spent at each pipeline stage."""
    def __init__(self):
        self.totals = {}
        self.counts = {}

    def add(self, stage, seconds):
        self.totals[stage] = self.totals.get(stage, 0.0) + seconds
        self.counts[stage] = self.counts.get(stage, 0) + 1

    def report(self):
        print("\n" + "=" * 60)
        print("⏱️  STAGE TIMING")
        print("=" * 60)
        items = sorted(self.totals.items(), key=lambda x: -x[1])
        for stage, total in items:
            count = self.counts[stage]
            avg_ms = (total / count) * 1000 if count else 0
            print(f"   {stage:25} total={total:7.2f}s  calls={count:4d}  avg={avg_ms:7.1f}ms")
        print(f"   {'TOTAL':25} {sum(self.totals.values()):7.2f}s")


def _timed(timer, stage, fn, *args, **kwargs):
    """Run fn, record elapsed under stage."""
    t0 = time.time()
    result = fn(*args, **kwargs)
    timer.add(stage, time.time() - t0)
    return result


# ==========================================================
# 🆕 TICK SIZE FETCHING (from Dhan CSV)
# ==========================================================
TICK_SIZE_CACHE = {}

def load_tick_sizes():
    """
    Load tick sizes from Dhan instrument master CSV.
    Returns dict: {symbol: tick_size}
    Caches result globally to avoid repeated downloads.
    """
    global TICK_SIZE_CACHE

    if TICK_SIZE_CACHE:
        dbg(f"✅ Using cached tick sizes ({len(TICK_SIZE_CACHE)} symbols)")
        return TICK_SIZE_CACHE

    try:
        print("📥 Loading tick sizes from Dhan instrument master...")
        url = "https://images.dhan.co/api-data/api-scrip-master.csv"
        df = pd.read_csv(url, low_memory=False)

        # Filter for NSE equities only
        df = df[
            (df['SEM_EXM_EXCH_ID'] == 'NSE') &
            (df['SEM_SEGMENT'] == 'E')
            ]

        # Build cache: symbol → tick size
        for _, row in df.iterrows():
            symbol = str(row.get('SEM_TRADING_SYMBOL', '')).strip().upper()
            tick = float(row.get('SEM_TICK_SIZE', 0.05))

            if symbol:
                TICK_SIZE_CACHE[symbol] = tick

        print(f"✅ Loaded tick sizes for {len(TICK_SIZE_CACHE)} NSE equity symbols")
        dbg(f"   Sample: {list(TICK_SIZE_CACHE.items())[:5]}")

        return TICK_SIZE_CACHE

    except Exception as e:
        print(f"❌ Failed to load tick sizes from CSV: {e}")
        print(f"⚠️  Falling back to default tick=0.05 for all symbols")
        return {}


def get_tick_size(symbol):
    """
    Get tick size for a symbol.
    symbol: e.g., "ONGC" (without .NS)
    Returns: float tick size
    """
    global TICK_SIZE_CACHE

    # Load if not already cached
    if not TICK_SIZE_CACHE:
        load_tick_sizes()

    symbol_clean = symbol.replace(".NS", "").strip().upper()

    # Return from cache, or default to 0.05
    tick = TICK_SIZE_CACHE.get(symbol_clean, 0.05)

    if tick == 0.05:
        dbg(f"   [{symbol}] Tick size: ₹{tick} (from Dhan CSV or default)")
    else:
        dbg(f"   [{symbol}] Tick size: ₹{tick} (from Dhan CSV)")

    return tick


def round_to_tick(price, tick, mode="up"):
    """
    Round price to nearest tick.
    mode='up' for entry (buy above signal), 'down' for SL (sell below signal).
    """
    if tick <= 0:
        return round(price, 2)
    steps = price / tick
    if mode == "up":
        return round(math.ceil(steps) * tick, 2)
    elif mode == "down":
        return round(math.floor(steps) * tick, 2)
    else:
        return round(round(steps) * tick, 2)


# ==========================
# P0 — ENTRY TECHNIQUE CONFIG
# ==========================
# TICK offset above/below signal bar: 1 tick by default. Set 0 for exact H/L.
ENTRY_TICK_OFFSET_MULTIPLIER = 1

TREND_BAR_CLOSE_THRESHOLD = 0.70   # ⬇ loosened from 0.75
PIN_BAR_MAX_BODY_PCT = 0.35        # ⬆ loosened from 0.30
PIN_BAR_MIN_LOWER_WICK_PCT = 0.55  # ⬇ loosened from 0.60 (LUPIN would have matched)
MIN_BAR_RANGE_PCT = 0.005


# ==========================
# P1 — PIPELINE CONFIG
# ==========================
MIN_DAILY_TURNOVER = 10_00_00_000   # ₹10 cr (your setting)

FUND_MIN_REVENUE_GROWTH = 0.10
FUND_MIN_EARNINGS_GROWTH = 0.10
FUND_MIN_ROE = 0.15
FUND_MIN_PROMOTER_HOLDING = 0.40
FUND_MAX_PE = 80
FUND_REQUIRE_POSITIVE_EPS = True

IFP_VOL_SURGE_MULTIPLE = 1.5
IFP_UP_DAY_CLOSE_POS_MIN = 0.60
IFP_LOOKBACK_DAYS = 100
IFP_MIN_SCORE = 0.25

REGIME_BULLISH_THRESHOLD = 0.60
REGIME_BEARISH_THRESHOLD = 0.30
HARD_STOP_ON_DECLINE = True

MAX_OPEN_RISK_PCT = 0.10
SKIP_MANUAL = True


# ==========================================================
# P2 — TECHNICAL FILTER CONFIG
# ==========================================================
TECH_MAX_BASE_RANGE = 0.20           # P2.2: loosened from 0.15 for DISTRIBUTION regime
TECH_VOL_MULT = 0.80
TECH_TREND_ALIGNMENT_MODE = "medium" # P2.2: relaxed from "strict"
TECH_TREND_CROSSBACK_BUFFER = 0.02
TECH_REQUIRE_TREND_ALIGNMENT = True
BASE_LOOKBACK_BARS = 20


# ==========================================================
# P2 — BASE QUALITY CONFIG
# ==========================================================
BASE_MIN_PRIOR_UPMOVE_PCT = 0.15
BASE_PRIOR_UPMOVE_LOOKBACK = 60
BASE_MAX_GIVEBACK_PCT = 0.30
BASE_VOL_DRYUP_MAX_RATIO = 1.3
NEAR_BREAKOUT_MAX_DISTANCE = 0.05
BASE_QUALITY_VERBOSE_LOGS = False


# ==========================================================
# P2 — BASE STAGE CLASSIFICATION CONFIG
# ==========================================================
BASE_STAGE_LOOKBACK = 250
BASE_MIN_WIDTH_BARS = 10
BASE_BOUNCE_MIN_PCT = 0.10

BASE_STAGE_SIZE_MULTIPLIER = {
    1: 1.00,
    2: 1.00,
    3: 0.50,
    4: 0.25,
}
BASE_STAGE_DEFAULT_MULTIPLIER = 0.25
BASE_STAGE_MAX_ALLOWED = 4


# ==========================================================
# P2 — TARGET STRATEGY CONFIG
# ==========================================================
TARGET_STRATEGY = "FIXED_R"
TARGET_FIXED_R_MULTIPLE = 2.0


# ==========================================================
# P2 — ENTRY TRIGGERS
# ==========================================================
ENABLE_PULLBACK_TRIGGER = False
ENABLE_BREAKOUT_RETEST_TRIGGER = False


# ==========================================================
# P2 — GPT DEMOTED TO CONFIRMATION-ONLY
# ==========================================================
USE_GPT_AS_CONFIRMATION_ONLY = True
MAX_ALERTS_PER_RUN = 3


# ==========================
# DHAN TOKEN
# ==========================
DHAN_TOKEN_CACHE = {"token": None, "generated_at": 0}
SECURITY_MAP_CACHE = {}


def validate_dhan_token(token):
    try:
        r = requests.get(
            "https://api.dhan.co/v2/fundlimit",
            headers={"access-token": token, "Content-Type": "application/json"},
            timeout=10
        )
        if r.status_code == 200:
            return True
        print(f"⚠️ Token validation failed: {r.status_code} {r.text}")
        return False
    except Exception as e:
        print(f"⚠️ Token validation error: {e}")
        return False


def _generate_new_token():
    try:
        import pyotp
        client_id = os.getenv("DHAN_CLIENT_ID")
        pin = os.getenv("DHAN_PIN")
        secret = os.getenv("DHAN_TOTP_SECRET")
        if not all([client_id, pin, secret]):
            print("❌ Missing Dhan credentials")
            return None, "missing_creds"
        totp = pyotp.TOTP(secret).now()
        r = requests.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={"dhanClientId": client_id, "pin": pin, "totp": totp},
            timeout=15
        )
        if r.status_code != 200:
            return None, "http_error"
        data = r.json()
        token = data.get("accessToken")
        if not token:
            msg = str(data).lower()
            if "2 minutes" in msg or "rate" in msg or "once every" in msg:
                return None, "rate_limited"
            return None, "no_token"
        return token, "ok"
    except Exception as e:
        print(f"❌ Token generation exception: {e}")
        return None, "exception"


def get_dhan_token(force_refresh=False):
    global DHAN_TOKEN_CACHE
    if not force_refresh and DHAN_TOKEN_CACHE["token"]:
        if (time.time() - DHAN_TOKEN_CACHE["generated_at"]) < 23 * 3600:
            return DHAN_TOKEN_CACHE["token"]
    token, status = _generate_new_token()
    if status == "rate_limited":
        print("⏳ Dhan token rate-limited. Waiting 125s...")
        time.sleep(125)
        token, status = _generate_new_token()
    if not token:
        if DHAN_TOKEN_CACHE["token"]:
            return DHAN_TOKEN_CACHE["token"]
        return None
    if not validate_dhan_token(token):
        if DHAN_TOKEN_CACHE["token"]:
            return DHAN_TOKEN_CACHE["token"]
        return None
    DHAN_TOKEN_CACHE = {"token": token, "generated_at": time.time()}
    print("✅ New Dhan token generated, validated, cached")
    return token


# ==========================
# DB
# ==========================
def save_trade(payload):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_setups (
            setup_id TEXT PRIMARY KEY,
            symbol TEXT,
            qty INTEGER,
            entry REAL,
            sl REAL,
            target REAL,
            score REAL,
            status TEXT DEFAULT 'PENDING',
            executed_at TIMESTAMP,
            order_id TEXT,
            broker_response TEXT,
            risk REAL,
            reward REAL,
            rr_ratio REAL,
            entry_type TEXT,
            signal_bar_date TEXT,
            regime TEXT,
            ifp_score REAL,
            liquidity_turnover REAL,
            base_stage INTEGER,
            base_quality_score REAL,
            tick_size REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col, typ in [
        ("risk", "REAL"), ("reward", "REAL"), ("rr_ratio", "REAL"),
        ("entry_type", "TEXT"), ("signal_bar_date", "TEXT"),
        ("regime", "TEXT"), ("ifp_score", "REAL"),
        ("liquidity_turnover", "REAL"),
        ("base_stage", "INTEGER"), ("base_quality_score", "REAL"),
        ("tick_size", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE trade_setups ADD COLUMN {col} {typ}")
        except Exception:
            pass

    conn.execute("""
        INSERT OR REPLACE INTO trade_setups
        (setup_id, symbol, qty, entry, sl, target, score, status,
         risk, reward, rr_ratio, entry_type, signal_bar_date,
         regime, ifp_score, liquidity_turnover,
         base_stage, base_quality_score, tick_size)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload["setup_id"], payload["symbol"], payload["qty"],
        payload["entry"], payload["sl"], payload["target"],
        payload["score"], payload.get("status", "PENDING"),
        payload.get("risk", 0), payload.get("reward", 0), payload.get("rr_ratio", 0),
        payload.get("entry_type", "UNKNOWN"),
        payload.get("signal_bar_date", ""),
        payload.get("regime", "UNKNOWN"),
        payload.get("ifp_score", 0),
        payload.get("liquidity_turnover", 0),
        payload.get("base_stage", 0),
        payload.get("base_quality_score", 0),
        payload.get("tick_size", 0),
    ))
    conn.commit()
    conn.close()


def get_open_risk():
    conn = sqlite3.connect(DB_FILE)
    try:
        cur = conn.execute("""
            SELECT COALESCE(SUM((entry - sl) * qty), 0)
            FROM trade_setups
            WHERE status IN ('PENDING', 'PENDING_MANUAL_REVIEW', 'OPEN', 'EXECUTED')
        """)
        total = cur.fetchone()[0] or 0
    except Exception as e:
        print(f"⚠️ open risk query failed: {e}")
        total = 0
    conn.close()
    return total


# ==========================
# TELEGRAM
# ==========================
def escape_markdown_v2(text):
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    for ch in escape_chars:
        text = text.replace(ch, f"\\{ch}")
    return text

def send_message(text, buttons=None):
    if DRY_RUN:
        print(f"🔕 [DRY_RUN] Would send Telegram message:\n{text[:200]}{'...' if len(text)>200 else ''}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": escape_markdown_v2(text), "parse_mode": "MarkdownV2"}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        print("\n📤 TELEGRAM DEBUG ------------------------")
        print(f"Message length: {len(text)}")
        print(f"Buttons: {buttons}")
        print(f"Payload preview: {text[:200]}...")
        res = requests.post(url, data=payload, timeout=10)
        print(f"📡 Response: {res.text}")
        print(f"📡 Telegram status: {res.status_code}")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


def send_document(path, caption=None):
    if DRY_RUN:
        print(f"🔕 [DRY_RUN] Would send Telegram document: {path}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        with open(path, "rb") as f:
            res = requests.post(
                url, files={"document": f},
                data={"chat_id": CHAT_ID, "caption": caption or ""},
                timeout=20
            )
        print(f"📡 Document status: {res.status_code}")
    except Exception as e:
        print(f"❌ Document send failed: {e}")


# ==========================
# STOCK UNIVERSE
# ==========================
def get_stocks():
    headers = {"User-Agent": "Mozilla/5.0"}
    indices = ["NIFTY 500", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250"]
    stocks = set()
    for index in indices:
        try:
            url = f"https://www.nseindia.com/api/equity-stockIndices?index={index.replace(' ', '%20')}"
            res = requests.get(url, headers=headers, timeout=10)
            data = res.json()
            for item in data.get("data", []):
                symbol = item.get("symbol")
                if symbol and symbol.isalpha():
                    stocks.add(symbol + ".NS")
            time.sleep(0.5)
        except:
            continue
    return list(stocks)


# ==========================
# DHAN DATA FETCH
# ==========================
def fetch(stock):
    try:
        global SECURITY_MAP_CACHE
        if not SECURITY_MAP_CACHE:
            try:
                url = "https://images.dhan.co/api-data/api-scrip-master.csv"
                df_map = pd.read_csv(url, low_memory=False)
                df_map = df_map[df_map["SEM_EXM_EXCH_ID"] == "NSE"]
                SECURITY_MAP_CACHE = {
                    f"{row['SEM_TRADING_SYMBOL']}.NS": str(row["SEM_SMST_SECURITY_ID"])
                    for _, row in df_map.iterrows()
                }
                print(f"✅ Loaded {len(SECURITY_MAP_CACHE)} instruments from Dhan")
            except Exception as e:
                print(f"❌ Failed to load instrument list: {e}")
                return pd.DataFrame()

        security_id = SECURITY_MAP_CACHE.get(stock)
        if not security_id:
            return pd.DataFrame()

        from datetime import datetime, timedelta, timezone
        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        to_date = now_ist + timedelta(days=1)
        from_date = to_date - timedelta(days=400)

        payload = {
            "securityId": security_id,
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": from_date.strftime("%Y-%m-%d"),
            "toDate": to_date.strftime("%Y-%m-%d")
        }
        token = get_dhan_token()
        if not token:
            return pd.DataFrame()
        headers = {"Content-Type": "application/json", "access-token": token}

        response = None
        for attempt in range(3):
            try:
                response = requests.post(
                    "https://api.dhan.co/v2/charts/historical",
                    json=payload, headers=headers, timeout=15
                )
                if response.status_code == 200:
                    break
                if response.status_code in (401, 403):
                    token = get_dhan_token(force_refresh=True)
                    if token:
                        headers["access-token"] = token
                    else:
                        return pd.DataFrame()
                time.sleep(2 ** attempt)
            except Exception:
                time.sleep(2 ** attempt)

        if not response or response.status_code != 200:
            return pd.DataFrame()
        data = response.json()
        if not data.get("close"):
            return pd.DataFrame()

        df = pd.DataFrame({
            "Open": data["open"], "High": data["high"], "Low": data["low"],
            "Close": data["close"], "Volume": data["volume"],
            "Timestamp": data["timestamp"]
        })
        df["Date"] = pd.to_datetime(df["Timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df.set_index("Date", inplace=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna().sort_index()

        try:
            last_date = df.index[-1].date()
            today_ist = now_ist.date()
            if last_date < today_ist:
                intraday_payload = {
                    "securityId": security_id,
                    "exchangeSegment": "NSE_EQ",
                    "instrument": "EQUITY",
                    "interval": "60",
                    "oi": False,
                    "fromDate": (now_ist - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
                    "toDate": now_ist.strftime("%Y-%m-%d %H:%M:%S")
                }
                intraday_resp = requests.post(
                    "https://api.dhan.co/v2/charts/intraday",
                    json=intraday_payload, headers=headers, timeout=15
                )
                if intraday_resp.status_code == 200:
                    intraday_data = intraday_resp.json()
                    if intraday_data.get("close"):
                        df_1h = pd.DataFrame({
                            "Open": intraday_data["open"], "High": intraday_data["high"],
                            "Low": intraday_data["low"], "Close": intraday_data["close"],
                            "Volume": intraday_data["volume"],
                            "Timestamp": intraday_data["timestamp"]
                        })
                        df_1h["Date"] = pd.to_datetime(df_1h["Timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                        df_1h.set_index("Date", inplace=True)
                        df_1h = df_1h.sort_index()
                        today_df = df_1h[df_1h.index.date == today_ist]
                        if not today_df.empty:
                            df.loc[pd.Timestamp(today_ist, tz="Asia/Kolkata")] = [
                                today_df.iloc[0]["Open"], today_df["High"].max(),
                                today_df["Low"].min(), today_df.iloc[-1]["Close"],
                                today_df["Volume"].sum()
                            ]
        except Exception as e:
            print(f"❌ {stock}: Intraday fallback error → {e}")

        if len(df) > 1:
            last_ts = df.index[-1]; prev_ts = df.index[-2]
            if last_ts.date() == prev_ts.date() and df.iloc[-1]["Volume"] < df.iloc[-2]["Volume"] * 0.2:
                df = df.iloc[:-1]

        return df
    except Exception as e:
        print(f"❌ Dhan fetch error for {stock}: {e}")
        return pd.DataFrame()


def to_weekly(df):
    return df.resample('W').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min',
        'Close': 'last', 'Volume': 'sum'
    }).dropna()


# ==========================================================
# P1 STAGE 1 — LIQUIDITY
# ==========================================================
def check_liquidity(df, symbol="?"):
    if df is None or len(df) < 20:
        dbg(f"   [{symbol}] LIQUIDITY ❌ — insufficient bars")
        return False, 0.0
    last_20 = df.tail(20)
    avg_turnover = float((last_20["Close"] * last_20["Volume"]).mean())
    passed = avg_turnover >= MIN_DAILY_TURNOVER
    mark = "✅" if passed else "❌"
    dbg(f"   [{symbol}] LIQUIDITY {mark} | 20d avg turnover = ₹{avg_turnover/1e7:.2f} cr "
        f"(min = ₹{MIN_DAILY_TURNOVER/1e7:.2f} cr)")
    return passed, avg_turnover


# ==========================================================
# P1 STAGE 2 — FUNDAMENTAL
# ==========================================================
FUND_CACHE = {}

def check_fundamentals(symbol):
    if symbol in FUND_CACHE:
        return FUND_CACHE[symbol]
    details = {"reasons_failed": [], "reasons_passed": [], "fields": {}}
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as e:
        dbg(f"   [{symbol}] FUND ⚠️ yfinance error: {e} — default PASS")
        res = (True, {"reasons_failed": [], "reasons_passed": ["yf unavailable"], "fields": {}})
        FUND_CACHE[symbol] = res
        return res

    def gate(name, value, threshold, op=">="):
        details["fields"][name] = value
        if value is None:
            details["reasons_passed"].append(f"{name}: missing (default pass)")
            return True
        ok = (value >= threshold) if op == ">=" else (value <= threshold)
        (details["reasons_passed"] if ok else details["reasons_failed"]).append(
            f"{name}: {value} {op} {threshold}"
        )
        return ok

    rev_growth = info.get("revenueGrowth")
    earn_growth = info.get("earningsGrowth")
    roe = info.get("returnOnEquity")
    promoter = info.get("heldPercentInsiders")
    pe = info.get("trailingPE")
    eps = info.get("trailingEps")

    checks = [
        gate("revenue_growth", rev_growth, FUND_MIN_REVENUE_GROWTH),
        gate("earnings_growth", earn_growth, FUND_MIN_EARNINGS_GROWTH),
        gate("return_on_equity", roe, FUND_MIN_ROE),
        gate("promoter_holding", promoter, FUND_MIN_PROMOTER_HOLDING),
        gate("pe_ratio", pe, FUND_MAX_PE, op="<="),
    ]
    if FUND_REQUIRE_POSITIVE_EPS:
        if eps is None:
            details["reasons_passed"].append("eps: missing (default pass)")
            checks.append(True)
        elif eps > 0:
            details["reasons_passed"].append(f"eps: {eps} > 0")
            checks.append(True)
        else:
            details["reasons_failed"].append(f"eps: {eps} not > 0")
            checks.append(False)

    passed = all(checks)
    mark = "✅" if passed else "❌"
    dbg(f"   [{symbol}] FUND {mark}")
    for r in details["reasons_passed"]:
        dbg(f"         ✓ {r}")
    for r in details["reasons_failed"]:
        dbg(f"         ✗ {r}")
    res = (passed, details)
    FUND_CACHE[symbol] = res
    return res


# ==========================================================
# P1/P2 STAGE 3 — TECHNICAL FILTER
# ==========================================================
def filter_technical(df, symbol="?"):
    if df is None or df.empty or len(df) < 200:
        dbg(f"   [{symbol}] TECH ❌ — need 200 bars, have {0 if df is None else len(df)}")
        return False

    ema50 = df['Close'].ewm(span=50).mean().iloc[-1]
    sma200 = df['Close'].rolling(200).mean().iloc[-1]
    last_close = df.iloc[-1]['Close']

    mode = TECH_TREND_ALIGNMENT_MODE
    if mode == "strict":
        cond1 = last_close > ema50 > sma200
        cond1_label = f"close>{ema50:.2f}>{sma200:.2f}? {cond1}"
    elif mode == "medium":
        cond1 = (last_close > sma200) and (last_close > ema50)
        cond1_label = (f"close({last_close:.2f})>SMA200({sma200:.2f})&EMA50({ema50:.2f})? {cond1}")
    elif mode == "loose":
        cond1 = last_close > sma200
        cond1_label = f"close({last_close:.2f})>SMA200({sma200:.2f})? {cond1}"
    elif mode == "very_loose":
        cond1 = last_close >= sma200 * (1 - TECH_TREND_CROSSBACK_BUFFER)
        cond1_label = (f"close({last_close:.2f})>=SMA200-{TECH_TREND_CROSSBACK_BUFFER:.0%}"
                       f"({sma200*(1-TECH_TREND_CROSSBACK_BUFFER):.2f})? {cond1}")
    elif mode == "off":
        cond1 = True
        cond1_label = "(trend check off)"
    else:
        cond1 = last_close > ema50 > sma200
        cond1_label = f"close>{ema50:.2f}>{sma200:.2f}? {cond1} (unknown mode={mode})"

    recent = df.tail(BASE_LOOKBACK_BARS)
    base_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
    cond2 = base_range < TECH_MAX_BASE_RANGE

    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    cond3 = df.iloc[-1]['Volume'] > TECH_VOL_MULT * vol_avg

    passed = cond1 and cond2 and cond3
    mark = "✅" if passed else "❌"
    dbg(f"   [{symbol}] TECH {mark} | mode={mode} | {cond1_label} | "
        f"base_range={base_range:.2%}<{TECH_MAX_BASE_RANGE:.0%}? {cond2} | "
        f"vol>{TECH_VOL_MULT}×avg? {cond3}")
    return passed


# ==========================================================
# P1 STAGE 4 — IFP
# ==========================================================
def compute_ifp_score(df, symbol="?"):
    if df is None or len(df) < (20 + IFP_LOOKBACK_DAYS):
        return 0.0, False, {"reason": "insufficient bars"}

    window = df.tail(IFP_LOOKBACK_DAYS + 1).copy()
    vol_avg20 = df['Volume'].rolling(20).mean()

    ifp_up = 0
    ifp_dry_down = 0
    up_days = 0
    down_days = 0
    for i in range(1, len(window)):
        today = window.iloc[i]
        prev = window.iloc[i - 1]
        today_idx = window.index[i]
        avg_vol = vol_avg20.loc[today_idx] if today_idx in vol_avg20.index else vol_avg20.iloc[-1]
        bar_range = today['High'] - today['Low']
        close_pos = (today['Close'] - today['Low']) / bar_range if bar_range > 0 else 0
        is_up = today['Close'] > prev['Close']
        if is_up:
            up_days += 1
            if (today['Volume'] > IFP_VOL_SURGE_MULTIPLE * avg_vol
                    and close_pos >= IFP_UP_DAY_CLOSE_POS_MIN):
                ifp_up += 1
        else:
            down_days += 1
            if today['Volume'] < avg_vol:
                ifp_dry_down += 1

    score = (ifp_up + ifp_dry_down) / IFP_LOOKBACK_DAYS
    passed = score >= IFP_MIN_SCORE
    details = {"ifp_up": ifp_up, "ifp_dry_down": ifp_dry_down,
               "up_days": up_days, "down_days": down_days}
    mark = "✅" if passed else "❌"
    dbg(f"   [{symbol}] IFP {mark} | score={score:.2f} (min={IFP_MIN_SCORE}) | "
        f"surge_up={ifp_up}, dry_down={ifp_dry_down}, up/down={up_days}/{down_days}")
    return score, passed, details


# ==========================================================
# P1 — REGIME
# ==========================================================
def detect_market_regime(stocks_with_data, nifty_df=None):
    if not stocks_with_data:
        return "UNKNOWN", {"reason": "no_data"}
    above_count = 0
    total_count = 0
    for sym, df in stocks_with_data.items():
        if df is None or len(df) < 200:
            continue
        sma200 = df['Close'].rolling(200).mean().iloc[-1]
        if pd.isna(sma200):
            continue
        total_count += 1
        if df['Close'].iloc[-1] > sma200:
            above_count += 1
    if total_count == 0:
        return "UNKNOWN", {"reason": "no_sma200"}
    pct_above = above_count / total_count

    nifty_above_200 = None
    if nifty_df is not None and len(nifty_df) >= 200:
        nifty_sma200 = nifty_df['Close'].rolling(200).mean().iloc[-1]
        nifty_above_200 = bool(nifty_df['Close'].iloc[-1] > nifty_sma200)

    if pct_above >= REGIME_BULLISH_THRESHOLD:
        regime = "ADVANCE"
    elif pct_above <= REGIME_BEARISH_THRESHOLD:
        regime = "DECLINE" if (nifty_above_200 is False) else "ACCUMULATION"
    else:
        regime = "DISTRIBUTION"

    details = {
        "pct_above_200sma": round(pct_above, 3),
        "stocks_above": above_count,
        "stocks_total": total_count,
        "nifty_above_200sma": nifty_above_200,
    }
    print(f"🌍 MARKET REGIME: {regime} | {above_count}/{total_count} "
          f"({pct_above:.1%}) above 200-SMA | nifty_above_200={nifty_above_200}")
    return regime, details


# ==========================================================
# P1 — AGGREGATE RISK
# ==========================================================
def check_aggregate_risk(proposed_risk_inr, symbol="?"):
    cap = CAPITAL * MAX_OPEN_RISK_PCT
    current = get_open_risk()
    projected = current + proposed_risk_inr
    allowed = projected <= cap
    mark = "✅" if allowed else "⛔"
    dbg(f"   [{symbol}] AGGR RISK {mark} | current=₹{current:.0f}, "
        f"proposed=₹{proposed_risk_inr:.0f}, projected=₹{projected:.0f}, "
        f"cap=₹{cap:.0f}")
    return allowed, current, cap


# ==========================================================
# P2 — BASE QUALITY CHECK
# ==========================================================
def assess_base_quality(df, symbol="?"):
    if df is None or len(df) < BASE_LOOKBACK_BARS + BASE_PRIOR_UPMOVE_LOOKBACK:
        dbg(f"   [{symbol}] BASE_QUALITY ❌ — insufficient history")
        return False, 0.0, {"reason": "insufficient_history"}

    base = df.tail(BASE_LOOKBACK_BARS)
    base_high = float(base['High'].max())
    base_low = float(base['Low'].min())
    current = float(df['Close'].iloc[-1])

    prior = df.iloc[-(BASE_LOOKBACK_BARS + BASE_PRIOR_UPMOVE_LOOKBACK):-BASE_LOOKBACK_BARS]
    if prior.empty:
        return False, 0.0, {"reason": "no_prior_data"}

    prior_low = float(prior['Low'].min())
    prior_high = float(prior['High'].max())
    upmove_pct = (prior_high - prior_low) / prior_low if prior_low > 0 else 0

    if prior_high - prior_low > 0:
        giveback_pct = (prior_high - current) / (prior_high - prior_low)
    else:
        giveback_pct = 1.0
    giveback_pct = max(0.0, giveback_pct)

    base_vol_avg = float(base['Volume'].mean())
    prior_vol_avg = float(prior['Volume'].mean()) if len(prior) > 0 else 1
    vol_ratio = base_vol_avg / prior_vol_avg if prior_vol_avg > 0 else 99

    distance_from_high = (base_high - current) / base_high if base_high > 0 else 1.0

    checks = {
        "prior_upmove": upmove_pct >= BASE_MIN_PRIOR_UPMOVE_PCT,
        "giveback_ok": giveback_pct <= BASE_MAX_GIVEBACK_PCT,
        "vol_dryup": vol_ratio <= BASE_VOL_DRYUP_MAX_RATIO,
        "near_breakout": distance_from_high <= NEAR_BREAKOUT_MAX_DISTANCE,
    }
    passed = all(checks.values())
    score = sum(checks.values()) / len(checks)

    details = {
        "base_high": round(base_high, 2),
        "base_low": round(base_low, 2),
        "current": round(current, 2),
        "prior_high": round(prior_high, 2),
        "prior_low": round(prior_low, 2),
        "upmove_pct": round(upmove_pct, 3),
        "giveback_pct": round(giveback_pct, 3),
        "vol_ratio": round(vol_ratio, 2),
        "distance_from_high_pct": round(distance_from_high, 4),
        "checks": checks,
    }

    mark = "✅" if passed else "❌"
    failed_gates = [k for k, v in checks.items() if not v]
    if BASE_QUALITY_VERBOSE_LOGS or passed:
        dbg(f"   [{symbol}] BASE_QUALITY {mark} | score={score:.2f}")
        dbg(f"         prior_upmove={upmove_pct:.1%} (≥{BASE_MIN_PRIOR_UPMOVE_PCT:.0%}) → {checks['prior_upmove']}")
        dbg(f"         giveback={giveback_pct:.1%} (≤{BASE_MAX_GIVEBACK_PCT:.0%}) → {checks['giveback_ok']}")
        dbg(f"         vol_ratio={vol_ratio:.2f} (≤{BASE_VOL_DRYUP_MAX_RATIO}) → {checks['vol_dryup']}")
        dbg(f"         dist_from_high={distance_from_high:.2%} (≤{NEAR_BREAKOUT_MAX_DISTANCE:.0%}) → {checks['near_breakout']}")
    else:
        dbg(f"   [{symbol}] BASE_QUALITY {mark} | score={score:.2f} | "
            f"fail: {','.join(failed_gates)} | "
            f"upmove={upmove_pct:.0%} giveback={giveback_pct:.0%} "
            f"vol_r={vol_ratio:.2f} dist_hi={distance_from_high:.2%}")
    return passed, score, details


# ==========================================================
# P2 — BASE STAGE CLASSIFICATION
# ==========================================================
def classify_base_stage(df, symbol="?"):
    if df is None or len(df) < BASE_STAGE_LOOKBACK:
        return 1, {"reason": "short_history_assumed_base1"}

    window = df.tail(BASE_STAGE_LOOKBACK).copy().reset_index(drop=True)
    closes = window['Close'].values

    in_base = []
    for i in range(len(window)):
        start = max(0, i - BASE_MIN_WIDTH_BARS + 1)
        chunk = window.iloc[start:i+1]
        if len(chunk) < BASE_MIN_WIDTH_BARS:
            in_base.append(False)
            continue
        rng = (chunk['High'].max() - chunk['Low'].min()) / chunk['Low'].min()
        in_base.append(rng < TECH_MAX_BASE_RANGE)

    bases = []
    i = 0
    while i < len(in_base):
        if in_base[i]:
            j = i
            while j < len(in_base) and in_base[j]:
                j += 1
            bases.append((i, j - 1))
            i = j
        else:
            i += 1

    valid_bases = []
    if bases:
        valid_bases.append(bases[0])
        for prev_base, cur_base in zip(bases, bases[1:]):
            gap_start_idx = prev_base[1]
            gap_end_idx = cur_base[0]
            if gap_end_idx <= gap_start_idx:
                continue
            net_move = (closes[gap_end_idx] - closes[gap_start_idx]) / closes[gap_start_idx]
            if net_move >= BASE_BOUNCE_MIN_PCT:
                valid_bases.append(cur_base)

    stage = max(1, len(valid_bases))
    details = {
        "total_bases_raw": len(bases),
        "valid_bases_with_bounces": len(valid_bases),
        "stage": stage,
    }
    dbg(f"   [{symbol}] BASE_STAGE | count={stage} (raw={len(bases)}, "
        f"valid_after_bounce_check={len(valid_bases)})")
    return stage, details


# ==========================================================
# P2 — ADDITIONAL ENTRY TRIGGERS
# ==========================================================
def detect_pullback_trigger(df, symbol="?"):
    if not ENABLE_PULLBACK_TRIGGER or len(df) < 25:
        return None
    ema21 = df['Close'].ewm(span=21).mean()
    last_close = df.iloc[-1]['Close']
    last_low = df.iloc[-1]['Low']
    last_ema21 = ema21.iloc[-1]
    prev_3_lows = df['Low'].iloc[-4:-1].min()
    prev_3_ema21 = ema21.iloc[-4:-1]
    touched_ema = (prev_3_lows <= prev_3_ema21.max() * 1.01)
    bounced = last_close > last_ema21 and last_low > prev_3_lows
    if touched_ema and bounced:
        entry = df.iloc[-1]['High']
        sl = prev_3_lows
        dbg(f"   [{symbol}] 🎯 PULLBACK trigger matched (EMA21 bounce)")
        return {"type": "PULLBACK", "entry_raw": entry, "sl_raw": sl,
                "signal_bar_date": str(df.index[-1].date())}
    return None


def detect_breakout_retest_trigger(df, symbol="?"):
    if not ENABLE_BREAKOUT_RETEST_TRIGGER or len(df) < BASE_LOOKBACK_BARS + 10:
        return None
    base = df.iloc[-(BASE_LOOKBACK_BARS + 10):-10]
    base_high = float(base['High'].max())
    recent = df.iloc[-10:]
    broke_above = (recent['High'] > base_high).any()
    came_back_near = (recent['Low'].iloc[-5:] <= base_high * 1.01).any()
    current_close = df.iloc[-1]['Close']
    current_above = current_close > base_high
    if broke_above and came_back_near and current_above:
        entry = df.iloc[-1]['High']
        sl = base_high * 0.98
        dbg(f"   [{symbol}] 🎯 BREAKOUT_RETEST trigger matched (base_high={base_high:.2f})")
        return {"type": "BREAKOUT_RETEST", "entry_raw": entry, "sl_raw": sl,
                "signal_bar_date": str(df.index[-1].date())}
    return None


# ==========================================================
# P0 — ENTRY TECHNIQUE DETECTION
# ==========================================================
def detect_entry_technique(df, symbol="?"):
    if df is None or len(df) < 2:
        return {"type": None}

    day1 = df.iloc[-2]
    day2 = df.iloc[-1]
    d1_high, d1_low = float(day1['High']), float(day1['Low'])
    d2_open, d2_high, d2_low, d2_close = (float(day2['Open']), float(day2['High']),
                                          float(day2['Low']), float(day2['Close']))

    d2_range = d2_high - d2_low
    d2_body = abs(d2_close - d2_open)
    d2_lower_wick = min(d2_open, d2_close) - d2_low

    if d2_range < d2_close * MIN_BAR_RANGE_PCT:
        dbg(f"   [{symbol}] ENTRY ❌ — bar range too small ({d2_range:.2f})")
        return {"type": None}

    close_position = (d2_close - d2_low) / d2_range if d2_range > 0 else 0
    body_pct = d2_body / d2_range if d2_range > 0 else 0
    lower_wick_pct = d2_lower_wick / d2_range if d2_range > 0 else 0
    signal_date = str(df.index[-1].date())

    dbg(f"   [{symbol}] ENTRY DETECT → Day1 H={d1_high:.2f} L={d1_low:.2f}, "
        f"Day2 H={d2_high:.2f} L={d2_low:.2f} C={d2_close:.2f}")
    dbg(f"                body%={body_pct:.2%}, lwick%={lower_wick_pct:.2%}, "
        f"close_pos={close_position:.2%}")

    # 1. HH-HL
    if d2_high > d1_high and d2_low > d1_low:
        entry = max(d1_high, d2_high)
        sl = min(d1_low, d2_low)
        dbg(f"   [{symbol}] ✅ HH_HL | raw entry={entry:.2f} sl={sl:.2f}")
        return {"type": "HH_HL", "entry_raw": entry, "sl_raw": sl,
                "signal_bar_date": signal_date}

    # 2. Inside
    if d2_high <= d1_high and d2_low >= d1_low:
        dbg(f"   [{symbol}] ✅ INSIDE_BAR | raw entry={d2_high:.2f} sl={d2_low:.2f}")
        return {"type": "INSIDE_BAR", "entry_raw": d2_high, "sl_raw": d2_low,
                "signal_bar_date": signal_date}

    # 3. Pin Bar
    if body_pct <= PIN_BAR_MAX_BODY_PCT and lower_wick_pct >= PIN_BAR_MIN_LOWER_WICK_PCT:
        dbg(f"   [{symbol}] ✅ PIN_BAR | raw entry={d2_high:.2f} sl={d2_low:.2f}")
        return {"type": "PIN_BAR", "entry_raw": d2_high, "sl_raw": d2_low,
                "signal_bar_date": signal_date}

    # 4. Trend Bar
    if close_position >= TREND_BAR_CLOSE_THRESHOLD:
        dbg(f"   [{symbol}] ✅ TREND_BAR | raw entry={d2_high:.2f} sl={d2_low:.2f}")
        return {"type": "TREND_BAR", "entry_raw": d2_high, "sl_raw": d2_low,
                "signal_bar_date": signal_date}

    dbg(f"   [{symbol}] ❌ NO P0 ENTRY TECHNIQUE MATCHED — trying P2 triggers...")
    return {"type": None}


# ==========================================================
# ENTRY RESOLUTION
# ==========================================================
def resolve_entry(df, symbol):
    res = detect_entry_technique(df, symbol=symbol)
    if res["type"] is None:
        for trigger_fn in (detect_pullback_trigger, detect_breakout_retest_trigger):
            t = trigger_fn(df, symbol=symbol)
            if t is not None:
                res = t
                break

    if res.get("type") is None:
        return None

    raw_entry = float(res["entry_raw"])
    raw_sl = float(res["sl_raw"])

    # GET TICK SIZE FROM DHAN CSV (FIX)
    tick = get_tick_size(symbol)

    entry = round_to_tick(raw_entry + ENTRY_TICK_OFFSET_MULTIPLIER * tick, tick, mode="up")
    sl = round_to_tick(raw_sl, tick, mode="down")

    dbg(f"   [{symbol}] TICK ROUNDING | tick=₹{tick} | "
        f"raw entry={raw_entry:.4f}→{entry} | raw sl={raw_sl:.4f}→{sl}")

    if entry <= sl:
        dbg(f"   [{symbol}] ⚠️ After rounding entry({entry}) <= sl({sl}) — rejecting")
        return None

    return {
        "type": res["type"],
        "entry": entry,
        "sl": sl,
        "signal_bar_date": res["signal_bar_date"],
        "tick_size": tick,
        "raw_entry": raw_entry,
        "raw_sl": raw_sl,
    }


# ==========================================================
# POSITION SIZING
# ==========================================================
def create_trade(df, symbol, base_stage):
    trigger = resolve_entry(df, symbol)
    if trigger is None:
        return None

    entry = trigger["entry"]
    sl = trigger["sl"]
    risk_per_share = entry - sl
    if risk_per_share <= 0:
        return None

    stage_mult = BASE_STAGE_SIZE_MULTIPLIER.get(base_stage, BASE_STAGE_DEFAULT_MULTIPLIER)
    if base_stage > BASE_STAGE_MAX_ALLOWED:
        dbg(f"   [{symbol}] ⛔ base_stage={base_stage} > MAX_ALLOWED={BASE_STAGE_MAX_ALLOWED} — rejecting")
        return None

    risk_amt = CAPITAL * 0.0025 * stage_mult
    qty_risk = int(risk_amt / risk_per_share)
    max_capital_per_trade = CAPITAL * 0.10 * stage_mult
    qty_cap = int(max_capital_per_trade / entry)
    qty = min(qty_risk, qty_cap)

    if qty <= 0:
        dbg(f"   [{symbol}] SIZING ❌ qty=0 (qty_risk={qty_risk}, qty_cap={qty_cap}, stage_mult={stage_mult})")
        return None

    total_risk = risk_per_share * qty
    dbg(f"   [{symbol}] SIZING ✅ qty={qty} | stage={base_stage} (mult={stage_mult}) | "
        f"risk={qty_risk} cap={qty_cap} | total_risk=₹{total_risk:.0f}")

    return {
        "entry": entry,
        "sl": sl,
        "qty": qty,
        "entry_type": trigger["type"],
        "signal_bar_date": trigger["signal_bar_date"],
        "risk_per_share": round(risk_per_share, 2),
        "total_risk_inr": round(total_risk, 2),
        "tick_size": trigger["tick_size"],
        "base_stage": base_stage,
        "stage_multiplier": stage_mult,
    }


# ==========================================================
# TARGET STRATEGY
# ==========================================================
def compute_target(entry, sl, base_high=None, base_low=None, symbol="?"):
    risk = entry - sl
    if TARGET_STRATEGY == "FIXED_R":
        target = entry + TARGET_FIXED_R_MULTIPLE * risk
        dbg(f"   [{symbol}] TARGET=FIXED_R ({TARGET_FIXED_R_MULTIPLE}R): {target:.2f}")
    elif TARGET_STRATEGY == "BASE_PROJ" and base_high is not None and base_low is not None:
        target = entry + (base_high - base_low)
        dbg(f"   [{symbol}] TARGET=BASE_PROJ (base height={base_high-base_low:.2f}): {target:.2f}")
    else:
        target = 0
        dbg(f"   [{symbol}] TARGET=NONE (trailing stop only)")
    return round(target, 2)


# ==========================================================
# DETERMINISTIC RANKING
# ==========================================================
def rank_candidates(candidates):
    def sort_key(c):
        _, _, m = c
        return (
            -m.get("base_quality_score", 0),
            -m.get("ifp_score", 0),
            m.get("base_range_pct", 1),
        )
    return sorted(candidates, key=sort_key)


# ==========================================================
# GPT CONFIRMATION-ONLY
# ==========================================================
def gpt_confirm(pdf_path, candidates_info):
    if not client:
        return set()
    file = client.files.create(file=open(pdf_path, "rb"), purpose="user_data")
    cand_str = "\n".join([
        f"- {c['symbol']}: {c['entry_type']}, base_stage={c['base_stage']}, "
        f"det_score={c['det_score']:.2f}"
        for c in candidates_info
    ])
    PROMPT = f"""
You are a senior reviewer of OHM-style breakout setups. The system already validated:
liquidity, fundamentals, technicals, IFP, base quality, entry technique, base stage.

Candidates (already ranked):
{cand_str}

Look at each chart and REJECT any that visually look weak despite passing all gates
(e.g., chart shows wild spikes, poor structure, failed breakout recently).

Return ONLY JSON:
{{"reject": ["SYMBOL1.NS", ...], "reasons": {{"SYMBOL1.NS": "short reason"}}}}

If all look fine, return {{"reject": [], "reasons": {{}}}}.
"""
    try:
        res = client.responses.create(
            model="gpt-4.1-mini", temperature=0,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_file", "file_id": file.id}
            ]}]
        )
        out = res.output_text
        dbg(f"🧠 GPT confirmation raw: {out}")
        data = json.loads(out)
        rejected = set(data.get("reject", []))
        reasons = data.get("reasons", {})
        for r in rejected:
            dbg(f"   🧠 GPT VETO {r}: {reasons.get(r, '-')}")
        return rejected
    except Exception as e:
        dbg(f"⚠️ GPT confirmation failed: {e} — treating as all-pass")
        return set()


# ==========================
# CHART + PDF
# ==========================
def plot_chart(stock, df, save_path):
    if df is None or df.empty:
        return
    df_weekly = to_weekly(df.copy())
    for ema in [10, 21, 50, 200]:
        df[f'EMA{ema}'] = df['Close'].ewm(span=ema).mean()
        df_weekly[f'EMA{ema}'] = df_weekly['Close'].ewm(span=ema).mean()
    recent = df.tail(BASE_LOOKBACK_BARS)
    breakout = recent['High'].max()
    base_low = recent['Low'].min()
    base_high = recent['High'].max()
    mc = mpf.make_marketcolors(up='green', down='red',
                               volume={'up': 'green', 'down': 'red'})
    style = mpf.make_mpf_style(base_mpf_style='yahoo', marketcolors=mc)
    apds = [mpf.make_addplot(df[f'EMA{e}'], color=c)
            for e, c in [(10, 'black'), (21, 'red'), (50, 'blue'), (200, 'purple')]]
    apds_w = [mpf.make_addplot(df_weekly[f'EMA{e}'], color=c)
              for e, c in [(10, 'black'), (21, 'red'), (50, 'blue'), (200, 'purple')]]
    legend = [Patch(facecolor=c, label=f'EMA{e}')
              for e, c in [(10, 'black'), (21, 'red'), (50, 'blue'), (200, 'purple')]]

    fig1, ax1 = mpf.plot(df, type='candle', style=style, addplot=apds,
                         volume=True, returnfig=True,
                         figsize=(12, 6), datetime_format='%b-%y')
    ax1[0].axhline(breakout, linestyle='--', color='green')
    ax1[0].axhspan(base_low, base_high, alpha=0.1)
    ax1[0].legend(handles=legend)
    ax1[0].set_title(f"{stock} (Daily)", fontsize=14)
    fig1.savefig("d.png", dpi=200, bbox_inches='tight', pad_inches=0)
    plt.close(fig1)

    fig2, ax2 = mpf.plot(df_weekly, type='candle', style=style, addplot=apds_w,
                         volume=True, returnfig=True,
                         figsize=(12, 6), datetime_format='%b-%y')
    ax2[0].legend(handles=legend)
    ax2[0].set_title(f"{stock} (Weekly)", fontsize=14)
    fig2.savefig("w.png", dpi=200, bbox_inches='tight', pad_inches=0)
    plt.close(fig2)

    fig = plt.figure(figsize=(12, 9))
    a1 = fig.add_subplot(2, 1, 1); a1.imshow(plt.imread("d.png")); a1.axis('off')
    a2 = fig.add_subplot(2, 1, 2); a2.imshow(plt.imread("w.png")); a2.axis('off')
    plt.subplots_adjust(hspace=0.05)
    plt.savefig(save_path, dpi=200, bbox_inches='tight', pad_inches=0)
    plt.close()
    for f in ["d.png", "w.png"]:
        if os.path.exists(f):
            os.remove(f)


def build_pdf(images, path):
    doc = SimpleDocTemplate(path, pagesize=letter)
    elements = []
    for img_path in images:
        img = ImageReader(img_path)
        w, h = img.getSize()
        scale = min(doc.width / w, doc.height * 0.9 / h)
        elements.append(Image(img_path, width=w * scale, height=h * scale))
        elements.append(Spacer(1, 10))
    doc.build(elements)


# ==========================================================
# MAIN PIPELINE
# ==========================================================
def run():
    print("=" * 60)
    print("🔐 PRE-FLIGHT CHECKS")
    print("=" * 60)

    # Load tick sizes from Dhan CSV at startup
    print("📥 Loading Dhan instrument master (for tick sizes)...")
    load_tick_sizes()

    print("🔐 Dhan token pre-flight check...")
    if not get_dhan_token():
        print("❌ No Dhan token — aborting")
        return

    timer = StageTimer()

    stocks = get_stocks()
    print(f"📋 Universe: {len(stocks)} stocks")
    if DRY_RUN:
        print("🔕 DRY_RUN mode ON — Telegram alerts will be skipped")

    print("\n" + "=" * 60)
    print("📥 STAGE 0 — Fetching universe data (Dhan)")
    print("=" * 60)
    all_data = {}
    fetch_t0 = time.time()
    for i, s in enumerate(stocks, 1):
        df = _timed(timer, "fetch_dhan", fetch, s)
        if df is not None and not df.empty:
            all_data[s] = df
        if i % 50 == 0:
            elapsed = time.time() - fetch_t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (len(stocks) - i) / rate if rate > 0 else 0
            print(f"   progress: {i}/{len(stocks)} ({rate:.1f}/s, ETA {eta:.0f}s)")
    print(f"   fetched {len(all_data)}/{len(stocks)} stocks in {time.time()-fetch_t0:.1f}s")

    nifty_df = _timed(timer, "fetch_dhan", fetch, "NIFTY.NS")

    print("\n" + "=" * 60)
    print("🌍 STAGE 0.5 — Regime detection")
    print("=" * 60)
    regime, regime_details = detect_market_regime(all_data, nifty_df)

    if HARD_STOP_ON_DECLINE and regime == "DECLINE":
        msg = f"⛔ MARKET IN DECLINE. Scan halted."
        print(msg)
        send_message(msg)
        timer.report()
        return

    funnel = {
        "universe": len(stocks),
        "data_fetched": len(all_data),
        "pass_liquidity": 0,
        "pass_technical": 0,
        "pass_base_quality": 0,
        "pass_base_stage": 0,
        "pass_fundamental": 0,
        "pass_ifp": 0,
        "pass_entry": 0,
        "pass_aggregate_risk": 0,
    }

    bq_fail_counts = {
        "prior_upmove": 0,
        "giveback_ok": 0,
        "vol_dryup": 0,
        "near_breakout": 0,
        "insufficient_history": 0,
    }

    candidates = []

    print("\n" + "=" * 60)
    print("🔎 STAGES 1–8 — Per-stock pipeline")
    print("   Ordering: cheap→expensive (local checks first, yfinance last)")
    print("=" * 60)

    for s, df in all_data.items():
        dbg(f"\n--- {s} ---")

        # 1. Liquidity
        liq_pass, turnover = _timed(timer, "1_liquidity",
                                    check_liquidity, df, symbol=s)
        if not liq_pass:
            continue
        funnel["pass_liquidity"] += 1

        # 2. Technical
        if not _timed(timer, "2_technical", filter_technical, df, symbol=s):
            continue
        funnel["pass_technical"] += 1

        # 3. Base quality
        bq_pass, bq_score, bq_details = _timed(timer, "3_base_quality",
                                               assess_base_quality, df, symbol=s)
        if not bq_pass:
            if bq_details.get("reason") == "insufficient_history":
                bq_fail_counts["insufficient_history"] += 1
            else:
                for gate, ok in bq_details.get("checks", {}).items():
                    if not ok:
                        bq_fail_counts[gate] = bq_fail_counts.get(gate, 0) + 1
            continue
        funnel["pass_base_quality"] += 1

        # 4. Base stage
        stage, _ = _timed(timer, "4_base_stage",
                          classify_base_stage, df, symbol=s)
        if stage > BASE_STAGE_MAX_ALLOWED:
            dbg(f"   [{s}] STAGE ⛔ stage={stage} > {BASE_STAGE_MAX_ALLOWED}")
            continue
        funnel["pass_base_stage"] += 1

        # 5. Fundamental
        fund_pass, fund_details = _timed(timer, "5_fundamental",
                                         check_fundamentals, s)
        if not fund_pass:
            continue
        funnel["pass_fundamental"] += 1

        # 6. IFP
        ifp_score, ifp_pass, _ = _timed(timer, "6_ifp",
                                        compute_ifp_score, df, symbol=s)
        if not ifp_pass:
            continue
        funnel["pass_ifp"] += 1

        # 7. Entry detection + sizing
        trade = _timed(timer, "7_entry",
                       create_trade, df, symbol=s, base_stage=stage)
        if trade is None:
            continue
        funnel["pass_entry"] += 1

        # 8. Aggregate risk
        allowed, _, _ = _timed(timer, "8_aggregate_risk",
                               check_aggregate_risk,
                               trade["total_risk_inr"], symbol=s)
        if not allowed:
            continue
        funnel["pass_aggregate_risk"] += 1

        recent = df.tail(BASE_LOOKBACK_BARS)
        base_range_pct = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
        meta = {
            "turnover": turnover,
            "ifp_score": ifp_score,
            "fund": fund_details,
            "regime": regime,
            "base_stage": stage,
            "base_quality_score": bq_score,
            "base_quality_details": bq_details,
            "base_range_pct": base_range_pct,
            "base_high": bq_details.get("base_high"),
            "base_low": bq_details.get("base_low"),
        }
        candidates.append((s, trade, meta))
        dbg(f"   [{s}] ✅✅ ADDED TO CANDIDATES")

    # Funnel
    print("\n" + "=" * 60)
    print("📉 PIPELINE FUNNEL")
    print("=" * 60)
    for stage_name, count in funnel.items():
        print(f"   {stage_name:25} → {count}")
    print(f"   regime                    → {regime}")
    print(f"   candidates                → {len(candidates)}")

    bq_total_fails = sum(bq_fail_counts.values())
    if bq_total_fails > 0:
        print("\n   --- base-quality fail breakdown ---")
        print(f"   (stocks can fail multiple gates; counts are per-gate)")
        for gate, cnt in bq_fail_counts.items():
            if cnt > 0:
                pct = (cnt / bq_total_fails) * 100 if bq_total_fails else 0
                print(f"   {gate:25} → {cnt}  ({pct:.0f}% of fails)")

    if not candidates:
        msg = f"📭 OHM Scan: 0 qualifying setups.\nRegime: {regime}\nFunnel: {funnel}"
        print(msg)
        send_message(msg)
        timer.report()
        return

    # Deterministic rank and take top N
    ranked = rank_candidates(candidates)
    top_picks = ranked[:MAX_ALERTS_PER_RUN]
    print(f"\n🏆 TOP {len(top_picks)} PICKS (deterministic ranking):")
    for i, (s, t, m) in enumerate(top_picks, 1):
        print(f"   {i}. {s}: type={t['entry_type']}, stage={m['base_stage']}, "
              f"bq={m['base_quality_score']:.2f}, ifp={m['ifp_score']:.2f}, "
              f"tight={m['base_range_pct']:.1%}")

    # Charts + PDF
    folder = f"run_{datetime.now().strftime('%H%M%S')}"
    os.makedirs(folder, exist_ok=True)
    images = []
    for s, _, _ in top_picks:
        img = f"{folder}/{s}.png"
        _timed(timer, "chart_plot", plot_chart, s, all_data[s], img)
        if os.path.exists(img):
            images.append(img)
    if not images:
        timer.report()
        return

    pdf_path = f"{folder}/charts.pdf"
    _timed(timer, "chart_pdf", build_pdf, images, pdf_path)
    send_document(pdf_path, f"📄 OHM charts ({regime})")

    # GPT confirmation
    rejected_by_gpt = set()
    if USE_GPT_AS_CONFIRMATION_ONLY:
        det_scores = {s: 1.0 - (i * 0.1) for i, (s, _, _) in enumerate(top_picks)}
        candidates_info = [{
            "symbol": s, "entry_type": t["entry_type"],
            "base_stage": m["base_stage"],
            "det_score": det_scores[s],
        } for s, t, m in top_picks]
        rejected_by_gpt = _timed(timer, "gpt_confirm",
                                 gpt_confirm, pdf_path, candidates_info)

    final = [(s, t, m) for (s, t, m) in top_picks if s not in rejected_by_gpt]
    if not final:
        send_message(f"⚠️ All {len(top_picks)} candidates vetoed by GPT review.")
        timer.report()
        return

    # Alerts
    for rank_i, (s, trade, m) in enumerate(final, 1):
        entry = trade["entry"]
        sl = trade["sl"]
        qty = trade["qty"]
        risk = round(entry - sl, 2)

        target = compute_target(entry, sl,
                                base_high=m.get("base_high"),
                                base_low=m.get("base_low"),
                                symbol=s)
        reward = round(target - entry, 2) if target > 0 else 0
        rr_ratio = round(reward / risk, 2) if risk > 0 and reward > 0 else 0

        setup_id = f"{datetime.now().strftime('%H%M%S%f')}{s[:4]}"
        status = "PENDING" if SKIP_MANUAL else "PENDING_MANUAL_REVIEW"

        payload = {
            "setup_id": setup_id, "symbol": s, "qty": qty,
            "entry": entry, "sl": sl, "target": target,
            "score": m["base_quality_score"] * 10, "status": status,
            "risk": risk, "reward": reward, "rr_ratio": rr_ratio,
            "entry_type": trade["entry_type"],
            "signal_bar_date": trade["signal_bar_date"],
            "regime": regime,
            "ifp_score": m["ifp_score"],
            "liquidity_turnover": m["turnover"],
            "base_stage": m["base_stage"],
            "base_quality_score": m["base_quality_score"],
            "tick_size": trade["tick_size"],
        }
        save_trade(payload)

        type_label = {
            "TREND_BAR": "Trend Bar", "PIN_BAR": "Pin Bar",
            "HH_HL": "Higher High – Higher Low", "INSIDE_BAR": "Inside Bar",
            "PULLBACK": "Pullback to EMA21",
            "BREAKOUT_RETEST": "Breakout Retest",
        }.get(trade["entry_type"], trade["entry_type"])

        target_str = f"{target} _(strategy: {TARGET_STRATEGY})_" if target > 0 else "trailing stop only"

        msg = f"""📈 *OHM TRADE #{rank_i}*

*{s}*
🌍 Regime: *{regime}*
🎯 Entry: `{type_label}`
📅 Signal Bar: `{trade['signal_bar_date']}`

Entry: `{entry}` _(buy above)_
SL: `{sl}` _(exit on close below)_
Target: {target_str}
Qty: `{qty}`  _(stage {m['base_stage']}, x{trade['stage_multiplier']})_
Risk/Share: `{risk}`  |  R:R: `1:{rr_ratio}`
Tick: `₹{trade['tick_size']}`

🧱 Base Stage: *{m['base_stage']}*
📊 Base Quality: `{m['base_quality_score']:.2f}` (upmove, giveback, vol, near-BO)
💧 Liquidity: ₹{m['turnover']/1e7:.2f} cr/day
🏛️ IFP: {m['ifp_score']:.2f}
📐 Base Range: {m['base_range_pct']:.1%}
"""
        short_cb = f"BUY|{setup_id}|{s}|{qty}|{entry}|{sl}|{target}|{m['base_quality_score']:.2f}"
        buttons = [[{"text": "✅ Confirm Buy", "callback_data": short_cb}]]
        send_message(msg, buttons)

    summary = (f"✅ OHM scan complete\nRegime: {regime}\n"
               f"Alerts: {len(final)} (from {len(candidates)} candidates, "
               f"{len(top_picks) - len(final)} vetoed by GPT)\n"
               f"Funnel: {funnel}")
    send_message(summary)

    timer.report()


if __name__ == "__main__":
    run()