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
import json
import logging
import os

router = APIRouter()
logger = logging.getLogger(__name__)

BASE_DIR = '/root/trade-execution-webhook'
RECS_FILE = os.path.join(BASE_DIR, 'latest_recommendations.json')
SCREENER = os.path.join(BASE_DIR, 'screen_gpt.py')
SCAN_LOG = os.path.join(BASE_DIR, 'screener_scan.log')


class RecommendationResponse(BaseModel):
    symbol: str
    company: str
    currentPrice: float
    change: float
    target: float
    stopLoss: float
    confidence: int
    reason: str
    recommendedQty: int


class RecommendationsListResponse(BaseModel):
    stocks: List[RecommendationResponse]
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

    stocks = [
        RecommendationResponse(
            symbol=s.get('symbol', ''),
            company=s.get('company', s.get('symbol', '')),
            currentPrice=float(s.get('currentPrice', 0)),
            change=float(s.get('change', 0)),
            target=float(s.get('target', 0)),
            stopLoss=float(s.get('stopLoss', 0)),
            confidence=int(s.get('confidence', 70)),
            reason=s.get('reason', ''),
            recommendedQty=int(s.get('recommendedQty', 1)),
        )
        for s in data.get('stocks', [])
    ]

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


@router.get("/recommendations-mock", response_model=RecommendationsListResponse)
async def get_recommendations_mock():
    """Mock recommendations for UI testing"""
    return RecommendationsListResponse(
        stocks=[
            RecommendationResponse(symbol="RELIANCE", company="Reliance Industries", currentPrice=2850.50, change=2.5, target=3100.0, stopLoss=2700.0, confidence=85, reason="Strong bullish divergence on daily RSI with breakout above 200-DMA", recommendedQty=5),
            RecommendationResponse(symbol="TCS", company="Tata Consultancy Services", currentPrice=3920.0, change=-1.2, target=4200.0, stopLoss=3750.0, confidence=78, reason="Cup and handle formation with institutional accumulation", recommendedQty=3),
            RecommendationResponse(symbol="INFY", company="Infosys", currentPrice=1820.75, change=1.8, target=2050.0, stopLoss=1750.0, confidence=72, reason="Golden cross on weekly chart with positive macro setup", recommendedQty=4),
            RecommendationResponse(symbol="WIPRO", company="Wipro", currentPrice=385.50, change=-0.5, target=450.0, stopLoss=360.0, confidence=68, reason="Consolidation breakout with increasing volume", recommendedQty=10),
            RecommendationResponse(symbol="HDFCBANK", company="HDFC Bank", currentPrice=1620.30, change=3.2, target=1850.0, stopLoss=1550.0, confidence=82, reason="Support bounce with sector rotation into financials", recommendedQty=6),
        ],
        generatedAt=datetime.utcnow().isoformat(),
        count=5,
    )
