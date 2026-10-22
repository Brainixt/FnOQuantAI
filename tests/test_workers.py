"""
================================================================================
FnOQuantAI Unit Tests: SignalWorker & OpenCallWorker
================================================================================
Tests:
1. SignalWorker: Cycle orchestration, watermark advancement, duplicate suppression,
   and execution logging in fo_worker_executions.
2. OpenCallWorker: Target hit, Stop loss hit, Intraday square-off cutoff,
   contract lot size resolution, P&L calculations, and fo_signal_events audit trail.
================================================================================
"""

import unittest
import uuid
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock

from database import db_manager
from signals.signal_repository import SignalRepository
from data.market_repository import WatermarkManager
from workers.signal_worker import SignalWorker
from workers.open_call_worker import OpenCallWorker, resolve_lot_size

IST = ZoneInfo("Asia/Kolkata")


class TestSignalWorker(unittest.TestCase):
    """Tests quantitative signal generation cycle execution."""

    def setUp(self):
        self.test_run_id = uuid.uuid4().hex[:8]
        self.cycle_id = f"TEST_CYCLE_{self.test_run_id}"
        self.symbol = f"CRUDE_{self.test_run_id}"

    def test_run_cycle_no_new_data(self):
        """Verifies graceful handling and audit logging when all cycles are watermarked."""
        with patch("data.market_repository.MarketRepository.get_latest_unprocessed_cycle", return_value=None):
            result = SignalWorker.run_cycle()
            self.assertEqual(result["status"], "SUCCESS")
            self.assertIsNone(result["cycle_id"])
            self.assertEqual(result["signals_generated"], 0)

            # Verify execution logged in fo_worker_executions
            logs = db_manager.execute_fo_query(
                "SELECT * FROM fo_worker_executions WHERE worker_name = 'signal_worker' ORDER BY id DESC LIMIT 1;"
            )
            self.assertTrue(len(logs) > 0)
            self.assertEqual(logs[0]["status"], "SUCCESS")

    def test_run_cycle_generates_and_watermarks(self):
        """Validates end-to-end signal generation, watermark advancement, and duplicate suppression."""
        mock_obs = [{
            "id": 999999,
            "cycle_id": self.cycle_id,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}FUT",
            "exchange": "NSE",
            "last_price": 5075.0,
            "open_price": 5070.0,
            "high_price": 5085.0,
            "low_price": 5065.0,
            "close_price": 5074.0,
            "open_interest": 50000,
            "volume": 20000,
            "tick_size": 1.0,
            "lot_size": 100,
            "expiry": (date.today() + timedelta(days=25)).isoformat(),
            "source_timestamp": datetime.now(IST),
            "retrieved_at": datetime.now(IST)
        }]

        # Realistic oscillating history so RSI ~60
        base = 5000.0
        increments = [0, 15, 25, 10, 20, 35, 25, 40, 30, 45, 55, 45, 60, 50, 65]
        mock_history = []
        for inc in increments:
            mock_history.append({
                "symbol": self.symbol,
                "last_price": base + inc,
                "high_price": base + inc + 10.0,
                "low_price": base + inc - 10.0,
                "close_price": base + inc - 1.0,
                "open_interest": 50000,
                "volume": 10000
            })

        with patch("data.market_repository.MarketRepository.get_latest_unprocessed_cycle", return_value=self.cycle_id),              patch("data.market_repository.MarketRepository.get_fresh_observations", return_value=mock_obs),              patch("data.market_repository.MarketRepository.get_validated_history", return_value=mock_history):

            # 1. First execution creates signals and advances watermark
            result = SignalWorker.run_cycle()
            self.assertEqual(result["status"], "SUCCESS")
            self.assertEqual(result["cycle_id"], self.cycle_id)
            self.assertGreaterEqual(result["signals_generated"], 1)

            # Verify watermark was advanced
            wm = WatermarkManager.get_watermark(self.symbol)
            self.assertIsNotNone(wm)
            self.assertEqual(wm["last_processed_cycle_id"], self.cycle_id)
            self.assertEqual(wm["last_processed_snapshot_id"], 999999)

            # Verify duplicate check detects already-processed snapshot
            is_new = WatermarkManager.is_new_observation(self.symbol, self.cycle_id, 999999)
            self.assertFalse(is_new, "Watermark must reject already-evaluated cycle and snapshot")

    def tearDown(self):
        try:
            db_manager.execute_fo_write("DELETE FROM fo_risk_events WHERE symbol = %s;", (self.symbol,))
            db_manager.execute_fo_write("DELETE FROM fo_trades WHERE symbol = %s;", (self.symbol,))
            db_manager.execute_fo_write(
                "DELETE FROM fo_signal_events WHERE signal_id IN (SELECT signal_id FROM fo_signals WHERE symbol = %s);",
                (self.symbol,)
            )
            db_manager.execute_fo_write("DELETE FROM fo_signals WHERE symbol = %s;", (self.symbol,))
            db_manager.execute_fo_write("DELETE FROM fo_processing_watermarks WHERE symbol = %s;", (self.symbol,))
        except Exception:
            pass


class TestOpenCallWorker(unittest.TestCase):
    """Tests open call lifecycle monitoring, triggers, and P&L financial calculations."""

    def setUp(self):
        self.test_run_id = uuid.uuid4().hex[:8]
        self.symbol = f"NATGAS_{self.test_run_id}"

    def test_lot_size_resolution(self):
        """Verifies resolution of contract multipliers."""
        # In cash equities, trade units are individual shares (default 1)
        self.assertEqual(resolve_lot_size("RELIANCE"), 1)
        self.assertEqual(resolve_lot_size("TCS"), 1)
        self.assertEqual(resolve_lot_size("INFY"), 1)
        # Snapshot metadata takes highest precedence if explicitly provided
        self.assertEqual(resolve_lot_size("RELIANCE", snapshot_lot_size=50), 50)

    def test_target_hit_resolution(self):
        """Validates TARGET_HIT transition, positive P&L, and event logging."""
        sig_id = f"SIG_TEST_TGT_{self.test_run_id}"
        sig_data = {
            "signal_id": sig_id,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}FUT",
            "exchange": "NSE",
            "strategy_category": "INTRADAY",
            "strategy_name": "IntradayBreakoutMomentum",
            "strategy_version": "1.0.0",
            "direction": "BUY",
            "entry_reference_price": 250.0,
            "stop_loss_price": 245.0,
            "target_price": 260.0,
            "risk_per_unit": 5.0,
            "reward_per_unit": 10.0,
            "reward_to_risk_ratio": 2.0,
            "risk_percentage": 2.0,
            "total_score": 85.0,
            "status": "NEW",
            "source_cycle_id": f"CYCLE_{self.test_run_id}",
            "source_snapshot_id": 111111,
            "intended_holding_period": "SAME_DAY"
        }
        SignalRepository.save_signal(sig_data)

        # Mock latest market quote touching target: LTP = 261.0 >= Target (260.0)
        mock_snapshot = [{
            "id": 111112,
            "symbol": self.symbol,
            "ltp": 261.0,
            "last_price": 261.0,
            "lot_size": 1250, # Natural gas lot size = 1250
            "expiry": (date.today() + timedelta(days=20)).isoformat()
        }]

        with patch("data.common_db_reader.CommonDbReader.execute_read", return_value=mock_snapshot):
            result = OpenCallWorker.run_cycle()
            self.assertEqual(result["status"], "SUCCESS")
            self.assertGreaterEqual(result["exit_events_triggered"], 1)

            # Verify signal record is now TARGET_HIT
            resolved = SignalRepository.get_signal_by_id(sig_id)
            self.assertEqual(resolved["status"], "TARGET_HIT")

            # Check audit event payload
            events = db_manager.execute_fo_query(
                "SELECT * FROM fo_signal_events WHERE signal_id = %s AND event_type = 'TARGET_HIT';",
                (sig_id,)
            )
            self.assertEqual(len(events), 1)
            details = events[0]["event_details"]
            self.assertEqual(details["pnl_points"], 10.0) # Target 260 - Entry 250 = 10 pts
            self.assertEqual(details["gross_pnl_inr"], 10.0) # 10 pts * 1 share = Rs 10
            self.assertGreaterEqual(details["net_pnl_inr"], 0.0)

    def test_stop_loss_hit_resolution(self):
        """Validates STOP_LOSS_HIT transition, negative P&L, and event logging."""
        sig_id = f"SIG_TEST_SL_{self.test_run_id}"
        sig_data = {
            "signal_id": sig_id,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}FUT",
            "exchange": "NSE",
            "strategy_category": "SHORT_TERM",
            "strategy_name": "ShortTermSwingTrend",
            "strategy_version": "1.0.0",
            "direction": "BUY",
            "entry_reference_price": 250.0,
            "stop_loss_price": 245.0,
            "target_price": 260.0,
            "risk_per_unit": 5.0,
            "reward_per_unit": 10.0,
            "reward_to_risk_ratio": 2.0,
            "risk_percentage": 2.0,
            "total_score": 80.0,
            "status": "NEW",
            "source_cycle_id": f"CYCLE_{self.test_run_id}",
            "source_snapshot_id": 222222,
            "intended_holding_period": "1-5_TRADING_DAYS"
        }
        SignalRepository.save_signal(sig_data)

        # Mock latest market quote breaching SL: LTP = 244.0 <= Stop Loss (245.0)
        mock_snapshot = [{
            "id": 222223,
            "symbol": self.symbol,
            "ltp": 244.0,
            "last_price": 244.0,
            "lot_size": 1250,
            "expiry": (date.today() + timedelta(days=15)).isoformat()
        }]

        with patch("data.common_db_reader.CommonDbReader.execute_read", return_value=mock_snapshot):
            result = OpenCallWorker.run_cycle()
            self.assertEqual(result["status"], "SUCCESS")
            self.assertGreaterEqual(result["exit_events_triggered"], 1)

            resolved = SignalRepository.get_signal_by_id(sig_id)
            self.assertEqual(resolved["status"], "STOP_LOSS_HIT")

            events = db_manager.execute_fo_query(
                "SELECT * FROM fo_signal_events WHERE signal_id = %s AND event_type = 'STOP_LOSS_HIT';",
                (sig_id,)
            )
            self.assertEqual(len(events), 1)
            details = events[0]["event_details"]
            self.assertEqual(details["pnl_points"], -5.0) # SL 245 - Entry 250 = -5 pts
            self.assertEqual(details["gross_pnl_inr"], -5.0) # -5 pts * 1 share = -Rs 5

    def test_intraday_squareoff_cutoff(self):
        """
        Validates:
        1. SELL positions are mandatorily squared-off at 15:00 IST.
        2. BUY positions are NOT squared off at 15:00 IST and continue till SL or target.
        """
        # A) SELL Signal (Must be mandatorily squared-off at/after 15:00 IST)
        sig_id_sell = f"SIG_TEST_SELL_{self.test_run_id}"
        sig_data_sell = {
            "signal_id": sig_id_sell,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}-EQ",
            "exchange": "NSE",
            "strategy_category": "INTRADAY",
            "strategy_name": "IntradayBreakoutMomentum",
            "strategy_version": "1.0.0",
            "direction": "SELL",
            "entry_reference_price": 250.0,
            "stop_loss_price": 255.0,
            "target_price": 240.0,
            "risk_per_unit": 5.0,
            "reward_per_unit": 10.0,
            "reward_to_risk_ratio": 2.0,
            "risk_percentage": 2.0,
            "total_score": 85.0,
            "status": "NEW",
            "source_cycle_id": f"CYCLE_SELL_{self.test_run_id}",
            "source_snapshot_id": 333333,
            "intended_holding_period": "SAME_SESSION"
        }
        SignalRepository.save_signal(sig_data_sell)

        # B) BUY Signal (Must NOT be auto squared-off, continues till SL/target)
        sig_id_buy = f"SIG_TEST_BUY_{self.test_run_id}"
        sig_data_buy = {
            "signal_id": sig_id_buy,
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}-EQ",
            "exchange": "NSE",
            "strategy_category": "INTRADAY",
            "strategy_name": "IntradayBreakoutMomentum",
            "strategy_version": "1.0.0",
            "direction": "BUY",
            "entry_reference_price": 250.0,
            "stop_loss_price": 245.0,
            "target_price": 260.0,
            "risk_per_unit": 5.0,
            "reward_per_unit": 10.0,
            "reward_to_risk_ratio": 2.0,
            "risk_percentage": 2.0,
            "total_score": 85.0,
            "status": "NEW",
            "source_cycle_id": f"CYCLE_BUY_{self.test_run_id}",
            "source_snapshot_id": 333334,
            "intended_holding_period": "DELIVERY"
        }
        SignalRepository.save_signal(sig_data_buy)

        # Mock current time at 15:05 IST (past 15:00 IST mandatory square-off cutoff)
        # LTP is at 248.0 (neither target nor SL hit for either signal)
        mock_time = datetime.combine(date.today(), dtime(15, 5), tzinfo=IST)
        mock_snapshot = [{
            "id": 333335,
            "symbol": self.symbol,
            "ltp": 248.0,
            "last_price": 248.0,
            "lot_size": 1,
            "expiry": (date.today() + timedelta(days=20)).isoformat()
        }]

        with patch("data.common_db_reader.CommonDbReader.execute_read", return_value=mock_snapshot):
            result = OpenCallWorker.run_cycle(current_dt=mock_time)
            self.assertEqual(result["status"], "SUCCESS")
            # Only the SELL signal should trigger square-off (1 exit event)
            self.assertEqual(result["exit_events_triggered"], 1)

            # SELL signal must transition to TIME_EXIT (mandatory square-off)
            resolved_sell = SignalRepository.get_signal_by_id(sig_id_sell)
            self.assertEqual(resolved_sell["status"], "TIME_EXIT")

            # BUY signal must remain NEW/ACTIVE (no mandatory square-off)
            unresolved_buy = SignalRepository.get_signal_by_id(sig_id_buy)
            self.assertEqual(unresolved_buy["status"], "NEW")

    def tearDown(self):
        try:
            db_manager.execute_fo_write(
                "DELETE FROM fo_signal_events WHERE signal_id IN (SELECT signal_id FROM fo_signals WHERE symbol = %s);",
                (self.symbol,)
            )
            db_manager.execute_fo_write("DELETE FROM fo_signals WHERE symbol = %s;", (self.symbol,))
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
