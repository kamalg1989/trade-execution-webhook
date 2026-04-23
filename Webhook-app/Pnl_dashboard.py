# ==============================================
# 📊 P/L DASHBOARD — Query & Reporting Module
# Provides insights into trading performance
# Works with the dual-table architecture
# ==============================================

import sqlite3
import json
from datetime import datetime, timezone, timedelta
import os

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trades.db")


class PnLDashboard:
    """P/L tracking and reporting dashboard."""

    def __init__(self, db_path=DB_FILE):
        self.db = db_path

    # ==========================
    # PENDING ORDERS REPORTS
    # ==========================
    def get_pending_summary(self):
        """Summary of orders awaiting Dhan confirmation."""
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status='PENDING' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status='FAILED' THEN 1 ELSE 0 END) as failed,
                    SUM(qty * entry_price) as total_value
                FROM pending_orders
            """).fetchone()

        return {
            "total": rows[0] or 0,
            "pending": rows[1] or 0,
            "failed": rows[2] or 0,
            "total_value_inr": round(rows[3] or 0, 2),
        }

    def get_pending_orders(self, status="PENDING"):
        """List pending orders."""
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT setup_id, symbol, qty, entry_price, sl_price, target_price,
                       score, base_stage, placed_at, attempt_count, last_error
                FROM pending_orders
                WHERE status = ?
                ORDER BY placed_at DESC
            """, (status,)).fetchall()

        return [dict(r) for r in rows]

    # ==========================
    # EXECUTED ORDERS REPORTS
    # ==========================
    def get_executed_summary(self):
        """Summary of filled/closed orders."""
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) as open_count,
                    SUM(CASE WHEN status='FILLED' THEN 1 ELSE 0 END) as filled_count,
                    SUM(CASE WHEN status LIKE 'CLOSED_%' THEN 1 ELSE 0 END) as closed_count,
                    SUM(qty_ordered) as total_qty,
                    SUM(qty_executed) as total_qty_executed,
                    SUM(pnl) as total_pnl,
                    AVG(pnl_percent) as avg_pnl_pct,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losing_trades
                FROM executed_orders
            """).fetchone()

        return {
            "total_orders": rows[0] or 0,
            "open": rows[1] or 0,
            "filled": rows[2] or 0,
            "closed": rows[3] or 0,
            "total_qty": rows[4] or 0,
            "total_qty_executed": rows[5] or 0,
            "total_pnl_inr": round(rows[6] or 0, 2),
            "avg_pnl_percent": round(rows[7] or 0, 2),
            "win_count": rows[8] or 0,
            "loss_count": rows[9] or 0,
            "win_rate_pct": round((rows[8] or 0) / ((rows[8] or 0) + (rows[9] or 0)) * 100, 2) if (rows[8] or 0) + (rows[9] or 0) > 0 else 0,
        }

    def get_open_positions(self):
        """Current open positions with unrealized P/L."""
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT 
                    setup_id, symbol, dhan_order_id, qty_executed, entry_price_executed,
                    current_price, sl_price, target_price, pnl, pnl_percent,
                    placed_at, current_price_update_at
                FROM executed_orders
                WHERE status = 'FILLED'
                ORDER BY placed_at DESC
            """).fetchall()

        return [dict(r) for r in rows]

    def get_closed_positions(self, limit=50):
        """Recently closed trades with realized P/L."""
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT 
                    setup_id, symbol, qty_executed, entry_price_executed,
                    current_price, pnl, pnl_percent, status,
                    placed_at, current_price_update_at
                FROM executed_orders
                WHERE status LIKE 'CLOSED_%'
                ORDER BY current_price_update_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

        return [dict(r) for r in rows]

    # ==========================
    # PERFORMANCE ANALYTICS
    # ==========================
    def get_daily_pnl(self, days=30):
        """Daily P/L aggregation."""
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT 
                    DATE(executed_at) as trade_date,
                    COUNT(*) as trade_count,
                    SUM(pnl) as daily_pnl,
                    AVG(pnl_percent) as avg_pnl_pct,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
                FROM executed_orders
                WHERE executed_at IS NOT NULL
                  AND DATE(executed_at) >= DATE('now', '-' || ? || ' days')
                GROUP BY trade_date
                ORDER BY trade_date DESC
            """, (days,)).fetchall()

        return [dict(r) for r in rows]

    def get_symbol_performance(self):
        """P/L by symbol (realized trades only)."""
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT 
                    symbol,
                    COUNT(*) as trades,
                    SUM(pnl) as total_pnl,
                    AVG(pnl_percent) as avg_pnl_pct,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    MAX(pnl) as best_trade,
                    MIN(pnl) as worst_trade
                FROM executed_orders
                WHERE status LIKE 'CLOSED_%'
                GROUP BY symbol
                ORDER BY total_pnl DESC
            """).fetchall()

        return [dict(r) for r in rows]

    def get_base_stage_performance(self):
        """P/L analysis by base stage."""
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT 
                    base_stage,
                    COUNT(*) as trades,
                    SUM(pnl) as total_pnl,
                    AVG(pnl_percent) as avg_pnl_pct,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    COUNT(*) - SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as losses
                FROM executed_orders
                WHERE status LIKE 'CLOSED_%'
                  AND base_stage > 0
                GROUP BY base_stage
                ORDER BY base_stage
            """).fetchall()

        return [dict(r) for r in rows]

    # ==========================
    # RISK METRICS
    # ==========================
    def get_risk_metrics(self):
        """Overall risk/reward metrics."""
        with sqlite3.connect(self.db) as conn:
            rows = conn.execute("""
                SELECT 
                    SUM(qty_executed * (entry_price_executed - sl_price)) as total_risk,
                    AVG(qty_executed * (entry_price_executed - sl_price)) as avg_risk_per_trade,
                    SUM(qty_executed * (target_price - entry_price_executed)) as total_potential_reward,
                    AVG(CASE WHEN status LIKE 'CLOSED_%' 
                             THEN ABS(pnl) 
                             ELSE qty_executed * (target_price - entry_price_executed) 
                        END) as avg_reward_per_trade,
                    SUM(CASE WHEN pnl > 0 THEN ABS(pnl) ELSE 0 END) as sum_wins,
                    SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as sum_losses,
                    AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win,
                    AVG(CASE WHEN pnl < 0 THEN pnl ELSE NULL END) as avg_loss
                FROM executed_orders
                WHERE status LIKE 'CLOSED_%'
            """).fetchone()

        total_risk = rows[0] or 0
        avg_risk = rows[1] or 0
        total_reward = rows[2] or 0
        avg_reward = rows[3] or 0
        sum_wins = rows[4] or 0
        sum_losses = rows[5] or 0
        avg_win = rows[6] or 0
        avg_loss = rows[7] or 0

        profit_factor = (sum_wins / sum_losses) if sum_losses != 0 else (1 if sum_wins > 0 else 0)

        return {
            "total_risk_exposed_inr": round(total_risk, 2),
            "avg_risk_per_trade_inr": round(avg_risk, 2),
            "total_potential_reward_inr": round(total_reward, 2),
            "avg_reward_per_trade_inr": round(avg_reward, 2),
            "total_wins_inr": round(sum_wins, 2),
            "total_losses_inr": round(sum_losses, 2),
            "avg_win_inr": round(avg_win, 2),
            "avg_loss_inr": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
        }

    # ==========================
    # QUICK STATS
    # ==========================
    def get_dashboard_snapshot(self):
        """One-liner summary for quick view."""
        pending = self.get_pending_summary()
        executed = self.get_executed_summary()
        risk = self.get_risk_metrics()

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pending": pending,
            "executed": executed,
            "risk": risk,
        }

    def print_dashboard(self):
        """Pretty-print full dashboard."""
        snapshot = self.get_dashboard_snapshot()

        print("\n" + "=" * 80)
        print("📊 OHM TRADING DASHBOARD")
        print("=" * 80)
        print(f"Updated: {snapshot['timestamp']}\n")

        # PENDING
        pend = snapshot["pending"]
        print("📋 PENDING ORDERS (awaiting Dhan confirmation)")
        print(f"   Total: {pend['total']} | Pending: {pend['pending']} | Failed: {pend['failed']}")
        print(f"   Value: ₹{pend['total_value_inr']:,.2f}\n")

        # EXECUTED
        exe = snapshot["executed"]
        print("✅ EXECUTED ORDERS")
        print(f"   Total: {exe['total_orders']} | Open: {exe['open']} | Filled: {exe['filled']} | Closed: {exe['closed']}")
        print(f"   Qty: {exe['total_qty']} ordered, {exe['total_qty_executed']} executed")
        print(f"   PnL: ₹{exe['total_pnl_inr']:,.2f} ({exe['avg_pnl_percent']:+.2f}%)")
        print(f"   Win Rate: {exe['win_count']}/{exe['win_count'] + exe['loss_count']} ({exe['win_rate_pct']:.1f}%)\n")

        # RISK
        rsk = snapshot["risk"]
        print("⚔️ RISK METRICS")
        print(f"   Total Risk: ₹{rsk['total_risk_exposed_inr']:,.2f}")
        print(f"   Profit Factor: {rsk['profit_factor']:.2f}x")
        print(f"   Sum of Wins: ₹{rsk['total_wins_inr']:,.2f}")
        print(f"   Sum of Losses: ₹{rsk['total_losses_inr']:,.2f}")
        print(f"   Avg Win/Loss: ₹{rsk['avg_win_inr']:,.2f} / ₹{rsk['avg_loss_inr']:,.2f}\n")

        # OPEN POSITIONS
        open_pos = self.get_open_positions()
        if open_pos:
            print("📈 OPEN POSITIONS")
            total_unrealized = 0
            for pos in open_pos:
                mark = "📗" if pos["pnl"] >= 0 else "📕"
                total_unrealized += pos["pnl"]
                print(f"   {mark} {pos['symbol']:12} | Qty:{pos['qty_executed']:4d} | "
                      f"Entry:₹{pos['entry_price_executed']:8.2f} | "
                      f"Current:₹{pos['current_price']:8.2f} | "
                      f"PnL: ₹{pos['pnl']:10.2f} ({pos['pnl_percent']:+6.2f}%)")
            print(f"   Total Unrealized: ₹{total_unrealized:,.2f}\n")

        # DAILY PnL (last 7 days)
        daily = self.get_daily_pnl(days=7)
        if daily:
            print("📅 LAST 7 DAYS")
            for day in daily:
                mark = "📗" if day["daily_pnl"] >= 0 else "📕"
                print(f"   {mark} {day['trade_date']} | Trades:{day['trade_count']:2d} | "
                      f"PnL: ₹{day['daily_pnl']:10.2f} | Avg: {day['avg_pnl_pct']:+.2f}% | "
                      f"Wins: {day['wins']}")

        print("\n" + "=" * 80)


def export_report(output_file="pnl_report.json"):
    """Export dashboard to JSON."""
    dashboard = PnLDashboard()
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pending_summary": dashboard.get_pending_summary(),
        "pending_orders": dashboard.get_pending_orders(),
        "executed_summary": dashboard.get_executed_summary(),
        "open_positions": dashboard.get_open_positions(),
        "closed_positions": dashboard.get_closed_positions(limit=20),
        "daily_pnl_7d": dashboard.get_daily_pnl(days=7),
        "symbol_performance": dashboard.get_symbol_performance(),
        "base_stage_performance": dashboard.get_base_stage_performance(),
        "risk_metrics": dashboard.get_risk_metrics(),
    }

    with open(output_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"✅ Report exported to {output_file}")
    return report


if __name__ == "__main__":
    # Usage examples
    dashboard = PnLDashboard()

    # Print full dashboard
    dashboard.print_dashboard()

    # Export JSON report
    export_report()