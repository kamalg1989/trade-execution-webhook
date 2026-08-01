#!/usr/bin/env python3
"""
sl_danger_monitor.py — real-time SL danger push alerts during market hours.

Runs every 10 minutes via sl-danger-monitor.timer (all day, every day - the
market-hours check below is what actually gates it, not the timer schedule,
so DST/holiday edge cases just mean occasional harmless no-ops rather than
missed alerts).

Reuses the already-computed zone/distanceToSL fields from the live
/api/sl-alerts endpoint (web_api/routers/sl_engine.py) rather than
re-implementing the SL-proximity logic. Sends at most one push per
security_id per calendar day per severity level reached (escalation-only:
a CRITICAL alert already sent today won't repeat, but a later DANGER on the
same symbol still fires since it's worse).
"""
import os
import sys
from datetime import datetime, time as dtime

import requests
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import push_notify

DB_DSN = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform")
SL_ALERTS_URL = "http://127.0.0.1:8004/api/sl-alerts"

MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

SEVERITY = {"WARNING": 0, "CRITICAL": 1, "DANGER": 2}


def _log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _is_market_hours():
    now = datetime.now()
    if now.weekday() >= 5:  # Sat/Sun
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _conn():
    return psycopg2.connect(DB_DSN, connect_timeout=5)


def _already_alerted_today(security_id, today, zone):
    """Return True if we've already sent an alert today at this severity or worse."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT zone FROM sl_danger_alerts_sent WHERE security_id = %s AND alert_date = %s",
                (str(security_id), today),
            )
            row = cur.fetchone()
            if not row:
                return False
            return SEVERITY.get(row[0], -1) >= SEVERITY.get(zone, -1)
    except Exception as e:
        _log(f"⚠️ dedup check failed for {security_id}: {e}")
        return False  # fail open — better a duplicate alert than a missed one


def _record_alert(security_id, today, zone):
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                INSERT INTO sl_danger_alerts_sent (security_id, alert_date, zone)
                VALUES (%s, %s, %s)
                ON CONFLICT (security_id, alert_date) DO UPDATE SET zone = EXCLUDED.zone, sent_at = NOW()
            """, (str(security_id), today, zone))
    except Exception as e:
        _log(f"⚠️ could not record alert for {security_id}: {e}")


def main():
    if not _is_market_hours():
        _log("outside market hours, skipping")
        return

    try:
        r = requests.get(SL_ALERTS_URL, timeout=30)
        r.raise_for_status()
        positions = r.json().get("positions", [])
    except Exception as e:
        _log(f"❌ could not fetch /api/sl-alerts: {e}")
        return

    today = datetime.now().date()
    alerted = 0
    for p in positions:
        zone = p.get("riskZone")
        if zone not in ("CRITICAL", "DANGER"):
            continue
        sec_id = p.get("securityId")
        if _already_alerted_today(sec_id, today, zone):
            continue

        symbol = str(p.get("symbol", "")).replace(".NS", "")
        current = p.get("current_price")
        sl_level = current - (current * (p.get("distanceToSL") or 0) / 100) if current is not None else None
        distance = p.get("distanceToSL")
        emoji = "🔴" if zone == "DANGER" else "⚠️"
        title = f"{emoji} {symbol}: {zone.title()} — SL proximity"
        body = (
            f"Current: ₹{current} | ~{distance}% from stop"
            if distance is not None else f"Current: ₹{current} — below structural SL on last close"
        )
        try:
            push_notify.notify_all(title, body, url="/")
            _record_alert(sec_id, today, zone)
            alerted += 1
            _log(f"alerted {symbol} ({zone})")
        except Exception as e:
            _log(f"⚠️ push failed for {symbol}: {e}")

    _log(f"done — {alerted} alert(s) sent, {len(positions)} position(s) checked")


if __name__ == "__main__":
    main()
