#!/usr/bin/env python3
"""
Automated Test Suite for Market Data API
Tests all endpoints without requiring PostgreSQL
Run mock_server.py in one terminal, then run this script

Usage:
    python market_data_setup/testing/test_suite.py
"""

import requests
import json
import time
from datetime import date
from typing import Dict, Any

# Configuration
BASE_URL = "http://localhost:8000"
TIMEOUT = 10

# Test results
results = {
    "passed": 0,
    "failed": 0,
    "errors": []
}

# ============================================================
# TEST UTILITIES
# ============================================================

def log_test(name: str, status: str, duration: float = 0):
    """Log test result"""
    if status == "PASS":
        print(f"✅ {name:<50} [{duration:.3f}s]")
        results["passed"] += 1
    else:
        print(f"❌ {name:<50}")
        results["failed"] += 1

def log_error(message: str):
    """Log error"""
    print(f"   ⚠️  {message}")
    results["errors"].append(message)

# ============================================================
# TESTS
# ============================================================

def test_health():
    """Test health check endpoint"""
    try:
        start = time.time()
        r = requests.get(f"{BASE_URL}/api/v1/health", timeout=TIMEOUT)
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        data = r.json()
        assert "status" in data, "Missing 'status' field"
        assert data["status"] == "ok", f"Status is '{data['status']}'"
        assert "timestamp" in data, "Missing 'timestamp'"
        assert "mode" in data, "Missing 'mode'"

        log_test("Health Check", "PASS", duration)
        return True

    except Exception as e:
        log_test("Health Check", "FAIL")
        log_error(str(e))
        return False

def test_ohlcv_single():
    """Test single symbol OHLCV"""
    try:
        start = time.time()
        r = requests.get(
            f"{BASE_URL}/api/v1/ohlcv",
            params={
                "symbol": "INFY",
                "from": "2024-01-01",
                "to": "2024-01-31"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        data = r.json()

        assert "meta" in data, "Missing 'meta'"
        assert "data" in data, "Missing 'data'"
        assert data["meta"]["symbol"] == "INFY", "Symbol mismatch"
        assert data["meta"]["count"] > 0, "No data returned"
        assert len(data["data"]) == data["meta"]["count"], "Count mismatch"

        # Check OHLCV fields
        first_record = data["data"][0]
        required_fields = ["date", "open", "high", "low", "close", "volume"]
        for field in required_fields:
            assert field in first_record, f"Missing '{field}' in data"

        log_test("Single Symbol OHLCV (INFY, 31 days)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Single Symbol OHLCV", "FAIL")
        log_error(str(e))
        return False

def test_ohlcv_multi():
    """Test multiple symbols OHLCV"""
    try:
        start = time.time()
        r = requests.get(
            f"{BASE_URL}/api/v1/ohlcv/multi",
            params={
                "symbols": "INFY,TCS,RELIANCE",
                "from": "2024-01-01",
                "to": "2024-01-31"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        data = r.json()

        assert data["meta"]["symbols"] == ["INFY", "TCS", "RELIANCE"], "Symbol list mismatch"
        assert len(data["data"]) == 3, f"Expected 3 symbols, got {len(data['data'])}"
        assert data["meta"]["count"] > 0, "No data"

        log_test("Multiple Symbols OHLCV (3 symbols × 31 days)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Multiple Symbols OHLCV", "FAIL")
        log_error(str(e))
        return False

def test_ohlcv_bulk():
    """Test bulk query (10 symbols)"""
    try:
        start = time.time()
        symbols = "INFY,TCS,RELIANCE,HDFCBANK,ICICIBANK,SBIN,BHARTIARTL,WIPRO,AXISBANK,HINDUNILVR"
        r = requests.get(
            f"{BASE_URL}/api/v1/ohlcv/multi",
            params={
                "symbols": symbols,
                "from": "2024-01-01",
                "to": "2024-12-31"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        data = r.json()
        assert len(data["data"]) == 10, f"Expected 10 symbols"
        assert data["meta"]["count"] > 2000, "Expected >2500 records (10 × 250 days)"

        log_test("Bulk Query (10 symbols × 365 days)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Bulk Query", "FAIL")
        log_error(str(e))
        return False

def test_symbols():
    """Test symbol list endpoint"""
    try:
        start = time.time()
        r = requests.get(f"{BASE_URL}/api/v1/symbols", timeout=TIMEOUT)
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        data = r.json()

        assert "count" in data, "Missing 'count'"
        assert "data" in data, "Missing 'data'"
        assert data["count"] == 10, f"Expected 10 symbols, got {data['count']}"

        # Check symbol fields
        first_symbol = data["data"][0]
        assert "symbol" in first_symbol, "Missing 'symbol'"
        assert "name" in first_symbol, "Missing 'name'"
        assert "sector" in first_symbol, "Missing 'sector'"

        log_test("Symbol List (10 symbols)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Symbol List", "FAIL")
        log_error(str(e))
        return False

def test_chart_daily():
    """Test daily chart generation"""
    try:
        start = time.time()
        r = requests.get(
            f"{BASE_URL}/api/v1/charts/daily",
            params={
                "symbol": "INFY",
                "from": "2024-01-01",
                "to": "2024-12-31"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        assert r.headers.get("content-type") == "image/svg+xml", f"Wrong content-type: {r.headers.get('content-type')}"
        assert b"<svg" in r.content, "Invalid SVG content"
        assert b"INFY" in r.content, "Symbol not in chart"
        assert len(r.content) > 1000, "Chart too small"

        log_test("Daily Chart (SVG, INFY, 365 days)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Daily Chart", "FAIL")
        log_error(str(e))
        return False

def test_chart_daily_with_indicators():
    """Test daily chart with indicators"""
    try:
        start = time.time()
        r = requests.get(
            f"{BASE_URL}/api/v1/charts/daily",
            params={
                "symbol": "TCS",
                "from": "2024-01-01",
                "to": "2024-12-31",
                "indicators": "ema,rsi,macd"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        assert b"<svg" in r.content, "Invalid SVG"

        log_test("Daily Chart (SVG + Indicators, TCS)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Daily Chart with Indicators", "FAIL")
        log_error(str(e))
        return False

def test_chart_weekly():
    """Test weekly chart generation"""
    try:
        start = time.time()
        r = requests.get(
            f"{BASE_URL}/api/v1/charts/weekly",
            params={
                "symbol": "RELIANCE",
                "from": "2020-01-01",
                "to": "2024-12-31"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        assert r.headers.get("content-type") == "image/svg+xml", "Wrong content-type"
        assert b"<svg" in r.content, "Invalid SVG"

        log_test("Weekly Chart (SVG, RELIANCE, 5 years)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Weekly Chart", "FAIL")
        log_error(str(e))
        return False

def test_indicators():
    """Test indicator values endpoint"""
    try:
        start = time.time()
        r = requests.get(
            f"{BASE_URL}/api/v1/indicators",
            params={
                "symbol": "HDFCBANK",
                "from": "2024-01-01",
                "to": "2024-01-31",
                "indicators": "ema,rsi,atr"
            },
            timeout=TIMEOUT
        )
        duration = time.time() - start

        assert r.status_code == 200, f"Status: {r.status_code}"
        data = r.json()

        assert "meta" in data, "Missing 'meta'"
        assert "data" in data, "Missing 'data'"
        assert len(data["data"]) > 0, "No data"

        # Check indicators are present
        first_record = data["data"][10]  # Skip NaN values
        assert "ema_10" in first_record, "Missing EMA 10"
        assert "rsi_14" in first_record, "Missing RSI 14"
        assert "atr" in first_record, "Missing ATR"

        log_test("Indicator Values (EMA, RSI, ATR)", "PASS", duration)
        return True

    except Exception as e:
        log_test("Indicator Values", "FAIL")
        log_error(str(e))
        return False

def test_invalid_date_range():
    """Test error handling: invalid date range"""
    try:
        r = requests.get(
            f"{BASE_URL}/api/v1/ohlcv",
            params={
                "symbol": "INFY",
                "from": "2024-12-31",
                "to": "2024-01-01"
            },
            timeout=TIMEOUT
        )

        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
        data = r.json()
        assert "detail" in data, "Missing error detail"

        log_test("Error Handling: Invalid Date Range", "PASS")
        return True

    except Exception as e:
        log_test("Error Handling: Invalid Date Range", "FAIL")
        log_error(str(e))
        return False

def test_too_many_symbols():
    """Test error handling: too many symbols"""
    try:
        symbols = ",".join([f"SYM{i}" for i in range(51)])  # 51 symbols
        r = requests.get(
            f"{BASE_URL}/api/v1/ohlcv/multi",
            params={
                "symbols": symbols,
                "from": "2024-01-01",
                "to": "2024-01-31"
            },
            timeout=TIMEOUT
        )

        assert r.status_code == 400, f"Expected 400, got {r.status_code}"

        log_test("Error Handling: Too Many Symbols (>50)", "PASS")
        return True

    except Exception as e:
        log_test("Error Handling: Too Many Symbols", "FAIL")
        log_error(str(e))
        return False

def test_nonexistent_symbol():
    """Test error handling: nonexistent symbol"""
    try:
        r = requests.get(
            f"{BASE_URL}/api/v1/ohlcv",
            params={
                "symbol": "NONEXISTENT",
                "from": "2024-01-01",
                "to": "2024-01-31"
            },
            timeout=TIMEOUT
        )

        # Should handle gracefully (200 with empty or 404)
        assert r.status_code in [200, 404], f"Unexpected status: {r.status_code}"

        log_test("Error Handling: Nonexistent Symbol", "PASS")
        return True

    except Exception as e:
        log_test("Error Handling: Nonexistent Symbol", "FAIL")
        log_error(str(e))
        return False

def test_cache_headers():
    """Test cache headers"""
    try:
        r = requests.get(
            f"{BASE_URL}/api/v1/charts/daily",
            params={
                "symbol": "INFY",
                "from": "2024-01-01",
                "to": "2024-12-31"
            }
        )

        assert r.status_code == 200, f"Status: {r.status_code}"
        assert "cache-control" in r.headers, "Missing Cache-Control header"
        assert "max-age" in r.headers["cache-control"], "Missing max-age in Cache-Control"

        log_test("Cache Headers Present", "PASS")
        return True

    except Exception as e:
        log_test("Cache Headers", "FAIL")
        log_error(str(e))
        return False

# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():
    """Run all tests"""
    print("\n" + "="*70)
    print("🧪 Market Data API - Automated Test Suite")
    print("="*70 + "\n")

    tests = [
        # Basic functionality
        test_health,
        test_ohlcv_single,
        test_ohlcv_multi,
        test_ohlcv_bulk,
        test_symbols,

        # Charting
        test_chart_daily,
        test_chart_daily_with_indicators,
        test_chart_weekly,

        # Indicators
        test_indicators,

        # Error handling
        test_invalid_date_range,
        test_too_many_symbols,
        test_nonexistent_symbol,

        # Headers
        test_cache_headers,
    ]

    for test_func in tests:
        try:
            test_func()
        except Exception as e:
            log_error(f"Exception in {test_func.__name__}: {e}")

    # Summary
    print("\n" + "="*70)
    print(f"📊 Test Results: {results['passed']} passed, {results['failed']} failed")
    print("="*70)

    if results["failed"] > 0:
        print("\n⚠️  Failed Tests:")
        for error in results["errors"]:
            print(f"   • {error}")
        return False
    else:
        print("\n✅ All tests passed!")
        return True

if __name__ == "__main__":
    success = run_all_tests()
    exit(0 if success else 1)
