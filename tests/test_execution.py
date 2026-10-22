"""
================================================================================
FnOQuantAI: Execution Layer Unit Tests
================================================================================
Tests for:
1. Tick size resolution (0.05 NSE tick) and realistic slippage modeling.
2. Simulated paper order execution for NSE equities.
3. Live trading safeguards:
   - Capital allocation gate (?10,000 capital allocation limit).
   - Price deviation gate (<= 5 ticks / 0.25%).
4. Live broker order placement (Kotak Neo mock):
   - Target segment: nse_cm
   - Symbol: {symbol}-EQ
   - Session expiration auto-refresh and retry.
   - Broker RMS / margin rejection handling.
5. ExecutionEngine complete entry and exit lifecycle.
================================================================================
"""

import os
import sys
import unittest
import uuid
from datetime import datetime, date
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config

from database import db_manager
from execution.order_dispatcher import OrderDispatcher, NSE_EQUITY_TICK_SIZE
from execution.execution_engine import ExecutionEngine
from risk.risk_manager import RiskManager
from signals.signal_repository import SignalRepository

IST = ZoneInfo("Asia/Kolkata")


class TestExecutionLayer(unittest.TestCase):
    """Validation test suite for FnOQuantAI Execution Layer."""

    @classmethod
    def setUpClass(cls):
        db_manager.init_schema()

    def setUp(self):
        self.test_id = uuid.uuid4().hex[:8]
        self.symbol = f"EQ_{self.test_id}"
        self.signal_id = f"SIG_EXEC_{self.test_id}"

    def tearDown(self):
        db_manager.execute_fo_write("DELETE FROM fo_trades WHERE signal_id = %s;", [self.signal_id])
        db_manager.execute_fo_write("DELETE FROM fo_risk_events WHERE signal_id = %s;", [self.signal_id])
        db_manager.execute_fo_write("DELETE FROM fo_signal_events WHERE signal_id = %s;", [self.signal_id])
        db_manager.execute_fo_write("DELETE FROM fo_signals WHERE signal_id = %s;", [self.signal_id])

    # 1. Tick Size & Slippage Calculations
    def test_tick_size_and_slippage_calculation(self):
        """Verifies resolution of NSE equity tick size (0.05) and slippage mechanics."""
        self.assertEqual(OrderDispatcher.get_tick_size("RELIANCE"), 0.05)
        self.assertEqual(OrderDispatcher.get_tick_size("TCS"), 0.05)
        self.assertEqual(OrderDispatcher.get_tick_size("UNKNOWN_SYM"), 0.05)

        # BUY fill: price should increase by adverse slippage (1 tick = 0.05)
        fill_buy, slip_buy = OrderDispatcher.calculate_simulated_fill(
            symbol="TCS", direction="BUY", reference_price=3500.0, slippage_ticks=1.0
        )
        self.assertEqual(slip_buy, 0.05)
        self.assertEqual(fill_buy, 3500.05)

        # SELL fill: price should decrease by adverse slippage (2 ticks = 0.10)
        fill_sell, slip_sell = OrderDispatcher.calculate_simulated_fill(
            symbol="RELIANCE", direction="SELL", reference_price=2800.0, slippage_ticks=2.0
        )
        self.assertEqual(slip_sell, 0.10)
        self.assertEqual(fill_sell, 2799.90)

    # 2. Paper Trading Simulation
    def test_paper_order_execution(self):
        """Tests that paper orders execute cleanly with realistic slippage and unique order ID."""
        order_plan = {
            "symbol": "RELIANCE",
            "direction": "BUY",
            "reference_price": 2800.0,
            "current_price": 2800.0,
            "quantity": 3,
            "action": "ENTRY",
            "is_live": False
        }

        res = OrderDispatcher.dispatch_order(order_plan)
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "FILLED")
        self.assertFalse(res["is_live"])
        self.assertTrue(res["broker_order_id"].startswith("SIM_FO_ENTRY_RELIANCE_"))
        self.assertGreater(res["fill_price"], 2800.0)
        self.assertEqual(res["quantity"], 3)

    # 3. Live Trading Safeguards: Capital & Deviation Gates
    @patch.object(config, "LIVE_TRADING_ENABLED", True)
    def test_live_order_position_limit_safeguard(self):
        """Verifies rejection when requested live trade capital exceeds allowed limit."""
        order_plan = {
            "symbol": self.symbol,
            "signal_id": self.signal_id,
            "direction": "BUY",
            "reference_price": 2500.0,
            "current_price": 2500.0,
            "quantity": 20,  # 20 * 2500 = 50,000 >> 15,000 threshold
            "is_live": True
        }

        res = OrderDispatcher.dispatch_order(order_plan)
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("exceeds max allowed", res["error"])

        events = db_manager.execute_fo_query(
            "SELECT * FROM fo_risk_events WHERE signal_id = %s AND event_type = 'POSITION_LIMIT_REACHED';",
            [self.signal_id]
        )
        self.assertEqual(len(events), 1)

    @patch.object(config, "LIVE_TRADING_ENABLED", True)
    def test_live_order_price_deviation_safeguard(self):
        """Verifies rejection when current price exceeds deviation thresholds."""
        order_plan = {
            "symbol": self.symbol,
            "signal_id": self.signal_id,
            "direction": "BUY",
            "reference_price": 2500.0,
            "current_price": 2550.0,  # 2.0% deviation > 0.25% threshold
            "quantity": 3,
            "is_live": True
        }

        res = OrderDispatcher.dispatch_order(order_plan)
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "REJECTED")
        self.assertIn("price deviation", res["error"])

        events = db_manager.execute_fo_query(
            "SELECT * FROM fo_risk_events WHERE signal_id = %s AND event_type = 'PRICE_DEVIATION_EXCEEDED';",
            [self.signal_id]
        )
        self.assertEqual(len(events), 1)

    # 4. Live Broker Order Placement (Kotak Neo Mocking)
    @patch.object(config, "LIVE_TRADING_ENABLED", True)
    def test_live_order_broker_placement_success(self):
        """Tests successful live order placement to Kotak Neo via nse_cm segment."""
        order_plan = {
            "symbol": "RELIANCE",
            "trading_symbol": "RELIANCE-EQ",
            "signal_id": self.signal_id,
            "direction": "BUY",
            "category": "INTRADAY",
            "reference_price": 2500.0,
            "current_price": 2500.0,
            "quantity": 4,
            "action": "ENTRY",
            "is_live": True
        }

        mock_client = MagicMock()
        mock_client.place_order.return_value = {
            "stat": "Ok",
            "nOrdNo": "KOTAK_ORD_998811",
            "errMsg": None
        }

        with patch("script.neo_connection.NeoConnectionManager.get_write_connection", return_value=mock_client):
            res = OrderDispatcher.dispatch_order(order_plan)
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "SUBMITTED")
            self.assertTrue(res["is_live"])
            self.assertEqual(res["broker_order_id"], "KOTAK_ORD_998811")

            mock_client.place_order.assert_called_once_with(
                exchange_segment="nse_cm",
                product="Normal",
                price="0",
                order_type="MKT",
                validity="DAY",
                trading_symbol="RELIANCE-EQ",
                transaction_type="B",
                quantity="4"
            )

    @patch.object(config, "LIVE_TRADING_ENABLED", True)
    def test_live_order_sell_uses_mis_product(self):
        """Verifies SELL order uses product='MIS'."""
        order_plan = {
            "symbol": "INFY",
            "trading_symbol": "INFY-EQ",
            "signal_id": self.signal_id,
            "direction": "SELL",
            "category": "INTRADAY",
            "reference_price": 1800.0,
            "current_price": 1800.0,
            "quantity": 5,
            "action": "ENTRY",
            "is_live": True
        }

        mock_client = MagicMock()
        mock_client.place_order.return_value = {
            "stat": "Ok",
            "nOrdNo": "KOTAK_ORD_SELL_123"
        }

        with patch("script.neo_connection.NeoConnectionManager.get_write_connection", return_value=mock_client):
            res = OrderDispatcher.dispatch_order(order_plan)
            self.assertTrue(res["success"])
            mock_client.place_order.assert_called_once_with(
                exchange_segment="nse_cm",
                product="MIS",
                price="0",
                order_type="MKT",
                validity="DAY",
                trading_symbol="INFY-EQ",
                transaction_type="S",
                quantity="5"
            )

    @patch.object(config, "LIVE_TRADING_ENABLED", True)
    def test_live_order_session_expired_auto_refresh(self):
        """Tests auto-refresh and retry when broker session has expired."""
        order_plan = {
            "symbol": "TCS",
            "trading_symbol": "TCS-EQ",
            "signal_id": self.signal_id,
            "direction": "SELL",
            "category": "INTRADAY",
            "reference_price": 3500.0,
            "current_price": 3500.0,
            "quantity": 2,
            "action": "ENTRY",
            "is_live": True
        }

        mock_client1 = MagicMock()
        mock_client1.place_order.return_value = {
            "stat": "Not_Ok",
            "errMsg": "Session expired or invalid token"
        }

        mock_client2 = MagicMock()
        mock_client2.place_order.return_value = {
            "stat": "Ok",
            "nOrdNo": "KOTAK_REFRESH_SUCCESS_77"
        }

        with patch("script.neo_connection.NeoConnectionManager.get_write_connection", side_effect=[mock_client1, mock_client2]),              patch("script.neo_connection.NeoConnectionManager.recreate_connection") as mock_refresh:
            res = OrderDispatcher.dispatch_order(order_plan)
            self.assertTrue(res["success"])
            self.assertEqual(res["broker_order_id"], "KOTAK_REFRESH_SUCCESS_77")
            mock_refresh.assert_called_once()

    # 5. ExecutionEngine End-to-End Entry & Exit Flow
    def test_execution_engine_full_lifecycle(self):
        """Tests complete flow from signal entry fill to exit fill in fo_trades."""
        sig_data = {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}-EQ",
            "exchange": "NSE",
            "strategy_category": "INTRADAY",
            "direction": "BUY",
            "entry_reference_price": 2500.0,
            "stop_loss_price": 2470.0,
            "target_price": 2550.0,
            "risk_per_unit": 30.0,
            "reward_per_unit": 50.0,
            "reward_to_risk_ratio": 1.6667,
            "risk_percentage": 1.20,
            "total_score": 80.0,
            "feature_contributions": {},
            "strategy_name": "TestStrategy",
            "strategy_version": "1.0.0",
            "intended_holding_period": "SAME_SESSION",
            "source_cycle_id": "CYCLE_TEST_01",
            "source_snapshot_id": 1
        }
        saved_id = SignalRepository.save_signal(sig_data)
        self.assertIsNotNone(saved_id)

        current_quote = {
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}-EQ",
            "ltp": 2501.0,
            "tick_size": 0.05,
            "retrieved_at": datetime.now(IST),
            "cycle_id": "CYCLE_TEST_01"
        }

        # Step 1: Execute Entry
        now_mock = datetime(2026, 10, 21, 11, 0, tzinfo=IST)
        with patch.object(config, "LIVE_TRADING_ENABLED", False):
            ok, trade_id, order_res = ExecutionEngine.execute_trade_entry(
                signal=sig_data,
                current_quote=current_quote,
                current_dt=now_mock
            )
            self.assertTrue(ok)
            self.assertTrue(trade_id.startswith("TRD_FO_INTRADAY_"))

            # Verify trade recorded in fo_trades
            trade_rows = db_manager.execute_fo_query("SELECT * FROM fo_trades WHERE trade_id = %s;", [trade_id])
            self.assertEqual(len(trade_rows), 1)
            t = trade_rows[0]
            self.assertEqual(t["symbol"], self.symbol)
            self.assertEqual(t["direction"], "BUY")
            self.assertIsNone(t["exit_time"])

            # Verify signal transitioned to ACTIVE
            sig_row = SignalRepository.get_signal(self.signal_id)
            self.assertEqual(sig_row["status"], "ACTIVE")

            # Step 2: Execute Exit (Target Hit)
            exit_quote = {"symbol": self.symbol, "ltp": 2550.0}
            exit_ok, err, exit_order = ExecutionEngine.execute_trade_exit(
                trade_id=trade_id,
                exit_reason="TARGET_HIT",
                exit_price=2550.0,
                current_quote=exit_quote
            )
            self.assertTrue(exit_ok)

            # Verify trade closed
            closed_trade = db_manager.execute_fo_query("SELECT * FROM fo_trades WHERE trade_id = %s;", [trade_id])[0]
            self.assertIsNotNone(closed_trade["exit_time"])
            self.assertEqual(closed_trade["exit_reason"], "TARGET_HIT")
            self.assertGreater(float(closed_trade["net_pnl"]), 0.0)


if __name__ == "__main__":
    unittest.main()
