"""API tests via FastAPI TestClient with an in-memory fake repo (no Postgres)."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app


class FakeRepo:
    def __init__(self):
        self._date = date(2026, 7, 8)
        self._rows = [
            {"symbol": "AAA", "close": 26.7, "turnover_1m_avg_cr": 12.0,
             "ema_10": 25.8, "ema_21": 26.0, "sma_50": 25.0, "sma_200": 20.0,
             "dist_sma_50_pct": 6.8, "dist_sma_200_pct": 33.5,
             "price_52w_high": 28.5, "price_52w_low": 15.2,
             "dist_52w_high_pct": -6.3, "dist_52w_low_pct": 75.6,
             "pct_chg_1d": 0.8, "pct_chg_5d": 2.1, "pct_chg_1m": 12.5,
             "pct_chg_3m": 45.0, "pct_chg_6m": 87.0, "pct_chg_1y": 120.0,
             "atr_14": 0.5, "volume_1m_avg": 560000, "bars_available": 800,
             "indicator_date": date(2026, 7, 8)},
            {"symbol": "BBB", "close": 100.0, "turnover_1m_avg_cr": 2.0,
             "ema_10": 105.0, "ema_21": 106.0, "sma_50": 110.0, "sma_200": 120.0,
             "dist_sma_50_pct": -9.0, "dist_sma_200_pct": -16.6,
             "price_52w_high": 150.0, "price_52w_low": 90.0,
             "dist_52w_high_pct": -33.0, "dist_52w_low_pct": 11.0,
             "pct_chg_1d": -2.0, "pct_chg_5d": -3.0, "pct_chg_1m": -4.0,
             "pct_chg_3m": -10.0, "pct_chg_6m": -20.0, "pct_chg_1y": -25.0,
             "atr_14": 3.0, "volume_1m_avg": 200000, "bars_available": 900,
             "indicator_date": date(2026, 7, 8)},
        ]
        self._snap = {
            "snapshot_date": self._date, "total_stocks": 2, "eligible_stocks": 2,
            "count_above_50sma": 1, "count_above_200sma": 1,
            "count_below_50sma": 1, "count_below_200sma": 1,
            "count_within_15pct_52w_high": 1, "count_within_10pct_52w_high": 1,
            "count_within_15pct_52w_low": 1, "count_within_10pct_52w_low": 0,
            "count_new_52w_high": 0, "count_new_52w_low": 0,
            "count_moved_gt_4_5pct_1d": 0, "count_moved_gt_20pct_1m": 0,
            "count_moved_gt_60pct_3m": 0, "count_moved_gt_100pct_6m": 1,
            "regime": "Consolidation", "trend_score": 0.0, "breadth_score": 0.5,
            "is_complete": True,
        }

    async def latest_complete_date(self): return self._date
    async def day_slice(self, d): return list(self._rows) if d == self._date else []
    async def snapshot(self, d): return self._snap if d == self._date else None
    async def historical(self, symbol, frm, to, limit):
        return [r for r in self._rows if r["symbol"] == symbol]


@pytest.fixture
def client():
    app.state.repo = FakeRepo()
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["dbReady"] is True


def test_snapshot(client):
    r = client.get("/api/market-snapshot")
    assert r.status_code == 200
    j = r.json()
    assert j["snapshotDate"] == "2026-07-08"
    assert j["regime"] == "Consolidation"
    assert j["counts"]["above200sma"] == 1


def test_filter_default(client):
    r = client.post("/api/filter", json={})
    assert r.status_code == 200
    j = r.json()
    assert j["matchCount"] == 2
    assert j["results"][0]["symbol"] == "AAA"       # sorted by pct_chg_1d DESC


def test_filter_min_turnover(client):
    r = client.post("/api/filter", json={"filters": {"minTurnoverCr": 5}})
    j = r.json()
    assert [x["symbol"] for x in j["results"]] == ["AAA"]


def test_filter_sma200_above(client):
    r = client.post("/api/filter", json={"filters": {"sma200": "above"}})
    assert [x["symbol"] for x in r.json()["results"]] == ["AAA"]


def test_filter_invalid_range_400(client):
    r = client.post("/api/filter", json={"filters": {"pctChg1m": {"min": 20, "max": 5}}})
    assert r.status_code == 400


def test_historical(client):
    r = client.get("/api/historical", params={"symbol": "AAA",
                    "fromDate": "2026-01-01", "toDate": "2026-07-08"})
    assert r.status_code == 200
    assert r.json()["rowCount"] == 1


def test_historical_bad_range_400(client):
    r = client.get("/api/historical", params={"symbol": "AAA",
                    "fromDate": "2026-07-08", "toDate": "2026-01-01"})
    assert r.status_code == 400
