"""AI analysis endpoints. Mounted under /api in app/main.py."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date as date_t

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request, Response

from .. import config, outcomes, pipeline
from ..charting import render_chart, resample_weekly
from ..storage import AiRepo
from .models import AnalyzeRequest, FeedbackRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ai-analysis"])


def _repos(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(503, "DB not ready")
    ai_repo = getattr(request.app.state, "ai_repo", None)
    if ai_repo is None:
        ai_repo = AiRepo(repo.pool)
        request.app.state.ai_repo = ai_repo
    return repo, ai_repo


@router.post("/ai-analyze")
async def ai_analyze(req: AnalyzeRequest, request: Request):
    """Instant AI analysis of screener-shortlisted symbols (daily + weekly)."""
    if not config.ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")
    repo, ai_repo = _repos(request)
    try:
        result = await pipeline.analyze_symbols(
            symbols=req.symbols,
            indicator_date=req.indicatorDate,
            screener_repo=repo,
            ai_repo=ai_repo,
            gate_mode=req.gateMode,
            ifp_threshold=req.ifpThreshold,
            force=req.force,
            ai_mode=req.aiMode,
            chart_scope=req.chartScope,
        )
        # Auto-score outcomes in the background — for historical dates the
        # forward data already exists, so returns fill in immediately.
        asyncio.create_task(_score_outcomes_safe(repo.pool))
        return result
    except pipeline.BudgetExceeded as e:
        raise HTTPException(429, str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("ai-analyze failed")
        raise HTTPException(500, f"AI analysis failed: {e}")


@router.get("/ai-analyze/charts/{filename}")
async def get_chart(filename: str):
    """Serve stored chart PNGs (filename whitelist, no path traversal)."""
    png = AiRepo.read_chart(filename)
    if png is None:
        raise HTTPException(404, "Chart not found")
    return Response(
        content=png, media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def _score_outcomes_safe(pool):
    try:
        await outcomes.run(pool)
    except Exception:
        logger.exception("Background outcome scoring failed")


async def _forward_frame(pool, symbol: str, d: date_t) -> pd.DataFrame | None:
    """OHLCV window: ~9 months before the analysis date + ~3 months after."""
    rows = await pool.fetch(
        """
        SELECT time, open::float, high::float, low::float, close::float, volume::float
        FROM ohlcv_data
        WHERE symbol = $1
          AND time::date BETWEEN $2::date - INTERVAL '280 days'
                              AND $2::date + INTERVAL '95 days'
        ORDER BY time ASC
        """, symbol, d)
    if not rows:
        return None
    df = pd.DataFrame([dict(r) for r in rows])
    # strip tz so the vline marker (naive date) compares cleanly in mplfinance
    df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return df.set_index("time").sort_index()


async def _latest_result_row(pool, symbol: str, d: date_t):
    return await pool.fetchrow(
        """
        SELECT analysis, features, ret_5d, ret_20d, ret_60d, hit_breakout, hit_stop
        FROM ai_analysis_results
        WHERE symbol = $1 AND analysis_date = $2
        ORDER BY created_at DESC LIMIT 1
        """, symbol, d)


@router.get("/ai-analyze/aftermath/{symbol}")
async def aftermath(symbol: str, request: Request,
                    date: date_t = Query(...)):
    """What happened after an analysis: outcome numbers + forward-chart URLs."""
    repo, _ = _repos(request)
    symbol = symbol.upper()
    row = await _latest_result_row(repo.pool, symbol, date)
    if not row:
        raise HTTPException(404, "No analysis found for that symbol/date")

    df = await _forward_frame(repo.pool, symbol, date)
    if df is None:
        raise HTTPException(404, "No OHLCV data")
    forward = df[df.index.date > date]
    if forward.empty:
        return {"available": False, "reason": "No trading days after the analysis date yet"}

    analysis = row["analysis"]
    features = row["features"]
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    if isinstance(features, str):
        features = json.loads(features)
    bp = (analysis or {}).get("buy_point") or {}
    entry = ((features or {}).get("daily") or {}).get("close")

    # Prefer persisted outcomes; compute live for whatever is still null
    bars = [{"high": r.high, "low": r.low, "close": r.close}
            for r in forward.itertuples()]
    live = outcomes.compute_outcome(float(entry), bars,
                                    bp.get("breakout_level"), bp.get("stop_level")) \
        if entry else {}
    out = {
        "ret5d": row["ret_5d"] if row["ret_5d"] is not None else live.get("ret_5d"),
        "ret20d": row["ret_20d"] if row["ret_20d"] is not None else live.get("ret_20d"),
        "ret60d": row["ret_60d"] if row["ret_60d"] is not None else live.get("ret_60d"),
        "hitBreakout": row["hit_breakout"] if row["hit_breakout"] is not None else live.get("hit_breakout"),
        "hitStop": row["hit_stop"] if row["hit_stop"] is not None else live.get("hit_stop"),
        "forwardBars": len(forward),
    }
    q = f"symbol={symbol}&date={date}"
    return {
        "available": True,
        "outcome": out,
        "levels": {"breakout": bp.get("breakout_level"), "stop": bp.get("stop_level")},
        "charts": {
            "daily": f"/api/ai-analyze/aftermath-chart?{q}&timeframe=daily",
            "weekly": f"/api/ai-analyze/aftermath-chart?{q}&timeframe=weekly",
        },
    }


@router.get("/ai-analyze/aftermath-chart")
async def aftermath_chart(request: Request,
                          symbol: str = Query(...),
                          date: date_t = Query(...),
                          timeframe: str = Query("daily", pattern="^(daily|weekly)$")):
    """Live-rendered forward chart: history + ~3 months after the analysis date,
    with the analysis date marked and the AI's levels drawn across."""
    repo, _ = _repos(request)
    symbol = symbol.upper()
    row = await _latest_result_row(repo.pool, symbol, date)
    if not row:
        raise HTTPException(404, "No analysis found")
    df = await _forward_frame(repo.pool, symbol, date)
    if df is None or df.empty:
        raise HTTPException(404, "No OHLCV data")

    analysis = row["analysis"]
    if isinstance(analysis, str):
        analysis = json.loads(analysis)
    bp = (analysis or {}).get("buy_point") or {}
    levels = {"breakout": bp.get("breakout_level"), "stop": bp.get("stop_level")}

    frame = resample_weekly(df) if timeframe == "weekly" else df
    png = await asyncio.to_thread(
        render_chart, frame, symbol, timeframe, levels,
        1200, 700, pd.Timestamp(date), "  |  AFTERMATH")
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/ai-analyze/outcomes/summary")
async def outcomes_summary(request: Request):
    """Win rates + forward returns per model and recommendation.

    The data that answers 'is Haiku good enough?' and 'does SETUP_READY earn?'
    """
    repo, _ = _repos(request)
    rows = await repo.pool.fetch(
        """
        SELECT CASE
                 WHEN model LIKE 'claude-haiku%' THEN 'haiku'
                 WHEN model LIKE 'claude-sonnet%' THEN 'sonnet'
                 WHEN model LIKE 'gemini%' THEN 'gemini'
                 ELSE model
               END AS engine,
               recommendation,
               count(*) AS n,
               count(ret_20d) AS n_20d,
               round(avg(ret_5d)::numeric, 2) AS avg_ret_5d,
               round(avg(ret_20d)::numeric, 2) AS avg_ret_20d,
               round(avg(ret_60d)::numeric, 2) AS avg_ret_60d,
               round(100.0 * count(*) FILTER (WHERE ret_20d > 0) / NULLIF(count(ret_20d), 0)) AS win_rate_20d,
               round(100.0 * count(*) FILTER (WHERE hit_breakout) / NULLIF(count(hit_breakout), 0)) AS breakout_hit_pct,
               round(100.0 * count(*) FILTER (WHERE hit_stop) / NULLIF(count(hit_stop), 0)) AS stop_hit_pct,
               count(user_feedback) AS feedback_n,
               count(*) FILTER (WHERE user_feedback = 'CORRECT') AS feedback_correct
        FROM ai_analysis_results
        GROUP BY 1, 2
        ORDER BY 1, 2
        """)
    return {"summary": [dict(r) for r in rows]}


@router.post("/ai-analyze/feedback")
async def save_feedback(req: FeedbackRequest, request: Request):
    """Record CORRECT / PARTIAL / WRONG feedback — future accuracy dataset."""
    _, ai_repo = _repos(request)
    ok = await ai_repo.save_feedback(
        req.symbol.upper(), req.analysisDate, req.feedback, req.notes)
    if not ok:
        raise HTTPException(404, "No analysis found for that symbol/date")
    return {"saved": True}
