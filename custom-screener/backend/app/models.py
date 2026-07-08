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
    ema10Above: Optional[float] = None
    ema10Below: Optional[float] = None
    within52wHighPct: Optional[float] = None
    within52wLowPct: Optional[float] = None
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
