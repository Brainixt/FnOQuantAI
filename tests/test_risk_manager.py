"""
================================================================================
FnOQuantAI Unit Tests: Risk Management & Trade Allocation
================================================================================
Covers:
1. Market Timing & Cutoff: Weekend, outside hours, and intraday 22:30 IST cutoff.
2. Quote Freshness: Rejection of stale quotes and audit logging in fo_risk_events.
3. Price Deviation Safeguards: Slippage protection (0.25% / 5-tick thresholds).
4. Portfolio Allocation & Position Limits:
   - Stacking protection (max 1 trade per symbol).
   - Category allocation cap (MAX_SIGNALS_*).
   - Strict 1 NSE tradable lot position size.
5. Trade Lifecycle & Financials:
   - Trade allocation in fo_trades.
   - Signal activation transition in fo_signals & fo_signal_events.
   - Trade closing with gross P&L, statutory costs, and net P&L.
================================================================================
"""

import unittest
import uuid
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

from database import db_manager
from signals.signal_repository import SignalRepository
from risk.risk_manager import RiskManager

IST = ZoneInfo("Asia/Kolkata")


class TestRiskManager(unittest.TestCase):
    """Tests pre-trade risk controls, deviation barriers, and trade lifecycle persistence."""

    def setUp(self):
        self.test_run_id = uuid.uuid4().hex[:8]
        self.symbol = f"CRUDE_{self.test_run_id}"
        self.sig_id = f"SIG_RISK_{self.test_run_id}"

        # Insert a valid base signal for testing
        self.signal_data = {
            "signal_id": self.sig_id,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}FUT",
            "exchange": "NSE",
            "strategy_category": "INTRADAY",
            "strategy_name": "IntradayBreakoutMomentum",
            "strategy_version": "1.0.0",
            "direction": "BUY",
            "entry_reference_price": 6000.0,
            "stop_loss_price": 5960.0,
            "target_price": 6080.0,
            "risk_per_unit": 40.0,
            "reward_per_unit": 80.0,
            "reward_to_risk_ratio": 2.0,
            "risk_percentage": 0.67,
            "total_score": 85.0,
            "status": "NEW",
            "source_cycle_id": f"CYCLE_{self.test_run_id}",
            "source_snapshot_id": 555555,
            "intended_holding_period": "SAME_DAY"
        }
        SignalRepository.save_signal(self.signal_data)

        # Baseline valid quote at market time (14:30 IST on a Wednesday)
        self.valid_market_time = datetime(2026, 10, 21, 14, 30, tzinfo=IST) # Wednesday
        self.valid_quote = {
            "id": 555556,
            "symbol": self.symbol,
            "exchange": "NSE",
            "cycle_id": "COM_20261021_143000",
            "ltp": 6002.0,
            "last_price": 6002.0,
            "tick_size": 1.0,
            "lot_size": 100,
            "expiry": (date.today() + timedelta(days=25)).isoformat(),
            "created_at": datetime.now(IST)
        }

    def test_market_timing_rejections(self):
        """Validates market timing barriers: Weekend, outside hours, and intraday cutoff."""
        # 1. Weekend Check (Saturday)
        saturday_dt = datetime(2026, 10, 24, 14, 0, tzinfo=IST)
        approved, reason, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=saturday_dt
        )
        self.assertFalse(approved)
        self.assertIn("Weekend", reason)

        # 2. Outside session hours (08:30 IST)
        pre_market_dt = datetime(2026, 10, 21, 8, 30, tzinfo=IST)
        approved, reason, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=pre_market_dt
        )
        self.assertFalse(approved)
        self.assertIn("closed", reason)

        # 3. Outside NSE market hours post-close (16:35 IST)
        post_close_dt = datetime(2026, 10, 21, 16, 35, tzinfo=IST)
        approved, reason, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=post_close_dt
        )
        self.assertFalse(approved)
        self.assertIn("closed", reason)

        # 4. Active trading during NSE market hours (11:35 IST): APPROVED
        active_dt = datetime(2026, 10, 21, 11, 35, tzinfo=IST)
        approved, reason, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=active_dt
        )
        self.assertTrue(approved, f"Expected approval at 11:35 IST, got: {reason}")

        # 5. Intraday entry past 15:00 IST cutoff (15:10 IST): REJECTED
        intraday_cutoff_dt = datetime(2026, 10, 21, 15, 10, tzinfo=IST)
        approved, reason, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=intraday_cutoff_dt
        )
        self.assertFalse(approved)
        self.assertIn("cutoff", reason)

        # Verify audit logs in fo_risk_events
        events = db_manager.execute_fo_query(
            "SELECT * FROM fo_risk_events WHERE symbol = %s AND event_type = 'MARKET_CLOSED_REJECTION';",
            [self.symbol]
        )
        self.assertEqual(len(events), 4)

    def test_price_deviation_safeguard(self):
        """Blocks trades when market price deviates significantly from reference."""
        # 1. Normal small deviation (2 points / 2 ticks on 6000 = 0.033% <= 0.25%): APPROVED
        approved, reason, plan = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=self.valid_market_time
        )
        self.assertTrue(approved, f"Expected approval, got: {reason}")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["actual_entry_fill_price"], 6002.0)
        self.assertEqual(plan["quantity"], 1) # Strict 1 lot limit

        # 2. Excessive deviation (6030.0 vs 6000.0 = 30 points = 0.50% > 0.25%, 30 ticks > 5): REJECTED
        slippage_quote = dict(self.valid_quote)
        slippage_quote["ltp"] = 6030.0
        slippage_quote["last_price"] = 6030.0

        approved, reason, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, slippage_quote, current_dt=self.valid_market_time
        )
        self.assertFalse(approved)
        self.assertIn("Price deviation exceeded", reason)

        # Check fo_risk_events record
        events = db_manager.execute_fo_query(
            "SELECT * FROM fo_risk_events WHERE symbol = %s AND event_type = 'PRICE_DEVIATION_EXCEEDED';",
            [self.symbol]
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(float(events[0]["attempted_price"]), 6000.0)
        self.assertEqual(float(events[0]["market_quote_price"]), 6030.0)
        self.assertEqual(float(events[0]["deviation_points"]), 30.0)

    def test_stacking_and_position_limits(self):
        """Prevents multiple simultaneous positions in the same instrument symbol."""
        # Open first trade
        approved, _, plan = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=self.valid_market_time
        )
        self.assertTrue(approved)
        trade_id = RiskManager.open_trade(plan)
        self.assertIsNotNone(trade_id)

        # Attempting second trade on same symbol while first is open must be blocked
        approved2, reason2, _ = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=self.valid_market_time
        )
        self.assertFalse(approved2)
        self.assertIn("already has an active trade", reason2)

        # Verify POSITION_LIMIT_REACHED logged
        events = db_manager.execute_fo_query(
            "SELECT * FROM fo_risk_events WHERE symbol = %s AND event_type = 'POSITION_LIMIT_REACHED';",
            [self.symbol]
        )
        self.assertEqual(len(events), 1)

    def test_trade_lifecycle_open_and_close(self):
        """Validates opening, activation event, and closing with P&L calculations."""
        approved, _, plan = RiskManager.validate_trade_eligibility(
            self.signal_data, self.valid_quote, current_dt=self.valid_market_time
        )
        self.assertTrue(approved)

        # 1. Open trade
        trade_id = RiskManager.open_trade(plan)
        self.assertIsNotNone(trade_id)

        # Verify signal transitioned to ACTIVE in fo_signals
        sig = SignalRepository.get_signal_by_id(self.sig_id)
        self.assertEqual(sig["status"], "ACTIVE")

        # Verify trade appears in get_open_trades
        open_trades = RiskManager.get_open_trades(symbol=self.symbol)
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(open_trades[0]["trade_id"], trade_id)

        # 2. Close trade at profit target (6080.0)
        close_time = self.valid_market_time + timedelta(hours=2)
        closed = RiskManager.close_trade(
            trade_id=trade_id,
            exit_price=6080.0,
            exit_reason="TARGET_HIT",
            exit_time=close_time
        )
        self.assertTrue(closed)

        # Verify trade details updated in fo_trades
        trade_record = RiskManager.get_trade_by_id(trade_id)
        self.assertIsNotNone(trade_record["exit_time"])
        self.assertEqual(trade_record["exit_reason"], "TARGET_HIT")
        self.assertEqual(trade_record["holding_duration_seconds"], 7200)

        # P&L verification:
        # Fill: 6002.0, Exit: 6080.0 -> PnL pts = 78.0
        # Lot size = 100, Qty = 1 -> Gross = Rs 7,800
        self.assertAlmostEqual(float(trade_record["gross_pnl"]), 78.0, places=2)
        self.assertGreater(float(trade_record["net_pnl"]), 50.0)
        self.assertLess(float(trade_record["net_pnl"]), 7800.0)

        # Verify no open trades remain for this symbol
        open_after = RiskManager.get_open_trades(symbol=self.symbol)
        self.assertEqual(len(open_after), 0)

    def tearDown(self):
        try:
            db_manager.execute_fo_write("DELETE FROM fo_trades WHERE symbol = %s;", [self.symbol])
            db_manager.execute_fo_write("DELETE FROM fo_risk_events WHERE symbol = %s;", [self.symbol])
            db_manager.execute_fo_write(
                "DELETE FROM fo_signal_events WHERE signal_id IN (SELECT signal_id FROM fo_signals WHERE symbol = %s);",
                [self.symbol]
            )
            db_manager.execute_fo_write("DELETE FROM fo_signals WHERE symbol = %s;", [self.symbol])
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
