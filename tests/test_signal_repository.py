"""
================================================================================
FnOQuantAI Unit Tests: Signal Repository
================================================================================
Tests persistence, event logging, status transitions, and query methods for fo_signals.
================================================================================
"""

import unittest
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from database import db_manager
from signals.signal_repository import SignalRepository

IST = ZoneInfo("Asia/Kolkata")


class TestSignalRepository(unittest.TestCase):
    """Tests CRUD, idempotency, and lifecycle management on fo_signals and fo_signal_events."""

    def setUp(self):
        self.test_run_id = uuid.uuid4().hex[:8]
        self.cycle_id = f"TEST_CYCLE_{self.test_run_id}"
        self.symbol = f"CRUDE_{self.test_run_id}"

    def test_save_and_duplicate_suppression(self):
        """Verifies signal creation, idempotency, and creation event persistence."""
        signal_data = {
            "symbol": self.symbol,
            "trading_symbol": f"{self.symbol}FUT",
            "exchange": "NSE",
            "strategy_category": "INTRADAY",
            "strategy_name": "IntradayBreakoutMomentum",
            "strategy_version": "1.0.0",
            "direction": "BUY",
            "entry_reference_price": 6250.0,
            "stop_loss_price": 6210.0,
            "target_price": 6320.0,
            "risk_per_unit": 40.0,
            "reward_per_unit": 70.0,
            "reward_to_risk_ratio": 1.75,
            "risk_percentage": 0.64,
            "total_score": 85.0,
            "feature_contributions": {"trend": 25.0, "rsi": 15.0},
            "status": "NEW",
            "source_cycle_id": self.cycle_id,
            "source_snapshot_id": 123456,
            "intended_holding_period": "SAME_DAY"
        }

        # 1. First save must succeed
        sig_id = SignalRepository.save_signal(signal_data)
        self.assertIsNotNone(sig_id)
        self.assertTrue(sig_id.startswith("SIG_FO_INTRADAY_") or sig_id.startswith("SIG_INTRADAY_"))

        # 2. Duplicate save with identical (symbol, category, cycle_id) must be suppressed
        dup_id = SignalRepository.save_signal(signal_data)
        self.assertIsNone(dup_id, "Duplicate signal in the same cycle must return None")

        # 3. Retrieve by ID
        fetched = SignalRepository.get_signal_by_id(sig_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["symbol"], self.symbol)
        self.assertEqual(float(fetched["entry_reference_price"]), 6250.0)
        self.assertEqual(fetched["status"], "NEW")

        # 4. Check active signals count
        active_count = SignalRepository.count_active_signals(symbol=self.symbol)
        self.assertEqual(active_count, 1)

        # 5. Transition status to TARGET_HIT
        updated = SignalRepository.update_signal_status(
            signal_id=sig_id,
            new_status="TARGET_HIT",
            event_type="TARGET_ACHIEVED",
            event_payload={"hit_price": 6320.0, "pnl_points": 70.0},
            trigger_price=6320.0
        )
        self.assertTrue(updated)

        # 6. Verify resolved state
        resolved = SignalRepository.get_signal_by_id(sig_id)
        self.assertEqual(resolved["status"], "TARGET_HIT")

        # 7. Check active count is now 0
        active_after = SignalRepository.count_active_signals(symbol=self.symbol)
        self.assertEqual(active_after, 0)

        # 8. Check events audit trail
        events = db_manager.execute_fo_query(
            "SELECT * FROM fo_signal_events WHERE signal_id = %s ORDER BY event_timestamp ASC;",
            (sig_id,)
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event_type"], "CREATED")
        self.assertEqual(events[1]["event_type"], "TARGET_ACHIEVED")
        self.assertEqual(float(events[1]["trigger_price"]), 6320.0)

    def tearDown(self):
        # Clean up test rows
        try:
            db_manager.execute_fo_write(
                "DELETE FROM fo_signal_events WHERE signal_id IN (SELECT signal_id FROM fo_signals WHERE symbol = %s);",
                (self.symbol,)
            )
            db_manager.execute_fo_write(
                "DELETE FROM fo_signals WHERE symbol = %s;",
                (self.symbol,)
            )
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
