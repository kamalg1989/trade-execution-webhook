"""Gemini vision client — async, structured JSON output via response_schema.

Mirrors client.py's contract (same inputs/outputs) so pipeline.py can treat
the two providers interchangeably. Gemini has no forced-tool-use concept like
Anthropic's tool_choice; instead response_mime_type="application/json" +
response_schema (a Pydantic model) constrains the output directly.
"""
from __future__ import annotations

import json
import logging
import time

from .. import config
from .gemini_schema import ChartAnalysis
from .prompts import SYSTEM_PROMPT_CORE, feature_block

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not configured")
        from google import genai
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


async def analyze_symbol_charts_gemini(
    symbol: str,
    daily_png: bytes,
    weekly_png: bytes | None,
    daily_feats: dict,
    weekly_feats: dict | None,
    model: str | None = None,
) -> dict:
    """Same contract as ai.client.analyze_symbol_charts, Gemini backend."""
    from google.genai import types

    client = _get_client()
    t0 = time.monotonic()
    model = model or config.GEMINI_MODEL

    parts = [types.Part.from_bytes(data=daily_png, mime_type="image/png")]
    if weekly_png is not None:
        parts.append(types.Part.from_bytes(data=weekly_png, mime_type="image/png"))
    parts.append(feature_block(symbol, daily_feats, weekly_feats))

    resp = await client.aio.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT_CORE,
            response_mime_type="application/json",
            response_schema=ChartAnalysis,
            max_output_tokens=config.AI_MAX_TOKENS,
        ),
    )

    u = getattr(resp, "usage_metadata", None)
    logger.info(
        "AI usage %s [%s]: in=%s out=%s cached=%s",
        symbol, model,
        getattr(u, "prompt_token_count", None),
        getattr(u, "candidates_token_count", None),
        getattr(u, "cached_content_token_count", None))

    if resp.parsed is not None:
        analysis = resp.parsed.model_dump()
    else:
        analysis = json.loads(resp.text)

    return {
        "analysis": analysis,
        "processing_ms": int((time.monotonic() - t0) * 1000),
        "model": model,
    }
