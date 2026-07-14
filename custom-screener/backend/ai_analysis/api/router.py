"""AI analysis endpoints. Mounted under /api in app/main.py."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response

from .. import config, pipeline
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
        return await pipeline.analyze_symbols(
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


@router.get("/ai-analyze/outcomes/summary")
async def outcomes_summary(request: Request):
    """Win rates + forward returns per model and recommendation.

    The data that answers 'is Haiku good enough?' and 'does SETUP_READY earn?'
    """
    repo, _ = _repos(request)
    rows = await repo.pool.fetch(
        """
        SELECT split_part(model, '-', 2) AS engine,
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
