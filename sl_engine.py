# ==============================================================
# 🚀 SL ENGINE — V19 (forever-order exits)
#
# Same close-based trail + hybrid-target logic as V18 (6b), but the
# EXIT/PARTIAL execution is reworked to use FOREVER orders instead of
# MARKET sells — because the SL engine runs at 18:00 IST (after close),
# when MARKET orders are rejected by Dhan (DH-906 "Market is Closed").
#
# A forever SELL with a trigger just BELOW the latest close rests at the
# broker and FILLS at next session open — which matches the design's
# "decide on close, act next session" philosophy exactly.
#
# WHAT CHANGED vs V18
#   • place_market_sell()  → place_exit_forever()  (forever SELL, trigger
#       just below close, fills at open). Used by BOTH structural exit and
#       2R partial.
#   • Exit no longer cancels the −8% then places a MARKET sell. Instead:
#       - structural exit: MODIFY the existing −8% forever-order up to the
#         exit trigger (one order becomes the exit); on modify-failure,
#         cancel + place a fresh exit forever-order.
#       - 2R partial: the −8% can't double as a half-sell, so place a
#         SEPARATE half-qty exit forever-order, then reconcile the −8% down
#         to the kept qty (cancel-then-replace at new qty, as before).
#   • confirm_fill() now tolerant: a resting forever exit-order will NOT
#       fill until open, so after-hours we mark EXIT_PENDING (not failure)
#       and reconcile next run. No re-protect needed — the exit order IS the
#       protection (it's a resting SELL).
#   • INVALID-ROW GUARD: rows where Structural_SL >= Entry (old/garbage)
#       are skipped for trail+partial (their R basis is meaningless).
#   • Status normalizer: legacy "TRAILING" is treated as OPEN.
#
# Everything else (ATR, three-phase trail, R-basis from Target, clamp,
# Telegram, sheet writes via google_sheets_db) is unchanged from V18.
# ==============================================================

import os
import uuid
import time
import logging
import requests
import pyotp
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

import tick_utils
import google_sheets_db as db

# ==========================
# LOAD ENV
# ==========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATHS = [
    os.path.join(BASE_DIR, ".env"),
    "/root/trade-execution-webhook/.env",
    os.path.expanduser("~/.env"),
]
env_loaded = False
for path in ENV_PATHS:
    if os.path.exists(path):
        load_dotenv(path)
        print(f"✅ Loaded .env from: {path}")
        env_loaded = True
        break
if not env_loaded:
    print("⚠️ WARNING: .env NOT FOUND - using environment variables")

# ==========================
# CONFIG
# ==========================
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID")
DHAN_PIN = os.getenv("DHAN_PIN")
DHAN_TOTP_SECRET = os.getenv("DHAN_TOTP_SECRET")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH")

TELEGRAM_TOKEN = os.getenv("SL_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("SL_TELEGRAM_CHAT_ID")
DRY_RUN = os.getenv("SL_ENGINE_DRY_RUN", "false").lower() in ("true", "1", "yes")

SAFETY_SL_PCT = 0.92
SAFETY_LIMIT_OFFSET = 0.995

# correlationId prefixes so cleanup can tell order roles apart in the Dhan
# order book. Kept short (Dhan correlationId limit). EXIT_ = a 2R/structural
# exit-forever (NEVER cancel as a duplicate). SAFE_ = the −8% backstop.
CID_EXIT = "EXIT_"
CID_SAFE = "SAFE_"


def _cid(prefix):
    """Build a tagged correlationId, e.g. 'EXIT_a1b2c3d4e5f6' (≤20 chars)."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"

# Exit forever-order: trigger sits this fraction below the latest close so
# it fills at next open regardless of small overnight moves. Limit a touch
# below the trigger. Both rounded DOWN to tick.
EXIT_TRIGGER_OFFSET = 0.995   # trigger = close * 0.995
EXIT_LIMIT_OFFSET = 0.990     # limit  = trigger * 0.990 (room to fill at open)

EXIT_POLL_ATTEMPTS = 6
EXIT_POLL_SLEEP = 2.0
FILLED_STATUSES = {"TRADED", "EXECUTED", "COMPLETE", "FILLED"}
DEAD_STATUSES = {"REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "FAILED"}

ONE_R_PHASE_THRESHOLD = 1.0
ATR_PERIOD = 14
ATR_TRAIL_MULT = 2.5
ATR_FETCH_SESSIONS = 25
FALLBACK_TRAIL_PCT = 0.05
MIN_CANDLES_FOR_ATR = ATR_PERIOD + 1

session = requests.Session()

logging.basicConfig(level=logging.DEBUG,
                    format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except Exception:
    yf = None
    _YF_AVAILABLE = False
    logger.warning("⚠️ yfinance not installed — fallback disabled")


# ==========================
# TELEGRAM
# ==========================
def escape_markdown_v2(text):
    if text is None:
        return ""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    text = str(text)
    for ch in escape_chars:
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram_alert(title, content_dict):
    if DRY_RUN:
        print(f"🔕 [DRY_RUN] Would send Telegram: {title}")
        for k, v in content_dict.items():
            print(f"    {k}: {v}")
        return
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram not configured")
        return
    try:
        lines = [f"*{escape_markdown_v2(title)}*", ""]
        for key, value in content_dict.items():
            lines.append(f"  • *{escape_markdown_v2(str(key))}:* `{escape_markdown_v2(str(value))}`")
        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "MarkdownV2"}
        r = requests.post(url, data=payload, timeout=10)
        if r.status_code == 200:
            logger.info(f"✅ Telegram alert sent: {title}")
        else:
            logger.warning(f"⚠️ Telegram failed ({r.status_code})")
    except Exception as e:
        logger.error(f"❌ Telegram error: {e}")


# ==========================
# HELPERS
# ==========================
def normalize_symbol(symbol):
    if symbol and isinstance(symbol, str):
        return symbol.replace(".NS", "").strip()
    return symbol


def validate_env():
    missing = []
    for name, val in [
        ("DHAN_CLIENT_ID", DHAN_CLIENT_ID), ("DHAN_PIN", DHAN_PIN),
        ("DHAN_TOTP_SECRET", DHAN_TOTP_SECRET), ("SPREADSHEET_ID", SPREADSHEET_ID),
        ("SERVICE_ACCOUNT_KEY_PATH", SERVICE_ACCOUNT_KEY_PATH),
    ]:
        if not val:
            missing.append(name)
    if missing:
        raise ValueError(f"❌ Missing ENV: {', '.join(missing)}")
    logger.info("✅ ENV OK")


def _f(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _normalize_status(raw):
    """Legacy 'TRAILING' (pre-enhancement) behaves like OPEN."""
    s = str(raw or "").upper()
    if s == "TRAILING":
        return db.STATUS_OPEN
    return s


def has_valid_risk(trade):
    """
    Valid if the IMMUTABLE R basis (entry + Target) is sound:
        entry > 0, target > 0, target > entry.

    Deliberately does NOT use live Structural_SL: once a trade closes ≥ 1R,
    breakeven/trail legitimately push Structural_SL to/above entry, so an
    `SL < entry` test would wrongly bench healthy trailing positions on the
    next run. Risk is anchored to Target (see compute_r_basis), which never
    moves. Only genuinely bad Target/entry data benches a row now.
    """
    entry = _f(trade.get("Entry_Price"))
    target = _f(trade.get("Target_Price"))
    return entry > 0 and target > 0 and target > entry


# ==========================
# TOKEN
# ==========================
_SHARED_TOKEN_CACHE = "/root/trade-execution-webhook/.dhan_token_cache.json"

def _read_shared_token():
    """Reuse token cached by the web API / ingestion jobs (avoids Dhan rate limits)."""
    try:
        import json as _json
        with open(_SHARED_TOKEN_CACHE) as _f:
            c = _json.load(_f)
        if time.time() - c.get("generated_at", 0) < 23 * 3600 and c.get("token"):
            return c["token"]
    except Exception:
        pass
    return None

def _write_shared_token(token):
    try:
        import json as _json
        with open(_SHARED_TOKEN_CACHE, "w") as _f:
            _json.dump({"token": token, "generated_at": time.time()}, _f)
    except Exception:
        pass

def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY
    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    # Reuse shared file cache first (written by web API / ingestion jobs),
    # but only if it still authenticates (Dhan invalidates superseded tokens).
    shared = _read_shared_token()
    if shared:
        try:
            chk = session.get("https://api.dhan.co/v2/fundlimit",
                              headers={"access-token": shared, "client-id": DHAN_CLIENT_ID},
                              timeout=8)
            if chk.status_code == 200:
                CURRENT_TOKEN = shared
                TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=1)
                return CURRENT_TOKEN
        except Exception:
            pass

    # Up to 3 attempts. "Invalid TOTP" means we hit a stale/edge 30s window;
    # wait into the NEXT fresh window before retrying (a fresh .now() inside
    # the same window returns the same code, so a plain retry wouldn't help).
    for attempt in range(1, 4):
        try:
            totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
            logger.info(f"🔑 Generating new token (attempt {attempt}/3)...")
            r = session.post(
                "https://auth.dhan.co/app/generateAccessToken",
                params={"dhanClientId": DHAN_CLIENT_ID, "pin": DHAN_PIN, "totp": totp},
                timeout=10,
            )
            data = r.json()
            if "accessToken" in data:
                CURRENT_TOKEN = data["accessToken"]
                TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
                _write_shared_token(CURRENT_TOKEN)
                logger.info("✅ Token generated")
                return CURRENT_TOKEN

            msg = str(data.get("message", "")).lower()
            logger.error(f"❌ Token failed: {data}")
            if "totp" in msg and attempt < 3:
                # Sleep past the current 30s TOTP boundary into a fresh window.
                secs_into = datetime.now(timezone.utc).timestamp() % 30
                wait = (30 - secs_into) + 1  # clear the boundary
                logger.info(f"⏳ Invalid TOTP — waiting {wait:.1f}s for fresh window...")
                time.sleep(wait)
                continue
            return None
        except Exception as e:
            logger.error(f"❌ Token error (attempt {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(5)
                continue
            return None
    return None


# ==========================
# POSITIONS / HOLDINGS / FOREVER ORDERS
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return []
    try:
        r = session.get("https://api.dhan.co/v2/positions",
                        headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
                        timeout=10)
        data = r.json()
        result = []
        for p in data:
            if p.get("netQty", 0) > 0:
                result.append({
                    "securityId": str(p["securityId"]),
                    "symbol": p["tradingSymbol"],
                    "qty": p["netQty"],
                    "avgPrice": p.get("buyAvg") or p.get("costPrice"),
                })
        logger.info(f"📊 Found {len(result)} positions")
        return result
    except Exception as e:
        logger.error(f"❌ Get positions failed: {e}")
        return []


def get_holdings():
    token = get_token()
    if not token:
        return []
    try:
        r = session.get("https://api.dhan.co/v2/holdings",
                        headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
                        timeout=10)
        data = r.json()
        result = []
        for h in data:
            if h.get("totalQty", 0) > 0:
                result.append({
                    "securityId": str(h["securityId"]),
                    "symbol": h["tradingSymbol"],
                    "qty": h["totalQty"],
                    "avgPrice": h.get("avgCostPrice"),
                })
        logger.info(f"📊 Found {len(result)} holdings")
        return result
    except Exception as e:
        logger.error(f"❌ Get holdings failed: {e}")
        return []


def get_forever_orders():
    token = get_token()
    if not token:
        return []
    try:
        r = session.get("https://api.dhan.co/v2/forever/orders",
                        headers={"access-token": token}, timeout=10)
        data = r.json()
        logger.info(f"📊 Found {len(data) if isinstance(data, list) else 0} forever orders")
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"❌ Get forever orders failed: {e}")
        return []


# ==========================
# DAILY CLOSE  (Dhan → yfinance → None)
# ==========================
def _dhan_daily_close(security_id, symbol, debug=False):
    try:
        token = get_token()
        if not token:
            return None
        now_ist = datetime.now(IST)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        trade_date = now_ist - timedelta(days=1) if now_ist < market_close else now_ist
        from_date = trade_date.strftime("%Y-%m-%d")
        to_date = (trade_date + timedelta(days=1)).strftime("%Y-%m-%d")
        payload = {
            "securityId": int(security_id), "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY", "oi": False,
            "fromDate": from_date, "toDate": to_date,
        }
        r = requests.post("https://api.dhan.co/v2/charts/historical",
                          json=payload,
                          headers={"Content-Type": "application/json", "access-token": token},
                          timeout=15)
        if r.status_code == 200:
            closes = r.json().get("close") or []
            if closes:
                close_price = float(closes[-1])
                logger.info(f"✅ {symbol} Dhan daily close: {close_price}")
                return close_price
            logger.warning(f"⚠️ {symbol} Dhan close empty")
        else:
            logger.warning(f"⚠️ {symbol} Dhan close API error: {r.status_code}")
        return None
    except Exception as e:
        logger.error(f"❌ {symbol} Dhan close error: {e}")
        return None


def _yf_daily_close(symbol):
    if not _YF_AVAILABLE:
        return None
    try:
        ticker = f"{normalize_symbol(symbol)}.NS"
        logger.info(f"🔁 {symbol} trying yfinance fallback ({ticker})...")
        hist = yf.Ticker(ticker).history(period="5d")
        if hist is None or hist.empty or "Close" not in hist:
            logger.warning(f"⚠️ {symbol} yfinance returned no data")
            return None
        close_price = float(hist["Close"].dropna().iloc[-1])
        logger.info(f"✅ {symbol} yfinance daily close: {close_price}")
        return close_price
    except Exception as e:
        logger.error(f"❌ {symbol} yfinance close error: {e}")
        return None


def get_daily_close(security_id, symbol):
    close = _dhan_daily_close(security_id, symbol)
    if close is not None:
        return close, "DHAN"
    close = _yf_daily_close(symbol)
    if close is not None:
        return close, "YFINANCE"
    logger.error(f"❌ {symbol} no daily close — skipping decision this run")
    return None, "NONE"


# ==========================
# CANDLES FOR ATR
# ==========================
def _dhan_daily_candles(security_id, symbol, sessions=ATR_FETCH_SESSIONS):
    try:
        token = get_token()
        if not token:
            return None, None, None
        now_ist = datetime.now(IST)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        end_date = now_ist - timedelta(days=1) if now_ist < market_close else now_ist
        start_date = end_date - timedelta(days=sessions * 2 + 10)
        payload = {
            "securityId": int(security_id), "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY", "oi": False,
            "fromDate": start_date.strftime("%Y-%m-%d"),
            "toDate": (end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        r = requests.post("https://api.dhan.co/v2/charts/historical",
                          json=payload,
                          headers={"Content-Type": "application/json", "access-token": token},
                          timeout=15)
        if r.status_code != 200:
            logger.warning(f"⚠️ {symbol} Dhan candles API error: {r.status_code}")
            return None, None, None
        data = r.json()
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        if not (highs and lows and closes):
            logger.warning(f"⚠️ {symbol} Dhan candles empty")
            return None, None, None
        n = min(len(highs), len(lows), len(closes))
        highs = [float(x) for x in highs[-n:]]
        lows = [float(x) for x in lows[-n:]]
        closes = [float(x) for x in closes[-n:]]
        logger.info(f"✅ {symbol} Dhan candles: {n} sessions")
        return highs, lows, closes
    except Exception as e:
        logger.error(f"❌ {symbol} Dhan candles error: {e}")
        return None, None, None


def _yf_daily_candles(symbol):
    if not _YF_AVAILABLE:
        return None, None, None
    try:
        ticker = f"{normalize_symbol(symbol)}.NS"
        logger.info(f"🔁 {symbol} trying yfinance candles fallback ({ticker})...")
        hist = yf.Ticker(ticker).history(period="1mo")
        if hist is None or hist.empty:
            logger.warning(f"⚠️ {symbol} yfinance returned no candle data")
            return None, None, None
        for col in ("High", "Low", "Close"):
            if col not in hist:
                logger.warning(f"⚠️ {symbol} yfinance missing {col} column")
                return None, None, None
        hist = hist.dropna(subset=["High", "Low", "Close"])
        highs = [float(x) for x in hist["High"].tolist()]
        lows = [float(x) for x in hist["Low"].tolist()]
        closes = [float(x) for x in hist["Close"].tolist()]
        if not closes:
            return None, None, None
        logger.info(f"✅ {symbol} yfinance candles: {len(closes)} sessions")
        return highs, lows, closes
    except Exception as e:
        logger.error(f"❌ {symbol} yfinance candles error: {e}")
        return None, None, None


def compute_atr(highs, lows, closes, period=ATR_PERIOD):
    try:
        n = min(len(highs), len(lows), len(closes))
        if n < period + 1:
            return None
        highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]
        trs = []
        for i in range(1, n):
            prev_close = closes[i - 1]
            tr = max(highs[i] - lows[i],
                     abs(highs[i] - prev_close),
                     abs(prev_close - lows[i]))
            trs.append(tr)
        window = trs[-period:]
        if len(window) < period:
            return None
        atr = sum(window) / len(window)
        return atr if atr > 0 else None
    except Exception as e:
        logger.error(f"❌ ATR compute error: {e}")
        return None


def get_atr(security_id, symbol):
    highs, lows, closes = _dhan_daily_candles(security_id, symbol)
    if closes and len(closes) >= MIN_CANDLES_FOR_ATR:
        atr = compute_atr(highs, lows, closes)
        if atr is not None:
            logger.info(f"   ATR(DHAN,{ATR_PERIOD}d) {symbol}: {round(atr, 4)}")
            return atr, "DHAN"
    highs, lows, closes = _yf_daily_candles(symbol)
    if closes and len(closes) >= MIN_CANDLES_FOR_ATR:
        atr = compute_atr(highs, lows, closes)
        if atr is not None:
            logger.info(f"   ATR(YFINANCE,{ATR_PERIOD}d) {symbol}: {round(atr, 4)}")
            return atr, "YFINANCE"
    logger.warning(f"⚠️ {symbol} ATR unavailable — trail uses "
                   f"{int(FALLBACK_TRAIL_PCT*100)}% fallback gap")
    return None, "NONE"


# ==========================
# TICK HELPERS
# ==========================
def _tick_for(symbol):
    try:
        return tick_utils.get_tick_size(symbol)
    except Exception as e:
        logger.warning(f"⚠️ tick lookup failed for {symbol}: {e} — using 0.05")
        return 0.05


def _round_down(value, symbol):
    tick = _tick_for(symbol)
    return tick_utils.round_to_tick(value, tick, mode="down")


# ==========================
# SAFETY_SL (−8%) — the dumb backstop forever-order
# ==========================
def place_safety_sl(sec_id, qty, entry, symbol):
    if not entry:
        logger.error(f"❌ {symbol} cannot place safety SL — no entry price")
        return False, None, None
    raw_trigger = entry * SAFETY_SL_PCT
    trigger = _round_down(raw_trigger, symbol)
    price = _round_down(trigger * SAFETY_LIMIT_OFFSET, symbol)
    logger.info(f"📤 Placing −8% safety SL: {symbol} | Trigger: {trigger} | Limit: {price}")
    send_telegram_alert("🛡️ PLACING −8% SAFETY SL",
                        {"Symbol": symbol, "Qty": qty, "Trigger": trigger, "Limit": price})
    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would place safety SL for {symbol}")
        return True, "DRYRUN_SAFETY_ID", trigger
    token = get_token()
    if not token:
        return False, None, None
    payload = {
        "dhanClientId": DHAN_CLIENT_ID, "correlationId": _cid(CID_SAFE),
        "orderFlag": "SINGLE", "transactionType": "SELL", "exchangeSegment": "NSE_EQ",
        "productType": "CNC", "orderType": "LIMIT", "validity": "DAY",
        "securityId": str(sec_id), "quantity": int(qty),
        "price": price, "triggerPrice": trigger,
    }
    try:
        r = session.post("https://api.dhan.co/v2/forever/orders", json=payload,
                         headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
                         timeout=30)
        if r.status_code in (200, 201):
            order_id = None
            try:
                order_id = r.json().get("orderId")
            except Exception:
                pass
            logger.info(f"✅ Safety SL placed: {symbol} (order {order_id})")
            send_telegram_alert("✅ −8% SAFETY SL PLACED",
                                {"Symbol": symbol, "Qty": qty, "Trigger": trigger, "OrderID": order_id})
            return True, order_id, trigger
        logger.error(f"❌ Place safety SL failed: HTTP {r.status_code} | {r.text}")
        send_telegram_alert("❌ −8% SAFETY SL FAILED",
                            {"Symbol": symbol, "Status": f"HTTP {r.status_code}"})
        return False, None, None
    except Exception as e:
        logger.error(f"❌ Place safety SL exception: {e}")
        return False, None, None


# ==========================
# CANCEL forever-order (DELETE)
# ==========================
def cancel_forever_order(order_id, symbol):
    logger.info(f"🗑️ Cancelling forever-order for {symbol} (id {order_id})")
    send_telegram_alert("🗑️ CANCELLING FOREVER ORDER", {"Symbol": symbol, "OrderID": order_id})
    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would DELETE forever-order {order_id}")
        return True
    token = get_token()
    if not token:
        logger.error("❌ No token for cancel")
        return False
    try:
        r = session.delete(f"https://api.dhan.co/v2/forever/orders/{order_id}",
                           headers={"Accept": "application/json", "access-token": token},
                           timeout=15)
        if r.status_code in (200, 202):
            logger.info(f"✅ Forever-order cancelled: {symbol}")
            return True
        logger.error(f"❌ Cancel failed: HTTP {r.status_code} | {r.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Cancel exception: {e}")
        return False


# ==========================
# PLACE EXIT FOREVER  (replaces place_market_sell)
# ==========================
def place_exit_forever(sec_id, qty, close_price, symbol):
    """
    Place a forever SELL whose trigger sits just BELOW the latest close, so
    it rests now (after hours) and FILLS at next session open.

    Returns (ok: bool, order_id: str|None, trigger: float|None).
    """
    raw_trigger = close_price * EXIT_TRIGGER_OFFSET
    trigger = _round_down(raw_trigger, symbol)
    price = _round_down(trigger * EXIT_LIMIT_OFFSET, symbol)

    logger.info(f"📤 Placing EXIT forever SELL: {symbol} | Qty: {qty} | "
                f"Trigger: {trigger} | Limit: {price}")
    send_telegram_alert("📤 PLACING EXIT FOREVER (fills at open)",
                        {"Symbol": symbol, "Qty": qty, "Trigger": trigger, "Limit": price})
    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would place EXIT forever for {symbol}")
        return True, "DRYRUN_EXIT_ID", trigger
    token = get_token()
    if not token:
        return False, None, None
    payload = {
        "dhanClientId": DHAN_CLIENT_ID, "correlationId": _cid(CID_EXIT),
        "orderFlag": "SINGLE", "transactionType": "SELL", "exchangeSegment": "NSE_EQ",
        "productType": "CNC", "orderType": "LIMIT", "validity": "DAY",
        "securityId": str(sec_id), "quantity": int(qty),
        "price": price, "triggerPrice": trigger,
    }
    try:
        r = session.post("https://api.dhan.co/v2/forever/orders", json=payload,
                         headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
                         timeout=30)
        if r.status_code in (200, 201):
            order_id = None
            try:
                order_id = r.json().get("orderId")
            except Exception:
                pass
            logger.info(f"✅ EXIT forever placed: {symbol} (order {order_id})")
            return True, order_id, trigger
        logger.error(f"❌ EXIT forever failed: HTTP {r.status_code} | {r.text}")
        return False, None, None
    except Exception as e:
        logger.error(f"❌ EXIT forever exception: {e}")
        return False, None, None


# ==========================
# CONFIRM FILL  (forever exits won't fill until open → PENDING is normal)
# ==========================
def confirm_fill(order_id, symbol):
    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would poll fill for {order_id}")
        return "PENDING", None
    token = get_token()
    if not token:
        return "PENDING", None
    last_status = None
    for attempt in range(1, EXIT_POLL_ATTEMPTS + 1):
        try:
            r = session.get(f"https://api.dhan.co/v2/orders/{order_id}",
                            headers={"Accept": "application/json",
                                     "access-token": token, "client-id": DHAN_CLIENT_ID},
                            timeout=15)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    data = data[0] if data else {}
                status = str(data.get("orderStatus", "")).upper()
                last_status = status
                logger.info(f"   poll {attempt}/{EXIT_POLL_ATTEMPTS}: {symbol} → {status}")
                if status in FILLED_STATUSES:
                    exit_price = None
                    for k in ("averageTradedPrice", "avgPrice", "price", "tradedPrice"):
                        v = data.get(k)
                        if v:
                            exit_price = _f(v, None)
                            break
                    return "FILLED", exit_price
                if status in DEAD_STATUSES:
                    logger.error(f"❌ {symbol} exit order dead: {status}")
                    return "DEAD", None
            else:
                logger.warning(f"⚠️ {symbol} order poll HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"❌ {symbol} order poll exception: {e}")
        time.sleep(EXIT_POLL_SLEEP)
    logger.info(f"ℹ️ {symbol} exit forever resting (last={last_status}) — "
                f"fills at open, will confirm next run")
    return "PENDING", None


# ==========================
# R MULTIPLE
# ==========================
def realized_r(entry, structural_sl, exit_price):
    try:
        risk = entry - structural_sl
        if not risk or risk <= 0 or exit_price is None:
            return None
        return round((exit_price - entry) / risk, 2)
    except Exception:
        return None


def realized_r_from_basis(exit_price, entry, risk_per_share):
    try:
        if not risk_per_share or risk_per_share <= 0 or exit_price is None:
            return None
        return round((exit_price - entry) / risk_per_share, 2)
    except Exception:
        return None


# ==========================
# STRUCTURAL EXIT  (forever-order: modify −8% → exit, else cancel+place)
# ==========================
def execute_structural_exit(trade, pos, close_price, safety_order):
    """
    Convert the position to a resting EXIT forever-order that fills at open.

      1. (alert) structural exit detected
      2. try MODIFY the −8% forever-order → exit trigger/qty
         • modify ok  → that order IS now the exit; mark EXIT_PENDING
         • modify fail → cancel −8% + place fresh exit forever
                          (if cancel also fails → keep −8%, abort, retry next run)
         • no −8% at all → place fresh exit forever directly
      3. Status EXIT_PENDING. Position stays PROTECTED (a resting SELL exists)
         until it fills at open; next run confirms FILLED → CLOSED.

    Returns: "EXIT_PENDING" | "CLOSED" | "ABORTED".
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")
    entry = _f(trade.get("Entry_Price"))
    structural_sl = _f(trade.get("Structural_SL"))
    qty = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])

    r_est = realized_r(entry, structural_sl, close_price)
    logger.warning(f"🔴 STRUCTURAL EXIT: {symbol} close {close_price} < SL {structural_sl}")
    send_telegram_alert("🔴 STRUCTURAL EXIT DETECTED",
                        {"Symbol": symbol, "Close": close_price,
                         "Structural_SL": structural_sl, "Entry": entry,
                         "Est_R_at_close": r_est, "Qty": qty})

    # Build the exit trigger/limit (just below close → fills at open).
    trigger = _round_down(close_price * EXIT_TRIGGER_OFFSET, symbol)
    price = _round_down(trigger * EXIT_LIMIT_OFFSET, symbol)

    safety_order_id = safety_order.get("orderId") if safety_order else None
    exit_order_id = None
    now = datetime.now(timezone.utc).isoformat()

    if safety_order_id:
        # Always cancel-and-replace (Dhan forever-modify needs legName and is
        # unreliable; cancel+replace is proven). Cancel the −8%, then place a
        # fresh exit forever.
        if not cancel_forever_order(safety_order_id, symbol):
            logger.error(f"❌ {symbol} cancel failed — abort, "
                         f"−8% still protecting; retry next run")
            send_telegram_alert("⚠️ EXIT ABORTED — CANCEL FAILED",
                                {"Symbol": symbol, "OrderID": safety_order_id,
                                 "Status": "−8% still in place; retry next run"})
            return "ABORTED"
        ok, exit_order_id, _ = place_exit_forever(sec_id, qty, close_price, symbol)
        if not ok:
            # Bare now — re-place the −8% backstop, abort the exit.
            logger.error(f"🚨 {symbol} exit forever place FAILED after cancel — "
                         f"re-placing −8% safety")
            rok, new_id, lvl = place_safety_sl(sec_id, qty, entry, symbol)
            upd = {"Status": db.STATUS_OPEN}
            if rok and lvl is not None:
                upd["Safety_SL"] = lvl
            db.update_trade(trade_id=trade_id, **upd)
            send_telegram_alert("🚨 EXIT FAILED — RE-PROTECTED",
                                {"Symbol": symbol,
                                 "Re-protect": "OK" if rok else "FAILED — ACT NOW"})
            return "ABORTED"
    else:
        ok, exit_order_id, _ = place_exit_forever(sec_id, qty, close_price, symbol)
        if not ok:
            logger.error(f"❌ {symbol} exit forever place failed (no −8% existed)")
            send_telegram_alert("🚨 EXIT FOREVER FAILED — UNPROTECTED",
                                {"Symbol": symbol, "Qty": qty,
                                 "Issue": "no safety + exit place failed; ACT NOW"})
            return "ABORTED"

    # A resting exit SELL now exists → confirm (won't fill till open).
    status, fill_price = confirm_fill(exit_order_id, symbol)
    if status == "FILLED":
        exit_price = fill_price if fill_price is not None else close_price
        r_real = realized_r(entry, structural_sl, exit_price)
        db.update_trade(trade_id=trade_id, Status=db.STATUS_CLOSED,
                        Exit_Price=exit_price, Exit_Time=now,
                        Exit_Reason="structural", Exit_Order_ID=exit_order_id,
                        Current_Price=exit_price)
        logger.info(f"✅ {symbol} CLOSED @ {exit_price} (R={r_real})")
        send_telegram_alert("✅ EXIT FILLED — CLOSED",
                            {"Symbol": symbol, "Exit_Price": exit_price,
                             "Realized_R": r_real, "Exit_Reason": "structural"})
        return "CLOSED"

    # PENDING (normal after-hours) or DEAD-but-order-exists → EXIT_PENDING.
    db.update_trade(trade_id=trade_id, Status=db.STATUS_EXIT_PENDING,
                    Exit_Order_ID=exit_order_id, Exit_Reason="structural",
                    Safety_SL="")  # the −8% is now the exit; clear stale level
    logger.info(f"⏳ {symbol} EXIT_PENDING — exit forever resting, fills at open")
    send_telegram_alert("⏳ EXIT RESTING — FILLS AT OPEN",
                        {"Symbol": symbol, "Exit_Order_ID": exit_order_id,
                         "Trigger": trigger,
                         "Status": "EXIT_PENDING; confirm next run"})
    return "EXIT_PENDING"


# ==========================
# R BASIS  (from Target_Price)
# ==========================
def compute_r_basis(trade):
    entry = _f(trade.get("Entry_Price"))
    target = _f(trade.get("Target_Price"))
    if entry <= 0 or target <= 0:
        return None
    risk = (target - entry) / 2.0
    if risk <= 0:
        return None
    return {"risk": risk, "one_r_price": entry + risk, "two_r_price": target}


# ==========================
# TRAILING −8% SAFETY  (ratchet the resting backstop up on close ≥ 1R)
# ==========================
def trail_safety_sl(trade, pos, close_price, safety_order, r_basis):
    """
    Ratchet the resting −8% SELL forever-order UP, based on the daily close.

    Rule (close-based decision; the order itself is a live broker stop):
      • Only acts once a daily close ≥ 1R (trade is "proven").
      • New safety level = close × SAFETY_SL_PCT (−8% from latest close),
        rounded DOWN to tick.
      • Ratchet UP only: act only if new level > current resting trigger.
      • Cancel + replace at FULL live Dhan qty (modify is unreliable).
        Place-first then cancel-old → never bare.

    Returns dict update for the sheet ({"Safety_SL": level}) or {} if nothing
    changed. Does NOT touch Structural_SL / trail phase (that's separate).
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")
    qty = int(_f(pos.get("qty")))

    # Gate: only trail the −8% once the trade has closed ≥ 1R.
    if close_price < r_basis["one_r_price"]:
        return {}

    new_level = _round_down(close_price * SAFETY_SL_PCT, symbol)
    cur_trigger = _f(safety_order.get("triggerPrice")) if safety_order else 0.0

    # Ratchet up only.
    if new_level <= cur_trigger:
        return {}

    entry = _f(trade.get("Entry_Price"))
    logger.info(f"   🔼 {symbol} trailing −8%: {cur_trigger} → {new_level} "
                f"(close {close_price} ≥ 1R {round(r_basis['one_r_price'],4)})")

    # Place the NEW safety first (never leave the position bare), at the
    # close-based level (not entry*0.92).
    ok, new_id, _ = _place_safety_at_level(sec_id, qty, new_level, symbol)
    if not ok:
        logger.error(f"🚨 {symbol} trailing −8% place FAILED — keeping old order")
        send_telegram_alert("🚨 TRAIL −8% FAILED — OLD KEPT",
                            {"Symbol": symbol, "Attempted_Level": new_level})
        return {}

    # New order live → cancel the old one.
    old_id = safety_order.get("orderId") if safety_order else None
    if old_id:
        cancel_forever_order(old_id, symbol)

    # Update the in-memory safety_map entry so later logic sees the new order.
    safety_order_new = {"orderId": new_id, "securityId": sec_id,
                        "quantity": qty, "transactionType": "SELL",
                        "orderStatus": "PENDING", "tradingSymbol": symbol,
                        "triggerPrice": new_level}
    # Mutate the dict in place so the caller's reference updates.
    if safety_order is not None:
        safety_order.clear()
        safety_order.update(safety_order_new)

    send_telegram_alert("🔼 −8% SAFETY TRAILED UP", {
        "Symbol": symbol, "Old_Trigger": cur_trigger,
        "New_Trigger": new_level, "Close": close_price, "New_OrderID": new_id})
    return {"Safety_SL": new_level}


def _place_safety_at_level(sec_id, qty, trigger_level, symbol):
    """
    Place a −8% SELL forever-order at an EXPLICIT trigger level (used by the
    trailing-safety path, where the level is close-based, not entry*0.92).
    Trigger and limit both rounded DOWN to tick.

    Returns (ok, order_id, trigger_level).
    """
    trigger = _round_down(trigger_level, symbol)
    price = _round_down(trigger * SAFETY_LIMIT_OFFSET, symbol)
    logger.info(f"📤 Placing trailing −8% SL: {symbol} | Trigger: {trigger} | Limit: {price}")
    send_telegram_alert("🛡️ PLACING TRAILING −8% SL",
                        {"Symbol": symbol, "Qty": qty, "Trigger": trigger, "Limit": price})
    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would place trailing −8% for {symbol}")
        return True, "DRYRUN_TRAIL_SAFETY_ID", trigger
    token = get_token()
    if not token:
        return False, None, None
    payload = {
        "dhanClientId": DHAN_CLIENT_ID, "correlationId": _cid(CID_SAFE),
        "orderFlag": "SINGLE", "transactionType": "SELL", "exchangeSegment": "NSE_EQ",
        "productType": "CNC", "orderType": "LIMIT", "validity": "DAY",
        "securityId": str(sec_id), "quantity": int(qty),
        "price": price, "triggerPrice": trigger,
    }
    try:
        r = session.post("https://api.dhan.co/v2/forever/orders", json=payload,
                         headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
                         timeout=30)
        if r.status_code in (200, 201):
            order_id = None
            try:
                order_id = r.json().get("orderId")
            except Exception:
                pass
            logger.info(f"✅ Trailing −8% placed: {symbol} (order {order_id})")
            return True, order_id, trigger
        logger.error(f"❌ Trailing −8% failed: HTTP {r.status_code} | {r.text}")
        return False, None, None
    except Exception as e:
        logger.error(f"❌ Trailing −8% exception: {e}")
        return False, None, None


# ==========================
# SAFETY RECONCILE  (after partial: cancel-then-replace at kept qty)
# ==========================
def reconcile_safety_qty(trade, pos, safety_order, new_qty, entry):
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    safety_order_id = safety_order.get("orderId") if safety_order else None

    if safety_order_id:
        if not cancel_forever_order(safety_order_id, symbol):
            logger.error(f"❌ {symbol} safety cancel failed during reconcile — "
                         f"leaving OLD −8% (full qty)")
            send_telegram_alert("⚠️ PARTIAL RECONCILE — CANCEL FAILED",
                                {"Symbol": symbol, "OrderID": safety_order_id,
                                 "Issue": "old −8% oversized vs remaining; retry next run"})
            return False, None
    else:
        logger.info(f"ℹ️ {symbol} no −8% to cancel during reconcile — placing fresh")

    ok, new_id, safety_level = place_safety_sl(sec_id, int(new_qty), entry, symbol)
    if ok:
        logger.info(f"✅ {symbol} −8% reconciled to qty {new_qty}")
        send_telegram_alert("✅ −8% RECONCILED (POST-PARTIAL)",
                            {"Symbol": symbol, "New_Qty": new_qty,
                             "New_Safety_Level": safety_level, "New_Safety_OrderID": new_id})
        return True, safety_level
    logger.error(f"🚨 {symbol} −8% re-place FAILED post-partial — {new_qty} BARE")
    send_telegram_alert("🚨 POST-PARTIAL UNPROTECTED — ACT NOW",
                        {"Symbol": symbol, "Remaining_Qty": new_qty,
                         "Issue": "half sold, old −8% cancelled, re-place failed"})
    return False, None


# ==========================
# HYBRID TARGET  (half at 2R via a SEPARATE exit forever-order)
# ==========================
def execute_partial_target(trade, pos, close_price, safety_order, r_basis):
    """
    On the first 2R close, place a forever SELL for floor(Remaining_Qty/2)
    (fills at open), then reconcile the −8% to the kept qty.

    Unlike structural exit, the −8% is NOT reused here (it must keep
    protecting the kept half), so the partial gets its OWN exit forever.

    Returns (did_partial, remaining_qty, new_safety_level|None).
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")
    entry = _f(trade.get("Entry_Price"))
    full_qty = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])

    sell_qty = full_qty // 2
    keep_qty = full_qty - sell_qty
    if sell_qty < 1:
        logger.info(f"ℹ️ {symbol} qty {full_qty} too small to halve — trailing whole")
        return False, full_qty, None

    r_real_est = realized_r_from_basis(close_price, entry, r_basis["risk"])
    logger.warning(f"🎯 2R TARGET HIT: {symbol} close {close_price} ≥ "
                   f"2R {round(r_basis['two_r_price'],4)} | selling {sell_qty}/{full_qty}")
    send_telegram_alert("🎯 2R TARGET — SELLING HALF (fills at open)",
                        {"Symbol": symbol, "Close": close_price,
                         "Two_R_Price": round(r_basis["two_r_price"], 4),
                         "Sell_Qty": sell_qty, "Keep_Qty": keep_qty, "Est_R": r_real_est})

    ok, exit_order_id, _ = place_exit_forever(sec_id, sell_qty, close_price, symbol)
    if not ok:
        logger.error(f"❌ {symbol} partial exit forever rejected — no partial this run")
        send_telegram_alert("❌ 2R PARTIAL REJECTED",
                            {"Symbol": symbol,
                             "Status": "full position intact + protected; retry next run"})
        return False, full_qty, None

    status, fill_price = confirm_fill(exit_order_id, symbol)
    exit_price = fill_price if fill_price is not None else close_price

    if status == "DEAD":
        logger.error(f"❌ {symbol} partial exit DEAD ({exit_order_id})")
        send_telegram_alert("❌ 2R PARTIAL DEAD",
                            {"Symbol": symbol, "Exit_Order_ID": exit_order_id,
                             "Status": "full position intact + protected; retry"})
        return False, full_qty, None

    # FILLED (rare, same-run) OR PENDING (normal — rests, fills at open).
    # In BOTH cases the half-sell is committed/queued, so we reduce qty and
    # reconcile the −8% now. (If a queued partial silently fails to fill,
    # next run's reconciliation + the kept −8% still cover us.)
    r_real = realized_r_from_basis(exit_price, entry, r_basis["risk"])
    now = datetime.now(timezone.utc).isoformat()

    db.update_trade(trade_id=trade_id, Status=db.STATUS_PARTIAL,
                    Remaining_Qty=keep_qty, Exit_Price=exit_price, Exit_Time=now,
                    Exit_Reason="target", Exit_Order_ID=exit_order_id)
    logger.info(f"✅ {symbol} HALF {'SOLD' if status=='FILLED' else 'QUEUED'} "
                f"@ {exit_price} (R={r_real}) | Remaining {full_qty} → {keep_qty}")
    send_telegram_alert("✅ 2R PARTIAL — HALF BANKED/QUEUED",
                        {"Symbol": symbol, "Sold_Qty": sell_qty,
                         "Exit_Price": exit_price, "Realized_R_half": r_real,
                         "Remaining_Qty": keep_qty,
                         "Fill": "FILLED" if status == "FILLED" else "RESTS→OPEN"})

    ok_rec, new_safety_level = reconcile_safety_qty(trade, pos, safety_order, keep_qty, entry)
    return True, keep_qty, (new_safety_level if ok_rec else None)


# ==========================
# TRAIL  (three-phase, close-based, ratchet-up, clamped)
# ==========================
def compute_trail_updates(trade, pos, close_price, atr, atr_source, r_basis):
    symbol = pos["symbol"]
    entry = _f(trade.get("Entry_Price"))
    cur_sl = _f(trade.get("Structural_SL"))
    cur_phase = int(_f(trade.get("Trail_Phase"), 1) or 1)
    prev_high = _f(trade.get("Highest_Close"))
    highest_close = max(prev_high, close_price) if prev_high else close_price
    one_r_price = r_basis["one_r_price"]

    updates = {}
    new_sl = cur_sl
    new_phase = cur_phase

    if highest_close > prev_high:
        updates["Highest_Close"] = round(highest_close, 4)

    reached_1r = close_price >= one_r_price
    if not reached_1r:
        new_phase = 1
    else:
        breakeven = _round_down(entry, symbol)
        if cur_sl < breakeven:
            new_sl = breakeven
            new_phase = max(cur_phase, 2)
        gap = ATR_TRAIL_MULT * atr if (atr is not None and atr > 0) else FALLBACK_TRAIL_PCT * highest_close
        raw_trail = highest_close - gap
        tick = _tick_for(symbol)
        clamp_ceiling = close_price - tick
        trail_stop = _round_down(min(raw_trail, clamp_ceiling), symbol)
        if trail_stop > new_sl:
            new_sl = trail_stop
            new_phase = 3
        logger.info(f"   trail({atr_source}) {symbol}: highest={round(highest_close,4)} "
                    f"gap={round(gap,4)} raw={round(raw_trail,4)} clamp≤{round(clamp_ceiling,4)} "
                    f"→ cand={trail_stop} | cur_SL={cur_sl} new_SL={new_sl} phase={new_phase}")

    if new_sl != cur_sl:
        updates["Previous_SL_Price"] = cur_sl
        updates["Structural_SL"] = round(new_sl, 4)
    if new_phase != cur_phase:
        updates["Trail_Phase"] = new_phase
    return updates


# ==========================
# PnL
# ==========================
def calculate_pnl(entry_price, current_price, qty):
    if not current_price or not entry_price:
        return 0, 0
    pnl = (current_price - entry_price) * qty
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    return round(pnl, 2), round(pnl_pct, 2)


# ==========================
# 6b ORCHESTRATOR
# ==========================
def manage_trail_and_target(trade, pos, close_price, safety_order):
    symbol = pos["symbol"]
    trade_id = trade.get("ID")
    status = _normalize_status(trade.get("Status"))
    partial_fired = False

    r_basis = compute_r_basis(trade)
    if r_basis is None:
        logger.warning(f"   ↪ {symbol} no valid Target-derived R basis — "
                       f"routine refresh only")
        qty_full = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])
        pnl, pnl_pct = calculate_pnl(_f(trade.get("Entry_Price")), close_price, qty_full)
        return {"trade_id": trade_id, "Current_Price": close_price,
                "PnL": pnl, "PnL_Percent": pnl_pct,
                "Status": db.STATUS_OPEN if status == db.STATUS_PENDING else status}

    qty_before = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])
    full_qty = int(_f(trade.get("Qty"), qty_before) or qty_before)
    remaining_qty = qty_before

    partial_allowed = (
            status not in (db.STATUS_PARTIAL, db.STATUS_EXIT_PENDING, db.STATUS_CLOSED)
            and qty_before == full_qty
            and close_price >= r_basis["two_r_price"]
    )
    if partial_allowed:
        did_partial, remaining_qty, _ns = execute_partial_target(
            trade, pos, close_price, safety_order, r_basis)
        if did_partial:
            partial_fired = True
            refreshed = db.get_trade(trade_id=trade_id)
            if refreshed:
                trade = refreshed
                status = _normalize_status(trade.get("Status"))

    atr, atr_source = get_atr(pos["securityId"], symbol)
    trail_updates = compute_trail_updates(trade, pos, close_price, atr, atr_source, r_basis)

    pnl, pnl_pct = calculate_pnl(_f(trade.get("Entry_Price")), close_price, int(remaining_qty))
    out = {"trade_id": trade_id, "Current_Price": close_price,
           "PnL": pnl, "PnL_Percent": pnl_pct, "_partial": partial_fired}
    out.update(trail_updates)
    if status != db.STATUS_PARTIAL:
        out["Status"] = db.STATUS_OPEN if status == db.STATUS_PENDING else status
    return out


# ==========================
# MAIN
# ==========================
# ==========================
# STALE / DUPLICATE FOREVER-ORDER CLEANUP
# ==========================
def _is_exit_order(o):
    """True if this forever-order is a tagged EXIT order (correlationId)."""
    cid = str(o.get("correlationId", "") or "")
    return cid.startswith(CID_EXIT)


def cleanup_forever_orders(forever, all_pos):
    """
    Cancel SELL forever-orders that are stale:
      • ORPHANS — securityId has no matching open position/holding.
      • DUPLICATES — more than one live −8% SELL for the same securityId;
        keep the NEWEST, cancel the rest.

    EXIT orders (correlationId prefixed EXIT_) are the 2R/structural
    exit-forevers; they are NEVER cancelled and NEVER counted as −8%
    duplicates (a position legitimately holds both an exit-forever and a
    −8% safety at once). Untagged legacy SELLs are treated as −8% safeties.

    Returns (kept_map: {sec_id: −8% order}, n_cancelled).
    """
    live = [o for o in forever
            if o.get("transactionType") == "SELL"
            and str(o.get("orderStatus", "")).upper() in ("PENDING", "CONFIRM")]

    # Group live −8% SELLs by securityId, SKIPPING tagged EXIT orders.
    by_sec = {}
    protected = 0
    for o in live:
        if _is_exit_order(o):
            protected += 1
            logger.info(f"   🔒 keeping EXIT-forever {o.get('orderId')} "
                        f"({o.get('tradingSymbol')}) — correlationId tagged")
            continue
        by_sec.setdefault(str(o.get("securityId")), []).append(o)

    pos_secs = set(all_pos.keys())
    kept_map = {}
    cancelled = 0

    for sec_id, orders in by_sec.items():
        # Orphan: no position/holding for this securityId → cancel ALL.
        if sec_id not in pos_secs:
            for o in orders:
                oid = o.get("orderId")
                sym = o.get("tradingSymbol", sec_id)
                logger.warning(f"🧹 Orphan −8% SELL (no position): {sym} "
                               f"sec {sec_id} order {oid} — cancelling")
                if cancel_forever_order(oid, sym):
                    cancelled += 1
            continue

        # Duplicates among −8% safeties: keep newest, cancel rest.
        if len(orders) > 1:
            try:
                orders_sorted = sorted(orders, key=lambda x: int(x.get("orderId", 0)))
            except (ValueError, TypeError):
                orders_sorted = orders
            keep = orders_sorted[-1]
            for o in orders_sorted[:-1]:
                oid = o.get("orderId")
                sym = o.get("tradingSymbol", sec_id)
                logger.warning(f"🧹 Duplicate −8% SELL: {sym} sec {sec_id} "
                               f"order {oid} — cancelling (keeping {keep.get('orderId')})")
                if cancel_forever_order(oid, sym):
                    cancelled += 1
            kept_map[sec_id] = keep
        else:
            kept_map[sec_id] = orders[0]

    if cancelled:
        send_telegram_alert("🧹 STALE FOREVER ORDERS CLEANED",
                            {"Cancelled": cancelled, "Live_kept": len(kept_map)})
    logger.info(f"🧹 Cleanup: cancelled {cancelled}, kept {len(kept_map)} −8% order(s), "
                f"protected {protected} EXIT-forever(s)")
    return kept_map, cancelled


def _order_qty(order):
    """Extract qty from a forever-order dict (Dhan uses 'quantity')."""
    for k in ("quantity", "qty", "remainingQuantity"):
        v = order.get(k)
        if v not in (None, ""):
            try:
                return int(float(v))
            except (ValueError, TypeError):
                pass
    return None


def protect_all_positions(all_pos, safety_map):
    """
    OPTION-3 guarantee: every Dhan position ends the run with a −8% SELL
    forever-order at the LIVE DHAN qty, UNLESS the row is EXIT_PENDING (its
    resting exit SELL is the protection).

    Behaviour per position:
      • EXIT_PENDING row  → skip (don't fight the resting exit order).
      • No SELL at all     → place −8% at protect-qty.
      • SELL qty mismatch  → place-first at correct qty, THEN cancel old
                             (never bare; brief after-hours overlap is safe
                             since neither fills until open).
      • SELL qty correct   → leave it.
      • No sheet row        → still protect at Dhan qty (force −8%).

    Mutates `safety_map` in place to reflect the new/kept orders.
    Returns dict counts.
    """
    placed = reconciled = ok_already = skipped_pending = 0

    for sec_id, pos in all_pos.items():
        symbol = pos["symbol"]
        dhan_qty = int(_f(pos.get("qty")))
        if dhan_qty <= 0:
            continue

        trade = db.get_trade(symbol=symbol, security_id=sec_id)

        # EXIT_PENDING → the resting exit SELL is the protection; leave alone.
        if trade and _normalize_status(trade.get("Status")) == db.STATUS_EXIT_PENDING:
            logger.info(f"   🛡️ {symbol} EXIT_PENDING — skip protect (exit order rests)")
            skipped_pending += 1
            continue

        entry = _f(trade.get("Entry_Price")) if trade else _f(pos.get("avgPrice"))
        if entry <= 0:
            logger.warning(f"   ⚠️ {symbol} no entry/avg price — cannot place −8%")
            continue

        # Protect qty = live Dhan position/holding qty (authoritative).
        protect_qty = dhan_qty
        if protect_qty <= 0:
            continue

        existing = safety_map.get(sec_id)
        existing_qty = _order_qty(existing) if existing else None

        if existing and existing_qty == protect_qty:
            ok_already += 1
            continue

        # Need to (re)place at protect_qty. Place FIRST (never bare).
        ok, new_id, lvl = place_safety_sl(sec_id, protect_qty, entry, symbol)
        if not ok:
            logger.error(f"🚨 {symbol} protect −8% place FAILED — position may be "
                         f"under-protected")
            send_telegram_alert("🚨 PROTECT FAILED — CHECK POSITION",
                                {"Symbol": symbol, "Qty": protect_qty,
                                 "Issue": "could not place −8%; old order (if any) kept"})
            continue

        # New −8% is live → now cancel the old wrong-qty one (if any).
        if existing:
            old_id = existing.get("orderId")
            logger.info(f"   ♻️ {symbol} reconcile qty {existing_qty}→{protect_qty}: "
                        f"placed {new_id}, cancelling old {old_id}")
            cancel_forever_order(old_id, symbol)
            reconciled += 1
        else:
            logger.info(f"   🛡️ {symbol} had no −8% — placed {new_id} @ qty {protect_qty}")
            placed += 1

        # Update map to the new order so later per-position logic sees it.
        safety_map[sec_id] = {"orderId": new_id, "securityId": sec_id,
                              "quantity": protect_qty, "transactionType": "SELL",
                              "orderStatus": "PENDING", "tradingSymbol": symbol}

        # Write the new Safety_SL level to the sheet if we have a row.
        if trade and lvl is not None:
            db.update_trade(trade_id=trade.get("ID"), Safety_SL=lvl)

    logger.info(f"🛡️ Protect-all: placed {placed}, reconciled {reconciled}, "
                f"already-ok {ok_already}, skipped(EXIT_PENDING) {skipped_pending}")
    if placed or reconciled:
        send_telegram_alert("🛡️ POSITIONS PROTECTED (−8% reconciled)",
                            {"Placed": placed, "Reconciled": reconciled,
                             "Already_OK": ok_already})
    return {"placed": placed, "reconciled": reconciled,
            "ok": ok_already, "skipped": skipped_pending}



def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V19.6 — + Target-based risk guard (trail no longer self-benches)")
    logger.info("=" * 80)
    validate_env()
    db.init_sheets()
    db.ensure_schema()

    trades = db.get_all_trades()
    logger.info(f"📊 Trades in sheet: {len(trades)}")

    positions = get_positions()
    holdings = get_holdings()
    forever = get_forever_orders()

    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    # Cancel orphan + duplicate −8% forever-orders (EXIT-tagged orders are
    # protected by correlationId inside cleanup); get a deduped safety map.
    safety_map, _cleaned = cleanup_forever_orders(forever, all_pos)

    # OPTION-3: guarantee every position is protected at the correct qty.
    protect_stats = protect_all_positions(all_pos, safety_map)

    logger.info(f"📊 Positions/holdings: {len(all_pos)} | Safety orders: {len(safety_map)}")

    routine_updates = []
    exited = skipped_close = trailed = partials = skipped_invalid = 0

    for sec_id, pos in all_pos.items():
        symbol = pos["symbol"]
        logger.info(f"\n{'='*80}\n📍 {symbol} (Qty: {pos['qty']}, Avg: {pos['avgPrice']})")

        trade = db.get_trade(symbol=symbol, security_id=sec_id)
        if not trade:
            logger.info(f"   ↪ no matching sheet row for {symbol}; skipping")
            continue

        status = _normalize_status(trade.get("Status"))
        if status in (db.STATUS_CLOSED, db.STATUS_EXIT_PENDING):
            logger.info(f"   ↪ {symbol} status={status}; skipping")
            continue

        entry_price = _f(trade.get("Entry_Price"))
        structural_sl = _f(trade.get("Structural_SL"))
        safety_order = safety_map.get(sec_id)

        # ----- INVALID-ROW GUARD: entry+Target basis must be sound -----
        # (Safety −8% already placed/reconciled by protect_all_positions.)
        if not has_valid_risk(trade):
            tgt = _f(trade.get("Target_Price"))
            logger.warning(f"   ⚠️ {symbol} invalid risk basis (entry {entry_price}, "
                           f"target {tgt}) — −8% protects it; "
                           f"NO trail/partial/structural-exit")
            skipped_invalid += 1
            continue

        # ----- Close-based structural exit decision -----
        close_price, source = get_daily_close(sec_id, symbol)
        if close_price is None:
            skipped_close += 1
            logger.warning(f"   ↪ {symbol} no daily close; skipping exit+trail this run")
            continue

        logger.info(f"   Close({source}): {close_price} | SL: {structural_sl} | "
                    f"Entry: {entry_price}")

        if structural_sl > 0 and close_price < structural_sl:
            outcome = execute_structural_exit(trade, pos, close_price, safety_order)
            if outcome in ("CLOSED", "EXIT_PENDING"):
                exited += 1
            continue

        # ----- Ratchet the −8% safety UP on close (close ≥ 1R) -----
        r_basis_for_safety = compute_r_basis(trade)
        if r_basis_for_safety is not None:
            safety_upd = trail_safety_sl(
                trade, pos, close_price, safety_order, r_basis_for_safety)
            if safety_upd:
                routine_updates.append({"trade_id": trade.get("ID"), **safety_upd})

        # ----- No exit → trail + hybrid target -----
        upd = manage_trail_and_target(trade, pos, close_price, safety_order)
        if upd:
            if upd.pop("_partial", False):
                partials += 1
            if "Structural_SL" in upd:
                trailed += 1
            routine_updates.append(upd)

    if routine_updates:
        merged = {}
        for u in routine_updates:
            tid = u.get("trade_id")
            if tid in merged:
                merged[tid].update({k: v for k, v in u.items() if k != "trade_id"})
            else:
                merged[tid] = dict(u)
        result = db.batch_update_trades(list(merged.values()))
        logger.info(f"📝 Routine batch update: {result}")

    logger.info(f"\n{'='*80}")
    logger.info(f"✅ COMPLETED | Exits: {exited} | Partials: {partials} | "
                f"Trailed: {trailed} | Protect(placed/recon): "
                f"{protect_stats['placed']}/{protect_stats['reconciled']} | "
                f"Close-skipped: {skipped_close} | Invalid-skipped: {skipped_invalid}")
    logger.info(f"{'='*80}")

    send_telegram_alert("🚀 SL ENGINE V19.6 COMPLETED",
                        {"Exits": exited, "Partials": partials, "Trailed": trailed,
                         "Protect_placed": protect_stats["placed"],
                         "Protect_reconciled": protect_stats["reconciled"],
                         "Close_skipped": skipped_close,
                         "Invalid_skipped": skipped_invalid, "Positions": len(all_pos)})


if __name__ == "__main__":
    run()