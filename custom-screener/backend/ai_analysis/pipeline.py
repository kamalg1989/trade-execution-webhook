"""Orchestrator: screener shortlist → features → gate → charts → AI → verify → store.

Instant mode only (parallel with semaphore). Per-symbol failures are isolated.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd

from . import config
from .ai import analyze_symbol_charts
from .charting import render_chart, resample_weekly
from .features import apply_gate, compute_features
from .storage import AiRepo
from .verification import verify_levels

logger = logging.getLogger(__name__)

# Fetch enough daily bars to build the weekly frame too (150w ≈ 750 daily bars)
FETCH_BARS = max(config.DAILY_BARS, config.WEEKLY_BARS * 5 + 30)


def _to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").sort_index()


async def analyze_symbols(
    symbols: list[str],
    indicator_date: date | None,
    screener_repo,
    ai_repo: AiRepo,
    gate_mode: str | None = None,
    ifp_threshold: float | None = None,
    force: bool = False,
) -> dict:
    if indicator_date is None:
        indicator_date = await screener_repo.latest_complete_date()

    symbols = [s.upper() for s in symbols]

    # 0. Store-first: reuse existing rows (same date + prompt_version + model)
    stored: dict[str, dict] = {}
    if not force:
        for sym in symbols:
            row = await ai_repo.get_result(sym, indicator_date)
            if row:
                stored[sym] = row
    todo = [s for s in symbols if s not in stored]

    # 1. OHLCV + features
    series = await screener_repo.ohlcv_tail(todo, indicator_date, FETCH_BARS) if todo else {}
    frames: dict[str, dict] = {}
    daily_feats_by_sym: dict[str, dict] = {}
    for sym in todo:
        rows = series.get(sym)
        if not rows:
            daily_feats_by_sym[sym] = {"error": "no_ohlcv"}
            continue
        ddf = _to_df(rows)
        wdf = resample_weekly(ddf).iloc[-config.WEEKLY_BARS:]
        ddf = ddf.iloc[-config.DAILY_BARS:]
        frames[sym] = {"daily": ddf, "weekly": wdf}
        daily_feats_by_sym[sym] = compute_features(ddf, "daily")

    # 2. Gate
    passed, gated = apply_gate(daily_feats_by_sym, gate_mode, ifp_threshold)

    # 3. Budget
    if passed and not await ai_repo.try_consume_budget(len(passed)):
        raise BudgetExceeded(
            f"Daily AI call cap ({config.AI_DAILY_CALL_CAP}) would be exceeded "
            f"({len(passed)} calls requested)")

    # 4. Analyze in parallel
    sem = asyncio.Semaphore(config.MAX_CONCURRENT_AI)

    async def _one(sym: str) -> dict:
        async with sem:
            try:
                return await _analyze_one(
                    sym, indicator_date, frames[sym],
                    daily_feats_by_sym[sym], ai_repo,
                    gate_mode or config.AI_GATE_MODE,
                )
            except Exception as e:
                logger.exception("AI analysis failed for %s", sym)
                return {"symbol": sym, "error": str(e)}

    analyzed = await asyncio.gather(*[_one(s) for s in passed]) if passed else []

    results = [_row_to_result(r, from_store=True) for r in stored.values()]
    results += list(analyzed)
    results.sort(key=lambda r: (r.get("analysis", {}).get("confidence") or 0), reverse=True)

    return {
        "indicatorDate": str(indicator_date),
        "gate": {
            "mode": gate_mode or config.AI_GATE_MODE,
            "threshold": ifp_threshold if ifp_threshold is not None else config.IFP_GATE_THRESHOLD,
            "in": len(symbols), "passed": len(passed) + len(stored), "gatedOut": len(gated),
        },
        "fromStore": len(stored),
        "analyzed": len([r for r in analyzed if not r.get("error")]),
        "gated": gated,
        "results": results,
    }


async def _analyze_one(sym: str, indicator_date: date, frames: dict,
                       daily_feats: dict, ai_repo: AiRepo, gate_mode: str) -> dict:
    ddf, wdf = frames["daily"], frames["weekly"]
    weekly_feats = compute_features(wdf, "weekly")

    # Clean charts (AI input) — rendering is CPU-bound; keep event loop free
    daily_png = await asyncio.to_thread(render_chart, ddf, sym, "daily")
    weekly_png = await asyncio.to_thread(render_chart, wdf, sym, "weekly")

    out = await analyze_symbol_charts(sym, daily_png, weekly_png, daily_feats, weekly_feats)
    analysis = out["analysis"]

    verification = verify_levels(analysis, daily_feats)

    # Annotated charts (user output) — AI levels + computed support
    bp = analysis.get("buy_point") or {}
    levels = {
        "breakout": bp.get("breakout_level"),
        "stop": bp.get("stop_level"),
        "support": daily_feats.get("support"),
    }
    daily_annot = await asyncio.to_thread(render_chart, ddf, sym, "daily", levels)
    weekly_annot = await asyncio.to_thread(render_chart, wdf, sym, "weekly", levels)

    names = {
        "daily": AiRepo.chart_filename(sym, indicator_date, "daily", "clean"),
        "weekly": AiRepo.chart_filename(sym, indicator_date, "weekly", "clean"),
        "daily_annotated": AiRepo.chart_filename(sym, indicator_date, "daily", "annot"),
        "weekly_annotated": AiRepo.chart_filename(sym, indicator_date, "weekly", "annot"),
    }
    AiRepo.write_chart(names["daily"], daily_png)
    AiRepo.write_chart(names["weekly"], weekly_png)
    AiRepo.write_chart(names["daily_annotated"], daily_annot)
    AiRepo.write_chart(names["weekly_annotated"], weekly_annot)

    features = {"daily": daily_feats, "weekly": weekly_feats}
    await ai_repo.save_result(
        symbol=sym, analysis_date=indicator_date, gate_mode=gate_mode,
        ifp_score=daily_feats.get("ifp_score"), features=features,
        analysis=analysis, verification=verification,
        recommendation=analysis.get("recommendation"),
        confidence=analysis.get("confidence"),
        chart_paths=names, processing_ms=out["processing_ms"],
    )

    return {
        "symbol": sym,
        "close": daily_feats.get("close"),
        "ifpScore": daily_feats.get("ifp_score"),
        "features": features,
        "analysis": analysis,
        "verification": verification,
        "charts": {k: f"/api/ai-analyze/charts/{v}" for k, v in names.items()},
        "fromStore": False,
    }


def _row_to_result(row: dict, from_store: bool) -> dict:
    charts = {}
    for key, col in (("daily", "chart_daily_path"), ("weekly", "chart_weekly_path"),
                     ("daily_annotated", "chart_daily_annotated_path"),
                     ("weekly_annotated", "chart_weekly_annotated_path")):
        if row.get(col):
            charts[key] = f"/api/ai-analyze/charts/{row[col]}"
    feats = row.get("features") or {}
    return {
        "symbol": row["symbol"],
        "close": (feats.get("daily") or {}).get("close"),
        "ifpScore": row.get("ifp_score"),
        "features": feats,
        "analysis": row.get("analysis"),
        "verification": row.get("verification"),
        "charts": charts,
        "fromStore": from_store,
    }


class BudgetExceeded(Exception):
    pass
