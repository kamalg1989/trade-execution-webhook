"""Config for the AI analysis module. Reuses the screener .env discovery."""
from __future__ import annotations

import os
from pathlib import Path

from app import config as _screener_config  # noqa: F401  (triggers .env load)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Gate
AI_GATE_MODE = os.getenv("AI_GATE_MODE", "hard")            # hard | soft
IFP_GATE_THRESHOLD = float(os.getenv("IFP_GATE_THRESHOLD", "0.30"))

# AI — engine per mode (UI-selectable per request; AI_MODE is the default)
AI_MODE = os.getenv("AI_MODE", "gemini")                 # gemini | haiku | hybrid | sonnet
HAIKU_MODEL = os.getenv("AI_HAIKU_MODEL", "claude-haiku-4-5-20251001")
SONNET_MODEL = os.getenv("AI_SONNET_MODEL", "claude-sonnet-4-5")
GEMINI_MODEL = os.getenv("AI_GEMINI_MODEL", "gemini-3.1-flash-lite")  # entire
# 2.5 series (flash + flash-lite) 404s for new API keys/projects ("no longer
# available to new users") even though still listed in models.list() - Google
# gates deprecated models off new accounts specifically. 3.1-flash-lite is the
# current-gen GA replacement and still the cheapest vision-capable tier
# ($0.25/$1.50 per M in/out). Verified live against this project's key.
AI_MODEL = os.getenv("AI_MODEL", HAIKU_MODEL)            # legacy fallback
MAX_CONCURRENT_AI = int(os.getenv("MAX_CONCURRENT_AI", "5"))
# Chart rendering (matplotlib/mplfinance) is the RAM-heavy step, not the
# Gemini network call — a render call briefly holds a full Figure + Agg
# canvas in memory. MAX_CONCURRENT_AI can safely be raised on a small VPS as
# long as this stays low, since it independently caps how many renders run
# at once regardless of how many symbols are in flight waiting on Gemini.
MAX_CONCURRENT_RENDER = int(os.getenv("MAX_CONCURRENT_RENDER", "2"))
AI_MAX_TOKENS = int(os.getenv("AI_MAX_TOKENS", "900"))   # output is 5x input price; schema fits in ~600
PROMPT_VERSION = os.getenv("AI_PROMPT_VERSION", "v2")    # v2: compact features + brevity rules
AI_DAILY_CALL_CAP = int(os.getenv("AI_DAILY_CALL_CAP", "500"))

# Charts
CHART_DIR = Path(os.getenv("AI_CHART_DIR", "/tmp/ai_analysis_charts"))
CHART_DIR.mkdir(parents=True, exist_ok=True)

# v3 few-shot example charts (persistent — rendered once by
# scripts/render_v3_examples.py; NOT in /tmp so they survive reboots)
EXAMPLES_DIR = Path(os.getenv("AI_EXAMPLES_DIR",
                              str(Path(__file__).parent / "examples")))

# v3 output cap (slim schema, ~250 typical)
AI_MAX_TOKENS_V3 = int(os.getenv("AI_MAX_TOKENS_V3", "400"))

# Verification tolerance (fraction)
LEVEL_TOLERANCE = float(os.getenv("AI_LEVEL_TOLERANCE", "0.02"))

# Data windows — 200 daily bars at 1600x800px gives ~8px/candle (was ~4px at
# 300 bars / 1200x700); still ~9-10 months of daily history in one chart.
DAILY_BARS = int(os.getenv("AI_DAILY_BARS", "200"))
WEEKLY_BARS = int(os.getenv("AI_WEEKLY_BARS", "150"))
