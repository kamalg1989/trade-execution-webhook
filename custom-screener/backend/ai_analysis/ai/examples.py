"""Few-shot example chart images for the v3 prompt.

PNGs are rendered once from our own OHLCV by scripts/render_v3_examples.py
(COHANCE + TNPETRO, Jan-Jun 2026, same renderer/style as candidate charts)
and loaded here lazily, cached in module memory for reuse across calls.
"""
from __future__ import annotations

import logging

from .. import config

logger = logging.getLogger(__name__)

_cache: dict[str, bytes] = {}

FILES = {
    "cohance": "example_COHANCE_2026H1_daily.png",
    "tnpetro": "example_TNPETRO_2026H1_daily.png",
}


def example_png(name: str) -> bytes:
    if name not in _cache:
        path = config.EXAMPLES_DIR / FILES[name]
        if not path.exists():
            raise RuntimeError(
                f"v3 example chart missing: {path} — run "
                "scripts/render_v3_examples.py once after deploy")
        _cache[name] = path.read_bytes()
    return _cache[name]


def examples_available() -> bool:
    try:
        example_png("cohance")
        example_png("tnpetro")
        return True
    except RuntimeError:
        return False
