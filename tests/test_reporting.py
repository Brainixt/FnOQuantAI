"""
================================================================================
FnOQuantAI Unit Tests: Reporting, Dashboards, and Performance Analytics
================================================================================
Tests:
1. PerformanceAnalytics: Zero-trade safety, win rate, profit factor, R-multiples,
   gross/net P&L, and multi-dimensional breakdowns (category, symbol, exit reason).
2. DailySummaryReporter: Daily activity extraction, ASCII report formatting, JSON export.
3. Dashboard: Live ASCII terminal dashboard rendering and portfolio allocation display.
================================================================================
"""

import unittest
import json
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from reporting.performance_analytics import PerformanceAnalytics
from reporting.daily_summary import DailySummaryReporter
from reporting.dashboard import render_cli_dashboard

IST = ZoneInfo("Asia/Kolkata")


class TestReportingAndAnalytics(unittest.TestCase):
    """Tests quantitative analytics metrics and daily reporting formatting."""

    def test_performance_metrics_empty(self):
        """Ensures safe zero-division handling when no trades exist."""
        metrics = PerformanceAnalytics.compute_metrics(trades=[])
        self.assertEqual(metrics["total_trades"], 0)
        self.assertEqual(metrics["win_rate_percentage"], 0.0)
        self.assertEqual(metrics["profit_factor"], 0.0)
        self.assertEqual(metrics["total_gross_pnl_inr"], 0.0)
        self.assertEqual(metrics["total_net_pnl_inr"], 0.0)
        self.assertEqual(metrics["average_win_inr"], 0.0)
        self.assertEqual(metrics["average_loss_inr"], 0.0)

    def test_performance_metrics_calculation(self):
        """Verifies mathematical correctness of performance indicators across synthetic trades."""
        now = datetime.now(IST)
        mock_trades = [
            # Trade 1: Winner (Crude Oil Intraday)
            {
                "trade_id": "T1",
                "symbol": "RELIANCE",
                "strategy_category": "INTRADAY",
                "gross_pnl": 5000.0,
                "estimated_transaction_costs": 150.0,
                "net_pnl": 4850.0,
                "holding_duration_seconds": 1800,
                "exit_reason": "TARGET_HIT",
                "exit_time": now
            },
            # Trade 2: Winner (Natural Gas Short Term)
            {
                "trade_id": "T2",
                "symbol": "TCS",
                "strategy_category": "SHORT_TERM",
                "gross_pnl": 7500.0,
                "estimated_transaction_costs": 200.0,
                "net_pnl": 7300.0,
                "holding_duration_seconds": 86400,
                "exit_reason": "TARGET_HIT",
                "exit_time": now
            },
            # Trade 3: Loser (Crude Oil Intraday)
            {
                "trade_id": "T3",
                "symbol": "RELIANCE",
                "strategy_category": "INTRADAY",
                "gross_pnl": -2500.0,
                "estimated_transaction_costs": 150.0,
                "net_pnl": -2650.0,
                "holding_duration_seconds": 1200,
                "exit_reason": "STOP_LOSS_HIT",
                "exit_time": now
            },
            # Trade 4: Winner (Gold Mid Term)
            {
                "trade_id": "T4",
                "symbol": "GOLD",
                "strategy_category": "MID_TERM",
                "gross_pnl": 10000.0,
                "estimated_transaction_costs": 300.0,
                "net_pnl": 9700.0,
                "holding_duration_seconds": 432000,
                "exit_reason": "TARGET_HIT",
                "exit_time": now
            }
        ]

        metrics = PerformanceAnalytics.compute_metrics(trades=mock_trades)

        self.assertEqual(metrics["total_trades"], 4)
        self.assertEqual(metrics["winning_trades"], 3)
        self.assertEqual(metrics["losing_trades"], 1)
        self.assertEqual(metrics["win_rate_percentage"], 75.0) # 3 / 4 = 75%

        # Gross PnL = 5000 + 7500 - 2500 + 10000 = 20000.0
        self.assertEqual(metrics["total_gross_pnl_inr"], 20000.0)
        # Costs = 150 + 200 + 150 + 300 = 800.0
        self.assertEqual(metrics["total_transaction_costs_inr"], 800.0)
        # Net PnL = 4850 + 7300 - 2650 + 9700 = 19200.0
        self.assertEqual(metrics["total_net_pnl_inr"], 19200.0)

        # Gross wins = 22500, Gross losses = 2500 -> Profit factor = 22500 / 2500 = 9.0
        self.assertEqual(metrics["profit_factor"], 9.0)

        # Average win = 22500 / 3 = 7500.0
        self.assertEqual(metrics["average_win_inr"], 7500.0)
        # Average loss = 2500 / 1 = 2500.0
        self.assertEqual(metrics["average_loss_inr"], 2500.0)
        # Payoff ratio = 7500 / 2500 = 3.0
        self.assertEqual(metrics["payoff_ratio"], 3.0)

        # Breakdown by Category
        by_cat = metrics["breakdown_by_category"]
        self.assertIn("INTRADAY", by_cat)
        self.assertIn("SHORT_TERM", by_cat)
        self.assertIn("MID_TERM", by_cat)
        self.assertEqual(by_cat["INTRADAY"]["trades_count"], 2)
        self.assertEqual(by_cat["INTRADAY"]["win_rate_percentage"], 50.0)

        # Breakdown by Exit Reason
        by_exit = metrics["breakdown_by_exit_reason"]
        self.assertIn("TARGET_HIT", by_exit)
        self.assertIn("STOP_LOSS_HIT", by_exit)
        self.assertEqual(by_exit["TARGET_HIT"]["trades_count"], 3)
        self.assertEqual(by_exit["STOP_LOSS_HIT"]["trades_count"], 1)

    def test_daily_summary_reporter(self):
        """Tests summary extraction, text report formatting, and JSON serialization."""
        summary = DailySummaryReporter.generate_summary(target_date=date.today())
        self.assertIsNotNone(summary)
        self.assertEqual(summary["report_date"], date.today().isoformat())
        self.assertIn("signals_generated_today", summary)
        self.assertIn("performance_metrics", summary)

        # Format ASCII Report
        text_report = DailySummaryReporter.format_text_report(summary)
        self.assertIn("FNOQUANTAI DAILY ACTIVITY & PERFORMANCE REPORT", text_report)
        self.assertIn("FINANCIAL & PERFORMANCE SUMMARY", text_report)
        self.assertIn("STRATEGY BREAKDOWN", text_report)

        # Export JSON
        json_output = DailySummaryReporter.export_json(summary)
        parsed = json.loads(json_output)
        self.assertEqual(parsed["report_date"], date.today().isoformat())

    def test_dashboard_rendering(self):
        """Verifies CLI dashboard renders successfully with expected headers and metrics."""
        dashboard_text = render_cli_dashboard()
        self.assertIn("FNOQUANTAI TERMINAL MONITOR", dashboard_text)
        self.assertIn("PORTFOLIO ALLOCATION USAGE", dashboard_text)
        self.assertIn("ACTIVE OPEN TRADES", dashboard_text)
        self.assertIn("RECENT QUANTITATIVE SIGNALS", dashboard_text)
        self.assertIn("BACKGROUND WORKER STATUS", dashboard_text)


if __name__ == "__main__":
    unittest.main()
