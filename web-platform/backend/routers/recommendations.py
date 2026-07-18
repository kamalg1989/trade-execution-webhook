"""
Stock Recommendations Router
Reads picks written by screen_gpt.py to latest_recommendations.json.
POST /recommendations/refresh triggers a new scan in the background.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import subprocess
import sys
import json
import logging
import os

router = APIRouter()
logger = logging.getLogger(__name__)

try:
    import dhan_client
except Exception as e:  # pragma: no cover
    dhan_client = None
    logger.warning(f"dhan_client unavailable in recommendations: {e}")


def _ownership_maps():
    """Sets of symbols (no .NS) already owned / in positions / with resting forever BUYs.
    Best-effort: any Dhan failure returns empty sets so recommendations still load."""
    held, pos, resting = {}, {}, set()
    if dhan_client is None:
        return held, pos, resting
    try:
        for h in dhan_client.get_holdings():
            qty = int(h.get("totalQty") or h.get("availableQty") or 0)
            if qty > 0:
                held[str(h.get("tradingSymbol", "")).replace(".NS", "").strip().upper()] = qty
    except Exception as e:
        logger.warning(f"holdings check failed: {e}")
    try:
        for p in dhan_client.get_positions():
            if str(p.get("positionType", "")).upper() == "LONG":
                qty = int(p.get("netQty") or 0)
                if qty > 0:
                    pos[str(p.get("tradingSymbol", "")).replace(".NS", "").strip().upper()] = qty
    except Exception as e:
        logger.warning(f"positions check failed: {e}")
    try:
        for o in dhan_client.get_forever_orders():
            if (str(o.get("transactionType", "")).upper() == "BUY"
                    and str(o.get("orderStatus", "")).upper() in ("PENDING", "CONFIRM", "TRIGGERED", "ACCEPTED")):
                resting.add(str(o.get("tradingSymbol", "")).replace(".NS", "").strip().upper())
    except Exception as e:
        logger.warning(f"forever-order check failed: {e}")
    return held, pos, resting

BASE_DIR = '/root/trade-execution-webhook'
RECS_FILE = os.path.join(BASE_DIR, 'latest_recommendations.json')
SCREENER = os.path.join(BASE_DIR, 'screen_gpt.py')
SCAN_LOG = os.path.join(BASE_DIR, 'screener_scan.log')
UPDATER = os.path.join(BASE_DIR, 'market_data_setup/scripts/update_ohlcv.py')
TODAY_UPDATER = os.path.join(BASE_DIR, 'market_data_setup/scripts/update_today.py')
UPDATE_LOG = os.path.join(BASE_DIR, 'market_data_setup/scripts/update.log')


class RecommendationsListResponse(BaseModel):
    stocks: List[dict]          # full pick objects (all screener fields passed through)
    generatedAt: str
    count: int
    stale: bool = False
    message: Optional[str] = None


def _read_recs_file():
    if not os.path.exists(RECS_FILE):
        return None
    try:
        with open(RECS_FILE) as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {RECS_FILE}: {e}")
        return None


def _is_scan_running():
    result = subprocess.run(['pgrep', '-f', 'screen_gpt.py'], capture_output=True, text=True)
    return result.returncode == 0


@router.get("/recommendations", response_model=RecommendationsListResponse)
async def get_recommendations():
    """Return latest screener picks from latest_recommendations.json"""
    data = _read_recs_file()

    if data is None:
        raise HTTPException(
            status_code=404,
            detail="No recommendations yet. Trigger a scan via POST /api/recommendations/refresh"
        )

    generated_at = data.get('generatedAt', '')
    stale = False
    try:
        gen_dt = datetime.fromisoformat(generated_at)
        stale = (datetime.now() - gen_dt).total_seconds() > 86400  # older than 24h
    except Exception:
        stale = True

    # Pass the full pick objects through (all screener fields), with safe defaults
    held, pos, resting = _ownership_maps()
    stocks = []
    for s in data.get('stocks', []):
        s = dict(s)
        s.setdefault('company', s.get('symbol', ''))
        s.setdefault('recommendedQty', 1)
        sym = str(s.get('symbol', '')).replace('.NS', '').strip().upper()
        s['heldQty'] = held.get(sym, 0)
        s['positionQty'] = pos.get(sym, 0)
        s['hasForeverBuy'] = sym in resting
        s['owned'] = bool(s['heldQty'] or s['positionQty'] or s['hasForeverBuy'])
        stocks.append(s)

    return RecommendationsListResponse(
        stocks=stocks,
        generatedAt=generated_at,
        count=len(stocks),
        stale=stale,
        message=data.get('message'),
    )


@router.post("/recommendations/refresh")
async def refresh_recommendations():
    """Trigger screen_gpt scan in the background (takes several minutes)"""
    if not os.path.exists(SCREENER):
        raise HTTPException(status_code=500, detail="screen_gpt.py not found on server")

    if _is_scan_running():
        return {"success": True, "status": "already_running",
                "message": "A scan is already in progress"}

    env = os.environ.copy()
    env['OHM_DRY_RUN'] = 'false'  # still send Telegram alerts; set true to disable

    with open(SCAN_LOG, 'a') as logf:
        logf.write(f"\n===== Scan triggered from web API at {datetime.now().isoformat()} =====\n")
        subprocess.Popen(
            ['python3', SCREENER],
            cwd=BASE_DIR,
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

    return {"success": True, "status": "started",
            "message": "Scan started. Results in a few minutes at GET /api/recommendations"}


@router.get("/recommendations/status")
async def scan_status():
    """Check whether a scan is currently running + last scan log tail"""
    running = _is_scan_running()
    log_tail = ""
    if os.path.exists(SCAN_LOG):
        try:
            with open(SCAN_LOG) as f:
                log_tail = ''.join(f.readlines()[-15:])
        except Exception:
            pass

    data = _read_recs_file()
    return {
        "scanRunning": running,
        "lastGeneratedAt": data.get('generatedAt') if data else None,
        "lastCount": data.get('count') if data else None,
        "logTail": log_tail,
    }


def _is_updater_running():
    r = subprocess.run(['pgrep', '-f', 'update_ohlcv.py|update_today.py'],
                       capture_output=True)
    return r.returncode == 0


@router.post("/data/update")
async def update_market_data():
    """
    Pull the latest candles into the local DB (background):
      1. update_ohlcv.py  — fills any missing *historical* daily candles (all symbols)
      2. update_today.py  — aggregates *today's* intraday into today's daily candle
                            for the NIFTY-500 (historical EOD lags a day)
    """
    if not os.path.exists(UPDATER):
        raise HTTPException(status_code=500, detail="update_ohlcv.py not found on server")
    if _is_updater_running():
        return {"success": True, "status": "already_running",
                "message": "A data update is already in progress"}

    # Chain both scripts in one background shell. sys.executable = web API's venv
    # python (has asyncpg + psycopg2); it also has requests/pyotp.
    py = sys.executable
    cmd = (
        f'echo "===== Data update triggered from web at {datetime.now().isoformat()} =====" ; '
        f'"{py}" "{UPDATER}" --days 7 ; '
        f'"{py}" "{TODAY_UPDATER}"'
    )
    with open(UPDATE_LOG, 'a') as logf:
        subprocess.Popen(['bash', '-c', cmd], cwd=BASE_DIR,
                         stdout=logf, stderr=subprocess.STDOUT, start_new_session=True)

    return {"success": True, "status": "started",
            "message": "Data update started — historical + today's candle. Takes ~2-4 min; then check the DB date."}


@router.get("/data/update-status")
async def update_data_status():
    """Whether a data update is running + last log lines."""
    tail = ""
    if os.path.exists(UPDATE_LOG):
        try:
            with open(UPDATE_LOG) as f:
                tail = ''.join(f.readlines()[-12:])
        except Exception:
            pass
    return {"updating": _is_updater_running(), "logTail": tail}


@router.get("/data-status")
async def data_status():
    """DB freshness + last scan info for the dashboard header."""
    db_latest = None
    try:
        import psycopg2
        conn = psycopg2.connect(
            host=os.getenv("MD_DB_HOST", "localhost"),
            port=int(os.getenv("MD_DB_PORT", "5432")),
            dbname=os.getenv("MD_DB_NAME", "market_data"),
            user=os.getenv("MD_DB_USER", "market_data_user"),
            password=os.getenv("MD_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
            connect_timeout=5,
        )
        cur = conn.cursor()
        cur.execute("SELECT MAX(time)::date FROM ohlcv_data")
        row = cur.fetchone()
        db_latest = str(row[0]) if row and row[0] else None
        conn.close()
    except Exception as e:
        logger.warning(f"data-status DB query failed: {e}")

    data = _read_recs_file() or {}
    stocks = data.get("stocks", [])
    signal_bar = stocks[0].get("signalBarDate") if stocks else None

    return {
        "dbLatestCandle": db_latest,               # last day present in the OHLCV DB
        "recsGeneratedAt": data.get("generatedAt"),
        "signalBarDate": signal_bar,               # last candle the screener acted on
        "regime": data.get("regime"),
        "count": data.get("count"),
        "scanRunning": _is_scan_running(),
    }
