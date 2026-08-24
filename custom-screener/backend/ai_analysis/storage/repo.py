"""Immutable AI results store + chart file store + daily call budget."""
from __future__ import annotations

import json
import re
from datetime import date

from .. import config

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+\.png$")


class AiRepo:
    def __init__(self, pool):
        self.pool = pool

    # --- results (immutable per symbol+date+prompt_version+model) ---

    async def get_result(self, symbol: str, analysis_date: date,
                         prompt_version: str | None = None,
                         model: str | None = None) -> dict | None:
        row = await self.pool.fetchrow(
            """
            SELECT * FROM ai_analysis_results
            WHERE symbol = $1 AND analysis_date = $2
              AND prompt_version = $3 AND model = $4
            """,
            symbol, analysis_date,
            prompt_version or config.PROMPT_VERSION,
            model or config.AI_MODEL,
        )
        if not row:
            return None
        return self._row_to_dict(row)

    async def get_results_batch(self, symbols: list[str], analysis_date: date,
                                 prompt_version: str | None = None,
                                 model: str | None = None) -> dict[str, dict]:
        """Batched get_result() — one query for every symbol instead of N
        sequential round trips. See BacktestAiRepo.get_results_batch, the
        original motivating case (backtest's per-day store-first check)."""
        if not symbols:
            return {}
        rows = await self.pool.fetch(
            """
            SELECT * FROM ai_analysis_results
            WHERE symbol = ANY($1) AND analysis_date = $2
              AND prompt_version = $3 AND model = $4
            """,
            symbols, analysis_date,
            prompt_version or config.PROMPT_VERSION,
            model or config.AI_MODEL,
        )
        return {r["symbol"]: self._row_to_dict(r) for r in rows}

    @staticmethod
    def _row_to_dict(row) -> dict:
        d = dict(row)
        for k in ("features", "analysis", "verification"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        return d

    async def save_result(self, *, symbol: str, analysis_date: date, gate_mode: str,
                          ifp_score: float | None, features: dict, analysis: dict,
                          verification: dict, recommendation: str, confidence: float,
                          chart_paths: dict, processing_ms: int,
                          model: str | None = None,
                          prompt_version: str | None = None) -> None:
        await self.pool.execute(
            """
            INSERT INTO ai_analysis_results
              (symbol, analysis_date, prompt_version, model, gate_mode, ifp_score,
               features, analysis, verification, recommendation, confidence,
               chart_daily_path, chart_weekly_path,
               chart_daily_annotated_path, chart_weekly_annotated_path,
               api_used, processing_ms)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,'regular',$16)
            ON CONFLICT (symbol, analysis_date, prompt_version, model) DO NOTHING
            """,
            symbol, analysis_date,
            prompt_version or config.PROMPT_VERSION,
            model or config.AI_MODEL,
            gate_mode, ifp_score,
            json.dumps(features, default=str), json.dumps(analysis, default=str),
            json.dumps(verification, default=str),
            recommendation, confidence,
            chart_paths.get("daily"), chart_paths.get("weekly"),
            chart_paths.get("daily_annotated"), chart_paths.get("weekly_annotated"),
            processing_ms,
        )

    async def save_feedback(self, symbol: str, analysis_date: date,
                            feedback: str, notes: str | None) -> bool:
        res = await self.pool.execute(
            """
            UPDATE ai_analysis_results
            SET user_feedback = $3, feedback_notes = $4, feedback_at = NOW()
            WHERE symbol = $1 AND analysis_date = $2
              AND prompt_version = $5 AND model = $6
            """,
            symbol, analysis_date, feedback, notes,
            config.PROMPT_VERSION, config.AI_MODEL,
        )
        return res.endswith("1")

    # --- daily call budget ---

    async def try_consume_budget(self, n: int) -> bool:
        """Atomically add n calls to today's counter; False if cap exceeded."""
        row = await self.pool.fetchrow(
            """
            INSERT INTO ai_call_budget (day, calls) VALUES (CURRENT_DATE, $1)
            ON CONFLICT (day) DO UPDATE SET calls = ai_call_budget.calls + $1
            WHERE ai_call_budget.calls + $1 <= $2
            RETURNING calls
            """,
            n, config.AI_DAILY_CALL_CAP,
        )
        return row is not None

    # --- chart files ---

    @staticmethod
    def chart_filename(symbol: str, analysis_date: date, timeframe: str,
                       kind: str) -> str:
        sym = re.sub(r"[^A-Za-z0-9]", "-", symbol)
        return f"{sym}_{analysis_date}_{timeframe}_{kind}_{config.PROMPT_VERSION}.png"

    @staticmethod
    def write_chart(filename: str, png: bytes) -> str:
        path = config.CHART_DIR / filename
        path.write_bytes(png)
        return filename

    @staticmethod
    def read_chart(filename: str) -> bytes | None:
        if not _SAFE_NAME.match(filename):
            return None
        path = (config.CHART_DIR / filename).resolve()
        if not str(path).startswith(str(config.CHART_DIR.resolve())) or not path.exists():
            return None
        return path.read_bytes()
