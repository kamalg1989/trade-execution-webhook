"""Claude vision client — async, forced tool use, bounded concurrency."""
from __future__ import annotations

import base64
import logging
import time

import anthropic

from .. import config
from .prompts import SYSTEM_PROMPT, feature_block
from .schema import ANALYSIS_TOOL

logger = logging.getLogger(__name__)

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        _client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _img(png_bytes: bytes) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(png_bytes).decode(),
        },
    }


async def analyze_symbol_charts(
    symbol: str,
    daily_png: bytes,
    weekly_png: bytes,
    daily_feats: dict,
    weekly_feats: dict,
) -> dict:
    """One call per symbol: both charts + feature block → schema-valid dict.

    Returns {"analysis": {...}, "processing_ms": int} or raises.
    """
    client = _get_client()
    t0 = time.monotonic()

    msg = await client.messages.create(
        model=config.AI_MODEL,
        max_tokens=config.AI_MAX_TOKENS,
        # cache_control: tools + system form a stable prefix, cached across
        # calls (90% discount on cached reads when above the model's minimum).
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        tools=[ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": ANALYSIS_TOOL["name"]},
        messages=[{
            "role": "user",
            "content": [
                _img(daily_png),
                _img(weekly_png),
                {"type": "text", "text": feature_block(symbol, daily_feats, weekly_feats)},
            ],
        }],
    )

    analysis = None
    for block in msg.content:
        if block.type == "tool_use" and block.name == ANALYSIS_TOOL["name"]:
            analysis = block.input
            break
    if analysis is None:
        raise RuntimeError(f"No tool_use block in response for {symbol}")

    return {
        "analysis": analysis,
        "processing_ms": int((time.monotonic() - t0) * 1000),
        "model": config.AI_MODEL,
    }
