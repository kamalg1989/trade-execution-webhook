# ==============================================================
# 🚀 SL ENGINE — V17 (step 6a)
#
# Trade Setup Enhancement v2 — §2 (two-tier stops), §3 (close-based
# structural exit), §6 (cancel-and-replace exit).
#
# WHAT CHANGED FROM V16
#   • Shared modules: imports tick_utils (tick rounding + security IDs)
#     and google_sheets_db (the consolidated data layer). All inline
#     gspread/sheet logic removed.
#   • Two-tier stops NO LONGER collapsed. The old
#         new_sl = max(current_sl, entry * 0.92)
#     is GONE. Structural_SL (from the sheet) is the operative stop;
#     Safety_SL (−8%) is a separate dumb broker backstop. Never max()'d.
#   • Close-based structural exit: exit fires ONLY when the daily CLOSE
#     < Structural_SL. Intraday wicks ignored. Daily close comes from
#     Dhan historical, falling back to yfinance, else we SKIP (never act
#     on an intraday/LTP number for a structural decision).
#   • Exit mechanism is Path B (cancel-and-replace), NOT modify-in-place:
#         DELETE forever-order  →  POST regular MARKET sell
#         →  poll GET /orders/{id} until TRADED  →  mark CLOSED
#     On sell/confirm FAILURE → re-place the −8% safety forever-order,
#     set status back to OPEN, fire a loud Telegram alert.
#   • Every price sent to Dhan is rounded with
#         tick_utils.round_to_tick(value, tick, mode="down")
#     at the payload-construction boundary. No more round(x, 2).
#   • Telegram alert on every Dhan order action (cancel / place / confirm
#     / fail / re-protect / initial safety placement).
#
# EXPLICITLY OUT OF SCOPE (step 6b, separate chat):
#   • Trailing (Phase 1/2/3), ATR, breakeven, hybrid half-at-2R partial.
#     This file places the −8% safety once and leaves it; it does not
#     move any stop. Target/trail logic is untouched here.
#
# DEPLOYMENT NOTE
#   • Requires yfinance on the VPS:  pip install yfinance
#     (used only as the daily-close fallback; if not installed the
#      fallback is skipped and the engine simply skips the exit decision
#      when Dhan-historical fails — it will not crash.)
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
# DAILY CLOSE  (Dhan historical → yfinance → None)
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
# SAFETY_SL (−8%) PLACEMENT  — the dumb backstop, placed once
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

    # Pre-action alert (touching Dhan).
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
# PATH B — CANCEL  (DELETE the forever-order)
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
        # Dhan returns 200/202 on a successful delete.
        if r.status_code in (200, 202):
            logger.info(f"✅ Forever-order cancelled: {symbol}")
            return True
        logger.error(f"❌ Cancel failed: HTTP {r.status_code} | {r.text}")
        return False
    except Exception as e:
        logger.error(f"❌ Cancel exception: {e}")
        return False


# ==========================
# PATH B — PLACE  (regular MARKET sell)
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
# PATH B — CONFIRM  (poll GET /orders/{id})
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
                # Dhan may return a dict or a single-element list.
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
# REALIZED R-MULTIPLE  (logged, not stored — §11)
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


# ==========================
# PATH B ORCHESTRATION  (the whole exit, end to end)
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
    """
    symbol = pos["symbol"]
    sec_id = pos["securityId"]
    trade_id = trade.get("ID")

    entry = _f(trade.get("Entry_Price"))
    structural_sl = _f(trade.get("Structural_SL"))
    # Full position in 6a; Remaining_Qty defaults to full qty via the data
    # layer, so this is already correct once 6b introduces partials.
    qty = int(_f(trade.get("Remaining_Qty"), pos["qty"]) or pos["qty"])

    r_est = realized_r(entry, structural_sl, close_price)  # estimate at close

    # 1) Detected (fired only now that we have a real close + located trade).
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

    # 2) Cancel the −8% safety order if one exists.
    safety_order_id = safety_order.get("orderId") if safety_order else None
    if safety_order_id:
        if not cancel_forever_order(safety_order_id, symbol):
            # Couldn't cancel → do NOT place a market sell (would orphan the
            # −8% order against a position we'd then be selling). Abort and
            # leave everything as-is; next run retries. Loud alert.
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

    # 3) Place the fresh MARKET sell.
    ok, exit_order_id = place_market_sell(sec_id, qty, symbol)
    if not ok:
        # Sell never accepted. Position is now bare (we deleted the −8% if it
        # existed). Re-protect immediately.
        return _reprotect_after_failed_exit(
            trade, pos, entry, qty, reason="market sell rejected at placement")

    # 4) Confirm the fill.
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
        # Sell is live but unconfirmed. Do NOT re-protect (would double-sell).
        # Leave EXIT_PENDING so a later run reconciles.
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

    # status == "DEAD" → order rejected/expired; position bare. Re-protect.
    return _reprotect_after_failed_exit(
        trade, pos, entry, qty, reason=f"exit order {exit_order_id} dead")


def _reprotect_after_failed_exit(trade, pos, entry, qty, reason):
    """
    The exit sell failed and the position may be bare. Re-place the −8%
    safety, flip status back to OPEN, fire a LOUD alert. Never leave the
    position silently unprotected (§6).
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
        # Worst case: couldn't even re-protect. Make it impossible to miss.
        send_telegram_alert("‼️‼️ UNPROTECTED POSITION — ACT NOW", {
            "Symbol": symbol,
            "Qty": qty,
            "Issue": "exit failed AND −8% re-placement failed",
        })

    return "REPROTECTED"


# ==========================
# PnL  (for the routine row refresh on non-exiting positions)
# ==========================
def calculate_pnl(entry_price, current_price, qty):
    if not current_price or not entry_price:
        return 0, 0
    pnl = (current_price - entry_price) * qty
    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    return round(pnl, 2), round(pnl_pct, 2)


# ==========================
# MAIN ENGINE  (step 6a)
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🚀 SL ENGINE V17 (step 6a) — two-tier + close-based exit + Path B")
    logger.info("=" * 80)

    validate_env()

    # Data layer: connect + ensure the 24-col schema once up front.
    db.init_sheets()
    db.ensure_schema()

    trades = db.get_all_trades()           # new-column blanks already defaulted
    logger.info(f"📊 Trades in sheet: {len(trades)}")

    positions = get_positions()
    holdings = get_holdings()
    forever = get_forever_orders()

    # Map sec_id → live resting SELL forever-order (the −8% safety).
    safety_map = {
        str(o["securityId"]): o
        for o in forever
        if o.get("transactionType") == "SELL"
           and str(o.get("orderStatus", "")).upper() in ("PENDING", "CONFIRM")
    }

    # Union of positions + holdings keyed by sec_id.
    all_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        all_pos.setdefault(h["securityId"], h)

    logger.info(f"📊 Positions/holdings: {len(all_pos)} | Safety orders: {len(safety_map)}")

    # Batched routine updates (PnL/price/status) for NON-exiting rows, so a
    # multi-row run is a single sheet write. Exits write immediately inside
    # execute_structural_exit (they're rare and need to be durable at once).
    routine_updates = []
    exited = safety_placed = skipped_close = 0

    for sec_id, pos in all_pos.items():
        symbol = pos["symbol"]
        logger.info(f"\n{'='*80}\n📍 {symbol} (Qty: {pos['qty']}, Avg: {pos['avgPrice']})")

        trade = db.get_trade(symbol=symbol, security_id=sec_id)
        if not trade:
            logger.info(f"   ↪ no matching sheet row for {symbol}; skipping")
            continue

        # Skip rows already terminal / in-flight.
        status = str(trade.get("Status", "")).upper()
        if status in (db.STATUS_CLOSED, db.STATUS_EXIT_PENDING):
            logger.info(f"   ↪ {symbol} status={status}; skipping")
            continue

        entry_price = _f(trade.get("Entry_Price"))
        structural_sl = _f(trade.get("Structural_SL"))
        safety_order = safety_map.get(sec_id)

        # ----- Two-tier: ensure the −8% backstop EXISTS (place once) -----
        # NOTE: we never max() Safety_SL with Structural_SL. They are
        # independent tracks. Structural_SL governs the exit; Safety_SL
        # is just the resting catastrophe order at the broker.
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

        # ----- Close-based structural exit decision -----
        close_price, source = get_daily_close(sec_id, symbol)
        if close_price is None:
            # Strictly close-based: no close → no exit decision this run.
            skipped_close += 1
            logger.warning(f"   ↪ {symbol} no daily close; skipping exit check, "
                           f"routine refresh only")
            # Still refresh PnL using entry as a neutral price (no LTP guess).
            continue

        logger.info(f"   Close({source}): {close_price} | "
                    f"Structural_SL: {structural_sl} | Entry: {entry_price}")

        if structural_sl > 0 and close_price < structural_sl:
            outcome = execute_structural_exit(trade, pos, close_price, safety_order)
            if outcome in ("CLOSED", "EXIT_PENDING", "REPROTECTED"):
                exited += 1
            # execute_structural_exit already wrote the row; don't queue a
            # routine update that could clobber the exit columns.
            continue

        # ----- No exit: routine PnL/price/status refresh (batched) -----
        unrealized_pnl, unrealized_pnl_pct = calculate_pnl(
            entry_price, close_price, int(pos["qty"]))
        routine_updates.append({
            "trade_id": trade.get("ID"),
            "Current_Price": close_price,
            "PnL": unrealized_pnl,
            "PnL_Percent": unrealized_pnl_pct,
            "Status": db.STATUS_OPEN if status == db.STATUS_PENDING else status,
        })

    # Single batched write for all routine (non-exit) updates.
    if routine_updates:
        # Collapse multiple dicts for the same trade_id into one (e.g. a
        # safety-placement update + a routine-refresh update on one row).
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
    logger.info(f"✅ COMPLETED | Exits: {exited} | Safety placed: {safety_placed} "
                f"| Close-skipped: {skipped_close}")
    logger.info(f"{'='*80}")

    send_telegram_alert("🚀 SL ENGINE V17 (6a) COMPLETED", {
        "Exits": exited,
        "Safety_placed": safety_placed,
        "Close_skipped": skipped_close,
        "Positions": len(all_pos),
    })


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    run()