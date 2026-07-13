"""AI client package. client.py imports `anthropic` — import lazily so the
schema/prompts stay importable (and testable) without the SDK installed."""


def analyze_symbol_charts(*args, **kwargs):  # lazy proxy
    from .client import analyze_symbol_charts as _impl
    return _impl(*args, **kwargs)
