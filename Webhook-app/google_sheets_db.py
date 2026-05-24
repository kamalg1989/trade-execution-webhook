# ==============================================
# 📊 GOOGLE SHEETS DATABASE MODULE  (consolidated data layer)
#
# Single source of truth for ALL Google Sheets operations.
# Imported by: entry_engine.py, sl_engine.py, app.py, intraday cron.
#
# Design ref: Trade Setup Enhancement v2, §9 (data layer) + §11 (schema).
#
# KEY PROPERTIES
#   • Batch writes only — never per-cell update_cell() in a loop.
#       - update_trade(): one update_cells() call, only the CHANGED cells.
#       - batch_update_trades(): ALL changed cells across ALL rows in ONE call.
#   • Resilient singleton — reactive re-init + one retry on a stale/failed
#     handle (app.py is a persistent process; its handle can go stale).
#   • Knows the full 24-column schema (15 legacy + 9 new per §11).
#   • Reads of new columns default sanely when blank, so legacy half-filled
#     rows degrade gracefully instead of crashing the trail math.
#
# NOTE ON COLUMN ORDER: the 9 new columns are grouped logically (trail block,
# then position, then exit block) rather than in the doc's §11 listing order.
# Functionally identical; reads better when eyeballing a row for debugging.
# ==============================================

import os
import json
import time
import uuid
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

# ==========================
# CONFIG
# ==========================
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
SERVICE_ACCOUNT_KEY_PATH = os.getenv("SERVICE_ACCOUNT_KEY_PATH")
SHEET_NAME = "Trades"
PORTFOLIO_SHEET_NAME = "Portfolio"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ---- Schema --------------------------------------------------------------
# Legacy 15 (order preserved). Column 8 renamed SL_Price -> Structural_SL.
LEGACY_COLUMNS = [
    "ID", "Symbol", "Security_ID", "Qty", "Entry_Price",
    "Entry_Time", "Status", "Structural_SL", "Target_Price",
    "Setup_ID", "Current_Price", "PnL", "PnL_Percent",
    "Updated_At", "Dhan_Order_ID",
]

# New 9 (§11), grouped: backstop, trail block, position, exit block.
NEW_COLUMNS = [
    "Safety_SL",            # P  — the −8% catastrophe level
    "Highest_Close",        # Q  — trail: highest daily close since entry
    "Trail_Phase",          # R  — trail: 1 fixed / 2 breakeven / 3 trailing
    "Previous_SL_Price",    # S  — trail: audit of how the stop climbed
    "Remaining_Qty",        # T  — shares left after a half-at-2R partial
    "Exit_Price",           # U  — exit block
    "Exit_Time",            # V  — exit block
    "Exit_Reason",          # W  — exit block: target/trail/structural/−8%/manual
    "Exit_Order_ID",        # X  — exit block: tracks cancel-and-replace sell
]

COLUMNS = LEGACY_COLUMNS + NEW_COLUMNS  # canonical 24-col order

# Canonical lifecycle statuses (data layer stores the string; callers honor these)
STATUS_PENDING = "PENDING"
STATUS_OPEN = "OPEN"
STATUS_PARTIAL = "PARTIAL"
STATUS_EXIT_PENDING = "EXIT_PENDING"
STATUS_CLOSED = "CLOSED"
VALID_STATUSES = {
    STATUS_PENDING, STATUS_OPEN, STATUS_PARTIAL, STATUS_EXIT_PENDING, STATUS_CLOSED
}

# Portfolio worksheet header (moved in wholesale from sl_engine)
PORTFOLIO_HEADERS = [
    "Date", "Active_Count", "Total_Open_PnL", "Total_Realized_PnL",
    "Win_Rate%", "Avg_Days_Held", "Best_Trade%", "Worst_Trade%",
    "Total_Trades", "Updated_At",
]

# Safety: refuse any bulk row deletion that would wipe more than this fraction
MAX_DELETE_FRACTION = 0.80

# Reactive-retry tuning
_MAX_RETRIES = 1          # one re-init + retry, then give up
_RATE_LIMIT_BACKOFF = 2.0  # seconds to wait on a 429 before retrying


# ==========================
# LOGGER
# ==========================
def log(*args):
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}]", *args, flush=True)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ==========================
# CONNECTION / SINGLETON  (reactive re-init only)
# ==========================
_client = None
_spreadsheet = None
_gsheet = None              # Trades worksheet
_portfolio_ws = None        # Portfolio worksheet (lazy)


def _connect():
    """(Re)build the client, spreadsheet, and Trades worksheet handles.

    Called on first use and again whenever a live handle turns out to be
    stale. Creates the Trades sheet with the full 24-col header if absent.
    """
    global _client, _spreadsheet, _gsheet

    if not SPREADSHEET_ID or not SERVICE_ACCOUNT_KEY_PATH:
        raise ValueError("SPREADSHEET_ID or SERVICE_ACCOUNT_KEY_PATH not set")
    if not os.path.exists(SERVICE_ACCOUNT_KEY_PATH):
        raise FileNotFoundError(f"Service account key not found: {SERVICE_ACCOUNT_KEY_PATH}")

    credentials = Credentials.from_service_account_file(
        SERVICE_ACCOUNT_KEY_PATH, scopes=SCOPES
    )
    _client = gspread.authorize(credentials)
    _spreadsheet = _client.open_by_key(SPREADSHEET_ID)

    try:
        _gsheet = _spreadsheet.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        log(f"Creating sheet '{SHEET_NAME}' with full 24-col header...")
        _gsheet = _spreadsheet.add_worksheet(
            title=SHEET_NAME, rows=1000, cols=max(26, len(COLUMNS))
        )
        _gsheet.insert_row(COLUMNS, 1)

    log("✅ Google Sheets connected")
    return _gsheet


def init_sheets():
    """Public initializer. Safe to call repeatedly; (re)builds the handle."""
    return _connect()


def get_sheet():
    """Return the Trades worksheet, connecting on first use.

    Reactive only — no liveness ping here. If the handle is stale, the next
    actual operation fails and _with_retry() transparently re-connects.
    """
    global _gsheet
    if _gsheet is None:
        _connect()
    return _gsheet


def _reset_handles():
    """Drop cached handles so the next _connect() rebuilds from scratch."""
    global _client, _spreadsheet, _gsheet, _portfolio_ws
    _client = _spreadsheet = _gsheet = _portfolio_ws = None


def _is_stale_or_auth_error(exc):
    """Heuristic: does this exception warrant a re-init + retry?"""
    if isinstance(exc, gspread.exceptions.APIError):
        # 401/403 (auth/perm) or 5xx (transient) — worth one reconnect.
        try:
            code = exc.response.status_code
        except Exception:
            return True
        return code in (401, 403, 500, 502, 503, 504)
    # Refreshed-credential / transport hiccups on a long-lived process.
    return isinstance(exc, (ConnectionError, RefreshError_t, BrokenPipeError))


# google-auth raises its own RefreshError; import defensively so the module
# still loads if the internal path ever changes.
try:
    from google.auth.exceptions import RefreshError as RefreshError_t
except Exception:  # pragma: no cover
    class RefreshError_t(Exception):
        pass


def _with_retry(fn, *args, **kwargs):
    """Run a sheet op with reactive re-init on stale handle + 429 backoff.

    On a 429 we back off once and retry without reconnecting. On a stale /
    auth / transient error we reconnect once and retry. Anything else, or a
    second failure, propagates to the caller (who logs and returns a default).
    """
    attempts = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            status = None
            try:
                status = e.response.status_code
            except Exception:
                pass

            if status == 429 and attempts < _MAX_RETRIES:
                attempts += 1
                log(f"⚠️ Rate limited (429); backing off {_RATE_LIMIT_BACKOFF}s and retrying...")
                time.sleep(_RATE_LIMIT_BACKOFF)
                continue

            if _is_stale_or_auth_error(e) and attempts < _MAX_RETRIES:
                attempts += 1
                log(f"⚠️ Sheet handle stale/auth error ({status}); reconnecting and retrying...")
                _reset_handles()
                _connect()
                continue
            raise
        except Exception as e:
            if _is_stale_or_auth_error(e) and attempts < _MAX_RETRIES:
                attempts += 1
                log(f"⚠️ Connection error ({type(e).__name__}); reconnecting and retrying...")
                _reset_handles()
                _connect()
                continue
            raise


# ==========================
# HEADER / SCHEMA HELPERS
# ==========================
def _read_headers():
    """Return the live header row (list of column names)."""
    sheet = get_sheet()
    return _with_retry(sheet.row_values, 1)


def ensure_schema():
    """Ensure row 1 carries all 24 canonical columns.

    Only ever APPENDS missing columns to the right; never reorders or renames
    existing cells (that would desync historical rows). Returns the final
    header list. Safe to call once at startup.
    """
    sheet = get_sheet()
    current = _read_headers()

    if current == COLUMNS:
        return current

    # Append any canonical columns missing from the live header.
    missing = [c for c in COLUMNS if c not in current]
    if not missing:
        return current

    start_col = len(current) + 1
    cells = [
        gspread.Cell(1, start_col + i, name) for i, name in enumerate(missing)
    ]
    log(f"📋 Appending {len(missing)} missing header(s): {', '.join(missing)}")
    _with_retry(sheet.update_cells, cells, value_input_option="USER_ENTERED")
    return _read_headers()


# ==========================
# DEFAULTS-ON-BLANK  (§ legacy rows degrade gracefully)
# ==========================
def _f(value, default=0.0):
    """float() that tolerates blanks/garbage."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def _apply_read_defaults(trade):
    """Return a copy of `trade` with sane defaults for blank NEW columns.

    Legacy open rows won't have the new columns populated; the trail and
    partial-exit logic READ these, so a blank must not crash them.
        Highest_Close  -> max(Entry_Price, Current_Price)
        Remaining_Qty  -> Qty
        Trail_Phase    -> 1
    Other new columns default to "" (exit fields) / Structural_SL passthrough.
    """
    t = dict(trade)

    entry = _f(t.get("Entry_Price"))
    current = _f(t.get("Current_Price"))
    qty = t.get("Qty")

    if not t.get("Highest_Close"):
        t["Highest_Close"] = max(entry, current) if (entry or current) else ""

    if not t.get("Remaining_Qty"):
        t["Remaining_Qty"] = qty if qty not in (None, "") else ""

    if not t.get("Trail_Phase"):
        t["Trail_Phase"] = 1

    # Safety_SL has no safe synthetic default (it's a real broker level);
    # leave blank so callers can detect "not yet placed".
    for k in ("Safety_SL", "Previous_SL_Price",
              "Exit_Price", "Exit_Time", "Exit_Reason", "Exit_Order_ID"):
        if k not in t:
            t[k] = ""

    return t


# ==========================
# ROW LOCATION
# ==========================
def _match(trade, symbol=None, trade_id=None, dhan_order_id=None, security_id=None):
    if trade_id and trade.get("ID") == trade_id:
        return True
    if dhan_order_id and trade.get("Dhan_Order_ID") == dhan_order_id:
        return True
    if symbol and trade.get("Symbol", "").upper() == symbol.upper():
        # If a security_id is also given, require both to match (precise).
        if security_id:
            return str(trade.get("Security_ID", "")) == str(security_id)
        return True
    if security_id and not symbol:
        return str(trade.get("Security_ID", "")) == str(security_id)
    return False


def _find_row_num(symbol=None, trade_id=None, dhan_order_id=None, security_id=None):
    """Return the 1-indexed sheet row number for a trade, or None."""
    sheet = get_sheet()
    all_values = _with_retry(sheet.get_all_values)
    if len(all_values) < 2:
        return None
    headers = all_values[0]
    for idx, row in enumerate(all_values[1:], start=2):
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        trade = dict(zip(headers, row))
        if _match(trade, symbol, trade_id, dhan_order_id, security_id):
            return idx
    return None


# ==========================
# READ
# ==========================
def get_all_trades(apply_defaults=True):
    """All trades as list of dicts. New-column blanks defaulted by default."""
    try:
        sheet = get_sheet()
        all_values = _with_retry(sheet.get_all_values)
        if len(all_values) < 2:
            return []
        headers = all_values[0]
        out = []
        for row in all_values[1:]:
            if len(row) < len(headers):
                row = row + [""] * (len(headers) - len(row))
            trade = dict(zip(headers, row))
            out.append(_apply_read_defaults(trade) if apply_defaults else trade)
        log(f"✅ Retrieved {len(out)} trades")
        return out
    except Exception as e:
        log(f"❌ Failed to get trades: {e}")
        return []


def get_trade(symbol=None, trade_id=None, dhan_order_id=None,
              security_id=None, apply_defaults=True):
    """Single trade by any key, or None."""
    try:
        for trade in get_all_trades(apply_defaults=apply_defaults):
            if _match(trade, symbol, trade_id, dhan_order_id, security_id):
                return trade
        return None
    except Exception as e:
        log(f"❌ Failed to get trade: {e}")
        return None


def filter_by_status(status):
    return [t for t in get_all_trades() if t.get("Status", "").upper() == status.upper()]


def filter_by_symbol(symbol):
    return [t for t in get_all_trades() if t.get("Symbol", "").upper() == symbol.upper()]


def get_open_trades():
    """Anything not CLOSED and not PENDING is considered live/open-ish."""
    return [t for t in get_all_trades()
            if t.get("Status", "").upper() not in (STATUS_CLOSED, STATUS_PENDING)]


def get_closed_trades():
    return filter_by_status(STATUS_CLOSED)


# ==========================
# CREATE
# ==========================
def add_trade(symbol, sec_id, qty, entry_price, structural_sl, target_price,
              setup_id, dhan_order_id, safety_sl=""):
    """Append a new trade. Remaining_Qty seeds to full qty; Trail_Phase=1.

    Returns trade_id or None. Single append_row call (one API write).
    """
    try:
        sheet = get_sheet()
        ensure_schema()
        ts = _now_iso()
        trade_id = str(uuid.uuid4())[:8]

        record = {
            "ID": trade_id,
            "Symbol": symbol,
            "Security_ID": sec_id,
            "Qty": qty,
            "Entry_Price": entry_price,
            "Entry_Time": ts,
            "Status": STATUS_PENDING,
            "Structural_SL": structural_sl,
            "Target_Price": target_price,
            "Setup_ID": setup_id,
            "Current_Price": entry_price,
            "PnL": 0,
            "PnL_Percent": 0,
            "Updated_At": ts,
            "Dhan_Order_ID": dhan_order_id,
            "Safety_SL": safety_sl,
            "Highest_Close": entry_price,
            "Trail_Phase": 1,
            "Previous_SL_Price": "",
            "Remaining_Qty": qty,
            "Exit_Price": "",
            "Exit_Time": "",
            "Exit_Reason": "",
            "Exit_Order_ID": "",
        }
        row = [record.get(col, "") for col in COLUMNS]
        _with_retry(sheet.append_row, row, value_input_option="USER_ENTERED")
        log(f"✅ Added trade: {symbol} (ID: {trade_id})")
        return trade_id
    except Exception as e:
        log(f"❌ Failed to add trade: {e}")
        return None


# ==========================
# UPDATE  (only-changed-cells, single batched write)
# ==========================
def _build_changed_cells(row_num, headers, updates, touch_timestamp=True):
    """Build a list of gspread.Cell for ONLY the fields that are changing.

    No read-before-write; we never touch cells the caller didn't name, so
    untouched data can't be clobbered. Unknown keys are ignored (logged).
    """
    cells = []
    seen_updated_at = False
    for key, value in updates.items():
        if key not in headers:
            log(f"⚠️ Ignoring unknown column in update: {key}")
            continue
        col_idx = headers.index(key) + 1
        cells.append(gspread.Cell(row_num, col_idx, value))
        if key == "Updated_At":
            seen_updated_at = True

    if touch_timestamp and not seen_updated_at and "Updated_At" in headers:
        col_idx = headers.index("Updated_At") + 1
        cells.append(gspread.Cell(row_num, col_idx, _now_iso()))
    return cells


def update_trade(symbol=None, trade_id=None, dhan_order_id=None,
                 security_id=None, **updates):
    """Update one trade. ONE update_cells() call, only changed cells.

    Always refreshes Updated_At unless caller set it explicitly.
    Returns True/False.
    """
    try:
        sheet = get_sheet()
        headers = _read_headers()
        row_num = _find_row_num(symbol, trade_id, dhan_order_id, security_id)
        if not row_num:
            log(f"❌ Trade not found: {symbol or trade_id or dhan_order_id or security_id}")
            return False

        cells = _build_changed_cells(row_num, headers, updates)
        if not cells:
            log("⚠️ No valid fields to update")
            return False

        _with_retry(sheet.update_cells, cells, value_input_option="USER_ENTERED")
        log(f"✅ Updated trade row {row_num} ({len(cells)} cell(s))")
        return True
    except Exception as e:
        log(f"❌ Failed to update trade: {e}")
        return False


def batch_update_trades(updates_list):
    """Update MANY trades in ONE API round-trip.

    `updates_list`: list of dicts, each carrying exactly one locator
        (symbol / trade_id / dhan_order_id / security_id) plus the fields to
        change. The caller's dicts are NOT mutated.

    All changed cells across all rows are collected into a single
    update_cells() call — this is the real 429 killer for the SL engine's
    multi-row runs. Rows that can't be located are skipped (and reported).

    Returns dict: {"updated": n_rows, "skipped": n_missing, "cells": n_cells}.
    """
    try:
        sheet = get_sheet()
        headers = _read_headers()

        # One read of the whole sheet to resolve every locator to a row num.
        all_values = _with_retry(sheet.get_all_values)
        if len(all_values) < 2:
            log("⚠️ Sheet empty; nothing to batch-update")
            return {"updated": 0, "skipped": len(updates_list), "cells": 0}

        live_headers = all_values[0]
        rows = []
        for idx, row in enumerate(all_values[1:], start=2):
            if len(row) < len(live_headers):
                row = row + [""] * (len(live_headers) - len(row))
            rows.append((idx, dict(zip(live_headers, row))))

        all_cells = []
        updated = skipped = 0

        for spec in updates_list:
            spec = dict(spec)  # copy — never mutate the caller's dict
            symbol = spec.pop("symbol", None)
            trade_id = spec.pop("trade_id", None)
            dhan_order_id = spec.pop("dhan_order_id", None)
            security_id = spec.pop("security_id", None)

            row_num = None
            for idx, trade in rows:
                if _match(trade, symbol, trade_id, dhan_order_id, security_id):
                    row_num = idx
                    break

            if not row_num:
                skipped += 1
                log(f"⚠️ batch: trade not found "
                    f"({symbol or trade_id or dhan_order_id or security_id})")
                continue

            cells = _build_changed_cells(row_num, headers, spec)
            if cells:
                all_cells.extend(cells)
                updated += 1

        if not all_cells:
            log("⚠️ batch: no cells to write")
            return {"updated": 0, "skipped": skipped, "cells": 0}

        _with_retry(sheet.update_cells, all_cells, value_input_option="USER_ENTERED")
        log(f"✅ Batch update: {updated} row(s), {len(all_cells)} cell(s), "
            f"{skipped} skipped — single API call")
        return {"updated": updated, "skipped": skipped, "cells": len(all_cells)}
    except Exception as e:
        log(f"❌ Batch update failed: {e}")
        return {"updated": 0, "skipped": len(updates_list), "cells": 0}


# ==========================
# DELETE  (cleanup split-B: mechanical row deletion + >80% guard)
# ==========================
def delete_trade(symbol=None, trade_id=None, dhan_order_id=None, security_id=None):
    """Delete a single trade by any key."""
    try:
        sheet = get_sheet()
        row_num = _find_row_num(symbol, trade_id, dhan_order_id, security_id)
        if not row_num:
            log(f"❌ Trade not found for delete")
            return False
        _with_retry(sheet.delete_rows, row_num)
        log(f"✅ Deleted trade (row {row_num})")
        return True
    except Exception as e:
        log(f"❌ Failed to delete trade: {e}")
        return False


def delete_trades_by_rows(row_nums, total_data_rows=None):
    """Delete the given 1-indexed sheet rows, with a hard >80% safety guard.

    This is the data-layer half of cleanup (split B): sl_engine decides WHICH
    rows are stale (it owns the Dhan calls + connection validation); this
    function just performs the deletion safely and refuses to nuke the sheet
    even if the caller is buggy.

    `row_nums`: iterable of 1-indexed row numbers (data rows, i.e. >= 2).
    `total_data_rows`: optional count of data rows for the fraction check;
        if omitted, it's read from the sheet.

    Returns dict {"deleted": n, "refused": bool, "reason": str|None}.
    """
    try:
        rows = sorted({int(r) for r in row_nums if int(r) >= 2}, reverse=True)
        if not rows:
            return {"deleted": 0, "refused": False, "reason": "no rows"}

        sheet = get_sheet()

        if total_data_rows is None:
            all_values = _with_retry(sheet.get_all_values)
            total_data_rows = max(0, len(all_values) - 1)

        if total_data_rows > 0:
            frac = len(rows) / total_data_rows
            if frac > MAX_DELETE_FRACTION:
                reason = (f"refusing to delete {len(rows)}/{total_data_rows} "
                          f"rows ({frac*100:.1f}% > {MAX_DELETE_FRACTION*100:.0f}%)")
                log(f"❌ SAFETY GUARD: {reason}")
                return {"deleted": 0, "refused": True, "reason": reason}

        deleted = 0
        # Delete bottom-up so earlier row numbers stay valid.
        for row_num in rows:
            try:
                _with_retry(sheet.delete_rows, row_num)
                deleted += 1
            except Exception as e:
                log(f"❌ Failed to delete row {row_num}: {e}")
        log(f"✅ Deleted {deleted} row(s)")
        return {"deleted": deleted, "refused": False, "reason": None}
    except Exception as e:
        log(f"❌ delete_trades_by_rows failed: {e}")
        return {"deleted": 0, "refused": False, "reason": str(e)}


# ==========================
# PORTFOLIO WORKSHEET  (moved in wholesale)
# ==========================
def get_portfolio_sheet():
    """Return the Portfolio worksheet, creating it (with header) if absent."""
    global _portfolio_ws
    if _portfolio_ws is not None:
        return _portfolio_ws

    def _get():
        global _portfolio_ws
        if _spreadsheet is None:
            _connect()
        try:
            _portfolio_ws = _spreadsheet.worksheet(PORTFOLIO_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            log(f"Creating '{PORTFOLIO_SHEET_NAME}' worksheet...")
            _portfolio_ws = _spreadsheet.add_worksheet(
                title=PORTFOLIO_SHEET_NAME, rows=1000, cols=len(PORTFOLIO_HEADERS)
            )
            _portfolio_ws.insert_row(PORTFOLIO_HEADERS, 1)
        return _portfolio_ws

    return _with_retry(_get)


def append_portfolio_snapshot(metrics):
    """Append a portfolio snapshot row.

    `metrics`: dict; recognized keys map to PORTFOLIO_HEADERS, missing -> 0.
    Date and Updated_At are filled automatically.
    """
    try:
        ws = get_portfolio_sheet()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now = _now_iso()
        row = [
            today,
            metrics.get("active_count", 0),
            metrics.get("open_pnl", 0),
            metrics.get("realized_pnl", 0),
            metrics.get("win_rate", 0),
            metrics.get("avg_days_held", 0),
            metrics.get("best_trade_pct", 0),
            metrics.get("worst_trade_pct", 0),
            metrics.get("total_trades", 0),
            now,
        ]
        _with_retry(ws.append_row, row, value_input_option="USER_ENTERED")
        log("✅ Portfolio snapshot appended")
        return True
    except Exception as e:
        log(f"❌ Portfolio append failed: {e}")
        return False


# ==========================
# MISC
# ==========================
def get_summary_stats():
    try:
        trades = get_all_trades()
        if not trades:
            return {"total": 0, "open": 0, "closed": 0, "total_pnl": 0}
        open_t = [t for t in trades if t.get("Status", "").upper() not in (STATUS_CLOSED, STATUS_PENDING)]
        closed_t = [t for t in trades if t.get("Status", "").upper() == STATUS_CLOSED]
        total_pnl = sum(_f(t.get("PnL")) for t in trades)
        return {
            "total": len(trades),
            "open": len(open_t),
            "closed": len(closed_t),
            "total_pnl": round(total_pnl, 2),
        }
    except Exception as e:
        log(f"❌ Failed to get stats: {e}")
        return {}


def export_to_json():
    try:
        return json.dumps(get_all_trades(), indent=2)
    except Exception as e:
        log(f"❌ Failed to export: {e}")
        return None

# ============================================================
# DROP-IN PATCH for google_sheets_db.py
# Replace the existing `_match` function with the two functions below.
# This makes symbol matching tolerant of .NS / .BO suffixes so that
# holdings returned as bare "SCHNEIDER" match sheet rows "SCHNEIDER.NS".
# ============================================================

def _norm_sym(s):
    """Strip .NS / .BO suffix and uppercase for tolerant symbol matching."""
    if not s:
        return ""
    return str(s).replace(".NS", "").replace(".BO", "").strip().upper()


def _match(trade, symbol=None, trade_id=None, dhan_order_id=None, security_id=None):
    if trade_id and trade.get("ID") == trade_id:
        return True
    if dhan_order_id and trade.get("Dhan_Order_ID") == dhan_order_id:
        return True
    if symbol and _norm_sym(trade.get("Symbol")) == _norm_sym(symbol):
        # If a security_id is also given, require both to match (precise).
        if security_id:
            return str(trade.get("Security_ID", "")) == str(security_id)
        return True
    if security_id and not symbol:
        return str(trade.get("Security_ID", "")) == str(security_id)
    return False


# ==========================
# DEMO
# ==========================
if __name__ == "__main__":
    log("=" * 80)
    log("📊 GOOGLE SHEETS DATABASE MODULE (consolidated) - DEMO")
    log("=" * 80)
    init_sheets()
    ensure_schema()
    trades = get_all_trades()
    log(f"Total trades: {len(trades)}")
    log(f"Open-ish: {len(get_open_trades())}")
    log(f"Stats: {get_summary_stats()}")
    log("✅ Module loaded successfully")