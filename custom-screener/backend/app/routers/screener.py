"""Custom Screener API routes (served under /api/... ; nginx maps the public path)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..filtering import FilterError, apply_filters
from ..models import FilterRequest

router = APIRouter()


def get_repo(request: Request):
    repo = getattr(request.app.state, "repo", None)
    if repo is None:
        raise HTTPException(503, "Data layer not ready")
    return repo


async def _resolve_date(repo, requested: date | None) -> date:
    if requested is not None:
        return requested
    d = await repo.latest_complete_date()
    if d is None:
        raise HTTPException(404, "No complete snapshot available yet")
    return d


# Client-facing projection (camelCase)
def _project(r: dict) -> dict:
    return {
        "symbol": r["symbol"],
        "close": r.get("close"),
        "ema10": r.get("ema_10"), "ema21": r.get("ema_21"),
        "sma50": r.get("sma_50"), "sma200": r.get("sma_200"),
        "distSma200Pct": r.get("dist_sma_200_pct"),
        "atr14": r.get("atr_14"),
        "price52wHigh": r.get("price_52w_high"), "price52wLow": r.get("price_52w_low"),
        "dist52wHighPct": r.get("dist_52w_high_pct"), "dist52wLowPct": r.get("dist_52w_low_pct"),
        "pctChg1d": r.get("pct_chg_1d"), "pctChg5d": r.get("pct_chg_5d"),
        "pctChg1m": r.get("pct_chg_1m"), "pctChg3m": r.get("pct_chg_3m"),
        "pctChg6m": r.get("pct_chg_6m"), "pctChg1y": r.get("pct_chg_1y"),
        "turnover1mAvgCr": r.get("turnover_1m_avg_cr"), "volume1mAvg": r.get("volume_1m_avg"),
        "barsAvailable": r.get("bars_available"),
    }


@router.get("/market-snapshot")
async def market_snapshot(date_q: date | None = Query(None, alias="date"), repo=Depends(get_repo)):
    d = await _resolve_date(repo, date_q)
    snap = await repo.snapshot(d)
    if snap is None or not snap.get("is_complete"):
        latest = await repo.latest_complete_date()
        raise HTTPException(404, f"No complete snapshot for {d}. Latest complete: {latest}")
    elig = snap.get("eligible_stocks") or 0
    pct200 = round(100 * (snap.get("count_above_200sma") or 0) / elig) if elig else 0
    return {
        "snapshotDate": str(snap["snapshot_date"]),
        "totalStocks": snap.get("total_stocks"),
        "eligibleStocks": elig,
        "counts": {
            "above50sma": snap.get("count_above_50sma"),
            "above200sma": snap.get("count_above_200sma"),
            "below50sma": snap.get("count_below_50sma"),
            "below200sma": snap.get("count_below_200sma"),
            "within15pct52wHigh": snap.get("count_within_15pct_52w_high"),
            "within10pct52wHigh": snap.get("count_within_10pct_52w_high"),
            "within15pct52wLow": snap.get("count_within_15pct_52w_low"),
            "within10pct52wLow": snap.get("count_within_10pct_52w_low"),
            "newHigh": snap.get("count_new_52w_high"),
            "newLow": snap.get("count_new_52w_low"),
            "movedGt4_5pct1d": snap.get("count_moved_gt_4_5pct_1d"),
            "movedGt20pct1m": snap.get("count_moved_gt_20pct_1m"),
            "movedGt60pct3m": snap.get("count_moved_gt_60pct_3m"),
            "movedGt100pct6m": snap.get("count_moved_gt_100pct_6m"),
        },
        "regime": snap.get("regime"),
        "trendScore": _f(snap.get("trend_score")),
        "breadthScore": _f(snap.get("breadth_score")),
        "message": f"{pct200}% of eligible NSE equities above 200-day SMA",
    }


@router.post("/filter")
async def filter_stocks(req: FilterRequest, repo=Depends(get_repo)):
    d = await _resolve_date(repo, req.indicatorDate)
    rows = await repo.day_slice(d)
    if not rows:
        raise HTTPException(404, f"No indicator data for {d}")
    filters = req.filters.model_dump()
    # unwrap RangeFilter dicts already as {min,max}
    try:
        matched = apply_filters(
            rows, filters,
            include_insufficient=req.includeInsufficientHistory,
            sort_by=req.sort.by, order=req.sort.order,
        )
    except FilterError as e:
        raise HTTPException(400, str(e))
    return {
        "indicatorDate": str(d),
        "matchCount": len(matched),
        "results": [_project(r) for r in matched],
    }


@router.get("/historical")
async def historical(
    symbol: str = Query(...),
    fromDate: date = Query(...),
    toDate: date = Query(...),
    limit: int = Query(500, ge=1, le=1000),
    repo=Depends(get_repo),
):
    if fromDate > toDate:
        raise HTTPException(400, "fromDate must be <= toDate")
    rows = await repo.historical(symbol.upper(), fromDate, toDate, limit)
    if not rows:
        raise HTTPException(404, f"No indicator data for symbol {symbol}")
    data = [dict(_project(r), date=str(r["indicator_date"])) for r in rows]
    return {
        "symbol": symbol.upper(),
        "dateRange": {"from": str(fromDate), "to": str(toDate)},
        "rowCount": len(data),
        "data": data,
    }


def _f(v):
    return float(v) if v is not None else None
