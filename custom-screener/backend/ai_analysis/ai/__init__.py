"""AI client package. client.py imports `anthropic`, gemini_client.py imports
`google.genai` — both lazily, so schema/prompts stay importable (and
testable) without either SDK installed."""


def analyze_symbol_charts(*args, **kwargs):  # lazy proxy (Anthropic)
    from .client import analyze_symbol_charts as _impl
    return _impl(*args, **kwargs)


def analyze_symbol_charts_gemini(*args, **kwargs):  # lazy proxy (Gemini)
    from .gemini_client import analyze_symbol_charts_gemini as _impl
    return _impl(*args, **kwargs)
