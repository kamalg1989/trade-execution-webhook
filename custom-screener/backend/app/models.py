"""Pydantic request/response schemas for the Custom Screener API."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class RangeFilter(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None


class Filters(BaseModel):
    minTurnoverCr: Optional[float] = None
    sma200: Optional[Literal["any", "above", "below"]] = "any"
    sma50: Optional[Literal["any", "above", "below"]] = "any"
    ema50: Optional[Literal["any", "above", "below"]] = "any"
    maAligned: Optional[bool] = None          # close > EMA50 > SMA200
    ema10Above: Optional[float] = None
    ema10Below: Optional[float] = None

    # 52-week high/low — within, or the "away" direction (below high / above low)
    within52wHighPct: Optional[float] = None  # within X% of 52W high
    below52wHighPct: Optional[float] = None   # more than X% below 52W high
    within52wLowPct: Optional[float] = None   # within X% of 52W low
    above52wLowPct: Optional[float] = None    # more than X% above 52W low

    # Group-1 (BAU technical / base-quality)
    baseRange20dMaxPct: Optional[float] = None   # base tightness: 20d range <= X%
    within20dHighPct: Optional[float] = None     # within X% of 20-day high (near breakout)
    volRatioMin: Optional[float] = None          # today vol / 20d avg >= X (expansion)
    volDryupMaxRatio: Optional[float] = None      # base/prior vol <= X (dry-up)
    priorUpmoveMinPct: Optional[float] = None    # prior run-up >= X%
    givebackMaxPct: Optional[float] = None        # giveback <= X%
    atrPct: Optional[RangeFilter] = None          # volatility % of price (range)

    pctChg1d: Optional[RangeFilter] = None
    pctChg5d: Optional[RangeFilter] = None
    pctChg1m: Optional[RangeFilter] = None
    pctChg3m: Optional[RangeFilter] = None
    pctChg6m: Optional[RangeFilter] = None
    pctChg1y: Optional[RangeFilter] = None


class SortSpec(BaseModel):
    by: str = "pct_chg_1d"
    order: Literal["ASC", "DESC"] = "DESC"


class FilterRequest(BaseModel):
    indicatorDate: Optional[date] = None
    includeInsufficientHistory: bool = False
    filters: Filters = Field(default_factory=Filters)
    sort: SortSpec = Field(default_factory=SortSpec)
