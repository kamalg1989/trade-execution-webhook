"""Claude vision client — async, forced tool use, bounded concurrency."""
from __future__ import annotations

import base64
import logging
import time

import anthropic

from .. import config
from .prompts import SYSTEM_PROMPT, feature_block
from .schema import ANALYSIS_TOOL, ANALYSIS_TOOL_V3

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
    prompt_version: str = "v2",
) -> dict:
    """One call per symbol → schema-valid dict.

    v2: chart(s) + computed feature block (grounded).
    v3: pure visual — daily chart only, few-shot example charts in a cached
        prefix, slim schema, no computed features.
    Returns {"analysis": {...}, "processing_ms": int, "model": str} or raises.
    """
    client = _get_client()
    t0 = time.monotonic()
    model = model or config.AI_MODEL

    if prompt_version.startswith("v3"):
        from .examples import example_png
        from .prompts_v3 import (EXAMPLE_COHANCE_TEXT, EXAMPLE_TNPETRO_TEXT,
                                 SYSTEM_PROMPT_V3, candidate_text_v3)
        system = SYSTEM_PROMPT_V3
        tool = ANALYSIS_TOOL_V3
        max_tokens = config.AI_MAX_TOKENS_V3
        content = [
            _img(example_png("cohance")),
            {"type": "text", "text": EXAMPLE_COHANCE_TEXT},
            _img(example_png("tnpetro")),
            # cache breakpoint: everything up to here (tools + system +
            # examples) is identical across calls
            {"type": "text", "text": EXAMPLE_TNPETRO_TEXT,
             "cache_control": {"type": "ephemeral"}},
            _img(daily_png),
            {"type": "text", "text": candidate_text_v3(symbol)},
        ]
    else:
        system = SYSTEM_PROMPT
        tool = ANALYSIS_TOOL
        max_tokens = config.AI_MAX_TOKENS
        content = [_img(daily_png)]
        if weekly_png is not None:
            content.append(_img(weekly_png))
        content.append({"type": "text",
                        "text": feature_block(symbol, daily_feats, weekly_feats)})

    msg = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        # cache_control: for v2 the breakpoint is on the system block; for v3
        # it sits after the example images inside the user content, covering
        # tools + system + examples in one cached prefix.
        system=[{"type": "text", "text": system}
                if prompt_version.startswith("v3") else
                {"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}],
    )

    u = msg.usage
    logger.info(
        "AI usage %s [%s]: in=%d out=%d cache_write=%s cache_read=%s",
        symbol, model, u.input_tokens, u.output_tokens,
        getattr(u, "cache_creation_input_tokens", None),
        getattr(u, "cache_read_input_tokens", None))

    analysis = None
    for block in msg.content:
        if block.type == "tool_use" and block.name == tool["name"]:
            analysis = block.input
            break
    if analysis is None:
        raise RuntimeError(f"No tool_use block in response for {symbol}")

    return {
        "analysis": analysis,
        "processing_ms": int((time.monotonic() - t0) * 1000),
        "model": model,
    }
