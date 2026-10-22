"""
================================================================================
FnOQuantAI: Market Repository & Durable Watermark Manager
================================================================================
Controls data retrieval, watermark progression, and new-data guarantees:
- Durable checkpointing via fo_processing_watermarks.
- Prevents reprocessing of unchanged cycles or identical snapshot IDs.
- Provides validated price histories for technical and quantitative indicator engines.
================================================================================
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

from database.db_manager import execute_fo_query, execute_fo_write
from data.common_db_reader import CommonDbReader
from data.freshness import ObservationValidator

log = logging.getLogger("FnOQuantAI.MarketRepository")


class WatermarkManager:

    @classmethod
    def advance_watermarks(cls, observations: List[Dict[str, Any]]) -> int:
        """Advances watermarks for a batch of fresh observations."""
        count = 0
        for obs in observations:
            cls.advance_watermark(
                symbol=obs["symbol"],
                cycle_id=obs.get("cycle_id", "UNKNOWN"),
                snapshot_id=obs.get("id", 0),
                source_timestamp=obs.get("source_timestamp"),
                retrieved_at=obs.get("retrieved_at")
            )
            count += 1
        return count

    """Manages persistent processing progress in fo_processing_watermarks."""

    @classmethod
    def get_watermark(cls, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieves current watermark record for a given equity symbol."""
        sql = """
        SELECT symbol, last_processed_cycle_id, last_processed_snapshot_id,
               last_source_timestamp, last_retrieved_at, updated_at
        FROM fo_processing_watermarks
        WHERE symbol = %s;
        """
        rows = execute_fo_query(sql, [symbol])
        return rows[0] if rows else None

    @classmethod
    def get_all_watermarks(cls) -> Dict[str, Dict[str, Any]]:
        """Returns mapping of symbol -> watermark dict for fast in-memory checking."""
        sql = """
        SELECT symbol, last_processed_cycle_id, last_processed_snapshot_id,
               last_source_timestamp, last_retrieved_at, updated_at
        FROM fo_processing_watermarks;
        """
        rows = execute_fo_query(sql)
        return {r["symbol"]: r for r in rows}

    @classmethod
    def is_new_observation(
        cls,
        symbol: str,
        cycle_id: str,
        snapshot_id: int,
        cached_watermarks: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> bool:
        """
        Returns True ONLY if this snapshot represents genuinely new, un-evaluated data.
        Returns False if cycle_id matches last processed cycle or snapshot_id <= watermark.
        """
        if cached_watermarks is not None:
            wm = cached_watermarks.get(symbol)
        else:
            wm = cls.get_watermark(symbol)

        if not wm:
            return True

        last_cycle = wm.get("last_processed_cycle_id")
        last_snap_id = wm.get("last_processed_snapshot_id", 0)

        # Repetition of identical cycle ID must never trigger duplicate signal generation
        if cycle_id == last_cycle:
            return False

        # Snapshot ID must be strictly newer
        if snapshot_id <= last_snap_id:
            return False

        return True

    @classmethod
    def advance_watermark(
        cls,
        symbol: str,
        cycle_id: str,
        snapshot_id: int,
        source_timestamp: Optional[datetime] = None,
        retrieved_at: Optional[datetime] = None
    ) -> None:
        """Atomically advances the watermark for a symbol in PostgreSQL."""
        sql = """
        INSERT INTO fo_processing_watermarks (
            symbol, last_processed_cycle_id, last_processed_snapshot_id,
            last_source_timestamp, last_retrieved_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (symbol) DO UPDATE SET
            last_processed_cycle_id = EXCLUDED.last_processed_cycle_id,
            last_processed_snapshot_id = EXCLUDED.last_processed_snapshot_id,
            last_source_timestamp = EXCLUDED.last_source_timestamp,
            last_retrieved_at = EXCLUDED.last_retrieved_at,
            updated_at = CURRENT_TIMESTAMP;
        """
        execute_fo_write(sql, [symbol, cycle_id, snapshot_id, source_timestamp, retrieved_at])
        log.debug(f"Watermark advanced for {symbol}: Cycle={cycle_id}, SnapshotID={snapshot_id}")


class MarketRepository:
    """Public data access interface for FnOQuantAI quantitative analysis."""

    @classmethod
    def get_latest_unprocessed_cycle(cls) -> Optional[str]:
        """
        Finds the newest cycle from CommonModule that has pending work.
        Returns None if all available cycles have already been watermarked.
        """
        recent_cycles = CommonDbReader.fetch_distinct_cycles(limit=10)
        if not recent_cycles:
            return None

        watermarks = WatermarkManager.get_all_watermarks()

        # Check latest cycles from newest to oldest
        for cycle_id in reversed(recent_cycles):
            snapshots = CommonDbReader.fetch_snapshots_by_cycle(cycle_id)
            if not snapshots:
                continue

            has_unprocessed_symbol = False
            for s in snapshots:
                sym = s["symbol"]
                snap_id = s["id"]
                if WatermarkManager.is_new_observation(sym, cycle_id, snap_id, watermarks):
                    has_unprocessed_symbol = True
                    break

            if has_unprocessed_symbol:
                return cycle_id

        return None

    @classmethod
    def get_fresh_observations(
        cls,
        cycle_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves, validates, and filters equity snapshots for a cycle.
        Only returns records that pass integrity checks AND represent genuinely new data.
        """
        target_cycle = cycle_id or cls.get_latest_unprocessed_cycle()
        if not target_cycle:
            return []

        raw_records = CommonDbReader.fetch_snapshots_by_cycle(target_cycle)
        watermarks = WatermarkManager.get_all_watermarks()

        validated_observations: List[Dict[str, Any]] = []

        for record in raw_records:
            sym = record.get("symbol", "UNKNOWN")
            snap_id = record.get("id", 0)

            # 1. Freshness & Integrity Validation
            is_valid, reason = ObservationValidator.validate_snapshot(record)
            if not is_valid:
                log.warning(f"Rejected snapshot for {sym} in {target_cycle}: {reason}")
                continue

            # 2. Watermark / Duplicate Protection
            if not WatermarkManager.is_new_observation(sym, target_cycle, snap_id, watermarks):
                log.debug(f"Skipping already-processed observation: {sym} in {target_cycle}")
                continue

            validated_observations.append(record)

        return validated_observations

    @classmethod
    def get_validated_history(
        cls,
        symbol: str,
        min_bars: int = 5,
        max_bars: int = 60,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieves chronological historical observations for indicator calculation.
        Validates monotonic ordering, non-empty LTP, and absence of duplicate cycles.
        """
        effective_max = limit or max_bars
        raw_history = CommonDbReader.fetch_recent_history(symbol, limit=effective_max)
        if len(raw_history) < min_bars:
            return []

        clean_bars: List[Dict[str, Any]] = []
        seen_cycles = set()

        for bar in raw_history:
            cid = bar.get("cycle_id")
            if cid in seen_cycles:
                continue
            seen_cycles.add(cid)

            # Price integrity check on historical bars
            ltp = bar.get("ltp")
            if ltp is None or float(ltp) <= 0:
                continue

            clean_bars.append(bar)

        if len(clean_bars) < min_bars:
            return []

        return clean_bars
