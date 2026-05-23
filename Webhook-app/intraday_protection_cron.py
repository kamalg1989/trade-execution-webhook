# ==============================================================
# 🛡️ INTRADAY PROTECTION CRON  (build step 8 — FINAL)
#
# Trade Setup Enhancement v2 — §7 (close the first-day unprotected
# window). Runs on the VPS every 15 min during market hours.
#
# SINGLE RESPONSIBILITY
#   Find positions that have FILLED on Dhan but do NOT yet have a −8%
#   protective forever-order, and place that −8% backstop now. Nothing
#   else: no trailing, no structural exit, no partials, no PnL refresh,
#   no aggregate-risk check. That all lives in the after-hours SL engine.
#
# WHY THIS EXISTS
#   Day 1 (after hours): buy forever-order placed with entry as trigger.
#   Day 2 (market hours): price crosses trigger → BUY FILLS. Position now
#       held with NO protective stop until the after-hours SL run.
#   This cron shrinks that unprotected window from a full session to
#   ~15 min max by placing the −8% as soon as it sees the fill.
#
# DECISIONS (confirmed for this build)
#   1. Only place a safety SELL for positions that ACTUALLY EXIST on Dhan
#      (live positions ∪ holdings). Never place a naked SELL for a sheet
#      row that hasn't filled.
#   2. Size the safety order off Remaining_Qty when populated (a PARTIAL
#      position's correct size), else the live Dhan position qty.
#   3. SKIP EXIT_PENDING rows entirely — a market sell is already in flight
#      and the SL engine deliberately left it un-re-protected.
#   4. DRY_RUN supported via INTRADAY_CRON_DRY_RUN.
#
# IDEMPOTENCE
#   A position needs protection iff (Safety_SL blank in sheet) OR (no live
#   −8% SELL forever-order on Dhan). The Dhan existing-SELL check is the
#   real guard: even if Safety_SL is blank but the order exists, we skip —
#   no duplicate orders across repeated 15-min runs.
#
# PROTECTIVE → NEVER FAIL SILENTLY
#   Every failure path (open-trades read, Dhan API, sheet write) logs
#   loudly AND fires a Telegram alert.
#
# SHARED MODULES (build steps 1 & 2)
#   • google_sheets_db — single data layer (reads OPEN trades, writes back)
#   • tick_utils        — round_to_tick for valid on-grid order prices
#
# Token logic / Telegram / Dhan payloads mirror sl_engine.py VERBATIM so
# the −8% placed here is byte-identical to the after-hours path.
# ==============================================================

import os
import uuid
import logging
import requests
import pyotp
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# ---- Shared modules (build steps 1 & 2) ----------------------
import tick_utils
import google_sheets_db as db

# ==========================
# LOAD ENV  (same search order as sl_engine.py)
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
DRY_RUN = os.getenv("INTRADAY_CRON_DRY_RUN", "false").lower() in ("true", "1", "yes")

# −8% catastrophe backstop level (identical to sl_engine.py).
# Safety_SL trigger = entry * SAFETY_SL_PCT.
SAFETY_SL_PCT = 0.92
# Limit price for the resting safety SELL sits just under its trigger.
SAFETY_LIMIT_OFFSET = 0.995

# Forever-order statuses that mean "this SELL is live and protecting us".
LIVE_SAFETY_STATUSES = ("PENDING", "CONFIRM")

session = requests.Session()

# ==========================
# LOGGING
# ==========================
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

CURRENT_TOKEN = None
TOKEN_EXPIRY = datetime.now(timezone.utc)


# ==========================
# TELEGRAM HELPERS  (verbatim from sl_engine.py)
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
    """float() that tolerates blanks/garbage (mirrors db._f / sl_engine._f)."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


# ==========================
# TOKEN  (verbatim from sl_engine.py)
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
# POSITIONS / HOLDINGS / FOREVER ORDERS  (verbatim from sl_engine.py)
# ==========================
def get_positions():
    token = get_token()
    if not token:
        return None  # None = API failure (distinct from "no positions" = [])
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
        return None


def get_holdings():
    token = get_token()
    if not token:
        return None
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
        return None


def get_forever_orders():
    token = get_token()
    if not token:
        return None
    try:
        r = session.get(
            "https://api.dhan.co/v2/forever/orders",
            headers={"access-token": token},
            timeout=10,
        )
        data = r.json()
        n = len(data) if isinstance(data, list) else 0
        logger.info(f"📊 Found {n} forever orders")
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"❌ Get forever orders failed: {e}")
        return None


# ==========================
# TICK HELPERS  (mirror sl_engine.py)
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
# SAFETY_SL (−8%) PLACEMENT  (mirrors sl_engine.place_safety_sl)
# ==========================
def place_safety_sl(sec_id, qty, entry, symbol):
    """
    Place the −8% catastrophe forever-order (the Tier-1 backstop).

    Returns (ok: bool, order_id: str|None, safety_level: float|None).
    Trigger and limit are both rounded DOWN to the tick grid so the stop
    is never accidentally set higher than intended. Identical shape to the
    SL engine's placement so both paths produce byte-identical orders.
    """
    if not entry:
        logger.error(f"❌ {symbol} cannot place safety SL — no entry price")
        return False, None, None

    if not qty or int(qty) < 1:
        logger.error(f"❌ {symbol} cannot place safety SL — bad qty {qty}")
        return False, None, None

    raw_trigger = entry * SAFETY_SL_PCT
    trigger = _round_down(raw_trigger, symbol)
    price = _round_down(trigger * SAFETY_LIMIT_OFFSET, symbol)

    logger.info(f"📤 Placing −8% safety SL: {symbol} | Qty: {qty} | "
                f"Trigger: {trigger} | Limit: {price}")

    send_telegram_alert("🛡️ INTRADAY: PLACING −8% SAFETY SL", {
        "Symbol": symbol, "Qty": qty, "Trigger": trigger, "Limit": price,
    })

    if DRY_RUN:
        logger.info(f"🔕 [DRY_RUN] Would place safety SL for {symbol}")
        return True, "DRYRUN_SAFETY_ID", trigger

    token = get_token()
    if not token:
        logger.error(f"❌ {symbol} no token for safety SL placement")
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
            send_telegram_alert("✅ INTRADAY: −8% SAFETY SL PLACED", {
                "Symbol": symbol, "Qty": qty, "Trigger": trigger,
                "OrderID": order_id,
            })
            return True, order_id, trigger
        else:
            logger.error(f"❌ Place safety SL failed: HTTP {r.status_code} | {r.text}")
            send_telegram_alert("❌ INTRADAY: −8% SAFETY SL FAILED", {
                "Symbol": symbol, "Qty": qty, "Status": f"HTTP {r.status_code}",
                "Detail": (r.text or "")[:300],
            })
            return False, None, None
    except Exception as e:
        logger.error(f"❌ Place safety SL exception: {e}")
        send_telegram_alert("❌ INTRADAY: −8% SAFETY SL EXCEPTION", {
            "Symbol": symbol, "Qty": qty, "Error": str(e)[:300],
        })
        return False, None, None


# ==========================
# MAIN
# ==========================
def run():
    logger.info("=" * 80)
    logger.info("🛡️ INTRADAY PROTECTION CRON — place −8% on filled-but-unprotected")
    logger.info("=" * 80)

    validate_env()

    # ----- Data layer init (loud failure, never silent) -----
    try:
        db.init_sheets()
        db.ensure_schema()
    except Exception as e:
        logger.error(f"❌ Sheet init failed: {e}")
        send_telegram_alert("🚨 INTRADAY CRON ABORTED — SHEET INIT FAILED", {
            "Error": str(e)[:300],
            "Impact": "could not read trades; NO protection placed this run",
        })
        return

    # ----- Read OPEN-ish trades from the sheet -----
    # get_open_trades() returns everything not CLOSED and not PENDING,
    # i.e. OPEN ∪ PARTIAL ∪ EXIT_PENDING. We filter EXIT_PENDING below.
    try:
        open_trades = db.get_open_trades()
    except Exception as e:
        logger.error(f"❌ Failed to read open trades: {e}")
        send_telegram_alert("🚨 INTRADAY CRON ABORTED — OPEN-TRADES READ FAILED", {
            "Error": str(e)[:300],
            "Impact": "NO protection placed this run",
        })
        return

    logger.info(f"📊 OPEN-ish trades in sheet: {len(open_trades)}")
    if not open_trades:
        logger.info("✅ No open trades — nothing to protect. Done.")
        return

    # ----- Fetch live Dhan state ONCE per run -----
    positions = get_positions()
    holdings = get_holdings()
    forever = get_forever_orders()

    # Any of these being None means the Dhan API call failed. Without
    # reliable live state we cannot safely decide what to protect (could
    # place a naked SELL, or a duplicate). Abort loudly rather than guess.
    if positions is None or holdings is None or forever is None:
        logger.error("❌ Dhan API failure — cannot fetch positions/holdings/"
                     "forever-orders reliably; aborting to avoid bad orders")
        send_telegram_alert("🚨 INTRADAY CRON ABORTED — DHAN API FAILURE", {
            "Positions": "OK" if positions is not None else "FAILED",
            "Holdings": "OK" if holdings is not None else "FAILED",
            "ForeverOrders": "OK" if forever is not None else "FAILED",
            "Impact": "NO protection placed this run; will retry next 15-min run",
        })
        return

    # Build the live "actually filled on Dhan" map: positions ∪ holdings,
    # keyed by securityId. Positions take precedence on overlap.
    live_pos = {p["securityId"]: p for p in positions}
    for h in holdings:
        live_pos.setdefault(h["securityId"], h)

    # Map of live −8% SELL forever-orders, keyed by securityId. Same filter
    # as the SL engine's safety_map: SELL + status in PENDING/CONFIRM.
    safety_map = {
        str(o["securityId"]): o
        for o in forever
        if o.get("transactionType") == "SELL"
           and str(o.get("orderStatus", "")).upper() in LIVE_SAFETY_STATUSES
    }

    logger.info(f"📊 Live positions/holdings: {len(live_pos)} | "
                f"Live −8% SELL orders: {len(safety_map)}")

    placed = skipped_protected = skipped_not_filled = 0
    skipped_exit_pending = failed = 0

    for trade in open_trades:
        symbol = trade.get("Symbol", "")
        sec_id = str(trade.get("Security_ID", "")).strip()
        trade_id = trade.get("ID")
        status = str(trade.get("Status", "")).upper()

        logger.info(f"\n{'-'*70}\n📍 {symbol} (sec_id={sec_id}, status={status})")

        # (3) Skip EXIT_PENDING — a market sell is already in flight and the
        # SL engine deliberately left it un-re-protected. Placing a safety
        # SELL now would risk an oversell against the live exit order.
        if status == db.STATUS_EXIT_PENDING:
            logger.info(f"   ↪ {symbol} EXIT_PENDING — exit in flight; skipping")
            skipped_exit_pending += 1
            continue

        if not sec_id:
            logger.warning(f"   ↪ {symbol} missing Security_ID; cannot match "
                           f"to Dhan — skipping")
            skipped_not_filled += 1
            continue

        # (1) Only protect positions that ACTUALLY exist on Dhan. A sheet row
        # whose buy hasn't filled yet must never get a naked SELL.
        pos = live_pos.get(sec_id)
        if not pos:
            logger.info(f"   ↪ {symbol} not in live positions/holdings "
                        f"(buy not filled yet) — nothing to protect")
            skipped_not_filled += 1
            continue

        # Idempotence: protected already iff a live −8% SELL exists on Dhan.
        # This Dhan check is the real guard against duplicates — even if the
        # sheet's Safety_SL is blank, an existing order means we skip.
        has_live_safety = sec_id in safety_map
        safety_sl_in_sheet = trade.get("Safety_SL") not in (None, "")

        if has_live_safety:
            logger.info(f"   ↪ {symbol} already has a live −8% SELL on Dhan "
                        f"(order {safety_map[sec_id].get('orderId')}) — skipping")
            # Self-heal: if Dhan has the order but the sheet lost the
            # Safety_SL value, backfill it so the record stays truthful.
            if not safety_sl_in_sheet:
                _backfill_safety_sl(trade_id, symbol, trade, safety_map[sec_id])
            skipped_protected += 1
            continue

        # No live −8% on Dhan. (Per spec, "Safety_SL empty OR no order" →
        # place. The authoritative signal is the broker: no live order means
        # the position is genuinely unprotected, regardless of the sheet.)
        logger.warning(f"   🛡️ {symbol} FILLED but UNPROTECTED "
                       f"(Safety_SL_in_sheet={safety_sl_in_sheet}, "
                       f"no live −8% order) — placing now")

        # (2) Size off Remaining_Qty when populated (correct for a PARTIAL),
        # else the live Dhan position qty.
        remaining_qty = _f(trade.get("Remaining_Qty"), 0)
        if remaining_qty >= 1:
            qty = int(remaining_qty)
            qty_source = "Remaining_Qty"
        else:
            qty = int(_f(pos.get("qty"), 0))
            qty_source = "Dhan position qty"

        if qty < 1:
            logger.error(f"   ❌ {symbol} no usable qty "
                         f"(Remaining_Qty/{pos.get('qty')}) — cannot protect")
            send_telegram_alert("🚨 INTRADAY: CANNOT PROTECT — BAD QTY", {
                "Symbol": symbol, "Remaining_Qty": trade.get("Remaining_Qty"),
                "Dhan_Qty": pos.get("qty"),
                "Impact": "position may be unprotected; CHECK MANUALLY",
            })
            failed += 1
            continue

        entry = _f(trade.get("Entry_Price"))
        logger.info(f"   Placing −8% for {symbol}: qty={qty} ({qty_source}), "
                    f"entry={entry}")

        ok, order_id, safety_level = place_safety_sl(sec_id, qty, entry, symbol)

        if not ok:
            # place_safety_sl already logged + alerted on the failure.
            logger.error(f"   ❌ {symbol} −8% placement failed — still unprotected")
            failed += 1
            continue

        # (5) Persist the new Safety_SL + order id. A write failure here is
        # NOT silent: the order IS live on Dhan, but the sheet is now out of
        # sync, so alert loudly so it can be reconciled.
        try:
            wrote = db.update_trade(
                trade_id=trade_id,
                Safety_SL=safety_level,
                Dhan_Order_ID=order_id if order_id else trade.get("Dhan_Order_ID"),
            )
            if not wrote:
                raise RuntimeError("update_trade returned False")
            logger.info(f"   ✅ {symbol} sheet updated (Safety_SL={safety_level})")
            placed += 1
        except Exception as e:
            logger.error(f"   ❌ {symbol} sheet write failed after placing order: {e}")
            send_telegram_alert("⚠️ INTRADAY: ORDER PLACED BUT SHEET WRITE FAILED", {
                "Symbol": symbol,
                "Safety_Level": safety_level,
                "Safety_OrderID": order_id,
                "Error": str(e)[:300],
                "Action": "−8% IS live on Dhan; reconcile Safety_SL in sheet manually",
            })
            # The position IS protected on the broker, so count it as placed
            # for the protective summary; the alert flags the sheet drift.
            placed += 1

    # ----- Summary -----
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ INTRADAY CRON COMPLETE | Placed: {placed} | "
                f"Already-protected: {skipped_protected} | "
                f"Not-filled: {skipped_not_filled} | "
                f"Exit-pending: {skipped_exit_pending} | Failed: {failed}")
    logger.info(f"{'='*80}")

    # Only ping Telegram with a summary when something actually happened
    # (placed or failed) — avoid spamming every 15 min with "nothing to do".
    if placed or failed:
        send_telegram_alert("🛡️ INTRADAY PROTECTION CRON SUMMARY", {
            "Placed": placed,
            "Already_protected": skipped_protected,
            "Not_filled": skipped_not_filled,
            "Exit_pending": skipped_exit_pending,
            "Failed": failed,
        })


def _backfill_safety_sl(trade_id, symbol, trade, safety_order):
    """
    Dhan has a live −8% SELL but the sheet's Safety_SL is blank. Backfill
    the sheet from the order's triggerPrice (best-effort) so the record
    matches reality. Never places an order; purely a sheet correction.
    """
    try:
        trigger = safety_order.get("triggerPrice")
        level = _f(trigger, None)
        if level is None or level <= 0:
            # Fall back to the computed −8% level from entry if the order
            # didn't expose a usable trigger.
            entry = _f(trade.get("Entry_Price"))
            level = _round_down(entry * SAFETY_SL_PCT, symbol) if entry else None
        if level is None or level <= 0:
            logger.info(f"   ↪ {symbol} could not derive Safety_SL for backfill")
            return
        ok = db.update_trade(
            trade_id=trade_id,
            Safety_SL=level,
            Dhan_Order_ID=safety_order.get("orderId") or trade.get("Dhan_Order_ID"),
        )
        if ok:
            logger.info(f"   🩹 {symbol} backfilled Safety_SL={level} from live order")
        else:
            logger.warning(f"   ⚠️ {symbol} Safety_SL backfill write returned False")
    except Exception as e:
        logger.warning(f"   ⚠️ {symbol} Safety_SL backfill failed: {e}")


# ==========================
# ENTRY
# ==========================
if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        # Last-resort catch — a protective cron must never die quietly.
        logger.error(f"🚨 INTRADAY CRON CRASHED: {e}", exc_info=True)
        try:
            send_telegram_alert("🚨🚨 INTRADAY CRON CRASHED", {
                "Error": str(e)[:300],
                "Impact": "run aborted; positions may be unprotected until next run",
            })
        except Exception:
            pass