"""
================================================================================
FnOQuantAI: Signal Repository Module
================================================================================
Provides persistent PostgreSQL storage and query operations for:
- fo_signals: Generated quantitative trading signals for NSE equities.
- fo_signal_events: Lifecycle transition event log for full auditability.
Enforces unique constraints, idempotent signal ingestion, and lifecycle updates.
================================================================================
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
from zoneinfo import ZoneInfo

from database import db_manager

log = logging.getLogger("FnOQuantAI.Signals.Repository")
IST = ZoneInfo("Asia/Kolkata")


class SignalRepository:
    """PostgreSQL storage and query operations for quantitative signals and events."""

    @classmethod
    def save_signal(cls, signal_data: Dict[str, Any]) -> Optional[str]:
        """
        Persists a newly generated signal to fo_signals and logs creation in fo_signal_events.
        Idempotent: Rejects duplicates based on (symbol, strategy_category, source_cycle_id).
        Returns signal_id on successful insertion, or None if already exists.
        """
        category = signal_data.get("strategy_category", "UNKNOWN")
        symbol = signal_data.get("symbol", "UNKNOWN")
        trading_symbol = signal_data.get("trading_symbol") or f"{symbol}-EQ"
        cycle_id = signal_data.get("source_cycle_id", "UNKNOWN")

        signal_id = signal_data.get("signal_id")
        if not signal_id:
            now_ts = int(datetime.now(IST).timestamp() * 1000)
            signal_id = f"SIG_FO_{category}_{symbol}_{now_ts}"

        feature_json = json.dumps(signal_data.get("feature_contributions", {}))
        now_dt = datetime.now(IST)

        entry_price = float(signal_data.get("entry_reference_price") or signal_data.get("entry_price") or 0.0)
        stop_loss = float(signal_data.get("stop_loss_price") or signal_data.get("stop_loss") or 0.0)
        target = float(signal_data.get("target_price") or signal_data.get("target") or 0.0)
        risk_unit = float(signal_data.get("risk_per_unit") or signal_data.get("risk_points") or 0.0)
        reward_unit = float(signal_data.get("reward_per_unit") or signal_data.get("reward_points") or 0.0)
        rr_ratio = float(signal_data.get("reward_to_risk_ratio") or 0.0)
        risk_pct = float(signal_data.get("risk_percentage") or 0.0)
        score = float(signal_data.get("total_score") or 0.0)
        status = signal_data.get("status", "NEW")

        sql_insert_signal = """
            INSERT INTO fo_signals (
                signal_id,
                symbol,
                trading_symbol,
                exchange,
                strategy_category,
                direction,
                status,
                entry_reference_price,
                stop_loss_price,
                target_price,
                risk_per_unit,
                reward_per_unit,
                reward_to_risk_ratio,
                risk_percentage,
                total_score,
                feature_contributions,
                strategy_name,
                strategy_version,
                intended_holding_period,
                source_cycle_id,
                source_snapshot_id,
                source_market_timestamp,
                created_at,
                updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            ON CONFLICT (symbol, strategy_category, source_cycle_id) DO NOTHING;
        """

        params_signal = (
            signal_id,
            symbol,
            trading_symbol,
            signal_data.get("exchange", "NSE"),
            category,
            signal_data.get("direction", "BUY"),
            status,
            entry_price,
            stop_loss,
            target,
            risk_unit,
            reward_unit,
            rr_ratio,
            risk_pct,
            score,
            feature_json,
            signal_data.get("strategy_name", "UNKNOWN"),
            signal_data.get("strategy_version", "1.0.0"),
            signal_data.get("intended_holding_period", "SAME_SESSION"),
            cycle_id,
            int(signal_data.get("source_snapshot_id") or 0),
            signal_data.get("source_market_timestamp", now_dt),
            now_dt,
            now_dt
        )

        try:
            affected = db_manager.execute_fo_write(sql_insert_signal, params_signal)
            if affected == 0:
                log.info(f"Signal suppressed: {symbol} [{category}] for cycle {cycle_id} already exists.")
                return None

            event_payload = json.dumps({
                "action": "SIGNAL_CREATED",
                "direction": signal_data.get("direction"),
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "target_price": target,
                "score": score,
                "source_cycle_id": cycle_id
            })

            sql_insert_event = """
                INSERT INTO fo_signal_events (
                    signal_id,
                    event_type,
                    from_status,
                    to_status,
                    trigger_price,
                    market_timestamp,
                    event_timestamp,
                    event_details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb);
            """
            db_manager.execute_fo_write(
                sql_insert_event,
                (signal_id, "CREATED", None, status, entry_price, now_dt, now_dt, event_payload)
            )
            log.info(f"Signal persisted successfully: {signal_id} ({symbol} {signal_data.get('direction')} @ {entry_price})")
            return signal_id

        except Exception as e:
            log.error(f"Error persisting signal {signal_id}: {e}")
            raise

    @classmethod
    def get_signal_by_id(cls, signal_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single signal record by signal_id."""
        sql = """
            SELECT * FROM fo_signals WHERE signal_id = %s LIMIT 1;
        """
        rows = db_manager.execute_fo_query(sql, (signal_id,), use_primary=True)
        return rows[0] if rows else None

    @classmethod
    def get_signal(cls, signal_id: str) -> Optional[Dict[str, Any]]:
        """Alias for get_signal_by_id."""
        return cls.get_signal_by_id(signal_id)

    @classmethod
    def get_active_signals(cls, strategy_category: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieves all currently active or new signals.
        Optionally filtered by strategy category.
        """
        if strategy_category:
            sql = """
                SELECT * FROM fo_signals
                WHERE status IN ('NEW', 'ACTIVE') AND strategy_category = %s
                ORDER BY created_at ASC;
            """
            return db_manager.execute_fo_query(sql, (strategy_category,), use_primary=True)
        else:
            sql = """
                SELECT * FROM fo_signals
                WHERE status IN ('NEW', 'ACTIVE')
                ORDER BY created_at ASC;
            """
            return db_manager.execute_fo_query(sql, use_primary=True)

    @classmethod
    def count_active_signals(
        cls,
        symbol: Optional[str] = None,
        strategy_category: Optional[str] = None
    ) -> int:
        """Counts open/active signals for concurrency and risk allocation checks."""
        conditions = ["status IN ('NEW', 'ACTIVE')"]
        params = []

        if symbol:
            conditions.append("symbol = %s")
            params.append(symbol)
        if strategy_category:
            conditions.append("strategy_category = %s")
            params.append(strategy_category)

        where_clause = " AND ".join(conditions)
        sql = f"SELECT COUNT(*) as count FROM fo_signals WHERE {where_clause};"
        rows = db_manager.execute_fo_query(sql, tuple(params) if params else None)
        return int(rows[0]["count"]) if rows else 0

    @classmethod
    def update_signal_status(
        cls,
        signal_id: str,
        new_status: str,
        event_type: Optional[str] = None,
        event_payload: Optional[Dict[str, Any]] = None,
        trigger_price: Optional[float] = None,
        event_details: Optional[Dict[str, Any]] = None,
        market_timestamp: Optional[datetime] = None
    ) -> bool:
        """
        Updates signal status and logs lifecycle event in fo_signal_events.
        """
        now_dt = datetime.now(IST)
        existing = cls.get_signal_by_id(signal_id)
        from_status = existing.get("status") if existing else None

        sql_update = "UPDATE fo_signals SET status = %s, updated_at = %s WHERE signal_id = %s;"
        affected = db_manager.execute_fo_write(sql_update, (new_status, now_dt, signal_id))

        if affected > 0:
            payload = event_payload or event_details or {}
            payload_str = json.dumps(payload)
            ev_type = event_type or new_status
            sql_event = """
                INSERT INTO fo_signal_events (
                    signal_id,
                    event_type,
                    from_status,
                    to_status,
                    trigger_price,
                    market_timestamp,
                    event_timestamp,
                    event_details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb);
            """
            db_manager.execute_fo_write(
                sql_event,
                (signal_id, ev_type, from_status, new_status, trigger_price, market_timestamp or now_dt, now_dt, payload_str)
            )
            log.info(f"Signal {signal_id} transitioned [{from_status} -> {new_status}] (Event: {ev_type})")
            return True

        return False

    @classmethod
    def get_recent_signals(cls, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetches the most recent signals ordered by creation timestamp."""
        query = """
            SELECT s.*, 
                   t.trade_id, t.is_live_trade, t.quantity, t.allocated_capital_inr,
                   t.actual_entry_fill_price, t.actual_exit_fill_price, t.net_pnl,
                   t.net_pnl_percentage, t.exit_reason
            FROM fo_signals s
            LEFT JOIN fo_trades t ON s.signal_id = t.signal_id
            ORDER BY s.created_at DESC
            LIMIT %s;
        """
        return db_manager.execute_fo_query(query, (limit,))
