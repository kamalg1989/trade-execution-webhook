# ==============================================
# 🚀 OHM SYSTEM — P1 UPDATE
# Adds the full "Liquidity → Fundamental → Technical → IFP → Entry" pipeline
# from OHM "What to Buy" slide 11, plus:
#   - Market cycle (regime) awareness
#   - Aggregate open-risk cap (10% of capital)
#   - Funnel logging at every pipeline stage
#   - Manual review queue (can be bypassed with SKIP_MANUAL)
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

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")

# ==========================
# GENERAL CONFIG
# ==========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL = int(os.getenv("CAPITAL") or "200000")

# ==========================
# P0 — ENTRY TECHNIQUE CONFIG
# ==========================
TICK_SIZE = 0.05
TREND_BAR_CLOSE_THRESHOLD = 0.75
PIN_BAR_MAX_BODY_PCT = 0.30
PIN_BAR_MIN_LOWER_WICK_PCT = 0.60
MIN_BAR_RANGE_PCT = 0.005

# ==========================
# 🆕 P1 — PIPELINE CONFIG
# ==========================

# ---- Liquidity filter ----
# Minimum 20-day avg daily turnover (price × volume) in ₹
MIN_DAILY_TURNOVER = 1_00_00_000   # ₹1 crore

# ---- Fundamental filter (from yfinance) ----
# All thresholds expressed as pass/fail gates. Stock must pass ALL to qualify.
# If yfinance doesn't return a field, we default to "pass" (don't punish missing data).
FUND_MIN_REVENUE_GROWTH = 0.10     # 10% YoY
FUND_MIN_EARNINGS_GROWTH = 0.10    # 10% YoY
FUND_MIN_ROE = 0.15                # 15% (yfinance ROE used as ROCE proxy)
FUND_MIN_PROMOTER_HOLDING = 0.40   # 40% (yfinance: heldPercentInsiders)
FUND_MAX_PE = 80                   # absolute cap — filter out extreme valuations
FUND_REQUIRE_POSITIVE_EPS = True   # EPS must be > 0

# ---- IFP (Institutional Foot Print) config ----
IFP_VOL_SURGE_MULTIPLE = 1.5
IFP_UP_DAY_CLOSE_POS_MIN = 0.60
IFP_LOOKBACK_DAYS = 20
IFP_MIN_SCORE = 0.25

# ---- Market cycle / regime config ----
REGIME_BULLISH_THRESHOLD = 0.60    # >= 60% above 200-SMA → Advance
REGIME_BEARISH_THRESHOLD = 0.30    # <= 30% above 200-SMA → Decline/Accumulation
HARD_STOP_ON_DECLINE = True        # hard stop scanning during Decline

# ---- Aggregate risk cap ----
MAX_OPEN_RISK_PCT = 0.10           # 10% of capital across all open positions

# ---- Manual review ----
SKIP_MANUAL = True                 # True = direct alerts; False = queued


# ==========================
# DHAN TOKEN
# ==========================
DHAN_TOKEN_CACHE = {"token": None, "generated_at": 0}


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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for col, typ in [
        ("risk", "REAL"), ("reward", "REAL"), ("rr_ratio", "REAL"),
        ("entry_type", "TEXT"), ("signal_bar_date", "TEXT"),
        ("regime", "TEXT"), ("ifp_score", "REAL"),
        ("liquidity_turnover", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE trade_setups ADD COLUMN {col} {typ}")
        except Exception:
            pass

    conn.execute("""
        INSERT OR REPLACE INTO trade_setups
        (setup_id, symbol, qty, entry, sl, target, score, status,
         risk, reward, rr_ratio, entry_type, signal_bar_date,
         regime, ifp_score, liquidity_turnover)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ))
    conn.commit()
    conn.close()


def get_open_risk():
    """Sum of (entry-sl)*qty for all trades not yet closed. Returns ₹ amount."""
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
def send_message(text, buttons=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    try:
        res = requests.post(url, data=payload, timeout=10)
        print(f"📡 Telegram status: {res.status_code}")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


def send_document(path, caption=None):
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
        if "SECURITY_MAP_CACHE" not in globals():
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
        # ⬆ bumped to 300 days so SMA-200 is reliable
        from_date = to_date - timedelta(days=300)

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

        # Intraday fallback for today (unchanged)
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
# 🆕 P1 STAGE 1 — LIQUIDITY FILTER
# ==========================================================
def check_liquidity(df, symbol="?"):
    if df is None or len(df) < 20:
        print(f"   [{symbol}] LIQUIDITY ❌ — insufficient bars")
        return False, 0.0

    last_20 = df.tail(20)
    turnover_series = last_20["Close"] * last_20["Volume"]
    avg_turnover = float(turnover_series.mean())
    passed = avg_turnover >= MIN_DAILY_TURNOVER

    mark = "✅" if passed else "❌"
    print(f"   [{symbol}] LIQUIDITY {mark} | 20d avg turnover = ₹{avg_turnover/1e7:.2f} cr "
          f"(min = ₹{MIN_DAILY_TURNOVER/1e7:.2f} cr)")
    return passed, avg_turnover


# ==========================================================
# 🆕 P1 STAGE 2 — FUNDAMENTAL FILTER (yfinance)
# ==========================================================
FUND_CACHE = {}


def check_fundamentals(symbol):
    """Missing fields default to pass (don't punish sparse yfinance data)."""
    if symbol in FUND_CACHE:
        return FUND_CACHE[symbol]

    details = {"reasons_failed": [], "reasons_passed": [], "fields": {}}
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
    except Exception as e:
        print(f"   [{symbol}] FUND ⚠️ yfinance error: {e} — defaulting to PASS")
        res = (True, {"reasons_failed": [], "reasons_passed": ["yfinance unavailable"], "fields": {}})
        FUND_CACHE[symbol] = res
        return res

    def gate(name, value, threshold, op=">="):
        details["fields"][name] = value
        if value is None:
            details["reasons_passed"].append(f"{name}: missing (default pass)")
            return True
        ok = (value >= threshold) if op == ">=" else (value <= threshold)
        if ok:
            details["reasons_passed"].append(f"{name}: {value} {op} {threshold}")
        else:
            details["reasons_failed"].append(f"{name}: {value} fails {op} {threshold}")
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
    print(f"   [{symbol}] FUND {mark}")
    for r in details["reasons_passed"]:
        print(f"         ✓ {r}")
    for r in details["reasons_failed"]:
        print(f"         ✗ {r}")

    res = (passed, details)
    FUND_CACHE[symbol] = res
    return res


# ==========================================================
# 🆕 P1 STAGE 3 — TECHNICAL FILTER (UPDATED to SMA-200)
# ==========================================================
def filter_technical(df, symbol="?"):
    if df is None or df.empty or len(df) < 200:
        print(f"   [{symbol}] TECHNICAL ❌ — insufficient bars for SMA200")
        return False

    ema50 = df['Close'].ewm(span=50).mean().iloc[-1]
    sma200 = df['Close'].rolling(200).mean().iloc[-1]
    last_close = df.iloc[-1]['Close']

    cond1 = last_close > ema50 > sma200
    recent = df.tail(20)
    base_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
    cond2 = base_range < 0.15
    vol_avg = df['Volume'].rolling(20).mean().iloc[-1]
    cond3 = df.iloc[-1]['Volume'] > 0.8 * vol_avg

    passed = cond1 and cond2 and cond3
    mark = "✅" if passed else "❌"
    print(f"   [{symbol}] TECHNICAL {mark} | close({last_close:.2f})>EMA50({ema50:.2f})>SMA200({sma200:.2f})? {cond1} | "
          f"base_range={base_range:.2%}<15%? {cond2} | vol>0.8×avg? {cond3}")
    return passed


# ==========================================================
# 🆕 P1 STAGE 4 — IFP (INSTITUTIONAL FOOT PRINT)
# ==========================================================
def compute_ifp_score(df, symbol="?"):
    """
    Score between 0 and 1 indicating institutional participation.
    UP day + high vol + close near high = IFP_UP
    DOWN day + low vol (< 20d avg) = IFP_DRY_DOWN (constructive)
    Score = (IFP_UP + IFP_DRY_DOWN) / lookback
    """
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

    details = {
        "ifp_up_days": ifp_up, "ifp_dry_down_days": ifp_dry_down,
        "total_up_days": up_days, "total_down_days": down_days,
        "lookback": IFP_LOOKBACK_DAYS,
    }
    mark = "✅" if passed else "❌"
    print(f"   [{symbol}] IFP {mark} | score={score:.2f} (min={IFP_MIN_SCORE}) | "
          f"vol_surge_up={ifp_up}, dry_down={ifp_dry_down}, "
          f"total up/down={up_days}/{down_days}")
    return score, passed, details


# ==========================================================
# 🆕 P1 — MARKET REGIME DETECTION
# ==========================================================
def detect_market_regime(stocks_with_data, nifty_df=None):
    """
    Primary signal: % of scanned stocks above 200-SMA
    Tiebreaker: Nifty vs own 200-SMA
    """
    if not stocks_with_data:
        print("⚠️ REGIME: no data → UNKNOWN")
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
# 🆕 P1 — AGGREGATE OPEN RISK CHECK
# ==========================================================
def check_aggregate_risk(proposed_risk_inr, symbol="?"):
    cap = CAPITAL * MAX_OPEN_RISK_PCT
    current = get_open_risk()
    projected = current + proposed_risk_inr
    allowed = projected <= cap
    mark = "✅" if allowed else "⛔"
    print(f"   [{symbol}] AGGR RISK {mark} | current=₹{current:.0f}, "
          f"proposed=₹{proposed_risk_inr:.0f}, projected=₹{projected:.0f}, "
          f"cap=₹{cap:.0f} ({MAX_OPEN_RISK_PCT:.0%})")
    return allowed, current, cap


# ==========================================================
# P0 — ENTRY TECHNIQUE DETECTION (unchanged)
# ==========================================================
def detect_entry_technique(df, symbol="?"):
    if df is None or len(df) < 2:
        print(f"   [{symbol}] ENTRY: insufficient bars")
        return {"type": None}

    day1 = df.iloc[-2]
    day2 = df.iloc[-1]
    d1_open, d1_high, d1_low, d1_close = float(day1['Open']), float(day1['High']), float(day1['Low']), float(day1['Close'])
    d2_open, d2_high, d2_low, d2_close = float(day2['Open']), float(day2['High']), float(day2['Low']), float(day2['Close'])

    d2_range = d2_high - d2_low
    d2_body = abs(d2_close - d2_open)
    d2_upper_wick = d2_high - max(d2_open, d2_close)
    d2_lower_wick = min(d2_open, d2_close) - d2_low

    if d2_range < d2_close * MIN_BAR_RANGE_PCT:
        print(f"   [{symbol}] ENTRY ❌ — bar range too small")
        return {"type": None}

    close_position = (d2_close - d2_low) / d2_range if d2_range > 0 else 0
    body_pct = d2_body / d2_range if d2_range > 0 else 0
    lower_wick_pct = d2_lower_wick / d2_range if d2_range > 0 else 0
    signal_date = str(df.index[-1].date())
    prev_date = str(df.index[-2].date())

    print(f"   [{symbol}] ENTRY → Day1({prev_date}) O={d1_open:.2f} H={d1_high:.2f} L={d1_low:.2f} C={d1_close:.2f}")
    print(f"                 Day2({signal_date}) O={d2_open:.2f} H={d2_high:.2f} L={d2_low:.2f} C={d2_close:.2f}")
    print(f"                 body%={body_pct:.2%}, lwick%={lower_wick_pct:.2%}, close_pos={close_position:.2%}")

    if d2_high > d1_high and d2_low > d1_low:
        entry = max(d1_high, d2_high) + TICK_SIZE
        sl = min(d1_low, d2_low)
        print(f"   [{symbol}] ✅ HH_HL | Entry={entry:.2f} SL={sl:.2f}")
        return {"type": "HH_HL", "entry": round(entry, 2), "sl": round(sl, 2),
                "signal_bar_date": signal_date}

    if d2_high <= d1_high and d2_low >= d1_low:
        entry = d2_high + TICK_SIZE
        sl = d2_low
        print(f"   [{symbol}] ✅ INSIDE_BAR | Entry={entry:.2f} SL={sl:.2f}")
        return {"type": "INSIDE_BAR", "entry": round(entry, 2), "sl": round(sl, 2),
                "signal_bar_date": signal_date}

    if body_pct <= PIN_BAR_MAX_BODY_PCT and lower_wick_pct >= PIN_BAR_MIN_LOWER_WICK_PCT:
        entry = d2_high + TICK_SIZE
        sl = d2_low
        print(f"   [{symbol}] ✅ PIN_BAR | Entry={entry:.2f} SL={sl:.2f}")
        return {"type": "PIN_BAR", "entry": round(entry, 2), "sl": round(sl, 2),
                "signal_bar_date": signal_date}

    if close_position >= TREND_BAR_CLOSE_THRESHOLD:
        entry = d2_high + TICK_SIZE
        sl = d2_low
        print(f"   [{symbol}] ✅ TREND_BAR | Entry={entry:.2f} SL={sl:.2f}")
        return {"type": "TREND_BAR", "entry": round(entry, 2), "sl": round(sl, 2),
                "signal_bar_date": signal_date}

    print(f"   [{symbol}] ❌ NO ENTRY TECHNIQUE MATCHED")
    return {"type": None}


# ==========================
# POSITION SIZING
# ==========================
def create_trade(df, symbol="?"):
    technique = detect_entry_technique(df, symbol=symbol)
    if technique["type"] is None:
        return None

    entry = technique["entry"]
    sl = technique["sl"]
    risk_per_share = entry - sl
    if risk_per_share <= 0:
        return None

    risk_amt = CAPITAL * 0.0025
    qty_risk = int(risk_amt / risk_per_share)
    max_capital_per_trade = CAPITAL * 0.10
    qty_cap = int(max_capital_per_trade / entry)
    qty = min(qty_risk, qty_cap)
    if qty <= 0:
        return None

    total_risk = risk_per_share * qty
    print(f"   [{symbol}] SIZING | qty={qty} (risk={qty_risk}, cap={qty_cap}), "
          f"total_risk=₹{total_risk:.0f}")

    return {
        "entry": entry, "sl": sl, "qty": qty,
        "entry_type": technique["type"],
        "signal_bar_date": technique["signal_bar_date"],
        "risk_per_share": round(risk_per_share, 2),
        "total_risk_inr": round(total_risk, 2),
    }


# ==========================
# CHARTS + PDF
# ==========================
def plot_chart(stock, df, save_path):
    if df is None or df.empty:
        return
    df_weekly = to_weekly(df.copy())
    for ema in [10, 21, 50, 200]:
        df[f'EMA{ema}'] = df['Close'].ewm(span=ema).mean()
        df_weekly[f'EMA{ema}'] = df_weekly['Close'].ewm(span=ema).mean()

    recent = df.tail(20)
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


# ==========================
# GPT CONFIRMATION
# ==========================
def gpt_decision(pdf_path, detected_entries):
    if not client:
        return json.dumps({"picks": []})
    file = client.files.create(file=open(pdf_path, "rb"), purpose="user_data")
    detected_str = "\n".join([f"- {sym}: code detected {et}"
                              for sym, et in detected_entries.items()])
    PROMPT = f"""
You are an institutional breakout trader following OHM rules.

Stocks passed these gates: liquidity, fundamentals, technicals, IFP, entry technique.
Pre-detected entry types:
{detected_str}

Cross-check charts visually. Reject weak-looking setups.

SCORING (0-10): 9-10 perfect | 8 strong | 7 acceptable | <7 reject
SELECTION: Max 3 stocks | score >= 7 | entry_type in {{TREND_BAR, PIN_BAR, HH_HL, INSIDE_BAR}}

OUTPUT JSON:
{{"picks": [{{"stock": "ABC.NS", "score": 8.5, "quality": "STRONG",
             "reason": "...", "entry_type": "TREND_BAR"}}]}}

If none: {{"picks": []}}
"""
    res = client.responses.create(
        model="gpt-4.1-mini", temperature=0,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": PROMPT},
            {"type": "input_file", "file_id": file.id}
        ]}]
    )
    return res.output_text


def parse_gpt_output(output):
    try:
        return json.loads(output).get("picks", [])
    except Exception as e:
        print(f"⚠️ GPT parse failed: {e}\n   Raw: {output}")
        return []


# ==========================================================
# MAIN PIPELINE
# ==========================================================
def run():
    print("🔐 Dhan token pre-flight check...")
    if not get_dhan_token():
        print("❌ No Dhan token — aborting")
        return

    # Stage 0 — Universe fetch
    stocks = get_stocks()
    print(f"📋 Universe: {len(stocks)} stocks")

    print("\n" + "=" * 60)
    print("📥 STAGE 0 — Fetching data for universe")
    print("=" * 60)
    all_data = {}
    for s in stocks:
        df = fetch(s)
        if df is not None and not df.empty:
            all_data[s] = df
    print(f"   fetched data for {len(all_data)}/{len(stocks)} stocks")

    nifty_df = fetch("NIFTY.NS")

    # Stage 0.5 — Regime
    print("\n" + "=" * 60)
    print("🌍 STAGE 0.5 — Market regime detection")
    print("=" * 60)
    regime, regime_details = detect_market_regime(all_data, nifty_df)

    if HARD_STOP_ON_DECLINE and regime == "DECLINE":
        msg = f"⛔ MARKET IN DECLINE ({regime_details['pct_above_200sma']:.1%} above 200-SMA). Scan halted."
        print(msg)
        send_message(msg)
        return

    funnel = {
        "universe": len(stocks),
        "data_fetched": len(all_data),
        "pass_liquidity": 0,
        "pass_fundamental": 0,
        "pass_technical": 0,
        "pass_ifp": 0,
        "pass_entry": 0,
        "pass_aggregate_risk": 0,
    }

    trade_map = {}
    detected_entries = {}
    meta = {}

    print("\n" + "=" * 60)
    print("🔎 STAGES 1-6 — Per-stock pipeline")
    print("=" * 60)

    for s, df in all_data.items():
        print(f"\n--- {s} ---")

        liq_pass, turnover = check_liquidity(df, symbol=s)
        if not liq_pass:
            continue
        funnel["pass_liquidity"] += 1

        fund_pass, fund_details = check_fundamentals(s)
        if not fund_pass:
            continue
        funnel["pass_fundamental"] += 1

        if not filter_technical(df, symbol=s):
            continue
        funnel["pass_technical"] += 1

        ifp_score, ifp_pass, ifp_details = compute_ifp_score(df, symbol=s)
        if not ifp_pass:
            continue
        funnel["pass_ifp"] += 1

        trade = create_trade(df, symbol=s)
        if trade is None:
            continue
        funnel["pass_entry"] += 1

        allowed, current_open, cap = check_aggregate_risk(trade["total_risk_inr"], symbol=s)
        if not allowed:
            continue
        funnel["pass_aggregate_risk"] += 1

        trade_map[s] = trade
        detected_entries[s] = trade["entry_type"]
        meta[s] = {
            "turnover": turnover,
            "ifp_score": ifp_score,
            "fund": fund_details,
            "regime": regime,
        }

    # Funnel summary
    print("\n" + "=" * 60)
    print("📉 PIPELINE FUNNEL")
    print("=" * 60)
    for stage, count in funnel.items():
        print(f"   {stage:25} → {count}")
    print(f"   regime                    → {regime}")

    if not trade_map:
        msg = f"📭 OHM Scan: 0 qualifying setups today.\nRegime: {regime}\nFunnel: {funnel}"
        print(msg)
        send_message(msg)
        return

    # Charts + PDF
    folder = f"run_{datetime.now().strftime('%H%M%S')}"
    os.makedirs(folder, exist_ok=True)
    images = []
    for s in trade_map:
        img = f"{folder}/{s}.png"
        plot_chart(s, all_data[s], img)
        if os.path.exists(img):
            images.append(img)

    if not images:
        print("⚠️ No charts generated")
        return

    pdf_path = f"{folder}/charts.pdf"
    build_pdf(images, pdf_path)
    send_document(pdf_path, f"📄 OHM charts ({regime})")

    # GPT confirmation
    print("\n🧠 GPT confirmation pass...")
    output = gpt_decision(pdf_path, detected_entries)
    print(f"GPT raw: {output}")
    picks = parse_gpt_output(output)
    print(f"GPT picks: {picks}")

    if not picks:
        msg = (f"⚠️ Code found {len(trade_map)} setups but GPT rejected all.\n"
               f"Regime: {regime}\nCode candidates: {list(trade_map.keys())}")
        send_message(msg)
        return

    # Alerts
    for p in picks:
        s = p["stock"]
        if s not in trade_map:
            continue
        trade = trade_map[s]
        m = meta[s]

        gpt_type = p.get("entry_type", "").upper().replace(" ", "_").replace("-", "_")
        code_type = trade["entry_type"]
        type_match = gpt_type == code_type

        entry = trade["entry"]; sl = trade["sl"]; qty = trade["qty"]
        risk = round(entry - sl, 2)
        reward = round(risk * 2, 2)   # 2R placeholder (P2)
        target = round(entry + reward, 2)
        rr_ratio = round(reward / risk, 2) if risk > 0 else 0
        score = p.get("score", 0)
        setup_id = f"{datetime.now().strftime('%H%M%S%f')}{s[:4]}"
        status = "PENDING" if SKIP_MANUAL else "PENDING_MANUAL_REVIEW"

        payload = {
            "setup_id": setup_id, "symbol": s, "qty": qty,
            "entry": entry, "sl": sl, "target": target, "score": score,
            "status": status,
            "risk": risk, "reward": reward, "rr_ratio": rr_ratio,
            "entry_type": code_type,
            "signal_bar_date": trade["signal_bar_date"],
            "regime": regime,
            "ifp_score": m["ifp_score"],
            "liquidity_turnover": m["turnover"],
        }
        save_trade(payload)

        type_label = {
            "TREND_BAR": "Trend Bar", "PIN_BAR": "Pin Bar",
            "HH_HL": "Higher High – Higher Low", "INSIDE_BAR": "Inside Bar",
        }.get(code_type, code_type)

        review_tag = "" if SKIP_MANUAL else "\n⏳ *PENDING MANUAL REVIEW*"
        msg = f"""📈 *OHM TRADE*{review_tag}

*{s}*
Score: {score} ({p.get('quality', '-')})
🌍 Regime: *{regime}*

🎯 OHM Entry: `{type_label}`
Signal Bar Date: `{trade['signal_bar_date']}`

Entry: `{entry}` _(buy above)_
SL: `{sl}` _(exit on close below)_
Target: `{target}` _(2R placeholder)_
Qty: `{qty}`
Risk/Share: `{risk}`  |  R:R: `1:{rr_ratio}`

💧 Liquidity: ₹{m['turnover']/1e7:.2f} cr/day
🏛️ IFP score: {m['ifp_score']:.2f}
📊 Fundamentals: {len(m['fund']['reasons_passed'])} passed, {len(m['fund']['reasons_failed'])} failed

Reason: {p.get('reason', '-')}
GPT type: `{p.get('entry_type', '-')}` {'✅' if type_match else '⚠️ mismatch'}
"""
        short_cb = f"BUY|{setup_id}|{s}|{qty}|{entry}|{sl}|{target}|{score}"
        buttons = [[{"text": "✅ Confirm Buy", "callback_data": short_cb}]]
        send_message(msg, buttons)

    summary = f"✅ OHM scan complete\nRegime: {regime}\nAlerts sent: {len(picks)}\nFunnel: {funnel}"
    send_message(summary)


if __name__ == "__main__":
    run()