"""
================================================================================
FnOQuantAI: Common Database Reader
================================================================================
Dedicated read-only interface to CommonModule market_equity_snapshots:
- Connects exclusively to read replica.
- Provides queries for latest equity quotes, history, cycles, and watermarks.
- Enforces strict read-only guarantees (rejects any mutation queries).
================================================================================
"""

import sys
import os
import re
import logging
from typing import List, Dict, Any, Optional

# Resolve CommonModule
COMMON_MODULE_DIR = r"D:\SM_Server\CommonModule"
if COMMON_MODULE_DIR not in sys.path:
    sys.path.insert(0, COMMON_MODULE_DIR)

from script import postgres_connection

log = logging.getLogger("FnOQuantAI.CommonDbReader")


class CommonDbReader:
    """Read-only reader for CommonModule market_equity_snapshots."""

    @staticmethod
    def _assert_read_only(sql_query: str) -> None:
        """Ensures query is strictly a read operation."""
        cleaned = re.sub(r"--.*?$|\/\*.*?\*\/", "", sql_query, flags=re.MULTILINE).strip().upper()
        if not (cleaned.startswith("SELECT") or cleaned.startswith("WITH")):
            raise PermissionError(
                f"CommonDbReader error: Query must start with SELECT or WITH. Received: {sql_query[:50]}"
            )
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"):
            if re.search(r"\b" + forbidden + r"\b", cleaned):
                raise PermissionError(
                    f"CommonDbReader error: Mutation statement '{forbidden}' detected in read query."
                )

    @classmethod
    def execute_read(
        cls,
        sql_query: str,
        params: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """Executes validated read-only query against the PostgreSQL read replica with transaction cleanup."""
        cls._assert_read_only(sql_query)
        try:
            conn = postgres_connection.get_read_connection()
        except Exception as exc:
            log.warning(f'Read replica connection unavailable ({exc}); falling back to primary connection.')
            conn = postgres_connection.get_write_connection()
        try:
            return postgres_connection.execute_query(sql_query, params, conn=conn)
        finally:
            if conn and getattr(conn, "pgconn", None) and conn.pgconn.transaction_status != 0:
                try:
                    conn.rollback()
                except Exception:
                    pass

    @classmethod
    def fetch_latest_equity_snapshots(cls) -> List[Dict[str, Any]]:
        """
        Retrieves the most recent snapshot for each equity symbol.
        Uses DISTINCT ON (symbol) ordered by retrieved_at DESC.
        """
        sql = """
        SELECT DISTINCT ON (symbol)
            id, symbol, exchange, instrument_type, trading_symbol,
            open, high, low, close, ltp,
            previous_close, change, per_change, volume, traded_value,
            bid_price, ask_price, bid_quantity, ask_quantity,
            total_buy_quantity, total_sell_quantity, open_interest,
            cycle_id, source_timestamp, retrieved_at, created_at
        FROM market_equity_snapshots
        ORDER BY symbol, retrieved_at DESC;
        """
        return cls.execute_read(sql)

    @classmethod
    def fetch_snapshots_by_cycle(cls, cycle_id: str) -> List[Dict[str, Any]]:
        """Fetches all equity records for a specific collection cycle."""
        sql = """
        SELECT
            id, symbol, exchange, instrument_type, trading_symbol,
            open, high, low, close, ltp,
            previous_close, change, per_change, volume, traded_value,
            bid_price, ask_price, bid_quantity, ask_quantity,
            total_buy_quantity, total_sell_quantity, open_interest,
            cycle_id, source_timestamp, retrieved_at, created_at
        FROM market_equity_snapshots
        WHERE cycle_id = %s
        ORDER BY symbol;
        """
        return cls.execute_read(sql, [cycle_id])

    @classmethod
    def fetch_recent_history(
        cls,
        symbol: str,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetches chronological price history for a given equity symbol.
        Returns records ordered by retrieved_at ASC (oldest to newest).
        """
        sql = """
        SELECT
            id, symbol, trading_symbol,
            open, high, low, close, ltp, previous_close,
            change, per_change, volume, open_interest,
            cycle_id, source_timestamp, retrieved_at
        FROM (
            SELECT *
            FROM market_equity_snapshots
            WHERE symbol = %s
            ORDER BY retrieved_at DESC
            LIMIT %s
        ) sub
        ORDER BY retrieved_at ASC;
        """
        return cls.execute_read(sql, [symbol, limit])

    @classmethod
    def fetch_distinct_cycles(cls, limit: int = 20) -> List[str]:
        """Returns the most recent cycle IDs in chronological order."""
        sql = """
        SELECT cycle_id, MIN(retrieved_at) as min_ts
        FROM market_equity_snapshots
        GROUP BY cycle_id
        ORDER BY min_ts DESC
        LIMIT %s;
        """
        rows = cls.execute_read(sql, [limit])
        return [r["cycle_id"] for r in reversed(rows)]
