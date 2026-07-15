"""Forced tool-use schema — taxonomy from the 'When to Buy' methodology deck."""

PATTERN_TYPES = [
    "vcp", "flag", "pennant", "inverse_hs", "double_bottom", "triple_bottom",
    "double_top", "triple_top", "hs_top", "rectangle", "wedge", "tennis_ball",
]

# v3 — slim visual-analysis schema (see prompts_v3.py / V3_PROMPT_SPEC.md)
STRENGTH = {"type": "string", "enum": ["strong", "moderate", "weak"]}

ANALYSIS_TOOL_V3 = {
    "name": "report_chart_analysis",
    "description": "Report the visual IFP analysis of the stock's daily chart.",
    "input_schema": {
        "type": "object",
        "properties": {
            "base_type": {"type": "string", "enum": ["A", "B"],
                          "description": "A = base after uptrend; B = accumulation after distribution"},
            "ifp": {
                "type": "object",
                "properties": {
                    "volume_pattern": STRENGTH,
                    "base_structure": STRENGTH,
                    "pullback_depth": STRENGTH,
                },
                "required": ["volume_pattern", "base_structure", "pullback_depth"],
            },
            "extended": {"type": "boolean"},
            "buy_point": {
                "type": "object",
                "properties": {
                    "breakout_level": {"type": ["number", "null"]},
                    "stop_level": {"type": ["number", "null"]},
                },
                "required": ["breakout_level", "stop_level"],
            },
            "recommendation": {"type": "string",
                               "enum": ["SETUP_READY", "EARLY_STAGE", "NOT_READY", "AVOID"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "verdict": {"type": "string", "description": "Max 2 sentences, chart-specific."},
        },
        "required": ["base_type", "ifp", "extended", "buy_point",
                     "recommendation", "confidence", "verdict"],
    },
}

ANALYSIS_TOOL = {
    "name": "report_chart_analysis",
    "description": "Report the structured technical analysis of the stock's daily and weekly charts.",
    "input_schema": {
        "type": "object",
        "properties": {
            "market_cycle_phase": {
                "type": "string",
                "enum": ["accumulation", "advance", "distribution", "decline"],
            },
            "base_count": {
                "type": "string",
                "enum": ["0", "1", "2", "3", "4_plus"],
                "description": "Base counting since accumulation (base 0). 4_plus = late, prone to failure.",
            },
            "base_quality": {
                "type": "string",
                "enum": ["constructive", "suspect", "broken"],
            },
            "base_quality_reasons": {
                "type": "array", "items": {"type": "string"}, "maxItems": 4,
            },
            "patterns": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": PATTERN_TYPES},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "timeframe": {"type": "string", "enum": ["daily", "weekly"]},
                        "description": {"type": "string"},
                    },
                    "required": ["type", "confidence", "timeframe"],
                },
            },
            "ifp_verdict": {
                "type": "object",
                "properties": {
                    "present": {"type": "boolean"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string"},
                },
                "required": ["present", "confidence", "evidence"],
            },
            "buy_point": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["pullback", "reverse_hs_breakout", "high_breakout",
                                 "breakout_retest", "none"],
                    },
                    "structure": {"type": "string", "enum": ["hammer", "hh_hl", "none"]},
                    "breakout_level": {"type": ["number", "null"]},
                    "stop_level": {"type": ["number", "null"]},
                },
                "required": ["type", "breakout_level", "stop_level"],
            },
            "weekly_context": {
                "type": "string",
                "description": "How the weekly chart confirms or contradicts the daily base count.",
            },
            "recommendation": {
                "type": "string",
                "enum": ["SETUP_READY", "EARLY_STAGE", "NOT_READY", "AVOID"],
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "thesis": {"type": "string", "description": "1-2 sentence summary."},
        },
        "required": [
            "market_cycle_phase", "base_count", "base_quality", "patterns",
            "ifp_verdict", "buy_point", "recommendation", "confidence", "thesis",
        ],
    },
}
