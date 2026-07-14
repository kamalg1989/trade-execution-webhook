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
    weekly_png: bytes | None,
    daily_feats: dict,
    weekly_feats: dict | None,
    model: str | None = None,
) -> dict:
    """One call per symbol: chart(s) + feature block → schema-valid dict.

    weekly_png/weekly_feats may be None (daily-only scope, ~40% cheaper).
    Returns {"analysis": {...}, "processing_ms": int, "model": str} or raises.
    """
    client = _get_client()
    t0 = time.monotonic()
    model = model or config.AI_MODEL

    content = [_img(daily_png)]
    if weekly_png is not None:
        content.append(_img(weekly_png))
    content.append({"type": "text",
                    "text": feature_block(symbol, daily_feats, weekly_feats)})

    msg = await client.messages.create(
        model=model,
        max_tokens=config.AI_MAX_TOKENS,
        # cache_control: tools + system form a stable prefix, cached across
        # calls (90% discount on cached reads when above the model's minimum).
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        tools=[ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": ANALYSIS_TOOL["name"]},
        messages=[{"role": "user", "content": content}],
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
        "model": model,
    }
