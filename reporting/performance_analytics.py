"""
================================================================================
FnOQuantAI: Performance Analytics Engine
================================================================================
Calculates institutional quantitative performance metrics from closed trades:
- Win rate (% of profitable trades) and loss rate.
- Profit factor: Gross Profits / Gross Losses.
- Cumulative Gross P&L, Transaction Costs, and Net P&L (INR).
- Average win, average loss, and payoff ratio.
- Average holding duration (seconds / minutes).
- Detailed multi-dimensional breakdowns:
  - By strategy category (INTRADAY, SHORT_TERM, MID_TERM).
  - By commodity instrument symbol.
  - By exit reason (TARGET_HIT, STOP_LOSS_HIT, TIME_EXIT).
================================================================================
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

from database import db_manager

log = logging.getLogger("FnOQuantAI.Reporting.Analytics")
IST = ZoneInfo("Asia/Kolkata")


class PerformanceAnalytics:
    """Computes quantitative performance metrics and trade distribution analytics."""

    @classmethod
    def compute_metrics(
        cls,
        trades: Optional[List[Dict[str, Any]]] = None,
        start_dt: Optional[datetime] = None,
        end_dt: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Calculates performance indicators across provided trades or queries closed trades from fo_trades.
        """
        if trades is None:
            conditions = ["exit_time IS NOT NULL"]
            params = []
            if start_dt:
                conditions.append("entry_time >= %s")
                params.append(start_dt)
            if end_dt:
                conditions.append("entry_time <= %s")
                params.append(end_dt)

            where_clause = " AND ".join(conditions)
            sql = f"SELECT * FROM fo_trades WHERE {where_clause} ORDER BY entry_time ASC;"
            closed_trades = db_manager.execute_fo_query(sql, tuple(params) if params else None, use_primary=True)
        else:
            closed_trades = [t for t in trades if t.get("exit_time") is not None]

        total_trades = len(closed_trades)
        if total_trades == 0:
            return cls._empty_metrics()

        winning_trades = [t for t in closed_trades if float(t.get("net_pnl") or 0.0) > 0]
        losing_trades = [t for t in closed_trades if float(t.get("net_pnl") or 0.0) < 0]
        breakeven_trades = [t for t in closed_trades if float(t.get("net_pnl") or 0.0) == 0]

        win_count = len(winning_trades)
        loss_count = len(losing_trades)
        win_rate = round((win_count / total_trades) * 100.0, 2)

        gross_wins = sum(float(t.get("gross_pnl") or 0.0) for t in winning_trades)
        gross_losses = abs(sum(float(t.get("gross_pnl") or 0.0) for t in losing_trades))

        total_gross_pnl = round(sum(float(t.get("gross_pnl") or 0.0) for t in closed_trades), 2)
        total_costs = round(sum(float(t.get("estimated_transaction_costs") or 0.0) for t in closed_trades), 2)
        total_net_pnl = round(sum(float(t.get("net_pnl") or 0.0) for t in closed_trades), 2)

        if gross_losses > 0:
            profit_factor = round(gross_wins / gross_losses, 2)
        elif gross_wins > 0:
            profit_factor = round(gross_wins, 2)
        else:
            profit_factor = 0.0

        avg_win = round(gross_wins / win_count, 2) if win_count > 0 else 0.0
        avg_loss = round(gross_losses / loss_count, 2) if loss_count > 0 else 0.0
        payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

        holding_durations = [int(t.get("holding_duration_seconds") or 0) for t in closed_trades]
        avg_holding_duration_sec = round(sum(holding_durations) / total_trades, 1)

        net_pnls = [float(t.get("net_pnl") or 0.0) for t in closed_trades]
        max_win_inr = max(net_pnls, default=0.0)
        max_loss_inr = min(net_pnls, default=0.0)

        # ----------------------------------------------------------------------
        # Multi-dimensional Segmentations
        # ----------------------------------------------------------------------
        by_category = cls._segment_by(closed_trades, "strategy_category")
        by_symbol = cls._segment_by(closed_trades, "symbol")
        by_exit_reason = cls._segment_by(closed_trades, "exit_reason")

        return {
            "total_trades": total_trades,
            "winning_trades": win_count,
            "losing_trades": loss_count,
            "breakeven_trades": len(breakeven_trades),
            "win_rate_percentage": win_rate,
            "profit_factor": profit_factor,
            "total_gross_pnl_inr": total_gross_pnl,
            "total_transaction_costs_inr": total_costs,
            "total_net_pnl_inr": total_net_pnl,
            "average_win_inr": avg_win,
            "average_loss_inr": avg_loss,
            "payoff_ratio": payoff_ratio,
            "average_holding_duration_seconds": avg_holding_duration_sec,
            "max_winning_trade_inr": max_win_inr,
            "max_losing_trade_inr": max_loss_inr,
            "breakdown_by_category": by_category,
            "breakdown_by_symbol": by_symbol,
            "breakdown_by_exit_reason": by_exit_reason
        }

    @classmethod
    def _segment_by(cls, trades: List[Dict[str, Any]], field: str) -> Dict[str, Dict[str, Any]]:
        """Segments trades by a given attribute and computes localized metrics."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for t in trades:
            val = str(t.get(field) or "UNKNOWN")
            groups.setdefault(val, []).append(t)

        result = {}
        for group_name, group_trades in sorted(groups.items()):
            n = len(group_trades)
            wins = len([t for t in group_trades if float(t.get("net_pnl") or 0.0) > 0])
            losses = len([t for t in group_trades if float(t.get("net_pnl") or 0.0) < 0])
            gross = round(sum(float(t.get("gross_pnl") or 0.0) for t in group_trades), 2)
            net = round(sum(float(t.get("net_pnl") or 0.0) for t in group_trades), 2)
            win_pct = round((wins / n) * 100.0, 1) if n > 0 else 0.0
            result[group_name] = {
                "trades_count": n,
                "wins": wins,
                "losses": losses,
                "win_rate_percentage": win_pct,
                "gross_pnl_inr": gross,
                "net_pnl_inr": net
            }
        return result

    @staticmethod
    def _empty_metrics() -> Dict[str, Any]:
        """Returns baseline empty metrics structure when no trades have occurred."""
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "breakeven_trades": 0,
            "win_rate_percentage": 0.0,
            "profit_factor": 0.0,
            "total_gross_pnl_inr": 0.0,
            "total_transaction_costs_inr": 0.0,
            "total_net_pnl_inr": 0.0,
            "average_win_inr": 0.0,
            "average_loss_inr": 0.0,
            "payoff_ratio": 0.0,
            "average_holding_duration_seconds": 0.0,
            "max_winning_trade_inr": 0.0,
            "max_losing_trade_inr": 0.0,
            "breakdown_by_category": {},
            "breakdown_by_symbol": {},
            "breakdown_by_exit_reason": {}
        }
