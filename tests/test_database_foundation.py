"""
================================================================================
FnOQuantAI: Phase 2 Database Foundation Tests
================================================================================
Verifies:
1. All cq_* tables exist and can store/retrieve project records.
2. Read-only source access works cleanly on market_equity_snapshots.
3. Strict isolation barrier rejects any mutation/write attempts targeting CommonModule tables.
4. Schema initialization is strictly idempotent.
================================================================================
"""

import sys
import os
import unittest
from datetime import datetime, timezone

# Add project root to sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from database.db_manager import (
    init_schema,
    execute_fo_write,
    execute_fo_query,
    assert_safe_write_statement,
    FORBIDDEN_MUTATION_TABLES
)
from data.common_db_reader import CommonDbReader


class TestDatabaseFoundation(unittest.TestCase):
    """Test suite for Phase 2 database foundation and isolation."""

    def test_01_read_only_access_can_query_market_data(self):
        """1. Verify CommonDbReader can read market_equity_snapshots cleanly."""
        snapshots = CommonDbReader.fetch_latest_equity_snapshots()
        self.assertIsInstance(snapshots, list)
        if snapshots:
            first = snapshots[0]
            self.assertIn("symbol", first)
            self.assertIn("ltp", first)
            self.assertIn("cycle_id", first)

    def test_02_reader_blocks_insert_mutation(self):
        """2. Verify CommonDbReader rejects INSERT statements with PermissionError."""
        with self.assertRaises(PermissionError):
            CommonDbReader.execute_read(
                "INSERT INTO market_equity_snapshots (symbol) VALUES ('TEST')"
            )

    def test_03_reader_blocks_update_mutation(self):
        """3. Verify CommonDbReader rejects UPDATE statements with PermissionError."""
        with self.assertRaises(PermissionError):
            CommonDbReader.execute_read(
                "UPDATE market_equity_snapshots SET ltp = 100 WHERE symbol = 'INFY'"
            )

    def test_04_reader_blocks_delete_mutation(self):
        """4. Verify CommonDbReader rejects DELETE statements with PermissionError."""
        with self.assertRaises(PermissionError):
            CommonDbReader.execute_read(
                "DELETE FROM market_equity_snapshots WHERE symbol = 'INFY'"
            )

    def test_05_reader_blocks_drop_mutation(self):
        """5. Verify CommonDbReader rejects DROP TABLE statements with PermissionError."""
        with self.assertRaises(PermissionError):
            CommonDbReader.execute_read(
                "DROP TABLE market_equity_snapshots"
            )

    def test_06_db_manager_guard_blocks_writes_to_common_tables(self):
        """6. Verify execute_fo_write raises PermissionError if targeting any CommonModule table."""
        for forbidden in FORBIDDEN_MUTATION_TABLES:
            with self.assertRaises(PermissionError):
                execute_fo_write(f"DELETE FROM {forbidden} WHERE 1=1")
            with self.assertRaises(PermissionError):
                execute_fo_write(f"UPDATE {forbidden} SET id=0")

    def test_07_db_manager_allows_writes_to_cq_tables(self):
        """7. Verify execute_fo_write successfully writes and reads to cq_* tables."""
        test_sym = "TEST_INFY"
        # Upsert watermark
        sql = """
        INSERT INTO fo_processing_watermarks (
            symbol, last_processed_cycle_id, last_processed_snapshot_id
        ) VALUES (%s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            last_processed_cycle_id = EXCLUDED.last_processed_cycle_id,
            last_processed_snapshot_id = EXCLUDED.last_processed_snapshot_id,
            updated_at = CURRENT_TIMESTAMP;
        """
        rows_aff = execute_fo_write(sql, [test_sym, "COM_CYCLE_TEST_01", 999999])
        self.assertGreaterEqual(rows_aff, 1)

        # Query back
        res = execute_fo_query(
            "SELECT * FROM fo_processing_watermarks WHERE symbol = %s",
            [test_sym]
        )
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["symbol"], test_sym)
        self.assertEqual(res[0]["last_processed_cycle_id"], "COM_CYCLE_TEST_01")
        self.assertEqual(res[0]["last_processed_snapshot_id"], 999999)

        # Clean up test row
        execute_fo_write("DELETE FROM fo_processing_watermarks WHERE symbol = %s", [test_sym])

    def test_08_schema_initialization_is_idempotent(self):
        """8. Verify calling init_schema() repeatedly does not cause duplicate errors."""
        ok1 = init_schema()
        ok2 = init_schema()
        self.assertTrue(ok1)
        self.assertTrue(ok2)

    def test_09_fetch_recent_history_returns_chronological(self):
        """9. Verify fetch_recent_history returns records in ascending time order."""
        history = CommonDbReader.fetch_recent_history("INFY", limit=5)
        if len(history) > 1:
            for i in range(len(history) - 1):
                self.assertLessEqual(
                    history[i]["retrieved_at"],
                    history[i + 1]["retrieved_at"]
                )

    def test_10_fetch_distinct_cycles(self):
        """10. Verify fetch_distinct_cycles returns list of cycle IDs."""
        cycles = CommonDbReader.fetch_distinct_cycles(limit=10)
        self.assertIsInstance(cycles, list)
        for c in cycles:
            self.assertIsInstance(c, str)


if __name__ == "__main__":
    unittest.main()
