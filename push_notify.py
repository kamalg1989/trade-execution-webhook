"""
push_notify.py — Web Push notifications (VAPID) for the mobile browser.

Three triggers use this module:
  1. ai_rank_candidates.py   -> top-picks summary once the AI ranking pass
                                (or its quant fallback) completes
  2. trade_journal.py        -> daily reconciliation summary (18:15 IST)
  3. sl_danger_monitor.py    -> real-time SL danger alerts during market hours

Single shared helper so all three send through the same subscription table
and the same pywebpush call. Never raises out of notify_all() - a push
failure must never break the screener/reconciliation/monitor it's attached to.
"""
import os
import json
import logging

import psycopg2
from pywebpush import webpush, WebPushException

logger = logging.getLogger(__name__)

DB_DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform")
VAPID_PRIVATE_KEY_PATH = os.getenv("VAPID_PRIVATE_KEY_PATH", "/root/trade-execution-webhook/vapid_private_key.pem")
VAPID_CLAIM_EMAIL = os.getenv("VAPID_CLAIM_EMAIL", "mailto:kamalprabakaran@gmail.com")


def _conn():
    return psycopg2.connect(DB_DSN, connect_timeout=5)


# ---------------------------------------------------------------------------
# Subscription storage
# ---------------------------------------------------------------------------
def save_subscription(endpoint, p256dh, auth):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO push_subscriptions (endpoint, p256dh, auth)
            VALUES (%s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE SET p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth
        """, (endpoint, p256dh, auth))


def remove_subscription(endpoint):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))


def get_subscriptions():
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT endpoint, p256dh, auth FROM push_subscriptions")
        return [{"endpoint": e, "keys": {"p256dh": p, "auth": a}} for e, p, a in cur.fetchall()]


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------
def send_push(subscription_info, title, body, url="/"):
    """Send to a single subscription. Returns True/False. Removes the
    subscription automatically if the browser reports it gone (404/410)."""
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=VAPID_PRIVATE_KEY_PATH,
            vapid_claims={"sub": VAPID_CLAIM_EMAIL},
        )
        return True
    except WebPushException as e:
        status = getattr(e.response, "status_code", None)
        if status in (404, 410):
            logger.info(f"Push subscription gone ({status}), removing: {subscription_info['endpoint'][:60]}...")
            try:
                remove_subscription(subscription_info["endpoint"])
            except Exception:
                pass
        else:
            logger.warning(f"Push send failed ({status}): {e}")
        return False
    except Exception as e:
        logger.warning(f"Push send failed: {e}")
        return False


def notify_all(title, body, url="/"):
    """Send to every stored subscription. Never raises."""
    try:
        subs = get_subscriptions()
    except Exception as e:
        logger.warning(f"notify_all: could not load subscriptions: {e}")
        return 0
    if not subs:
        logger.info("notify_all: no push subscriptions registered, skipping")
        return 0
    sent = 0
    for sub in subs:
        if send_push(sub, title, body, url):
            sent += 1
    logger.info(f"notify_all: sent to {sent}/{len(subs)} subscription(s) — \"{title}\"")
    return sent
