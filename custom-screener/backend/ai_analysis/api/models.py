"""Pydantic request models for the AI analysis endpoints."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


class AnalyzeRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, max_length=50)
    indicatorDate: Optional[date] = None
    gateMode: Optional[Literal["hard", "soft"]] = None
    ifpThreshold: Optional[float] = Field(None, ge=0, le=1)
    aiMode: Optional[Literal["haiku", "hybrid", "sonnet"]] = None
    chartScope: Optional[Literal["daily", "both"]] = None
    force: bool = False


class FeedbackRequest(BaseModel):
    symbol: str
    analysisDate: date
    feedback: Literal["CORRECT", "PARTIAL", "WRONG"]
    notes: Optional[str] = None
