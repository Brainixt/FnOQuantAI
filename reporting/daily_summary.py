"""
================================================================================
FnOQuantAI: Daily Performance & Activity Reporter
================================================================================
Generates comprehensive daily EOD and intraday status summaries:
- Signals generated today and their current statuses.
- Active open calls with unrealized P&L.
- Closed trades executed today with realized financial outcomes.
- Risk events, threshold violations, and trade blocks.
- Background worker execution health (SignalWorker, OpenCallWorker).
- Exportable to structured dictionary, human-readable ASCII report, and JSON.
================================================================================
"""

import json
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

from database import db_manager
from reporting.performance_analytics import PerformanceAnalytics

log = logging.getLogger("FnOQuantAI.Reporting.DailySummary")
IST = ZoneInfo("Asia/Kolkata")


class DailySummaryReporter:

    @classmethod
    def render_ascii_report(cls, target_date: Optional[date] = None) -> str:
        """Renders ASCII summary report for target_date."""
        summary = cls.generate_summary(target_date)
        return cls.format_text_report(summary)

    """Aggregates and formats daily operational, risk, and financial reports."""

    @classmethod
    def generate_summary(cls, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Extracts all operational data for target_date (defaults to today IST).
        Returns a structured summary dictionary.
        """
        report_date = target_date or datetime.now(IST).date()
        date_str = report_date.isoformat()

        # 1. Signals Created Today
        sql_signals = """
            SELECT signal_id, symbol, strategy_category, direction, status,
                   entry_reference_price, target_price, stop_loss_price,
                   total_score, created_at
            FROM fo_signals
            WHERE DATE(created_at AT TIME ZONE 'Asia/Kolkata') = %s
            ORDER BY created_at ASC;
        """
        signals_today = db_manager.execute_fo_query(sql_signals, [date_str], use_primary=True)

        # 2. Active Open Trades (Currently open)
        sql_open_trades = """
            SELECT trade_id, signal_id, symbol, trading_symbol, strategy_category,
                   direction, quantity, allocated_capital_inr, entry_reference_price,
                   actual_entry_fill_price, entry_time
            FROM fo_trades
            WHERE exit_time IS NULL
            ORDER BY entry_time ASC;
        """
        active_trades = db_manager.execute_fo_query(sql_open_trades, use_primary=True)

        # 3. Closed Trades Today
        sql_closed_trades = """
            SELECT trade_id, signal_id, symbol, trading_symbol, strategy_category,
                   direction, quantity, allocated_capital_inr, entry_reference_price,
                   actual_entry_fill_price, actual_exit_fill_price, gross_pnl,
                   estimated_transaction_costs, net_pnl, net_pnl_percentage,
                   entry_time, exit_time, holding_duration_seconds, exit_reason
            FROM fo_trades
            WHERE DATE(exit_time AT TIME ZONE 'Asia/Kolkata') = %s
            ORDER BY exit_time ASC;
        """
        closed_trades_today = db_manager.execute_fo_query(sql_closed_trades, [date_str], use_primary=True)

        # 4. Performance Analytics on Closed Trades Today
        metrics = PerformanceAnalytics.compute_metrics(closed_trades_today)

        # 5. Risk Events Today
        sql_risk = """
            SELECT id, event_type, symbol, signal_id, deviation_percentage,
                   details, created_at
            FROM fo_risk_events
            WHERE DATE(created_at AT TIME ZONE 'Asia/Kolkata') = %s
            ORDER BY created_at ASC;
        """
        risk_events_today = db_manager.execute_fo_query(sql_risk, [date_str], use_primary=True)

        # 6. Worker Execution Summary Today
        sql_exec = """
            SELECT worker_name, status, COUNT(*) as runs_count,
                   COALESCE(SUM(signals_generated), 0) as total_signals,
                   COALESCE(SUM(exit_events_triggered), 0) as total_exits,
                   COALESCE(AVG(duration_seconds), 0) as avg_duration
            FROM fo_worker_executions
            WHERE DATE(started_at AT TIME ZONE 'Asia/Kolkata') = %s
            GROUP BY worker_name, status;
        """
        worker_summary = db_manager.execute_fo_query(sql_exec, [date_str], use_primary=True)

        return {
            "report_date": date_str,
            "generated_at": datetime.now(IST).isoformat(),
            "signals_generated_today": len(signals_today),
            "signals_list": signals_today,
            "active_open_positions_count": len(active_trades),
            "active_positions": active_trades,
            "closed_trades_today_count": len(closed_trades_today),
            "closed_trades": closed_trades_today,
            "performance_metrics": metrics,
            "risk_events_today_count": len(risk_events_today),
            "risk_events": risk_events_today,
            "worker_execution_summary": worker_summary
        }

    @classmethod
    def format_text_report(cls, summary: Dict[str, Any]) -> str:
        """Renders summary dictionary as a formatted, human-readable ASCII report."""
        d = summary["report_date"]
        perf = summary["performance_metrics"]

        lines = [
            "=" * 78,
            f" FNOQUANTAI DAILY ACTIVITY & PERFORMANCE REPORT: {d}",
            "=" * 78,
            f"Report Generated: {summary['generated_at']}",
            "",
            "1. FINANCIAL & PERFORMANCE SUMMARY",
            "-" * 78,
            f"  Total Trades Closed Today:  {perf['total_trades']}",
            f"  Winning Trades:             {perf['winning_trades']} ({perf['win_rate_percentage']}%)",
            f"  Losing Trades:              {perf['losing_trades']}",
            f"  Profit Factor:              {perf['profit_factor']}",
            f"  Total Gross P&L:            Rs {perf['total_gross_pnl_inr']:>12,.2f}",
            f"  Transaction Costs (Est.):   Rs {perf['total_transaction_costs_inr']:>12,.2f}",
            f"  Total Net P&L:              Rs {perf['total_net_pnl_inr']:>12,.2f}",
            f"  Average Trade Duration:     {perf['average_holding_duration_seconds']:.0f} seconds",
            "",
            "2. STRATEGY BREAKDOWN",
            "-" * 78
        ]

        by_cat = perf.get("breakdown_by_category", {})
        if by_cat:
            lines.append(f"  {'Category':<16} | {'Trades':<8} | {'Win Rate':<10} | {'Gross P&L (Rs)':<16} | {'Net P&L (Rs)':<16}")
            lines.append("  " + "-" * 74)
            for cat, c_data in by_cat.items():
                lines.append(
                    f"  {cat:<16} | {c_data['trades_count']:<8} | {c_data['win_rate_percentage']:>8.1f}% | "
                    f"Rs {c_data['gross_pnl_inr']:>12,.2f} | Rs {c_data['net_pnl_inr']:>12,.2f}"
                )
        else:
            lines.append("  No closed trades recorded today.")

        lines.extend([
            "",
            f"3. ACTIVE OPEN POSITIONS ({summary['active_open_positions_count']})",
            "-" * 78
        ])
        open_pos = summary.get("active_positions", [])
        if open_pos:
            lines.append(f"  {'Symbol':<18} | {'Strategy':<14} | {'Dir':<5} | {'Fill Price':<12} | {'Entry Time (IST)'}")
            lines.append("  " + "-" * 74)
            for p in open_pos:
                e_time = str(p['entry_time'])[:19]
                lines.append(f"  {p['symbol']:<18} | {p['strategy_category']:<14} | {p['direction']:<5} | {float(p['actual_entry_fill_price']):>10.2f} | {e_time}")
        else:
            lines.append("  No active open positions.")

        lines.extend([
            "",
            f"4. RISK EVENTS & INTERVENTIONS ({summary['risk_events_today_count']})",
            "-" * 78
        ])
        risks = summary.get("risk_events", [])
        if risks:
            for r in risks[:5]:
                lines.append(f"  - [{r['event_type']}] {r['symbol']}: {r.get('details', {})}")
            if len(risks) > 5:
                lines.append(f"  ... and {len(risks) - 5} more risk events.")
        else:
            lines.append("  Zero risk interventions or blocks recorded today.")

        lines.extend([
            "",
            "=" * 78
        ])
        return chr(10).join(lines)

    @classmethod
    def export_json(cls, summary: Dict[str, Any], filepath: Optional[str] = None) -> str:
        """Serializes summary to JSON and optionally writes to disk."""
        # Convert datetime objects to string representation
        def serialize_dt(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            return str(obj)

        json_text = json.dumps(summary, default=serialize_dt, indent=2)
        if filepath:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(json_text)
            log.info(f"Daily summary exported to {filepath}")
        return json_text
