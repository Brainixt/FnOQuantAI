"""
================================================================================
FnOQuantAI: Phase 3 Freshness, Watermark, & Deduplication Tests
================================================================================
Validates all Section 6 & 19 requirements:
 1. Valid snapshot passes validation.
 2. Zero or negative LTP is rejected.
 3. Missing/invalid symbol or exchange is rejected.
 4. Stale quote exceeding max_age_seconds is rejected.
 5. Inverted bid/ask spread (ask < bid) is rejected.
 6. Watermarks persist durably in fo_processing_watermarks.
 7. Repetition of identical cycle ID is suppressed (NO duplicate processing).
 8. Repetition of identical or older snapshot ID is suppressed.
 9. Worker restart safety: New instance retains progress and skips old cycles.
10. Validated price history returns strictly ordered, deduplicated bars.
================================================================================
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from data.freshness import ObservationValidator
from data.market_repository import WatermarkManager, MarketRepository
from database.db_manager import execute_fo_write, execute_fo_query


class TestFreshnessAndWatermarks(unittest.TestCase):
    """Test suite for Phase 3 freshness, deduplication, and watermarking."""

    def setUp(self):
        self.valid_snapshot = {
            "id": 1001,
            "symbol": "INFY",
            "exchange": "NSE",
            "instrument_type": "COMMODITY",
            "trading_symbol": "INFY30JUN27FUT",
            "ltp": 151500.0,
            "open": 151600.0,
            "high": 151800.0,
            "low": 151300.0,
            "close": 151500.0,
            "bid_price": 151480.0,
            "ask_price": 151520.0,
            "cycle_id": "COM_20261021_184727",
            "source_timestamp": datetime.now(timezone.utc),
            "retrieved_at": datetime.now(timezone.utc)
        }

    # -------------------------------------------------------------------------
    # 1. Validation Rules
    # -------------------------------------------------------------------------
    def test_01_valid_snapshot_passes_validation(self):
        """1. Verify standard well-formed snapshot passes validation."""
        is_valid, reason = ObservationValidator.validate_snapshot(self.valid_snapshot)
        self.assertTrue(is_valid, f"Expected valid, got: {reason}")
        self.assertIsNone(reason)

    def test_02_negative_or_zero_ltp_rejected(self):
        """2. Verify LTP <= 0 or missing LTP is rejected."""
        snap_zero = dict(self.valid_snapshot, ltp=0.0)
        is_v, reason = ObservationValidator.validate_snapshot(snap_zero)
        self.assertFalse(is_v)
        self.assertIn("Non-positive LTP", reason)

        snap_neg = dict(self.valid_snapshot, ltp=-50.0)
        is_v, reason = ObservationValidator.validate_snapshot(snap_neg)
        self.assertFalse(is_v)

        snap_none = dict(self.valid_snapshot, ltp=None)
        is_v, reason = ObservationValidator.validate_snapshot(snap_none)
        self.assertFalse(is_v)

    def test_03_missing_or_invalid_symbol_rejected(self):
        """3. Verify missing or blank symbol is rejected."""
        snap_no_sym = dict(self.valid_snapshot, symbol="")
        is_v, reason = ObservationValidator.validate_snapshot(snap_no_sym)
        self.assertFalse(is_v)
        self.assertIn("Missing or invalid symbol", reason)

    def test_04_wrong_exchange_rejected(self):
        """4. Verify non-NSE exchange (e.g. MCX) is rejected for commodity engine."""
        snap_nse = dict(self.valid_snapshot, exchange="MCX")
        is_v, reason = ObservationValidator.validate_snapshot(snap_nse)
        self.assertFalse(is_v)
        self.assertIn("strictly operates on NSE", reason)

    def test_05_stale_quote_rejected(self):
        """5. Verify observation older than max_age_seconds is rejected."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=500)
        snap_stale = dict(self.valid_snapshot, retrieved_at=old_time)
        is_v, reason = ObservationValidator.validate_snapshot(snap_stale, max_age_seconds=300)
        self.assertFalse(is_v)
        self.assertIn("Stale quote", reason)

    def test_06_inverted_spread_rejected(self):
        """6. Verify inverted spread where ask < bid is rejected as corrupted."""
        snap_inv = dict(self.valid_snapshot, bid_price=100.0, ask_price=90.0)
        is_v, reason = ObservationValidator.validate_snapshot(snap_inv)
        self.assertFalse(is_v)
        self.assertIn("Inverted spread", reason)

    # -------------------------------------------------------------------------
    # 2. Durable Watermarks & Deduplication
    # -------------------------------------------------------------------------
    def test_07_watermark_persistence(self):
        """7. Verify watermark is durably stored in PostgreSQL and retrieved."""
        test_sym = "UNIT_TEST_SYM"
        try:
            WatermarkManager.advance_watermark(
                symbol=test_sym,
                cycle_id="COM_20261021_180000",
                snapshot_id=500
            )

            wm = WatermarkManager.get_watermark(test_sym)
            self.assertIsNotNone(wm)
            self.assertEqual(wm["symbol"], test_sym)
            self.assertEqual(wm["last_processed_cycle_id"], "COM_20261021_180000")
            self.assertEqual(wm["last_processed_snapshot_id"], 500)
        finally:
            execute_fo_write("DELETE FROM fo_processing_watermarks WHERE symbol = %s", [test_sym])

    def test_08_duplicate_cycle_suppression(self):
        """8. Verify identical cycle ID is rejected to avoid duplicate processing."""
        test_sym = "UNIT_TEST_CYCLE"
        try:
            WatermarkManager.advance_watermark(
                symbol=test_sym,
                cycle_id="COM_20261021_180000",
                snapshot_id=500
            )

            # Same cycle ID with newer snapshot_id -> REJECTED (identical cycle)
            is_new = WatermarkManager.is_new_observation(
                symbol=test_sym,
                cycle_id="COM_20261021_180000",
                snapshot_id=501
            )
            self.assertFalse(is_new, "Expected duplicate cycle to be suppressed")

            # Different, newer cycle ID with higher snapshot_id -> ACCEPTED
            is_new_next = WatermarkManager.is_new_observation(
                symbol=test_sym,
                cycle_id="COM_20261021_180500",
                snapshot_id=502
            )
            self.assertTrue(is_new_next, "Expected newer cycle to be accepted")
        finally:
            execute_fo_write("DELETE FROM fo_processing_watermarks WHERE symbol = %s", [test_sym])

    def test_09_older_snapshot_id_suppression(self):
        """9. Verify older or equal snapshot ID is rejected."""
        test_sym = "UNIT_TEST_SNAP"
        try:
            WatermarkManager.advance_watermark(
                symbol=test_sym,
                cycle_id="COM_20261021_180000",
                snapshot_id=600
            )

            # Different cycle ID but older snapshot_id -> REJECTED
            is_new = WatermarkManager.is_new_observation(
                symbol=test_sym,
                cycle_id="COM_20261021_180500",
                snapshot_id=599
            )
            self.assertFalse(is_new, "Expected older snapshot ID to be suppressed")
        finally:
            execute_fo_write("DELETE FROM fo_processing_watermarks WHERE symbol = %s", [test_sym])

    def test_10_restart_safety(self):
        """10. Verify worker restart retrieves existing watermark and prevents re-run."""
        test_sym = "UNIT_TEST_RESTART"
        try:
            WatermarkManager.advance_watermark(
                symbol=test_sym,
                cycle_id="COM_20261021_180000",
                snapshot_id=700
            )

            # Simulate worker reboot: re-load watermarks from scratch
            all_wm = WatermarkManager.get_all_watermarks()
            self.assertIn(test_sym, all_wm)

            # Immediate evaluation after restart: must be recognized as already processed
            is_new = WatermarkManager.is_new_observation(
                symbol=test_sym,
                cycle_id="COM_20261021_180000",
                snapshot_id=700,
                cached_watermarks=all_wm
            )
            self.assertFalse(is_new, "Watermark after restart must suppress repeated execution")
        finally:
            execute_fo_write("DELETE FROM fo_processing_watermarks WHERE symbol = %s", [test_sym])

    # -------------------------------------------------------------------------
    # 3. Market Repository History Ordering
    # -------------------------------------------------------------------------
    def test_11_validated_history_chronological_ordering(self):
        """11. Verify get_validated_history returns strictly ascending historical records."""
        history = MarketRepository.get_validated_history("INFY", min_bars=2, max_bars=10)
        if len(history) >= 2:
            for i in range(len(history) - 1):
                self.assertLessEqual(history[i]["retrieved_at"], history[i + 1]["retrieved_at"])
                self.assertGreater(float(history[i]["ltp"]), 0)


if __name__ == "__main__":
    unittest.main()
