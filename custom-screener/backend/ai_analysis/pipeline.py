"""Orchestrator: screener shortlist → features → gate → charts → AI → verify → store.

Instant mode only (parallel with semaphore). Per-symbol failures are isolated.

ai_mode:  gemini (default, cheapest) | haiku (cheap scan) | sonnet (best judgment)
          | hybrid (Haiku scans all, Sonnet re-analyzes anything Haiku rates
          SETUP_READY / EARLY_STAGE — Gemini is not part of the hybrid chain).
chart_scope: daily (default, single chart, ~40% cheaper, weaker base counting)
          | both (daily + weekly).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date

import pandas as pd

from . import config
from .ai import analyze_symbol_charts, analyze_symbol_charts_gemini
from .charting import render_chart, resample_weekly
from .features import apply_gate, compute_features
from .storage import AiRepo
from .verification import verify_levels

logger = logging.getLogger(__name__)

# Fetch enough daily bars to build the weekly frame too (150w ≈ 750 daily bars)
FETCH_BARS = max(config.DAILY_BARS, config.WEEKLY_BARS * 5 + 30)

CONFIRM_RECS = {"SETUP_READY", "EARLY_STAGE"}   # hybrid: these get a Sonnet pass


class BudgetExceeded(Exception):
    pass


def _to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").sort_index()


def _resolve_pv(prompt_version: str | None, scope: str) -> tuple[str, str]:
    """Returns (pv_key_for_store, effective_scope). v3 is always daily-only."""
    pv = (prompt_version or config.PROMPT_VERSION).lower()
    if pv.startswith("v3"):
        return "v3", "daily"
    return ("v2" if scope == "both" else "v2-d"), scope


async def analyze_symbols(
    symbols: list[str],
    indicator_date: date | None,
    screener_repo,
    ai_repo: AiRepo,
    gate_mode: str | None = None,
    ifp_threshold: float | None = None,
    force: bool = False,
    ai_mode: str | None = None,
    chart_scope: str | None = None,
    prompt_version: str | None = None,
    persist_charts: bool = True,
) -> dict:
    if indicator_date is None:
        indicator_date = await screener_repo.latest_complete_date()

    symbols = [s.upper() for s in symbols]
    mode = (ai_mode or config.AI_MODE).lower()
    scope = (chart_scope or "both").lower()
    pv, scope = _resolve_pv(prompt_version, scope)

    # 0. Store-first: reuse existing rows (same date + prompt_version + model).
    # Single-model modes (gemini/sonnet/haiku) resolve to exactly one lookup
    # per symbol, so batch it into one query instead of N sequential round
    # trips per candidate — this ran as a plain for-loop over every candidate
    # every day for the life of a backtest run before this. hybrid mode needs
    # the two-tier sonnet-then-haiku fallback in _lookup_stored() per symbol,
    # so it keeps the per-symbol path.
    stored: dict[str, dict] = {}
    if not force and mode != "hybrid" and hasattr(ai_repo, "get_results_batch"):
        model_for_lookup = {"sonnet": config.SONNET_MODEL, "haiku": config.HAIKU_MODEL}.get(
            mode, config.GEMINI_MODEL)
        stored = await ai_repo.get_results_batch(symbols, indicator_date, pv, model_for_lookup)
    elif not force:
        for sym in symbols:
            row = await _lookup_stored(ai_repo, sym, indicator_date, mode, pv)
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
        wdf = resample_weekly(ddf).iloc[-config.WEEKLY_BARS:] if scope == "both" else None
        ddf = ddf.iloc[-config.DAILY_BARS:]
        frames[sym] = {"daily": ddf, "weekly": wdf}
        daily_feats_by_sym[sym] = compute_features(ddf, "daily")

    # 2. Gate
    passed, gated = apply_gate(daily_feats_by_sym, gate_mode, ifp_threshold)

    # 3. Budget (hybrid confirmations consume extra, checked per-call below)
    if passed and not await ai_repo.try_consume_budget(len(passed)):
        raise BudgetExceeded(
            f"Daily AI call cap ({config.AI_DAILY_CALL_CAP}) would be exceeded "
            f"({len(passed)} calls requested)")

    # 4. Analyze in parallel. Two separate semaphores on purpose: `sem` caps
    # how many symbols are in flight overall (mostly waiting on the Gemini
    # network call — cheap to parallelize), while `render_sem` independently
    # caps concurrent matplotlib chart rendering (RAM-heavy — must stay low
    # on a small VPS). This lets MAX_CONCURRENT_AI go up for throughput
    # without raising the actual OOM risk, since renders still queue behind
    # MAX_CONCURRENT_RENDER regardless of how many symbols are "in flight".
    sem = asyncio.Semaphore(config.MAX_CONCURRENT_AI)
    render_sem = asyncio.Semaphore(config.MAX_CONCURRENT_RENDER)

    async def _one(sym: str) -> dict:
        async with sem:
            try:
                return await _analyze_one(
                    sym, indicator_date, frames[sym], daily_feats_by_sym[sym],
                    ai_repo, gate_mode or config.AI_GATE_MODE, mode, scope, pv, render_sem,
                    persist_charts)
            except Exception as e:
                logger.exception("AI analysis failed for %s", sym)
                return {"symbol": sym, "error": str(e)}

    analyzed = await asyncio.gather(*[_one(s) for s in passed]) if passed else []

    results = [_row_to_result(r, from_store=True) for r in stored.values()]
    results += list(analyzed)
    results.sort(key=lambda r: (r.get("analysis", {}).get("confidence") or 0), reverse=True)

    return {
        "indicatorDate": str(indicator_date),
        "aiMode": mode,
        "chartScope": scope,
        "gate": {
            "mode": gate_mode or config.AI_GATE_MODE,
            "threshold": ifp_threshold if ifp_threshold is not None else config.IFP_GATE_THRESHOLD,
            "in": len(symbols), "passed": len(passed) + len(stored), "gatedOut": len(gated),
        },
        "promptVersion": pv,
        "fromStore": len(stored),
        "analyzed": len([r for r in analyzed if not r.get("error")]),
        "gated": gated,
        "results": results,
    }


async def _lookup_stored(ai_repo: AiRepo, sym: str, d: date, mode: str, pv: str) -> dict | None:
    """Mode-aware store lookup."""
    if mode == "gemini":
        return await ai_repo.get_result(sym, d, pv, config.GEMINI_MODEL)
    if mode == "sonnet":
        return await ai_repo.get_result(sym, d, pv, config.SONNET_MODEL)
    if mode == "haiku":
        return await ai_repo.get_result(sym, d, pv, config.HAIKU_MODEL)
    # hybrid: prefer a Sonnet confirmation; else a Haiku row that didn't need one
    row = await ai_repo.get_result(sym, d, pv, config.SONNET_MODEL)
    if row:
        return row
    row = await ai_repo.get_result(sym, d, pv, config.HAIKU_MODEL)
    if row and row.get("recommendation") not in CONFIRM_RECS:
        return row
    return None


async def _render(render_sem: asyncio.Semaphore, *args, **kwargs):
    """Chart render, gated by render_sem — see analyze_symbols for why this
    is a separate semaphore from the overall per-symbol one."""
    async with render_sem:
        return await asyncio.to_thread(render_chart, *args, **kwargs)


async def _analyze_one(sym: str, indicator_date: date, frames: dict, daily_feats: dict,
                       ai_repo: AiRepo, gate_mode: str, mode: str, scope: str, pv: str,
                       render_sem: asyncio.Semaphore, persist_charts: bool = True) -> dict:
    ddf, wdf = frames["daily"], frames["weekly"]
    weekly_feats = compute_features(wdf, "weekly") if wdf is not None else None

    daily_png = await _render(render_sem, ddf, sym, "daily")
    weekly_png = (await _render(render_sem, wdf, sym, "weekly")
                  if wdf is not None else None)

    if mode == "gemini":
        out = await analyze_symbol_charts_gemini(sym, daily_png, weekly_png,
                                                 daily_feats, weekly_feats,
                                                 model=config.GEMINI_MODEL,
                                                 prompt_version=pv)
    else:
        first_model = config.SONNET_MODEL if mode == "sonnet" else config.HAIKU_MODEL
        out = await analyze_symbol_charts(sym, daily_png, weekly_png,
                                          daily_feats, weekly_feats,
                                          model=first_model, prompt_version=pv)
    result = await _finalize(sym, indicator_date, ddf, wdf, daily_png, weekly_png,
                             daily_feats, weekly_feats, out, ai_repo, gate_mode, pv, render_sem,
                             persist_charts)
    result["stage"] = ("gemini" if mode == "gemini"
                       else "haiku" if out["model"] == config.HAIKU_MODEL else "sonnet")

    # Hybrid: Sonnet confirmation pass on candidate setups
    if mode == "hybrid" and (out["analysis"].get("recommendation") in CONFIRM_RECS):
        if await ai_repo.try_consume_budget(1):
            out2 = await analyze_symbol_charts(sym, daily_png, weekly_png,
                                               daily_feats, weekly_feats,
                                               model=config.SONNET_MODEL,
                                               prompt_version=pv)
            haiku_rec = out["analysis"].get("recommendation")
            result = await _finalize(sym, indicator_date, ddf, wdf, daily_png, weekly_png,
                                     daily_feats, weekly_feats, out2, ai_repo, gate_mode, pv, render_sem,
                                     persist_charts)
            result["stage"] = "sonnet_confirmed"
            result["haikuRec"] = haiku_rec
        else:
            result["stage"] = "haiku_unconfirmed_budget"
    return result


async def _finalize(sym, indicator_date, ddf, wdf, daily_png, weekly_png,
                    daily_feats, weekly_feats, out, ai_repo, gate_mode, pv,
                    render_sem: asyncio.Semaphore, persist_charts: bool = True) -> dict:
    """Verify, annotate, persist, and shape one model-pass result.

    persist_charts=False skips the second (level-annotated) matplotlib
    render entirely AND skips writing any chart (raw or annotated) to
    CHART_DIR. Callers that never display these ai_analysis chart files
    (e.g. the backtest engine — nothing in the Backtest UI reads
    backtest_ai_signals.chart_daily_path/chart_weekly_path, it shows its own
    separate per-trade chart, see backtest/chart.py) get the exact same
    analysis/recommendation output for roughly half the render work per
    symbol, with zero unused files written to the VPS's small disk.
    """
    analysis = out["analysis"]
    verification = verify_levels(analysis, daily_feats)

    bp = analysis.get("buy_point") or {}
    levels = {
        "breakout": bp.get("breakout_level"),
        "stop": bp.get("stop_level"),
        "support": daily_feats.get("support"),
    }
    daily_annot = await _render(render_sem, ddf, sym, "daily", levels) if persist_charts else None
    weekly_annot = (await _render(render_sem, wdf, sym, "weekly", levels)
                    if persist_charts and wdf is not None else None)

    names: dict[str, str] = {}
    if persist_charts:
        if out["model"] == config.HAIKU_MODEL:
            tag = "clean"
        elif out["model"] == config.SONNET_MODEL:
            tag = "cleanS"
        else:
            tag = "cleanG"  # gemini
        names["daily"] = AiRepo.chart_filename(sym, indicator_date, "daily", tag)
        AiRepo.write_chart(names["daily"], daily_png)
        names["daily_annotated"] = AiRepo.chart_filename(sym, indicator_date, "daily", tag + "A")
        AiRepo.write_chart(names["daily_annotated"], daily_annot)
        if weekly_png is not None:
            names["weekly"] = AiRepo.chart_filename(sym, indicator_date, "weekly", tag)
            AiRepo.write_chart(names["weekly"], weekly_png)
            names["weekly_annotated"] = AiRepo.chart_filename(sym, indicator_date, "weekly", tag + "A")
            AiRepo.write_chart(names["weekly_annotated"], weekly_annot)

    features = {"daily": daily_feats, "weekly": weekly_feats}
    await ai_repo.save_result(
        symbol=sym, analysis_date=indicator_date, gate_mode=gate_mode,
        ifp_score=daily_feats.get("ifp_score"), features=features,
        analysis=analysis, verification=verification,
        recommendation=analysis.get("recommendation"),
        confidence=analysis.get("confidence"),
        chart_paths=names, processing_ms=out["processing_ms"],
        model=out["model"], prompt_version=pv,
    )

    return {
        "symbol": sym,
        "close": daily_feats.get("close"),
        "ifpScore": daily_feats.get("ifp_score"),
        "model": out["model"],
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
        "model": row.get("model"),
        "features": feats,
        "analysis": row.get("analysis"),
        "verification": row.get("verification"),
        "charts": charts,
        "fromStore": from_store,
    }
