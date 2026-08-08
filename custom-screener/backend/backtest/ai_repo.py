"""Backtest-scoped AI result store — same interface as
ai_analysis.storage.AiRepo, but persists to backtest_ai_signals instead of
the live ai_analysis_results table (deliberate isolation, see spec §5).

Passed directly into ai_analysis.pipeline.analyze_symbols() as the `ai_repo`
dependency, so the real chart-render + Gemini-call + verify logic is reused
unmodified — only where results are stored/looked-up changes.
"""
from __future__ import annotations

import json
from datetime import date


class BacktestAiRepo:
    def __init__(self, pool):
        self.pool = pool

    async def get_result(self, symbol: str, analysis_date: date,
                          prompt_version: str | None = None,
                          model: str | None = None) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT * FROM backtest_ai_signals
            WHERE symbol = $1 AND signal_date = $2 AND prompt_version = $3
            """,
            symbol, analysis_date, prompt_version or "v2",
        )
        if not row:
            return None
        d = dict(row)
        for k in ("features", "analysis"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        # pipeline._row_to_result() expects a 'verification' key too (only used
        # for display, backtest doesn't need it) — default to empty dict.
        d.setdefault("verification", {})
        return d

    async def save_result(self, *, symbol: str, analysis_date: date, gate_mode: str,
                           ifp_score: float | None, features: dict, analysis: dict,
                           verification: dict, recommendation: str, confidence: float,
                           chart_paths: dict, processing_ms: int,
                           model: str | None = None,
                           prompt_version: str | None = None) -> None:
        await self.pool.execute(
            """
            INSERT INTO backtest_ai_signals
              (symbol, signal_date, prompt_version, model, recommendation, confidence,
               ifp_score, analysis, features, chart_daily_path, chart_weekly_path)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (symbol, signal_date, prompt_version) DO NOTHING
            """,
            symbol, analysis_date, prompt_version or "v2", model,
            recommendation, confidence, ifp_score,
            json.dumps(analysis, default=str), json.dumps(features, default=str),
            chart_paths.get("daily"), chart_paths.get("weekly"),
        )

    async def try_consume_budget(self, n: int) -> bool:
        """No daily cap for the offline backtest batch — always allow."""
        return True
