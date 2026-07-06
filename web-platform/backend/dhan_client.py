"""
Dhan API v2 client for the web platform.
Token is TOTP-generated and cached to a file (23h reuse) to respect rate limits.
"""

import os
import json
import time
import logging
import requests

logger = logging.getLogger(__name__)

BASE = "https://api.dhan.co/v2"
TOKEN_CACHE_FILE = "/root/trade-execution-webhook/.dhan_token_cache.json"
SCRIP_MASTER = "/root/trade-execution-webhook/api-scrip-master.csv"

_SECURITY_MAP = None  # symbol -> securityId (NSE_EQ)


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv("/root/trade-execution-webhook/.env")
    except ImportError:
        pass


def get_token(force_refresh=False):
    """File-cached Dhan access token (23h TTL)."""
    _load_env()

    if not force_refresh and os.path.exists(TOKEN_CACHE_FILE):
        try:
            with open(TOKEN_CACHE_FILE) as f:
                cache = json.load(f)
            if time.time() - cache.get("generated_at", 0) < 23 * 3600:
                return cache.get("token")
        except Exception:
            pass

    import pyotp
    client_id = os.getenv("DHAN_CLIENT_ID")
    pin = os.getenv("DHAN_PIN")
    secret = os.getenv("DHAN_TOTP_SECRET")
    if not all([client_id, pin, secret]):
        logger.error("Missing Dhan credentials in .env")
        return None

    totp = pyotp.TOTP(secret).now()
    r = requests.post(
        "https://auth.dhan.co/app/generateAccessToken",
        params={"dhanClientId": client_id, "pin": pin, "totp": totp},
        timeout=15,
    )
    if r.status_code != 200:
        logger.error(f"Token generation failed: {r.status_code} {r.text[:200]}")
        return None
    token = r.json().get("accessToken")
    if token:
        try:
            with open(TOKEN_CACHE_FILE, "w") as f:
                json.dump({"token": token, "generated_at": time.time()}, f)
        except Exception:
            pass
    return token


def _headers():
    token = get_token()
    if not token:
        raise RuntimeError("No Dhan token available")
    return {"access-token": token, "Content-Type": "application/json"}


def _is_invalid_token(resp):
    if resp.status_code == 401:
        return True
    # Dhan returns HTTP 400 with errorCode DH-906 for an expired/superseded token
    if resp.status_code == 400:
        try:
            return resp.json().get("errorCode") == "DH-906" or "invalid token" in resp.text.lower()
        except Exception:
            return "invalid token" in resp.text.lower()
    return False


def _get(path):
    r = requests.get(f"{BASE}{path}", headers=_headers(), timeout=20)
    if _is_invalid_token(r):
        r = requests.get(f"{BASE}{path}", headers={"access-token": get_token(force_refresh=True),
                                                   "Content-Type": "application/json"}, timeout=20)
    r.raise_for_status()
    return r.json()


def get_holdings():
    """Delivery holdings: [{tradingSymbol, securityId, totalQty, avgCostPrice, lastTradedPrice, ...}]"""
    try:
        return _get("/holdings")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []  # no holdings
        raise


def get_positions():
    """Intraday/derivative net positions"""
    try:
        return _get("/positions")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise


def get_orders():
    """Today's order book"""
    try:
        return _get("/orders")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise


def get_trades():
    """Today's trade book"""
    try:
        return _get("/trades")
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return []
        raise


def get_client_id():
    _load_env()
    return os.getenv("DHAN_CLIENT_ID")


def get_security_id(symbol):
    """Resolve NSE equity symbol -> Dhan securityId using local scrip master CSV."""
    global _SECURITY_MAP
    sym = symbol.replace(".NS", "").strip().upper()
    if _SECURITY_MAP is None:
        import csv
        _SECURITY_MAP = {}
        try:
            with open(SCRIP_MASTER, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("SEM_EXM_EXCH_ID") == "NSE" and row.get("SEM_SEGMENT") == "E":
                        _SECURITY_MAP[str(row["SEM_TRADING_SYMBOL"]).strip().upper()] = str(row["SEM_SMST_SECURITY_ID"]).strip()
        except Exception as e:
            logger.error(f"Failed to load scrip master: {e}")
            _SECURITY_MAP = {}
    return _SECURITY_MAP.get(sym)


def place_order(security_id, quantity, transaction_type, order_type="MARKET",
                price=0, trigger_price=0, product_type="CNC"):
    """Place an order. order_type: MARKET / LIMIT / STOP_LOSS / STOP_LOSS_MARKET"""
    payload = {
        "dhanClientId": get_client_id(),
        "transactionType": transaction_type,   # BUY / SELL
        "exchangeSegment": "NSE_EQ",
        "productType": product_type,           # CNC for delivery
        "orderType": order_type,
        "validity": "DAY",
        "securityId": str(security_id),
        "quantity": int(quantity),
        "price": float(price) if order_type in ("LIMIT", "STOP_LOSS") else 0,
        "triggerPrice": float(trigger_price) if order_type in ("STOP_LOSS", "STOP_LOSS_MARKET") else 0,
        "disclosedQuantity": 0,
        "afterMarketOrder": False,
    }
    r = requests.post(f"{BASE}/orders", headers=_headers(), json=payload, timeout=20)
    body = r.json() if r.text else {}
    if r.status_code not in (200, 201):
        return {"success": False, "error": body.get("errorMessage") or body.get("message") or r.text[:200]}
    return {"success": True, "orderId": body.get("orderId"), "orderStatus": body.get("orderStatus"), "raw": body}


def modify_order(order_id, quantity, order_type, trigger_price=0, price=0):
    payload = {
        "dhanClientId": get_client_id(),
        "orderId": str(order_id),
        "orderType": order_type,
        "quantity": int(quantity),
        "price": float(price),
        "triggerPrice": float(trigger_price),
        "disclosedQuantity": 0,
        "validity": "DAY",
    }
    r = requests.put(f"{BASE}/orders/{order_id}", headers=_headers(), json=payload, timeout=20)
    body = r.json() if r.text else {}
    if r.status_code != 200:
        return {"success": False, "error": body.get("errorMessage") or r.text[:200]}
    return {"success": True, "orderId": order_id, "raw": body}


def cancel_order(order_id):
    r = requests.delete(f"{BASE}/orders/{order_id}", headers=_headers(), timeout=20)
    body = r.json() if r.text else {}
    if r.status_code != 200:
        return {"success": False, "error": body.get("errorMessage") or r.text[:200]}
    return {"success": True, "orderId": order_id, "raw": body}
