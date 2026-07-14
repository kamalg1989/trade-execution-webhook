"""Pydantic mirror of schema.py's ANALYSIS_TOOL, for Gemini's response_schema.

Gemini's structured-output mode rejects schema fields carrying default values
(a documented google-genai limitation), so every field below is required —
the model must always supply a value (use "" / [] / null rather than omitting
a key). Keep this in sync with schema.py by hand; Anthropic tool-schema JSON
and Gemini's response_schema aren't interchangeable formats.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

PatternType = Literal[
    "vcp", "flag", "pennant", "inverse_hs", "double_bottom", "triple_bottom",
    "double_top", "triple_top", "hs_top", "rectangle", "wedge", "tennis_ball",
]


class GeminiPattern(BaseModel):
    type: PatternType
    confidence: float = Field(..., ge=0, le=1)
    timeframe: Literal["daily", "weekly"]
    description: str


class GeminiIfpVerdict(BaseModel):
    present: bool
    confidence: float = Field(..., ge=0, le=1)
    evidence: str


class GeminiBuyPoint(BaseModel):
    type: Literal["pullback", "reverse_hs_breakout", "high_breakout",
                  "breakout_retest", "none"]
    structure: Literal["hammer", "hh_hl", "none"]
    breakout_level: Optional[float]
    stop_level: Optional[float]


class ChartAnalysis(BaseModel):
    market_cycle_phase: Literal["accumulation", "advance", "distribution", "decline"]
    base_count: Literal["0", "1", "2", "3", "4_plus"]
    base_quality: Literal["constructive", "suspect", "broken"]
    base_quality_reasons: list[str] = Field(..., max_length=4)
    patterns: list[GeminiPattern] = Field(..., max_length=4)
    ifp_verdict: GeminiIfpVerdict
    buy_point: GeminiBuyPoint
    weekly_context: str
    recommendation: Literal["SETUP_READY", "EARLY_STAGE", "NOT_READY", "AVOID"]
    confidence: float = Field(..., ge=0, le=1)
    thesis: str
