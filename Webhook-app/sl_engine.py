# ==============================================
# 🚀 SL ENGINE V13 — TELEGRAM ALERT ADDON
# Add this to your existing sl_engine.py
# ==============================================

# ========== ADD TO TOP (IMPORTS SECTION) ==========
# Add these imports at the very top of your sl_engine.py:

import os
from dotenv import load_dotenv

# Load these additional env variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DRY_RUN = os.getenv("SL_ENGINE_DRY_RUN", "false").lower() in ("true", "1", "yes")


# ========== ADD HELPER FUNCTION (after imports, before run()) ==========

def escape_markdown_v2(text):
    """Escape special characters for Telegram MarkdownV2"""
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    for ch in escape_chars:
        text = text.replace(ch, f"\\{ch}")
    return text


def send_telegram_alert(title, content_dict):
    """
    Send structured Telegram alert

    Args:
        title: Alert title (e.g., "🚀 SL PLACED")
        content_dict: Dict with key-value pairs to format
            Example: {
                "Symbol": "ONGC.NS",
                "Qty": "100",
                "Trigger": "450.50",
            }
    """
    if DRY_RUN:
        print(f"🔕 [DRY_RUN] Would send Telegram: {title}")
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️ Telegram not configured (missing token/chat_id)")
        return

    try:
        # Build message
        lines = [f"*{escape_markdown_v2(title)}*", ""]
        for key, value in content_dict.items():
            lines.append(f"  • *{escape_markdown_v2(str(key))}:* `{escape_markdown_v2(str(value))}`")

        message = "\n".join(lines)

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "MarkdownV2"
        }

        r = requests.post(url, data=payload, timeout=10)

        if r.status_code == 200:
            logger.info(f"✅ Telegram alert sent: {title}")
        else:
            logger.warning(f"⚠️ Telegram send failed ({r.status_code}): {r.text}")

    except Exception as e:
        logger.error(f"❌ Telegram error: {e}")


    # ========== REPLACE THE run() FUNCTION SUMMARY SECTION ==========
    # Find this section at the end of run():

    # ===== SUMMARY =====
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ SL ENGINE COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"   📊 SL Placed (new): {placed}")
    logger.info(f"   🔄 SL Modified (trailed): {modified}")
    logger.info(f"   🔴 SL Modified (exit): {marked_exit}")
    logger.info(f"   ➕ Stocks Inserted: {inserted}")
    logger.info(f"{'='*80}")


    # REPLACE WITH THIS (adds Telegram summary):

    # ===== SUMMARY =====
    logger.info(f"\n{'='*80}")
    logger.info(f"✅ SL ENGINE COMPLETED")
    logger.info(f"{'='*80}")
    logger.info(f"   📊 SL Placed (new): {placed}")
    logger.info(f"   🔄 SL Modified (trailed): {modified}")
    logger.info(f"   🔴 SL Modified (exit): {marked_exit}")
    logger.info(f"   ➕ Stocks Inserted: {inserted}")
    logger.info(f"{'='*80}")

    # ===== TELEGRAM SUMMARY ALERT =====
    summary_content = {
        "SL Placed (new)": placed,
        "SL Modified (trailed)": modified,
        "SL Modified (exit)": marked_exit,
        "Stocks Inserted": inserted,
        "Total Positions": len(all_pos),
        "Timestamp": datetime.now(timezone.utc).isoformat(),
    }
    send_telegram_alert("🚀 SL ENGINE DAILY RUN COMPLETED", summary_content)


# ========== REPLACE place_sl() FUNCTION ==========
# Find this function and replace it:

def place_sl(sec_id, qty, avg, symbol):
    """Place initial stop-loss order"""
    if not avg:
        logger.error(f"❌ Invalid avg price for {sec_id}")
        return False

    trigger = calculate_sl(avg, avg, None)
    price = round(trigger * 0.995, 2)

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "correlationId": str(uuid.uuid4())[:20],
        "orderFlag": "SINGLE",
        "transactionType": "SELL",
        "exchangeSegment": "NSE_EQ",
        "productType": "CNC",
        "orderType": "LIMIT",
        "validity": "DAY",
        "securityId": sec_id,
        "quantity": qty,
        "price": price,
        "triggerPrice": trigger
    }

    logger.info(f"📤 Placing SL → {symbol} | trigger={trigger} | price={price}")

    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for SL on {symbol}")
        return False

    r = session.post(
        "https://api.dhan.co/v2/forever/orders",
        json=payload,
        headers={
            "access-token": token,
            "client-id": DHAN_CLIENT_ID
        },
        timeout=30
    )

    logger.info(f"📡 SL Place status ({symbol}): {r.status_code}")

    if r.status_code in (200, 201):
        # ✅ SEND TELEGRAM ALERT
        alert_content = {
            "Symbol": symbol,
            "Quantity": qty,
            "Entry Price": avg,
            "Trigger Price": trigger,
            "Limit Price": price,
            "Status": "PENDING",
        }
        send_telegram_alert("📊 SL ORDER PLACED (NEW)", alert_content)
        return True
    else:
        logger.error(f"❌ Place SL failed for {symbol}: {r.text}")
        # Send failure alert
        alert_content = {
            "Symbol": symbol,
            "Quantity": qty,
            "Trigger": trigger,
            "Error": r.text[:100],
        }
        send_telegram_alert("❌ SL PLACEMENT FAILED", alert_content)
        return False


# ========== REPLACE modify_sl() FUNCTION ==========
# Find this function and replace it:

def modify_sl(order_id, qty, trigger, symbol):
    """Modify SL order with new trigger price"""
    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for modifying SL on {symbol}")
        return False

    price = round(trigger * 0.995, 2)
    disclosed_qty = max(1, int(qty * 0.3))

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "LIMIT",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "price": price,
        "triggerPrice": round(trigger, 2),
        "disclosedQuantity": disclosed_qty,
        "validity": "DAY"
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token
    }

    logger.info(f"🔄 Modifying SL for {symbol}: trigger={trigger}")

    r = session.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers=headers,
        timeout=15
    )

    logger.info(f"📡 SL Modify status ({symbol}): {r.status_code}")

    if r.status_code not in (200, 201):
        logger.error(f"❌ SL MODIFY FAILED for {symbol}")
        return False

    logger.info(f"✅ SL trailed for {symbol} to {trigger}")

    # ✅ SEND TELEGRAM ALERT FOR TRAILING
    alert_content = {
        "Symbol": symbol,
        "Quantity": qty,
        "New Trigger": trigger,
        "New Limit": price,
        "Order ID": order_id,
        "Action": "TRAILING",
    }
    send_telegram_alert("🔄 SL MODIFIED (TRAILING)", alert_content)

    return True


# ========== REPLACE modify_sl_for_exit() FUNCTION ==========
# Find this function and replace it:

def modify_sl_for_exit(order_id, qty, symbol):
    """Modify SL order to exit at market close price (set trigger to 0.01)"""
    token = get_token()
    if not token:
        logger.error(f"❌ Failed to get token for exit on {symbol}")
        return False

    # Set a very low trigger to ensure exit (market will hit this)
    trigger = 0.01
    price = 0.01

    payload = {
        "dhanClientId": DHAN_CLIENT_ID,
        "orderId": order_id,
        "orderFlag": "SINGLE",
        "orderType": "MARKET",
        "legName": "STOP_LOSS_LEG",
        "quantity": int(qty),
        "validity": "DAY"
    }

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "access-token": token
    }

    logger.info(f"🔴 EXITING {symbol} (Close < SL_Price)")

    r = session.put(
        f"https://api.dhan.co/v2/forever/orders/{order_id}",
        json=payload,
        headers=headers,
        timeout=15
    )

    logger.info(f"📡 Exit order status ({symbol}): {r.status_code}")

    if r.status_code not in (200, 201):
        logger.error(f"❌ Exit order FAILED for {symbol}: {r.text}")
        # Send failure alert
        alert_content = {
            "Symbol": symbol,
            "Quantity": qty,
            "Reason": "Close < SL_Price",
            "Error": r.text[:100],
        }
        send_telegram_alert("❌ EXIT ORDER FAILED", alert_content)
        return False

    logger.info(f"✅ Exit order placed for {symbol}")

    # ✅ SEND TELEGRAM ALERT FOR EXIT
    alert_content = {
        "Symbol": symbol,
        "Quantity": qty,
        "Order ID": order_id,
        "Action": "MARKET EXIT",
        "Reason": "Close < SL_Price",
        "Trigger": "0.01 (market)",
    }
    send_telegram_alert("🔴 SL MODIFIED (EXIT - CLOSE BELOW SL)", alert_content)

    return True


# ========== ADD TO .env FILE ==========
# Add these lines to your .env file:

"""
# Telegram Configuration for SL Engine
TELEGRAM_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
SL_ENGINE_DRY_RUN=false
"""

# ========== USAGE NOTES ==========
"""
1. Copy the imports section to the top of sl_engine.py
2. Add escape_markdown_v2() and send_telegram_alert() functions
3. Replace the three modified functions: place_sl(), modify_sl(), modify_sl_for_exit()
4. Update the run() summary section at the end
5. Add the env vars to your .env file
6. Test with SL_ENGINE_DRY_RUN=true first to see alerts without sending

Example alerts sent:
- 📊 SL ORDER PLACED (NEW) - when new SL is placed
- 🔄 SL MODIFIED (TRAILING) - when SL is trailed up
- 🔴 SL MODIFIED (EXIT - CLOSE BELOW SL) - when position is marked for exit
- 🚀 SL ENGINE DAILY RUN COMPLETED - summary at end of run
- ❌ error alerts - if any step fails

Alert Format:
All alerts use MarkdownV2 formatting with:
- Bold titles
- Inline code for values
- Bullet points for key details
"""