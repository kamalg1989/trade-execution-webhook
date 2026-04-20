# ==============================================
# 🚀 OHM SYSTEM — P0 UPDATE
# Adds proper entry-technique detection per OHM PDF:
#   - Trend Bar
#   - Pin Bar (bullish)
#   - HH-HL (double bar)
#   - Inside Bar
# Entry / SL now derived from the matched pattern, not blindly
# from last candle's high/low.
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
# CONFIG
# ==========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)
else:
    client = None

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

CAPITAL = int(os.getenv("CAPITAL") or "200000")

# ==========================
# OHM ENTRY TECHNIQUE CONFIG
# ==========================
# Tick size for entry offset above signal bar high.
# NSE equity default = 0.05. Set to 0 if you want entry = exact high.
TICK_SIZE = 0.05

# Trend Bar: close should be in top X% of bar range (closer to high)
TREND_BAR_CLOSE_THRESHOLD = 0.75   # close >= low + 0.75 * range

# Pin Bar (bullish): body small, lower wick large
PIN_BAR_MAX_BODY_PCT = 0.30        # body <= 30% of range
PIN_BAR_MIN_LOWER_WICK_PCT = 0.60  # lower wick >= 60% of range

# Minimum bar range as % of price — filters out dojis / dead candles
# where every technique would technically match on nothing
MIN_BAR_RANGE_PCT = 0.005  # 0.5% of close


# ==========================
# GLOBAL TOKEN CACHE
# ==========================
DHAN_TOKEN_CACHE = {"token": None, "generated_at": 0}


def validate_dhan_token(token):
    """Lightweight validation — call a cheap Dhan endpoint to check token validity."""
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
    """Single attempt to generate a new Dhan token. Returns (token, status)."""
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
            params={
                "dhanClientId": client_id,
                "pin": pin,
                "totp": totp
            },
            timeout=15
        )

        if r.status_code != 200:
            print(f"❌ Token HTTP error: {r.status_code} {r.text}")
            return None, "http_error"

        data = r.json()
        token = data.get("accessToken")

        if not token:
            msg = str(data).lower()
            if "2 minutes" in msg or "rate" in msg or "once every" in msg:
                print(f"⚠️ Rate-limited: {data}")
                return None, "rate_limited"
            print(f"❌ No token in response: {data}")
            return None, "no_token"

        return token, "ok"

    except Exception as e:
        print(f"❌ Token generation exception: {e}")
        return None, "exception"


def get_dhan_token(force_refresh=False):
    """
    Get a valid Dhan token.
    """
    global DHAN_TOKEN_CACHE

    if not force_refresh and DHAN_TOKEN_CACHE["token"]:
        if (time.time() - DHAN_TOKEN_CACHE["generated_at"]) < 23 * 3600:
            return DHAN_TOKEN_CACHE["token"]

    token, status = _generate_new_token()

    if status == "rate_limited":
        print("⏳ Dhan token rate-limited. Waiting 125 seconds before retry...")
        time.sleep(125)
        print("🔄 Retrying token generation after wait...")
        token, status = _generate_new_token()

    if not token:
        if DHAN_TOKEN_CACHE["token"]:
            print("⚠️ Token generation failed. Falling back to cached token.")
            return DHAN_TOKEN_CACHE["token"]
        print("❌ No token available and no cache to fall back to.")
        return None

    if not validate_dhan_token(token):
        print("❌ Newly generated token failed validation.")
        if DHAN_TOKEN_CACHE["token"]:
            print("⚠️ Falling back to previous cached token.")
            return DHAN_TOKEN_CACHE["token"]
        return None

    DHAN_TOKEN_CACHE = {"token": token, "generated_at": time.time()}
    print("✅ New Dhan token generated, validated, and cached")
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Schema migrations (idempotent)
    for col, typ in [
        ("risk", "REAL"),
        ("reward", "REAL"),
        ("rr_ratio", "REAL"),
        ("entry_type", "TEXT"),
        ("signal_bar_date", "TEXT"),
    ]:
        try:
            conn.execute(f"ALTER TABLE trade_setups ADD COLUMN {col} {typ}")
        except Exception:
            pass

    conn.execute("""
        INSERT OR REPLACE INTO trade_setups
        (setup_id, symbol, qty, entry, sl, target, score,
         status, risk, reward, rr_ratio, entry_type, signal_bar_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        payload["setup_id"],
        payload["symbol"],
        payload["qty"],
        payload["entry"],
        payload["sl"],
        payload["target"],
        payload["score"],
        "PENDING",
        payload.get("risk", 0),
        payload.get("reward", 0),
        payload.get("rr_ratio", 0),
        payload.get("entry_type", "UNKNOWN"),
        payload.get("signal_bar_date", ""),
    ))

    conn.commit()
    conn.close()


# ==========================
# TELEGRAM
# ==========================
def send_message(text, buttons=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})

    print("📡 Telegram sendMessage payload:")
    print(payload)

    try:
        res = requests.post(url, data=payload, timeout=10)
        print(f"📡 Telegram status: {res.status_code}")
        print(f"📡 Telegram response: {res.text}")
    except Exception as e:
        print(f"❌ Telegram send failed: {e}")


def send_document(path, caption=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    print(f"📡 Sending document: {path}")
    try:
        with open(path, "rb") as f:
            res = requests.post(
                url,
                files={"document": f},
                data={"chat_id": CHAT_ID, "caption": caption or ""},
                timeout=20
            )
        print(f"📡 Document status: {res.status_code}")
        print(f"📡 Document response: {res.text}")
    except Exception as e:
        print(f"❌ Document send failed: {e}")


# ==========================
# NSE STOCK FETCH
# ==========================
def get_stocks():
    headers = {"User-Agent": "Mozilla/5.0"}

    indices = [
        "NIFTY 500",
        "NIFTY MIDCAP 150",
        "NIFTY SMALLCAP 250"
    ]

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
# DATA FETCH
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
            print(f"❌ securityId not found for {stock}")
            return pd.DataFrame()

        from datetime import datetime, timedelta, timezone

        IST = timezone(timedelta(hours=5, minutes=30))
        now_ist = datetime.now(IST)
        to_date = now_ist + timedelta(days=1)
        from_date = to_date - timedelta(days=200)

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
            print(f"❌ Skipping {stock} due to token failure")
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
                    print(f"⚠️ Token unauthorized for {stock}. Forcing refresh...")
                    token = get_dhan_token(force_refresh=True)
                    if token:
                        headers["access-token"] = token
                    else:
                        return pd.DataFrame()
                print(f"⚠️ Retry {attempt+1} failed for {stock}: {response.text}")
                time.sleep(2 ** attempt)
            except Exception as e:
                print(f"⚠️ Retry {attempt+1} exception for {stock}: {e}")
                time.sleep(2 ** attempt)

        if not response or response.status_code != 200:
            print(f"❌ Dhan API failed for {stock}")
            return pd.DataFrame()

        data = response.json()
        if not data.get("close"):
            return pd.DataFrame()

        df = pd.DataFrame({
            "Open": data["open"],
            "High": data["high"],
            "Low": data["low"],
            "Close": data["close"],
            "Volume": data["volume"],
            "Timestamp": data["timestamp"]
        })

        df["Date"] = pd.to_datetime(df["Timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df.set_index("Date", inplace=True)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna().sort_index()

        # Intraday fallback for today's candle (unchanged)
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
                            "Open": intraday_data["open"],
                            "High": intraday_data["high"],
                            "Low": intraday_data["low"],
                            "Close": intraday_data["close"],
                            "Volume": intraday_data["volume"],
                            "Timestamp": intraday_data["timestamp"]
                        })
                        df_1h["Date"] = pd.to_datetime(df_1h["Timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                        df_1h.set_index("Date", inplace=True)
                        df_1h = df_1h.sort_index()
                        today_df = df_1h[df_1h.index.date == today_ist]
                        if not today_df.empty:
                            o = today_df.iloc[0]["Open"]
                            h = today_df["High"].max()
                            l = today_df["Low"].min()
                            c = today_df.iloc[-1]["Close"]
                            v = today_df["Volume"].sum()
                            df.loc[pd.Timestamp(today_ist, tz="Asia/Kolkata")] = [o, h, l, c, v]
        except Exception as e:
            print(f"❌ {stock}: Intraday fallback error → {e}")

        if len(df) > 1:
            last_ts = df.index[-1]
            prev_ts = df.index[-2]
            if last_ts.date() == prev_ts.date() and df.iloc[-1]["Volume"] < df.iloc[-2]["Volume"] * 0.2:
                df = df.iloc[:-1]

        print(f"📊 {stock} | candles={len(df)} | last={df.index[-1].date()} | close={df.iloc[-1]['Close']}")
        return df

    except Exception as e:
        print(f"❌ Dhan fetch error for {stock}: {e}")
        return pd.DataFrame()


def to_weekly(df):
    return df.resample('W').agg({
        'Open': 'first', 'High': 'max', 'Low': 'min',
        'Close': 'last', 'Volume': 'sum'
    }).dropna()


# ==========================
# FILTER (UNCHANGED FOR NOW — P1 TOUCHES THIS)
# ==========================
def filter_stock(df):
    if df is None or df.empty or len(df) < 50:
        return False

    df['EMA50'] = df['Close'].ewm(span=50).mean()
    df['EMA200'] = df['Close'].ewm(span=200).mean()

    cond1 = df.iloc[-1]['Close'] > df.iloc[-1]['EMA50'] > df.iloc[-1]['EMA200']
    recent = df.tail(20)
    base_range = (recent['High'].max() - recent['Low'].min()) / recent['Low'].min()
    cond2 = base_range < 0.15
    vol_avg = df['Volume'].rolling(20).mean()
    cond3 = df.iloc[-1]['Volume'] > 0.8 * vol_avg.iloc[-1]

    return cond1 and cond2 and cond3


# ==========================================================
# 🆕 OHM ENTRY TECHNIQUE DETECTION
# ==========================================================
# Priority order (strongest → weakest):
#   1. HH-HL (Higher High, Higher Low) — 2-bar
#   2. Inside Bar                      — 2-bar
#   3. Pin Bar (bullish)               — 1-bar
#   4. Trend Bar                       — 1-bar
#
# Returns a dict:
#   {
#     "type": "TREND_BAR" | "PIN_BAR" | "HH_HL" | "INSIDE_BAR" | None,
#     "entry": float,          # price to buy above
#     "sl": float,             # stoploss (closing basis per OHM)
#     "signal_bar_date": str,  # date of the signal bar for traceability
#     "details": dict          # debug info for logs
#   }
# If nothing matches → returns {"type": None, ...} and caller rejects.
# ==========================================================
def detect_entry_technique(df, symbol="?"):
    if df is None or len(df) < 2:
        print(f"   [{symbol}] ENTRY DETECTION: insufficient bars ({len(df) if df is not None else 0})")
        return {"type": None}

    # Work on last 2 candles. Day1 = prior, Day2 = latest (signal bar)
    day1 = df.iloc[-2]
    day2 = df.iloc[-1]

    d1_open, d1_high, d1_low, d1_close = float(day1['Open']), float(day1['High']), float(day1['Low']), float(day1['Close'])
    d2_open, d2_high, d2_low, d2_close = float(day2['Open']), float(day2['High']), float(day2['Low']), float(day2['Close'])

    d2_range = d2_high - d2_low
    d2_body = abs(d2_close - d2_open)
    d2_upper_wick = d2_high - max(d2_open, d2_close)
    d2_lower_wick = min(d2_open, d2_close) - d2_low

    # Signal bar must have a meaningful range (not a doji / dead candle)
    if d2_range < d2_close * MIN_BAR_RANGE_PCT:
        print(f"   [{symbol}] ENTRY DETECTION: last bar range too small ({d2_range:.2f} < {d2_close * MIN_BAR_RANGE_PCT:.2f}) — rejecting")
        return {"type": None}

    # --- Normalized metrics for logging ---
    close_position = (d2_close - d2_low) / d2_range if d2_range > 0 else 0
    body_pct = d2_body / d2_range if d2_range > 0 else 0
    lower_wick_pct = d2_lower_wick / d2_range if d2_range > 0 else 0
    upper_wick_pct = d2_upper_wick / d2_range if d2_range > 0 else 0

    signal_date = str(df.index[-1].date())
    prev_date = str(df.index[-2].date())

    print(f"   [{symbol}] ENTRY DETECTION → analysing bars:")
    print(f"      Day1 ({prev_date}): O={d1_open:.2f} H={d1_high:.2f} L={d1_low:.2f} C={d1_close:.2f}")
    print(f"      Day2 ({signal_date}): O={d2_open:.2f} H={d2_high:.2f} L={d2_low:.2f} C={d2_close:.2f}")
    print(f"      Day2 metrics: range={d2_range:.2f}, body%={body_pct:.2%}, "
          f"upper_wick%={upper_wick_pct:.2%}, lower_wick%={lower_wick_pct:.2%}, "
          f"close_pos_in_range={close_position:.2%}")

    # ===== 1. HH-HL (Higher High, Higher Low) — strongest =====
    # Per PDF: Day2.high > Day1.high AND Day2.low > Day1.low
    # Entry = MAX(Day1.high, Day2.high) + tick
    # SL    = MIN(Day1.low, Day2.low)
    if d2_high > d1_high and d2_low > d1_low:
        entry = max(d1_high, d2_high) + TICK_SIZE
        sl = min(d1_low, d2_low)
        print(f"   [{symbol}] ✅ MATCHED: HH_HL | Entry={entry:.2f} SL={sl:.2f}")
        return {
            "type": "HH_HL",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "signal_bar_date": signal_date,
            "details": {
                "d1_high": d1_high, "d2_high": d2_high,
                "d1_low": d1_low, "d2_low": d2_low,
            }
        }

    # ===== 2. Inside Bar =====
    # Per PDF: Day2.high <= Day1.high AND Day2.low >= Day1.low (fully contained)
    # Entry = Day2 (inside bar) high + tick
    # SL    = Day2 (inside bar) low  [OHM allows outside-bar low alternative — NOT used here]
    if d2_high <= d1_high and d2_low >= d1_low:
        entry = d2_high + TICK_SIZE
        sl = d2_low
        print(f"   [{symbol}] ✅ MATCHED: INSIDE_BAR | Entry={entry:.2f} SL={sl:.2f}")
        print(f"      (Day2 fully inside Day1: {d1_low:.2f}-{d1_high:.2f} contains {d2_low:.2f}-{d2_high:.2f})")
        return {
            "type": "INSIDE_BAR",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "signal_bar_date": signal_date,
            "details": {
                "outer_high": d1_high, "outer_low": d1_low,
                "inner_high": d2_high, "inner_low": d2_low,
            }
        }

    # ===== 3. Pin Bar (bullish) =====
    # Per PDF: small body + long lower wick = buyers overwhelmed sellers and won
    # Body <= PIN_BAR_MAX_BODY_PCT of range
    # Lower wick >= PIN_BAR_MIN_LOWER_WICK_PCT of range
    # Entry = pin bar high + tick, SL = pin bar low
    if (body_pct <= PIN_BAR_MAX_BODY_PCT and
            lower_wick_pct >= PIN_BAR_MIN_LOWER_WICK_PCT):
        entry = d2_high + TICK_SIZE
        sl = d2_low
        print(f"   [{symbol}] ✅ MATCHED: PIN_BAR | Entry={entry:.2f} SL={sl:.2f}")
        print(f"      (body%={body_pct:.2%} ≤ {PIN_BAR_MAX_BODY_PCT:.0%}, "
              f"lower_wick%={lower_wick_pct:.2%} ≥ {PIN_BAR_MIN_LOWER_WICK_PCT:.0%})")
        return {
            "type": "PIN_BAR",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "signal_bar_date": signal_date,
            "details": {
                "body_pct": body_pct,
                "lower_wick_pct": lower_wick_pct,
                "upper_wick_pct": upper_wick_pct,
            }
        }

    # ===== 4. Trend Bar =====
    # Per PDF: close near the high (buyers overwhelming sellers)
    # close_position >= TREND_BAR_CLOSE_THRESHOLD
    # Entry = trend bar high + tick, SL = trend bar low
    if close_position >= TREND_BAR_CLOSE_THRESHOLD:
        entry = d2_high + TICK_SIZE
        sl = d2_low
        print(f"   [{symbol}] ✅ MATCHED: TREND_BAR | Entry={entry:.2f} SL={sl:.2f}")
        print(f"      (close in top {(1-TREND_BAR_CLOSE_THRESHOLD)*100:.0f}% of range: close_pos={close_position:.2%})")
        return {
            "type": "TREND_BAR",
            "entry": round(entry, 2),
            "sl": round(sl, 2),
            "signal_bar_date": signal_date,
            "details": {"close_position": close_position}
        }

    # No technique matched
    print(f"   [{symbol}] ❌ NO ENTRY TECHNIQUE MATCHED")
    print(f"      HH-HL?    no (need d2_high>d1_high AND d2_low>d1_low)")
    print(f"      Inside?   no (need d2 fully inside d1)")
    print(f"      Pin Bar?  no (body%={body_pct:.2%}, lower_wick%={lower_wick_pct:.2%})")
    print(f"      Trend?    no (close_pos={close_position:.2%} < {TREND_BAR_CLOSE_THRESHOLD:.0%})")
    return {"type": None}


# ==========================================================
# 🔄 POSITION SIZING (unchanged) + ENTRY-TECHNIQUE-AWARE
# ==========================================================
def create_trade(df, symbol="?"):
    """
    Returns trade dict if a valid OHM entry technique is detected, else None.
    Position sizing rules are unchanged from the original:
      - 0.25% capital at risk per trade
      - 10% capital cap per trade
    """
    technique = detect_entry_technique(df, symbol=symbol)

    if technique["type"] is None:
        print(f"   [{symbol}] create_trade: rejected — no entry technique matched")
        return None

    entry = technique["entry"]
    sl = technique["sl"]
    risk_per_share = entry - sl

    if risk_per_share <= 0:
        print(f"   [{symbol}] create_trade: rejected — entry ({entry}) <= sl ({sl})")
        return None

    # 0.25% risk
    risk_amt = CAPITAL * 0.0025
    qty_risk = int(risk_amt / risk_per_share)

    # 10% cap
    max_capital_per_trade = CAPITAL * 0.10
    qty_cap = int(max_capital_per_trade / entry)

    qty = min(qty_risk, qty_cap)

    if qty <= 0:
        print(f"   [{symbol}] create_trade: rejected — qty=0 (qty_risk={qty_risk}, qty_cap={qty_cap})")
        return None

    print(f"   [{symbol}] create_trade: qty={qty} (risk-sized={qty_risk}, cap-sized={qty_cap})")

    return {
        "entry": entry,
        "sl": sl,
        "qty": qty,
        "entry_type": technique["type"],
        "signal_bar_date": technique["signal_bar_date"],
        "risk_per_share": round(risk_per_share, 2),
    }


# ==========================
# CHART ENGINE (unchanged)
# ==========================
def plot_chart(stock, save_path):
    df = fetch(stock)
    if df is None or df.empty:
        print(f"❌ Skipping chart for {stock} due to no data")
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

    apds = [
        mpf.make_addplot(df['EMA10'], color='black'),
        mpf.make_addplot(df['EMA21'], color='red'),
        mpf.make_addplot(df['EMA50'], color='blue'),
        mpf.make_addplot(df['EMA200'], color='purple'),
    ]
    apds_w = [
        mpf.make_addplot(df_weekly['EMA10'], color='black'),
        mpf.make_addplot(df_weekly['EMA21'], color='red'),
        mpf.make_addplot(df_weekly['EMA50'], color='blue'),
        mpf.make_addplot(df_weekly['EMA200'], color='purple'),
    ]
    legend = [
        Patch(facecolor='black', label='EMA10'),
        Patch(facecolor='red', label='EMA21'),
        Patch(facecolor='blue', label='EMA50'),
        Patch(facecolor='purple', label='EMA200')
    ]

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
    a1 = fig.add_subplot(2, 1, 1)
    a1.imshow(plt.imread("d.png"))
    a1.axis('off')
    a2 = fig.add_subplot(2, 1, 2)
    a2.imshow(plt.imread("w.png"))
    a2.axis('off')
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
# GPT DECISION (updated prompt to cross-check entry types)
# ==========================
def gpt_decision(pdf_path, detected_entries):
    """
    detected_entries: dict of {symbol: entry_type_str} so GPT can cross-check
    what the code already detected. GPT is now a confirmatory layer,
    not the sole decision-maker.
    """
    if not client:
        print("⚠️ OPENAI_API_KEY missing → Skipping GPT")
        return json.dumps({"picks": []})

    file = client.files.create(file=open(pdf_path, "rb"), purpose="user_data")

    detected_str = "\n".join([f"- {sym}: code detected {et}"
                              for sym, et in detected_entries.items()])

    PROMPT = f"""
You are an institutional breakout trader following OHM rules.

The code has already pre-detected OHM entry techniques for each stock:
{detected_str}

Analyze charts and return ONLY valid JSON. No explanation. No text outside JSON.

RULES:
- Strong trend (EMA50 > EMA200)
- Tight base (<15%)
- Near breakout (price close to recent high)
- Volume expansion
- Strong entry candle

Reject weak setups strictly.

SCORING (0-10):
9-10 perfect
8 strong
7 acceptable
<7 reject

SELECTION RULES (STRICT):
- Return MAX 3 stocks ONLY
- Only include stocks with score >= 7
- NEVER return a stock whose code-detected entry type is not one of:
  TREND_BAR, PIN_BAR, HH_HL, INSIDE_BAR

RANKING LOGIC:
1. Tightest base (lowest range %)
2. Closest to breakout
3. Strongest volume expansion

OUTPUT FORMAT (STRICT JSON):
{{
  "picks": [
    {{
      "stock": "ABC.NS",
      "score": 8.5,
      "quality": "STRONG",
      "reason": "tight base + volume breakout",
      "entry_type": "TREND_BAR"
    }}
  ]
}}

If no valid setups:
{{"picks": []}}
"""

    res = client.responses.create(
        model="gpt-4.1-mini",
        temperature=0,
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": PROMPT},
                {"type": "input_file", "file_id": file.id}
            ]
        }]
    )
    return res.output_text


def parse_gpt_output(output):
    try:
        data = json.loads(output)
        return data.get("picks", [])
    except Exception as e:
        print(f"⚠️ GPT output parse failed: {e}")
        print(f"   Raw: {output}")
        return []


# ==========================
# MAIN
# ==========================
def run():
    print("🔐 Performing Dhan token pre-flight check...")
    token = get_dhan_token()
    if not token:
        print("❌ Cannot proceed — no valid Dhan token available.")
        return
    print("✅ Dhan token ready. Starting stock processing...")

    stocks = get_stocks()
    print(f"📋 Total stocks from NSE indices: {len(stocks)}")

    scored = []

    for s in stocks:
        try:
            df = fetch(s)
            if not filter_stock(df):
                continue

            df['EMA50'] = df['Close'].ewm(span=50).mean()
            recent = df.tail(20)
            base_high = recent['High'].max()
            base_low = recent['Low'].min()
            current = df['Close'].iloc[-1]

            if base_low == 0:
                continue

            tightness = (base_high - base_low) / base_low

            score = (
                    (current / base_high) * 0.5 +
                    (current / df['EMA50'].iloc[-1]) * 0.3 +
                    (1 - tightness) * 0.2
            )
            scored.append((s, score))
        except Exception as e:
            print(f"⚠️ Scoring error for {s}: {e}")
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    shortlist = [s for s, _ in scored[:10]]
    print(f"📊 Shortlist 10: {shortlist}")

    folder = f"run_{datetime.now().strftime('%H%M%S')}"
    os.makedirs(folder, exist_ok=True)

    images = []
    trade_map = {}
    detected_entries = {}

    print("\n" + "=" * 60)
    print("🔍 ENTRY TECHNIQUE DETECTION PHASE")
    print("=" * 60)

    for s in shortlist:
        img = f"{folder}/{s}.png"
        plot_chart(s, img)
        if not os.path.exists(img):
            continue
        images.append(img)

        df = fetch(s)
        if df is None or df.empty:
            continue

        trade = create_trade(df, symbol=s)
        if trade is None:
            print(f"   [{s}] ⛔ SKIPPED — no valid OHM entry technique\n")
            detected_entries[s] = "NONE"
            continue

        trade_map[s] = trade
        detected_entries[s] = trade["entry_type"]
        print(f"   [{s}] ✅ Trade candidate registered: {trade}\n")

    print("=" * 60)
    print(f"🎯 Stocks with valid OHM entry: {list(trade_map.keys())}")
    print(f"🎯 Entry-type summary: {detected_entries}")
    print("=" * 60 + "\n")

    if not trade_map:
        print("⚠️ No stocks passed entry-technique detection. No charts sent, no alerts.")
        return

    pdf_path = f"{folder}/charts.pdf"
    build_pdf(images, pdf_path)
    send_document(pdf_path, "📄 Charts sent to GPT")

    print("🧠 GPT RAW OUTPUT:")
    output = gpt_decision(pdf_path, detected_entries)
    print(output)

    picks = parse_gpt_output(output)
    print(f"🧠 Parsed Picks: {picks}")

    if not picks:
        print("⚠️ No picks returned from GPT.")

    for p in picks:
        s = p["stock"]
        print(f"\n🔍 Processing pick: {p}")

        if s not in trade_map:
            print(f"   [{s}] ❌ GPT picked but no trade in trade_map — SKIP")
            continue

        trade = trade_map[s]

        # Cross-check: did GPT's entry_type match what code detected?
        gpt_type = p.get("entry_type", "").upper().replace(" ", "_").replace("-", "_")
        code_type = trade["entry_type"]
        type_match = gpt_type == code_type
        print(f"   [{s}] Entry type check: code={code_type}, gpt={gpt_type}, match={type_match}")

        entry = trade["entry"]
        sl = trade["sl"]
        qty = trade["qty"]
        entry_type = trade["entry_type"]
        signal_date = trade["signal_bar_date"]

        # Risk/reward (2R target is a P2 TODO — keeping for now)
        risk = round(entry - sl, 2)
        reward = round(risk * 2, 2)
        target = round(entry + reward, 2)
        rr_ratio = round(reward / risk, 2) if risk > 0 else 0
        score = p.get("score", 0)

        setup_id = f"{datetime.now().strftime('%H%M%S%f')}{s[:4]}"

        payload = {
            "setup_id": setup_id,
            "symbol": s,
            "qty": qty,
            "entry": entry,
            "sl": sl,
            "target": target,
            "score": score,
            "risk": risk,
            "reward": reward,
            "rr_ratio": rr_ratio,
            "entry_type": entry_type,
            "signal_bar_date": signal_date,
        }

        save_trade(payload)
        print("🧾 SAVED TRADE PAYLOAD:")
        print(payload)

        # Pretty labels for Telegram
        type_label = {
            "TREND_BAR": "Trend Bar",
            "PIN_BAR": "Pin Bar",
            "HH_HL": "Higher High – Higher Low",
            "INSIDE_BAR": "Inside Bar",
        }.get(entry_type, entry_type)

        msg = f"""📈 *FINAL TRADE*

*{s}*
Score: {score} ({p.get('quality', '-')})

🎯 *OHM Entry Technique:* `{type_label}`
Signal Bar Date: `{signal_date}`

Entry: `{entry}`  _(buy above)_
SL: `{sl}`       _(exit on close below)_
Target: `{target}` _(2R — placeholder)_
Qty: `{qty}`

Risk/Share: `{risk}`
R:R: `1:{rr_ratio}`

Reason: {p.get('reason', '-')}
GPT said type: `{p.get('entry_type', '-')}`  {'✅' if type_match else '⚠️ mismatch'}
"""

        short_cb = f"BUY|{setup_id}|{s}|{qty}|{entry}|{sl}|{target}|{score}"
        buttons = [[{"text": "✅ Confirm Buy", "callback_data": short_cb}]]

        print(f"📤 Sending Telegram alert for {s}")
        send_message(msg, buttons)


if __name__ == "__main__":
    run()