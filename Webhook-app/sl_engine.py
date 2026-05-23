# ==============================================================
# 🚀 SL ENGINE — V18 (step 6b)
#
# Trade Setup Enhancement v2 — builds the TRAILING + HYBRID TARGET
# layer (§4, §5) ON TOP of the 6a exit foundation (§2 two-tier stops,
# §3 close-based structural exit, §6 cancel-and-replace exit).
#
# 6a is UNCHANGED below this line except for two small wiring edits in
# run(): the "no structural exit" branch now calls manage_trail_and_target()
# instead of the plain PnL refresh. The 6a exit primitives
# (execute_structural_exit / confirm_fill / _reprotect_after_failed_exit /
# place_safety_sl / cancel_forever_order / place_market_sell) are REUSED
# verbatim by 6b, never modified.
#
# WHAT 6b ADDS (all close-based, after-hours, acted next day)
#   • 14-day ATR from a NEW ~25-session candle fetch
#       Dhan candles → yfinance OHLC → 5%-of-highest-close fallback gap.
#     6a's single-close _dhan_daily_close is left intact (two historical
#     calls per position per run — deliberate, keeps the exit decision on
#     exactly the number 6a already trusts).
#   • R basis from Target_Price (immutable), NOT live Structural_SL
#     (Phase 2 rewrites Structural_SL to entry, which would zero the risk):
#         risk = (Target_Price − entry)/2 ; 1R ⟺ close ≥ entry+risk ;
#         2R ⟺ close ≥ Target_Price. Blank/garbage Target_Price → skip
#         trail+partial for that row (never guess).
#   • Three-phase trail, ratchet-up-only, with a CLAMP that keeps the stop
#     strictly below the latest close (so it can never be a disguised
#     "exit now" the moment it's written):
#         P1 fixed  — close < +1R: Structural_SL unchanged.
#         P2 b/even — first close ≥ +1R: Structural_SL → entry.
#         P3 trail  — new-high closes:
#             gap        = 2.5×ATR  (or 0.05×highest_close if no ATR)
#             trail_stop = highest_close − gap
#             trail_stop = min(trail_stop, latest_close − one_tick)  # CLAMP
#             Structural_SL = max(Structural_SL, trail_stop)
#   • Hybrid target: first 2R close sells floor(Remaining_Qty/2) at MARKET,
#     logs reason="target" + realized R for the sold half, status PARTIAL,
#     writes Remaining_Qty, THEN reconciles the −8% safety to the new qty
#     (cancel-then-replace, re-place-on-failure with a loud alert).
#   • 6b sizes safety orders off Remaining_Qty (sheet), never pos["qty"]
#     (Dhan) — robust to post-partial settlement lag.
#   • Previous_SL_Price records the pre-update Structural_SL on every change.
#
# DEPLOYMENT NOTE
#   • yfinance optional (daily-close AND candle fallback). Missing → those
#     fallbacks are skipped; the engine degrades, never crashes.
# ==============================================================

import os
import uuid
import time
import logging
import requests
import pyotp
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ---- Shared modules (build steps 1 & 2) ----------------------
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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DRY_RUN = os.getenv("SL_ENGINE_DRY_RUN", "false").lower() in ("true", "1", "yes")

# −8% catastrophe backstop level. The Safety_SL = entry * SAFETY_SL_PCT.
SAFETY_SL_PCT = 0.92
# Limit price for the resting safety SELL sits just under its trigger.
SAFETY_LIMIT_OFFSET = 0.995

# Fill-confirmation polling for the Path B market sell.
EXIT_POLL_ATTEMPTS = 6      # number of GET /orders/{id} polls
EXIT_POLL_SLEEP = 2.0       # seconds between polls
# Dhan order statuses that mean "filled".
FILLED_STATUSES = {"TRADED", "EXECUTED", "COMPLETE", "FILLED"}
# Statuses that mean "definitively dead, will never fill".
DEAD_STATUSES = {"REJECTED", "CANCELLED", "CANCELED", "EXPIRED", "FAILED"}

# ---- 6b CONFIG (trail + hybrid target) -----------------------
ONE_R_PHASE_THRESHOLD = 1.0     # +1R close flips Phase 1 → 2 (tunable §17)
ATR_PERIOD = 14                 # 14-day ATR (§4, §19)
ATR_TRAIL_MULT = 2.5            # 2.5 × ATR trail distance (§4, §17)
ATR_FETCH_SESSIONS = 25         # request ~25 sessions to net ≥15 candles
FALLBACK_TRAIL_PCT = 0.05       # 5% gap when ATR genuinely unavailable
MIN_CANDLES_FOR_ATR = ATR_PERIOD + 1   # 14 TRs need a prior close → 15

session = requests.Session()

# ==========================
# LOGGING
# ==========================
logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)

# yfinance is an optional fallback; import defensively so a missing
# package degrades to "skip the exit" rather than crashing the engine.
try:
    import yfinance as yf
    _YF_AVAILABLE = True
except Exception:
    yf = None
    _YF_AVAILABLE = False
    logger.warning("⚠️ yfinance not installed — daily-close fallback disabled "
                   "(pip install yfinance to enable)")


# ==========================
# TELEGRAM HELPERS
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
            key_str = escape_markdown_v2(str(key))
            value_str = escape_markdown_v2(str(value))
            lines.append(f"  • *{key_str}:* `{value_str}`")

        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "MarkdownV2",
        }
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
        ("DHAN_CLIENT_ID", DHAN_CLIENT_ID),
        ("DHAN_PIN", DHAN_PIN),
        ("DHAN_TOTP_SECRET", DHAN_TOTP_SECRET),
        ("SPREADSHEET_ID", SPREADSHEET_ID),
        ("SERVICE_ACCOUNT_KEY_PATH", SERVICE_ACCOUNT_KEY_PATH),
    ]:
        if not val:
            missing.append(name)
    if missing:
        raise ValueError(f"❌ Missing ENV: {', '.join(missing)}")
    logger.info("✅ ENV OK")


def _f(value, default=0.0):
    """float() that tolerates blanks/garbage (mirrors db._f)."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


# ==========================
# TOKEN
# ==========================
def get_token():
    global CURRENT_TOKEN, TOKEN_EXPIRY

    if CURRENT_TOKEN and datetime.now(timezone.utc) < TOKEN_EXPIRY:
        return CURRENT_TOKEN

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        logger.info("🔑 Generating new token...")
        r = session.post(
            "https://auth.dhan.co/app/generateAccessToken",
            params={"dhanClientId": DHAN_CLIENT_ID, "pin": DHAN_PIN, "totp": totp},
            timeout=10,
        )
        data = r.json()
        if "accessToken" not in data:
            logger.error(f"❌ Token failed: {data}")
            return None
        CURRENT_TOKEN = data["accessToken"]
        TOKEN_EXPIRY = datetime.now(timezone.utc) + timedelta(hours=23)
        logger.info("✅ Token generated")
        return CURRENT_TOKEN
    except Exception as e:
        logger.error(f"❌ Token error: {e}")
        return None


# ==========================
# POSITIONS / HOLDINGS / FOREVER ORDERS
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return []
    try:
        r = session.get(
            "https://api.dhan.co/v2/positions",
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=10,
        )
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
        r = session.get(
            "https://api.dhan.co/v2/holdings",
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=10,
        )
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
        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=10,
        )
        data = r.json()
        logger.info(f"📊 Found {len(data) if isinstance(data, list) else 0} forever orders")
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"❌ Get forever orders failed: {e}")
        return []


# ==========================
# DAILY CLOSE  (Dhan historical → yfinance → None)   [6a — UNCHANGED]
# ==========================
def _dhan_daily_close(security_id, symbol, debug=False):
    """Settled daily close from Dhan historical. None on any failure."""
    try:
        token = get_token()
        if not token:
            return None

        now_ist = datetime.now(IST)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        if now_ist < market_close:
            trade_date = now_ist - timedelta(days=1)
        else:
            trade_date = now_ist

        from_date = trade_date.strftime("%Y-%m-%d")
        to_date = (trade_date + timedelta(days=1)).strftime("%Y-%m-%d")

        payload = {
            "securityId": int(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date,
        }
        r = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            json=payload,
            headers={"Content-Type": "application/json", "access-token": token},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            closes = data.get("close") or []
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
    """Daily close from yfinance for SYMBOL.NS. None on failure / unavailable."""
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
    """
    Strictly close-based: Dhan historical → yfinance → None.

    Returns (close_price, source) where source ∈ {"DHAN","YFINANCE"},
    or (None, "NONE") if BOTH sources fail. We deliberately do NOT fall
    back to intraday/LTP — a structural exit must act on a settled daily
    close only (design §3). A None close means "skip the exit decision
    this run", never "guess".
    """
    close = _dhan_daily_close(security_id, symbol)
    if close is not None:
        return close, "DHAN"

    close = _yf_daily_close(symbol)
    if close is not None:
        return close, "YFINANCE"

    logger.error(f"❌ {symbol} no daily close from Dhan or yfinance — "
                 f"skipping structural exit decision this run")
    return None, "NONE"


# ==============================================================
# CANDLE FETCH FOR ATR  (6b — NEW, separate from _dhan_daily_close)
# ==============================================================
def _dhan_daily_candles(security_id, symbol, sessions=ATR_FETCH_SESSIONS):
    """
    Fetch ~`sessions` daily OHLC candles from Dhan historical.

    Returns (highs, lows, closes) parallel lists (oldest→newest), or
    (None, None, None) on any failure. The calendar-day window is padded
    (~2x + 10) to clear weekends/holidays so we still net enough sessions.
    This is a SECOND historical call (6a's _dhan_daily_close is untouched),
    accepted deliberately to keep the exit decision on 6a's exact number.
    """
    try:
        token = get_token()
        if not token:
            return None, None, None

        now_ist = datetime.now(IST)
        market_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        if now_ist < market_close:
            end_date = now_ist - timedelta(days=1)
        else:
            end_date = now_ist

        start_date = end_date - timedelta(days=sessions * 2 + 10)

        payload = {
            "securityId": int(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "oi": False,
            "fromDate": start_date.strftime("%Y-%m-%d"),
            "toDate": (end_date + timedelta(days=1)).strftime("%Y-%m-%d"),
        }
        r = requests.post(
            "https://api.dhan.co/v2/charts/historical",
            json=payload,
            headers={"Content-Type": "application/json", "access-token": token},
            timeout=15,
        )
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

        # Defensive: align lengths (Dhan returns parallel arrays).
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
    """
    OHLC candles from yfinance (~1mo). Returns (highs, lows, closes)
    oldest→newest, or (None, None, None) on failure / unavailable.
    """
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
    """
    14-day ATR via simple mean of True Range (§19: flat mean is fine).

        TR_today = max(high − low,
                       |high − prev_close|,    # gap-up
                       |prev_close − low|)     # gap-down

    Needs ≥ period+1 candles (first TR needs a prior close). Returns a
    float ATR, or None if not enough data.
    """
    try:
        n = min(len(highs), len(lows), len(closes))
        if n < period + 1:
            return None
        highs, lows, closes = highs[-n:], lows[-n:], closes[-n:]

        trs = []
        for i in range(1, n):
            prev_close = closes[i - 1]
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - prev_close),
                abs(prev_close - lows[i]),
                )
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
    """
    Resolve 14-day ATR: Dhan candles → yfinance candles → None.

    Returns (atr: float|None, source: str), source ∈
    {"DHAN","YFINANCE","NONE"}. A None ATR tells the trail to use the
    5%-of-highest-close fallback gap (NOT to skip trailing).
    """
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

    logger.warning(f"⚠️ {symbol} ATR unavailable (Dhan+yfinance) — "
                   f"trail will use {int(FALLBACK_TRAIL_PCT*100)}% fallback gap")
    return None, "NONE"


# ==========================
# TICK HELPER
# ==========================
def _tick_for(symbol):
    """Resolve the tick size for a symbol via the shared util."""
    try:
        return tick_utils.get_tick_size(symbol)
    except Exception as e:
        logger.warning(f"⚠️ tick lookup failed for {symbol}: {e} — using 0.05")
        return 0.05


def _round_down(value, symbol):
    """Round an outbound SELL price DOWN to the symbol's tick grid."""
    tick = _tick_for(symbol)
    return tick_utils.round_to_tick(value, tick, mode="down")


# ==========================
# SAFETY_SL (−8%) PLACEMENT  — the dumb backstop   [6a — UNCHANGED]
# ==========================
def place_safety_sl(sec_id, qty, entry, symbol):
    """
    Place the −8% catastrophe forever-order (the Tier-1 backstop).

    Returns (ok: bool, order_id: str|None, safety_level: float|None).
    Trigger and limit are both rounded DOWN to the tick grid so the stop
    is never accidentally set higher than intended.
    """
    if not entry:
        logger.error(f"❌ {symbol} cannot place safety SL — no entry price")
        return False, None, None

    raw_trigger = entry * SAFETY_SL_PCT
    trigger = _round_down(raw_trigger, symbol)
    price = _round_down(trigger * SAFETY_LIMIT_OFFSET, symbol)

    logger.info(f"📤 Placing −8% safety SL: {symbol} | Trigger: {trigger} | Limit: {price}")

    send_telegram_alert("🛡️ PLACING −8% SAFETY SL", {
        "Symbol": symbol, "Qty": qty, "Trigger": trigger, "Limit": price,
    })

    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would place safety SL for {symbol}")
        return True, "DRYRUN_SAFETY_ID", trigger

    token = get_token()
    if not token:
        return False, None, None

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": str(sec_id),
        "quantity": int(qty),
        "price": price,
        "triggerPrice": trigger,
    }

    try:
        r = session.post(
            "https://api.dhan.co/v2/forever/orders",
            json=payload,
            headers={"access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=30,
        )
        if r.status_code in (200, 201):
            order_id = None
            try:
                order_id = r.json().get("orderId")
            except Exception:
                pass
            logger.info(f"✅ Safety SL placed: {symbol} (order {order_id})")
            send_telegram_alert("✅ −8% SAFETY SL PLACED", {
                "Symbol": symbol, "Qty": qty, "Trigger": trigger, "OrderID": order_id,
            })
            return True, order_id, trigger
        else:
            logger.error(f"❌ Place safety SL failed: HTTP {r.status_code} | {r.text}")
            send_telegram_alert("❌ −8% SAFETY SL FAILED", {
                "Symbol": symbol, "Status": f"HTTP {r.status_code}",
            })
            return False, None, None
    except Exception as e:
        logger.error(f"❌ Place safety SL exception: {e}")
        return False, None, None


# ==========================
# PATH B — CANCEL  (DELETE the forever-order)   [6a — UNCHANGED]
# ==========================
def cancel_forever_order(order_id, symbol):
    """DELETE a pending forever-order. Returns True on success."""
    logger.info(f"🗑️ Cancelling forever-order for {symbol} (id {order_id})")
    send_telegram_alert("🗑️ CANCELLING −8% SAFETY ORDER", {
        "Symbol": symbol, "OrderID": order_id,
    })

    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would DELETE forever-order {order_id}")
        return True

    token = get_token()
    if not token:
        logger.error("❌ No token for cancel")
        return False

    try:
        r = session.delete(
            f"https://api.dhan.co/v2/forever/orders/{order_id}",
            headers={"Accept": "application/json", "access-token": token},
            timeout=15,
        )
        if r.status_code in (200, 202):
            logger.info(f"✅ Forever-order cancelled: {symbol}")
            return True
        logger.error(f"❌ Cancel failed: HTTP {r.status_code} | {r.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Cancel exception: {e}")
        return False


# ==========================
# PATH B — PLACE  (regular MARKET sell)   [6a — UNCHANGED]
# ==========================
def place_market_sell(sec_id, qty, symbol):
    """
    Place a regular MARKET SELL (NOT a forever-order). MARKET orders carry
    no price/trigger, so there is nothing to tick-round here.

    Returns (ok: bool, order_id: str|None).
    """
    logger.info(f"📤 Placing MARKET SELL: {symbol} | Qty: {qty}")
    send_telegram_alert("📤 PLACING MARKET SELL (EXIT)", {
        "Symbol": symbol, "Qty": qty,
    })

    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would place MARKET SELL for {symbol}")
        return True, "DRYRUN_EXIT_ID"

    token = get_token()
    if not token:
        logger.error("❌ No token for market sell")
        return False, None

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "MARKET",
        "validity": "DAY",
        "securityId": str(sec_id),
        "quantity": int(qty),
        "price": "",
        "triggerPrice": "",
        "disclosedQuantity": "",
        "afterMarketOrder": False,
    }

    try:
        r = session.post(
            "https://api.dhan.co/v2/orders",
            json=payload,
            headers={"Content-Type": "application/json",
                     "access-token": token, "client-id": DHAN_CLIENT_ID},
            timeout=30,
        )
        if r.status_code in (200, 201):
            order_id = None
            try:
                order_id = r.json().get("orderId")
            except Exception:
                pass
            logger.info(f"✅ MARKET SELL placed: {symbol} (order {order_id})")
            return True, order_id
        logger.error(f"❌ MARKET SELL failed: HTTP {r.status_code} | {r.text}")
        return False, None
    except Exception as e:
        logger.error(f"❌ MARKET SELL exception: {e}")
        return False, None


# ==========================
# PATH B — CONFIRM  (poll GET /orders/{id})   [6a — UNCHANGED]
# ==========================
def confirm_fill(order_id, symbol):
    """
    Poll the order book until the order is TRADED (or dead, or timeout).

    Returns (status: str, exit_price: float|None) where status is one of:
        "FILLED"   — order traded; exit_price is the avg traded price
        "DEAD"     — order rejected/cancelled/expired (will never fill)
        "PENDING"  — still working after the poll budget (limbo)
    """
    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would poll fill for {order_id}")
        return "FILLED", None

    token = get_token()
    if not token:
        return "PENDING", None

    last_status = None
    for attempt in range(1, EXIT_POLL_ATTEMPTS + 1):
        try:
            r = session.get(
                f"https://api.dhan.co/v2/orders/{order_id}",
                headers={"Accept": "application/json",
                         "access-token": token, "client-id": DHAN_CLIENT_ID},
                timeout=15,
            )
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

    logger.warning(f"⚠️ {symbol} exit not confirmed after "
                   f"{EXIT_POLL_ATTEMPTS} polls (last={last_status})")
    return "PENDING", None


# ==========================
# REALIZED R-MULTIPLE  (logged, not stored — §11)   [6a — UNCHANGED]
# ==========================
def realized_r(entry, structural_sl, exit_price):
    """
    R = (exit − entry) / (entry − structural_SL).
    Risk per share is (entry − structural_SL). None if undefined.
    """
    try:
        risk = entry - structural_sl
        if not risk or risk <= 0 or exit_price is None:
            return None
        return round((exit_price - entry) / risk, 2)
    except Exception:
        return None


def realized_r_from_basis(exit_price, entry, risk_per_share):
    """
    R for the hybrid partial, using the IMMUTABLE risk basis derived from
    Target_Price (not the live Structural_SL, which trailing rewrites).
        R = (exit − entry) / risk_per_share
    None if risk is non-positive or exit missing.
    """
    try:
        if not risk_per_share or risk_per_share <= 0 or exit_price is None:
            return None
        return round((exit_price - entry) / risk_per_share, 2)
    except Exception:
        return None


# ==========================
# PATH B ORCHESTRATION  (the whole exit, end to end)   [6a — UNCHANGED]
# ==========================
def execute_structural_exit(trade, pos, close_price, safety_order):
    """
    Run the full cancel-and-replace exit for ONE position.

      1. (alert) structural exit detected
      2. cancel the −8% forever-order  (skip if none exists)
      3. place a fresh MARKET sell
      4. confirm fill
         • FILLED  → mark CLOSED, write exit cols, log realized R
         • PENDING → leave EXIT_PENDING, alert (sell is live, no re-protect)
         • DEAD / place-failed → re-place −8% safety, status OPEN, loud alert

    `safety_order`: the live forever-order dict for this sec_id, or None.
    Returns one of: "CLOSED", "EXIT_PENDING", "REPROTECTED", "ABORTED".

    NOTE (6b): qty here is read from Remaining_Qty, so a structural exit on
    a position that already took its 2R partial sells only the remainder.
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")

    entry = _f(trade.get("Entry_Price"))
    structural_sl = _f(trade.get("Structural_SL"))
    qty = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])

    r_est = realized_r(entry, structural_sl, close_price)

    logger.warning(f"🔴 STRUCTURAL EXIT: {symbol} close {close_price} < "
                   f"Structural_SL {structural_sl}")
    send_telegram_alert("🔴 STRUCTURAL EXIT DETECTED", {
        "Symbol": symbol,
        "Close": close_price,
        "Structural_SL": structural_sl,
        "Entry": entry,
        "Est_R_at_close": r_est,
        "Qty": qty,
    })

    safety_order_id = safety_order.get("orderId") if safety_order else None
    if safety_order_id:
        if not cancel_forever_order(safety_order_id, symbol):
            logger.error(f"❌ {symbol} cancel failed — aborting exit, "
                         f"position remains protected by existing −8%")
            send_telegram_alert("⚠️ EXIT ABORTED — CANCEL FAILED", {
                "Symbol": symbol,
                "OrderID": safety_order_id,
                "Status": "−8% safety still in place; will retry next run",
            })
            return "ABORTED"
    else:
        logger.info(f"ℹ️ {symbol} no −8% safety order to cancel "
                    f"(never placed) — placing fresh sell directly")

    ok, exit_order_id = place_market_sell(sec_id, qty, symbol)
    if not ok:
        return _reprotect_after_failed_exit(
            trade, pos, entry, qty, reason="market sell rejected at placement")

    status, fill_price = confirm_fill(exit_order_id, symbol)
    exit_price = fill_price if fill_price is not None else close_price
    now = datetime.now(timezone.utc).isoformat()

    if status == "FILLED":
        r_real = realized_r(entry, structural_sl, exit_price)
        db.update_trade(
            trade_id=trade_id,
            Status=db.STATUS_CLOSED,
            Exit_Price=exit_price,
            Exit_Time=now,
            Exit_Reason="structural",
            Exit_Order_ID=exit_order_id,
            Current_Price=exit_price,
        )
        logger.info(f"✅ {symbol} CLOSED @ {exit_price} (R={r_real})")
        send_telegram_alert("✅ EXIT CONFIRMED — CLOSED", {
            "Symbol": symbol,
            "Exit_Price": exit_price,
            "Realized_R": r_real,
            "Exit_Reason": "structural",
            "Exit_Order_ID": exit_order_id,
        })
        return "CLOSED"

    if status == "PENDING":
        db.update_trade(
            trade_id=trade_id,
            Status=db.STATUS_EXIT_PENDING,
            Exit_Order_ID=exit_order_id,
            Exit_Reason="structural",
        )
        logger.warning(f"⚠️ {symbol} exit not confirmed — left EXIT_PENDING")
        send_telegram_alert("⚠️ EXIT PLACED — NOT YET CONFIRMED", {
            "Symbol": symbol,
            "Exit_Order_ID": exit_order_id,
            "Status": "EXIT_PENDING — sell live, NOT re-protected; reconcile next run",
        })
        return "EXIT_PENDING"

    return _reprotect_after_failed_exit(
        trade, pos, entry, qty, reason=f"exit order {exit_order_id} dead")


def _reprotect_after_failed_exit(trade, pos, entry, qty, reason):
    """
    The exit sell failed and the position may be bare. Re-place the −8%
    safety, flip status back to OPEN, fire a LOUD alert. Never leave the
    position silently unprotected (§6).   [6a — UNCHANGED]
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")

    logger.error(f"🚨 {symbol} EXIT FAILED ({reason}) — re-placing −8% safety")

    ok, new_safety_id, safety_level = place_safety_sl(sec_id, qty, entry, symbol)

    updates = {"Status": db.STATUS_OPEN}
    if ok and safety_level is not None:
        updates["Safety_SL"] = safety_level
    db.update_trade(trade_id=trade_id, **updates)

    send_telegram_alert("🚨 EXIT FAILED — POSITION RE-PROTECTED", {
        "Symbol": symbol,
        "Reason": reason,
        "Re-protect": "OK" if ok else "FAILED — MANUAL ACTION NEEDED",
        "New_Safety_OrderID": new_safety_id,
        "Status": "OPEN (will retry exit next run)",
    })

    if not ok:
        send_telegram_alert("‼️‼️ UNPROTECTED POSITION — ACT NOW", {
            "Symbol": symbol,
            "Qty": qty,
            "Issue": "exit failed AND −8% re-placement failed",
        })

    return "REPROTECTED"


# ==============================================================
# R BASIS  (6b — from Target_Price, immutable across Phase-2 rewrite)
# ==============================================================
def compute_r_basis(trade):
    """
    Derive (risk_per_share, one_r_price, two_r_price) from Target_Price.

        risk        = (Target_Price − entry) / 2
        one_r_price = entry + risk
        two_r_price = Target_Price            (= entry + 2*risk)

    Returns None if Target_Price is blank/garbage or implies risk ≤ 0
    (caller then SKIPS trail+partial for that row — never guesses).
    """
    entry = _f(trade.get("Entry_Price"))
    target = _f(trade.get("Target_Price"))
    if entry <= 0 or target <= 0:
        return None
    risk = (target - entry) / 2.0
    if risk <= 0:
        return None
    return {
        "risk": risk,
        "one_r_price": entry + risk,
        "two_r_price": target,
    }


# ==============================================================
# SAFETY RECONCILE  (6b — after a partial: cancel-then-replace at new qty)
# ==============================================================
def reconcile_safety_qty(trade, pos, safety_order, new_qty, entry):
    """
    Reconcile the −8% safety order to `new_qty` after a partial sale.

    cancel-then-replace (design §5 decision): cancel the old full-qty
    forever-order, place a fresh one at new_qty. On a re-place FAILURE the
    remaining position is briefly bare → LOUD alert so the next run (or a
    human) re-protects. Mirrors 6a's exit philosophy.

    Returns (ok: bool, new_safety_level: float|None).
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]

    safety_order_id = safety_order.get("orderId") if safety_order else None

    if safety_order_id:
        if not cancel_forever_order(safety_order_id, symbol):
            # Couldn't cancel the OLD order. Don't place a second one (would
            # leave two live SELLs, old at full qty → oversell risk). Leave
            # the old order resting; alert; reconcile next run.
            logger.error(f"❌ {symbol} safety cancel failed during partial "
                         f"reconcile — leaving OLD −8% (full qty) in place")
            send_telegram_alert("⚠️ PARTIAL RECONCILE — CANCEL FAILED", {
                "Symbol": symbol,
                "OrderID": safety_order_id,
                "Issue": "old −8% (full qty) still resting; oversized vs remaining",
                "Action": "will retry reconcile next run",
            })
            return False, None
    else:
        logger.info(f"ℹ️ {symbol} no existing −8% to cancel during reconcile "
                    f"— placing fresh at new qty")

    ok, new_id, safety_level = place_safety_sl(sec_id, int(new_qty), entry, symbol)
    if ok:
        logger.info(f"✅ {symbol} −8% safety reconciled to qty {new_qty}")
        send_telegram_alert("✅ −8% SAFETY RECONCILED (POST-PARTIAL)", {
            "Symbol": symbol,
            "New_Qty": new_qty,
            "New_Safety_Level": safety_level,
            "New_Safety_OrderID": new_id,
        })
        return True, safety_level

    logger.error(f"🚨 {symbol} −8% re-place FAILED post-partial — "
                 f"remaining {new_qty} shares BARE")
    send_telegram_alert("🚨 POST-PARTIAL UNPROTECTED — ACT NOW", {
        "Symbol": symbol,
        "Remaining_Qty": new_qty,
        "Issue": "half sold, old −8% cancelled, re-place failed",
        "Action": "position OPEN + bare; re-protect next run or manually",
    })
    return False, None


# ==============================================================
# HYBRID TARGET  (6b — half at 2R; writes immediately, like exits)
# ==============================================================
def execute_partial_target(trade, pos, close_price, safety_order, r_basis):
    """
    Sell floor(Remaining_Qty/2) at MARKET on the first 2R close.

    GATED BY THE CALLER (status not PARTIAL/EXIT_PENDING/CLOSED AND
    Remaining_Qty == Qty AND close ≥ 2R). On a confirmed fill: write
    Remaining_Qty + log target partial + status PARTIAL FIRST, THEN
    reconcile the −8% to the new qty. Sheet reflects reality before the
    (riskier) broker reconcile, so a reconcile failure leaves correct
    position state + a loud "unprotected" alert.

    Returns (did_partial: bool, remaining_qty: int, new_safety_level: float|None).
    On any failure to sell, returns (False, full_qty, None) and leaves the
    row untouched (caller then just trails the full position this run).
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")

    entry = _f(trade.get("Entry_Price"))
    full_qty = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])

    sell_qty = full_qty // 2          # floor; odd 7 → sell 3, keep 4
    keep_qty = full_qty - sell_qty

    if sell_qty < 1:
        logger.info(f"ℹ️ {symbol} qty {full_qty} too small to halve — "
                    f"no partial, trailing whole position")
        return False, full_qty, None

    r_real_est = realized_r_from_basis(close_price, entry, r_basis["risk"])
    logger.warning(f"🎯 2R TARGET HIT: {symbol} close {close_price} ≥ "
                   f"2R {round(r_basis['two_r_price'],4)} | selling {sell_qty}/{full_qty}")
    send_telegram_alert("🎯 2R TARGET — SELLING HALF", {
        "Symbol": symbol,
        "Close": close_price,
        "Two_R_Price": round(r_basis["two_r_price"], 4),
        "Sell_Qty": sell_qty,
        "Keep_Qty": keep_qty,
        "Est_R": r_real_est,
    })

    # 1) Place the half MARKET sell + confirm (reuse 6a primitives).
    ok, exit_order_id = place_market_sell(sec_id, sell_qty, symbol)
    if not ok:
        logger.error(f"❌ {symbol} partial sell rejected at placement — "
                     f"no partial this run")
        send_telegram_alert("❌ 2R PARTIAL SELL REJECTED", {
            "Symbol": symbol,
            "Status": "no partial taken; full position intact + still protected",
        })
        return False, full_qty, None

    status, fill_price = confirm_fill(exit_order_id, symbol)
    exit_price = fill_price if fill_price is not None else close_price

    if status == "PENDING":
        # Half-sell LIVE but unconfirmed. Do NOT touch Remaining_Qty (sheet
        # would understate shares we may still hold) and do NOT reconcile the
        # −8% (old full-qty order still matches if the sell silently fails).
        # Leave qty/status; record the order id for the audit; reconcile next
        # run. Do not trail this run.
        logger.warning(f"⚠️ {symbol} partial sell unconfirmed — leaving qty "
                       f"untouched, reconcile next run")
        db.update_trade(
            trade_id=trade_id,
            Exit_Order_ID=exit_order_id,
            Exit_Reason="target_pending",
        )
        send_telegram_alert("⚠️ 2R PARTIAL — NOT YET CONFIRMED", {
            "Symbol": symbol,
            "Exit_Order_ID": exit_order_id,
            "Status": "sell live, qty NOT reduced; reconcile next run",
        })
        return False, full_qty, None

    if status == "DEAD":
        logger.error(f"❌ {symbol} partial sell DEAD ({exit_order_id}) — "
                     f"no partial, full position still protected")
        send_telegram_alert("❌ 2R PARTIAL SELL DEAD", {
            "Symbol": symbol,
            "Exit_Order_ID": exit_order_id,
            "Status": "full position intact + protected; retry next run",
        })
        return False, full_qty, None

    # status == "FILLED" → the half is sold.
    r_real = realized_r_from_basis(exit_price, entry, r_basis["risk"])
    now = datetime.now(timezone.utc).isoformat()

    # 2) Write sheet state FIRST (Remaining_Qty + target log + PARTIAL).
    db.update_trade(
        trade_id=trade_id,
        Status=db.STATUS_PARTIAL,
        Remaining_Qty=keep_qty,
        Exit_Price=exit_price,
        Exit_Time=now,
        Exit_Reason="target",
        Exit_Order_ID=exit_order_id,
    )
    logger.info(f"✅ {symbol} HALF SOLD @ {exit_price} (R={r_real}) | "
                f"Remaining_Qty {full_qty} → {keep_qty}")
    send_telegram_alert("✅ 2R PARTIAL FILLED — HALF BANKED", {
        "Symbol": symbol,
        "Sold_Qty": sell_qty,
        "Exit_Price": exit_price,
        "Realized_R_half": r_real,
        "Remaining_Qty": keep_qty,
        "Exit_Reason": "target",
    })

    # 3) Reconcile the −8% safety down to the kept quantity.
    ok_rec, new_safety_level = reconcile_safety_qty(
        trade, pos, safety_order, keep_qty, entry)

    return True, keep_qty, (new_safety_level if ok_rec else None)


# ==============================================================
# TRAIL  (6b — three-phase, close-based, ratchet-up-only, clamped)
# ==============================================================
def compute_trail_updates(trade, pos, close_price, atr, atr_source, r_basis):
    """
    Pure trail/phase computation (no I/O). Returns a dict of sheet field
    updates (subset of: Structural_SL, Highest_Close, Trail_Phase,
    Previous_SL_Price), or a dict with only Highest_Close / {} when nothing
    else changes.

    Phases (§4):
      P1 fixed     — close < +1R: Structural_SL unchanged.
      P2 breakeven — first close ≥ +1R: raise Structural_SL to entry.
      P3 trail     — subsequent new-high closes:
            gap        = 2.5×ATR  (or 0.05×highest_close if atr is None)
            trail_stop = highest_close − gap
            trail_stop = min(trail_stop, latest_close − one_tick)  # CLAMP
            Structural_SL = max(current_Structural_SL, trail_stop)

    Always records Previous_SL_Price = the pre-update Structural_SL when a
    change is made (audit of how the stop climbed).
    """
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
        # ----- Phase 1: fixed, no trailing -----
        new_phase = 1
    else:
        # ----- Phase 2: raise to breakeven (entry), once -----
        breakeven = _round_down(entry, symbol)
        if cur_sl < breakeven:
            new_sl = breakeven
            new_phase = max(cur_phase, 2)

        # ----- Phase 3: ATR (or 5% fallback) trail off highest close -----
        if atr is not None and atr > 0:
            gap = ATR_TRAIL_MULT * atr
        else:
            gap = FALLBACK_TRAIL_PCT * highest_close

        raw_trail = highest_close - gap

        # CLAMP: never sit at/above the latest close (else it's a disguised
        # "exit now" the moment it lands). Cap one tick below the close.
        tick = _tick_for(symbol)
        clamp_ceiling = close_price - tick
        trail_stop = min(raw_trail, clamp_ceiling)
        trail_stop = _round_down(trail_stop, symbol)

        # Ratchet UP only.
        if trail_stop > new_sl:
            new_sl = trail_stop
            new_phase = 3

        logger.info(f"   trail({atr_source}) {symbol}: "
                    f"highest_close={round(highest_close,4)} gap={round(gap,4)} "
                    f"raw={round(raw_trail,4)} clamp≤{round(clamp_ceiling,4)} "
                    f"→ cand={trail_stop} | cur_SL={cur_sl} new_SL={new_sl} "
                    f"phase={new_phase}")

    if new_sl != cur_sl:
        updates["Previous_SL_Price"] = cur_sl
        updates["Structural_SL"] = round(new_sl, 4)
    if new_phase != cur_phase:
        updates["Trail_Phase"] = new_phase

    return updates


# ==============================================================
# 6b ORCHESTRATOR — called from run() in place of the plain refresh
# ==============================================================
def manage_trail_and_target(trade, pos, close_price, safety_order):
    """
    Full 6b management for ONE non-exiting position on a real daily close.

    Order within a run (confirmed §phase ordering):
      0. R-basis guard (Target_Price). No basis → skip 6b, routine refresh.
      1. 2R partial (gated). Writes immediately (Remaining_Qty/PARTIAL/log).
      2. Phase/breakeven/trail on the (possibly reduced) remainder.

    Returns a dict of routine field updates to BATCH at end of run()
    (Current_Price/PnL/PnL_Percent plus any trail fields), carrying
    'trade_id'. The partial's own writes happen immediately inside
    execute_partial_target (rare, must be durable at once).
    """
    symbol = pos["symbol"]
    trade_id = trade.get("ID")
    status = str(trade.get("Status", "")).upper()
    partial_fired = False

    # 0) R basis or bust.
    r_basis = compute_r_basis(trade)
    if r_basis is None:
        logger.warning(f"   ↪ {symbol} no valid Target_Price-derived R basis — "
                       f"skipping trail/partial, routine refresh only")
        qty_full = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])
        pnl, pnl_pct = calculate_pnl(_f(trade.get("Entry_Price")), close_price, qty_full)
        return {
            "trade_id": trade_id,
            "Current_Price": close_price,
            "PnL": pnl,
            "PnL_Percent": pnl_pct,
            "Status": db.STATUS_OPEN if status == db.STATUS_PENDING else status,
        }

    qty_before = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])
    full_qty = int(_f(trade.get("Qty"), qty_before) or qty_before)
    remaining_qty = qty_before

    # 1) 2R partial — gate exactly per the confirmed idempotency rule.
    partial_allowed = (
            status not in (db.STATUS_PARTIAL, db.STATUS_EXIT_PENDING, db.STATUS_CLOSED)
            and qty_before == full_qty
            and close_price >= r_basis["two_r_price"]
    )
    if partial_allowed:
        did_partial, remaining_qty, _new_safety = execute_partial_target(
            trade, pos, close_price, safety_order, r_basis)
        if did_partial:
            partial_fired = True
            # Re-read so the trail math sees the just-written PARTIAL state
            # (Remaining_Qty, status). One extra read on the rare partial run.
            refreshed = db.get_trade(trade_id=trade_id)
            if refreshed:
                trade = refreshed
                status = str(trade.get("Status", "")).upper()

    # 2) ATR for the trail (Dhan → yfinance → None→5% fallback gap).
    atr, atr_source = get_atr(pos["securityId"], symbol)

    # 3) Phase/breakeven/trail on the remainder.
    trail_updates = compute_trail_updates(
        trade, pos, close_price, atr, atr_source, r_basis)

    # 4) Build the batched routine update. price/PnL on the REMAINING qty +
    #    any trail fields. The partial already wrote Remaining_Qty/status/exit
    #    cols immediately; do NOT overwrite those here.
    pnl, pnl_pct = calculate_pnl(
        _f(trade.get("Entry_Price")), close_price, int(remaining_qty))

    out = {
        "trade_id": trade_id,
        "Current_Price": close_price,
        "PnL": pnl,
        "PnL_Percent": pnl_pct,
        "_partial": partial_fired,
    }
    out.update(trail_updates)

    # Only manage Status here for non-partial rows. If a partial fired this
    # run, status is already PARTIAL on disk; don't touch it.
    if status != db.STATUS_PARTIAL:
        out["Status"] = db.STATUS_OPEN if status == db.STATUS_PENDING else status

    return out


# ==========================
# PnL  (for routine row refresh on non-exiting positions)   [6a — UNCHANGED]
# ==========================
def calculate_pnl(entry_price, current_price, qty):
    if not current_price or not entry_price:
        return 0, 0
    pnl = (current_price - entry_price) * qty
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    return round(pnl, 2), round(pnl_pct, 2)


# ==========================
# MAIN ENGINE  (step 6b)
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V18 (step 6b) — two-tier + close-exit + Path B "
                "+ three-phase trail + hybrid 2R partial")
    logger.info("=" * 80)

    validate_env()

    db.init_sheets()
    db.ensure_schema()

    trades = db.get_all_trades()
    logger.info(f"📊 Trades in sheet: {len(trades)}")

    positions = get_positions()
    holdings = get_holdings()
    forever = get_forever_orders()

    safety_map = {
        str(o["securityId"]): o
        for o in forever
        if o.get("transactionType") == "SELL"
           and str(o.get("orderStatus", "")).upper() in ("PENDING", "CONFIRM")
    }

    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 Positions/holdings: {len(all_pos)} | Safety orders: {len(safety_map)}")

    routine_updates = []
    exited = safety_placed = skipped_close = trailed = partials = 0

    for sec_id, pos in all_pos.items():
        symbol = pos["symbol"]
        logger.info(f"\n{'='*80}\n📍 {symbol} (Qty: {pos['qty']}, Avg: {pos['avgPrice']})")

        trade = db.get_trade(symbol=symbol, security_id=sec_id)
        if not trade:
            logger.info(f"   ↪ no matching sheet row for {symbol}; skipping")
            continue

        status = str(trade.get("Status", "")).upper()
        if status in (db.STATUS_CLOSED, db.STATUS_EXIT_PENDING):
            logger.info(f"   ↪ {symbol} status={status}; skipping")
            continue

        entry_price = _f(trade.get("Entry_Price"))
        structural_sl = _f(trade.get("Structural_SL"))
        safety_order = safety_map.get(sec_id)

        # ----- Two-tier: ensure the −8% backstop EXISTS (place once) -----
        # NOTE (6b): brand-new positions have no partial yet, so sizing off
        # pos["qty"] is correct here. Post-partial re-sizing is handled inside
        # the 6b partial/reconcile path off Remaining_Qty.
        if not safety_order:
            ok, new_id, safety_level = place_safety_sl(
                sec_id, int(pos["qty"]), entry_price, symbol)
            if ok:
                safety_placed += 1
                routine_updates.append({
                    "trade_id": trade.get("ID"),
                    "Safety_SL": safety_level,
                    "Status": db.STATUS_OPEN if status == db.STATUS_PENDING else status,
                })

        # ----- Close-based structural exit decision (6a) -----
        close_price, source = get_daily_close(sec_id, symbol)
        if close_price is None:
            skipped_close += 1
            logger.warning(f"   ↪ {symbol} no daily close; skipping exit + trail "
                           f"this run")
            continue

        logger.info(f"   Close({source}): {close_price} | "
                    f"Structural_SL: {structural_sl} | Entry: {entry_price}")

        if structural_sl > 0 and close_price < structural_sl:
            outcome = execute_structural_exit(trade, pos, close_price, safety_order)
            if outcome in ("CLOSED", "EXIT_PENDING", "REPROTECTED"):
                exited += 1
            # execute_structural_exit already wrote the row.
            continue

        # ----- No structural exit → 6b trail + hybrid target -----
        # (Replaces 6a's plain PnL refresh.) manage_trail_and_target returns
        # the routine update dict plus a private "_partial" flag we pop here
        # for the summary (not a sheet column, so it must not be batched).
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
    logger.info(f"✅ COMPLETED | Exits: {exited} | Partials: {partials} "
                f"| Trailed: {trailed} | Safety placed: {safety_placed} "
                f"| Close-skipped: {skipped_close}")
    logger.info(f"{'='*80}")

    send_telegram_alert("🚀 SL ENGINE V18 (6b) COMPLETED", {
        "Exits": exited,
        "Partials": partials,
        "Trailed": trailed,
        "Safety_placed": safety_placed,
        "Close_skipped": skipped_close,
        "Positions": len(all_pos),
    })


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()