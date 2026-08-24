"""Pure chart rendering for AI input and annotated user output.

Derived from market_data_setup/api/charting_enhanced.py create_png_chart,
with the fixes agreed in review:
  - always PNG (Claude vision does not accept SVG)
  - volume MA20 overlay on the volume panel (critical for IFP reading)
  - weekly charts get weekly-appropriate EMAs (10/40w), fixing the bug where
    weekly PNGs rendered with no EMAs at all
  - optional level annotations (breakout / stop) — local render, zero API cost
  - pure function: no HTTP, no DB
"""
from __future__ import annotations

import io
import logging

import pandas as pd

logger = logging.getLogger(__name__)

DAILY_EMAS = ((10, "#378ADD"), (21, "#1D9E75"), (50, "#EF9F27"), (200, "#E24B4A"))
WEEKLY_EMAS = ((10, "#378ADD"), (40, "#EF9F27"))


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLCV (DatetimeIndex asc) → weekly bars (W-FRI)."""
    weekly = df.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return weekly


def render_chart(
    df: pd.DataFrame,
    symbol: str,
    timeframe: str = "daily",
    levels: dict | None = None,
    width_px: int = 1600,
    height_px: int = 800,
    vline=None,
    title_suffix: str = "",
) -> bytes:
    """Render candlestick PNG: volume panel + vol MA20 + EMAs (+ level lines).

    df: DatetimeIndex ascending, columns open/high/low/close/volume.
    levels: optional {"breakout": float, "stop": float, "support": float}.
    vline: optional timestamp — vertical marker (e.g. analysis date on
           aftermath charts, splitting 'what the AI saw' from 'what happened').

    Default 1600x800px (up from 1200x700) so ~200 daily bars render at
    ~8px/candle instead of ~4px. EMA lines also get an on-chart legend
    (labeled by period, matching their color) — neither a human reviewer nor
    a vision model can otherwise tell which colored line is which EMA.
    """
    import mplfinance as mpf
    from matplotlib.lines import Line2D

    d = df[["open", "high", "low", "close", "volume"]].astype(float).copy()

    apd = []
    legend_entries = []  # (label, color) for the on-chart legend
    emas = DAILY_EMAS if timeframe == "daily" else WEEKLY_EMAS
    for span, color in emas:
        if len(d) >= span:
            apd.append(mpf.make_addplot(
                d["close"].ewm(span=span, adjust=False).mean(),
                color=color, width=1.2,
            ))
            legend_entries.append((f"EMA{span}", color))

    vol_ma = d["volume"].rolling(20).mean()
    if vol_ma.notna().any():
        apd.append(mpf.make_addplot(vol_ma, panel=1, color="#7F77DD", width=1.2))

    hlines = None
    if levels:
        lines, colors = [], []
        for key, color in (("breakout", "#3B6D11"), ("stop", "#A32D2D"), ("support", "#5F5E5A")):
            v = levels.get(key)
            if v:
                lines.append(float(v))
                colors.append(color)
        if lines:
            hlines = dict(hlines=lines, colors=colors, linestyle="--", linewidths=1.2)

    style = mpf.make_mpf_style(base_mpf_style="charles", gridcolor="#dddddd", y_on_right=True)

    title = f"{symbol} — {timeframe} (as of {d.index[-1].date()}){title_suffix}"
    kwargs = dict(
        type="candle",
        volume=True,
        addplot=apd or None,
        style=style,
        figsize=(width_px / 100, height_px / 100),
        title=title,
        ylabel="Price",
        ylabel_lower="Volume",
        returnfig=True,
    )
    if hlines:
        kwargs["hlines"] = hlines
    if vline is not None:
        kwargs["vlines"] = dict(vlines=[pd.Timestamp(vline)], colors=["#534AB7"],
                                linestyle="-.", linewidths=1.4, alpha=0.9)

    fig, axlist = mpf.plot(d, **kwargs)
    if legend_entries:
        handles = [Line2D([0], [0], color=c, lw=1.6, label=lbl) for lbl, c in legend_entries]
        axlist[0].legend(handles=handles, loc="upper left", fontsize=8,
                         framealpha=0.75, ncol=len(handles))

    buf = io.BytesIO()
    fig.savefig(buf, dpi=100, pad_inches=0.3)
    buf.seek(0)
    png = buf.getvalue()

    # mplfinance never closes the Figure it creates — without this, every
    # call leaves its Figure registered in matplotlib's global pyplot state,
    # and memory grows unbounded across many sequential/concurrent renders
    # (this is what OOM-killed the backtest engine, which calls this in a
    # tight loop; live usage never called it often enough in one process
    # lifetime to notice).
    import matplotlib.pyplot as plt
    plt.close(fig)

    return png
