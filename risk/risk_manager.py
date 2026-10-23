"""
================================================================================
FnOQuantAI: Risk Management & Trade Allocation Layer
================================================================================
Provides institutional-grade pre-execution safeguards and trade lifecycle tracking
for NSE F&O-eligible underlying cash equities:
1. Market Timing & Cutoff Checks:
   - Blocks trade initiation outside NSE trading hours (09:15 to 15:30 IST, Mon-Fri).
   - Intraday entries blocked after 15:00 IST.
2. Data Freshness Verification:
   - Rejects trades based on stale (>360s) or unverified quotes.
3. Capital Allocation & Sizing:
   - Configurable capital allocation of ?10,000 per actual trade.
   - Sizing: quantity = max(1, int(CAPITAL_PER_TRADE_INR // ltp)).
   - Single active position per equity symbol (no stacking / pyramiding).
   - Category concurrency limits (MAX_SIGNALS_INTRADAY, etc.).
4. Price Deviation Safeguards:
   - Rejects trade if current market price deviates from signal reference by
     > MAX_PRICE_DEVIATION_PERCENT (0.25%) or > MAX_PRICE_DEVIATION_TICKS (5 ticks).
5. Comprehensive Audit & Persistence:
   - Persists approved trades in fo_trades.
   - Logs all rejections, threshold breaches, and risk events in fo_risk_events.
================================================================================
"""

import json
import logging
from datetime import datetime, date, time as dtime
from typing import Dict, Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config

from database import db_manager
from data.freshness import ObservationValidator
from signals.signal_repository import SignalRepository

log = logging.getLogger("FnOQuantAI.Risk.RiskManager")
IST = ZoneInfo("Asia/Kolkata")

NSE_SESSION_OPEN = dtime(9, 15)
NSE_SESSION_CLOSE = dtime(15, 30)


class RiskManager:
    """Pre-trade risk validator, position controller, and trade allocation repository."""

    @classmethod
    def validate_trade_eligibility(
        cls,
        signal: Dict[str, Any],
        current_quote: Dict[str, Any],
        current_dt: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Runs comprehensive pre-trade validation on an eligible signal.
        Returns:
            (is_approved, rejection_reason, trade_plan)
        """
        now = current_dt or datetime.now(IST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)

        symbol = signal.get("symbol", "UNKNOWN")
        sig_id = signal.get("signal_id")
        category = signal.get("strategy_category", "INTRADAY")
        direction = signal.get("direction", "BUY")
        ref_price = float(signal.get("entry_reference_price") or signal.get("entry_price") or 0.0)

        # ----------------------------------------------------------------------
        # 1. Market Hours and Trading Session Validation (NSE Cash: 09:15 - 15:30 IST)
        # ----------------------------------------------------------------------
        if now.weekday() >= 5:
            cls.log_risk_event(
                event_type="MARKET_CLOSED_REJECTION",
                symbol=symbol,
                signal_id=sig_id,
                details={"reason": f"Weekend rejection (weekday {now.weekday()})", "timestamp": now.isoformat()}
            )
            return False, "Market is closed (Weekend)", None

        now_time = now.time()
        if not (NSE_SESSION_OPEN <= now_time < NSE_SESSION_CLOSE):
            cls.log_risk_event(
                event_type="MARKET_CLOSED_REJECTION",
                symbol=symbol,
                signal_id=sig_id,
                details={"reason": f"Outside NSE trading hours ({now_time.strftime('%H:%M:%S')})"}
            )
            return False, f"Market is closed (NSE Session: {NSE_SESSION_OPEN.strftime('%H:%M')} - {NSE_SESSION_CLOSE.strftime('%H:%M')} IST)", None

        # Intraday Entry Cutoff check (15:00 IST)
        if category == "INTRADAY" and now_time >= config.INTRADAY_ENTRY_CUTOFF_TIME:
            cls.log_risk_event(
                event_type="MARKET_CLOSED_REJECTION",
                symbol=symbol,
                signal_id=sig_id,
                details={"reason": f"Intraday entry cutoff breached ({now_time.strftime('%H:%M:%S')} >= {config.INTRADAY_ENTRY_CUTOFF_TIME.strftime('%H:%M')})"}
            )
            return False, f"Intraday entry cutoff reached ({config.INTRADAY_ENTRY_CUTOFF_TIME.strftime('%H:%M')} IST)", None

        # ----------------------------------------------------------------------
        # 2. Quote Integrity and Freshness Check
        # ----------------------------------------------------------------------
        is_fresh, freshness_err = ObservationValidator.validate_snapshot(current_quote)
        if not is_fresh:
            cls.log_risk_event(
                event_type="STALE_DATA_REJECTION",
                symbol=symbol,
                signal_id=sig_id,
                details={"reason": freshness_err}
            )
            return False, f"Data freshness rejected: {freshness_err}", None

        current_ltp = float(current_quote.get("ltp") or current_quote.get("last_price") or 0.0)
        tick_size = float(current_quote.get("tick_size") or config.DEFAULT_TICK_SIZE)

        if current_ltp <= 0:
            return False, "Invalid non-positive quote price", None

        # ----------------------------------------------------------------------
        # 3. Portfolio Allocation & Exposure Caps
        # ----------------------------------------------------------------------
        # A) Single Position per Instrument (No stacking / pyramiding)
        sql_existing = "SELECT trade_id FROM fo_trades WHERE symbol = %s AND exit_time IS NULL LIMIT 1;"
        existing_trades = db_manager.execute_fo_query(sql_existing, [symbol])
        if existing_trades:
            cls.log_risk_event(
                event_type="POSITION_LIMIT_REACHED",
                symbol=symbol,
                signal_id=sig_id,
                details={"reason": f"Active trade {existing_trades[0]['trade_id']} already exists for symbol {symbol}"}
            )
            return False, f"Position limit reached: Symbol {symbol} already has an active trade", None

        # B) Category Allocation Limits (MAX_SIGNALS_*)
        max_category_limit = getattr(config, f"MAX_SIGNALS_{category}", 3)
        sql_cat_count = "SELECT COUNT(*) as count FROM fo_trades WHERE strategy_category = %s AND exit_time IS NULL;"
        cat_rows = db_manager.execute_fo_query(sql_cat_count, [category])
        current_cat_open = int(cat_rows[0]["count"]) if cat_rows else 0
        if current_cat_open >= max_category_limit:
            cls.log_risk_event(
                event_type="POSITION_LIMIT_REACHED",
                symbol=symbol,
                signal_id=sig_id,
                details={"reason": f"Category {category} capacity reached ({current_cat_open}/{max_category_limit})"}
            )
            return False, f"Category allocation limit reached for {category} ({max_category_limit} max)", None

        # ----------------------------------------------------------------------
        # 4. Pre-Execution Price Deviation / Slippage Safeguard
        # ----------------------------------------------------------------------
        dev_points = abs(current_ltp - ref_price)
        dev_percentage = round((dev_points / ref_price) * 100.0, 4) if ref_price > 0 else 0.0
        dev_ticks = round(dev_points / tick_size, 1)

        max_pct_allowed = config.MAX_PRICE_DEVIATION_PERCENT
        max_ticks_allowed = config.MAX_PRICE_DEVIATION_TICKS

        if dev_percentage > max_pct_allowed or dev_ticks > max_ticks_allowed:
            cls.log_risk_event(
                event_type="PRICE_DEVIATION_EXCEEDED",
                symbol=symbol,
                signal_id=sig_id,
                attempted_price=ref_price,
                market_quote_price=current_ltp,
                deviation_points=dev_points,
                deviation_percentage=dev_percentage,
                threshold_allowed=max_pct_allowed,
                details={"deviation_ticks": dev_ticks, "max_ticks_allowed": max_ticks_allowed}
            )
            return False, f"Price deviation exceeded: {dev_percentage:.2f}% ({dev_ticks} ticks > {max_ticks_allowed} ticks)", None

        # ----------------------------------------------------------------------
        # 5. Position Sizing: Configurable ?10,000 Capital Allocation
        # ----------------------------------------------------------------------
        capital_alloc = float(config.CAPITAL_PER_TRADE_INR)
        quantity = max(1, int(capital_alloc // current_ltp))
        actual_capital = round(quantity * current_ltp, 2)

        trade_plan = {
            "signal_id": sig_id,
            "symbol": symbol,
            "trading_symbol": signal.get("trading_symbol") or f"{symbol}-EQ",
            "strategy_category": category,
            "direction": direction,
            "is_live_trade": config.LIVE_TRADING_ENABLED,
            "quantity": quantity,
            "allocated_capital_inr": actual_capital,
            "entry_reference_price": ref_price,
            "actual_entry_fill_price": current_ltp,
            "stop_loss_price": float(signal.get("stop_loss_price") or signal.get("stop_loss") or 0.0),
            "target_price": float(signal.get("target_price") or 0.0),
            "entry_time": now
        }

        log.info(f"[RiskApproved] {symbol} {direction} @ {current_ltp} (Ref: {ref_price:.2f}, "
                 f"Qty: {quantity}, Capital: Rs {actual_capital:.2f})")
        return True, None, trade_plan

    @classmethod
    def open_trade(cls, trade_plan: Dict[str, Any]) -> str:
        """
        Persists a validated trade into fo_trades and activates its parent signal.
        Returns unique trade_id.
        """
        now_dt = trade_plan.get("entry_time") or datetime.now(IST)
        now_ts = int(now_dt.timestamp() * 1000)
        symbol = trade_plan["symbol"]
        category = trade_plan["strategy_category"]
        trade_id = f"TRD_FO_{category}_{symbol}_{now_ts}"

        query = """
            INSERT INTO fo_trades (
                trade_id, signal_id, symbol, trading_symbol, strategy_category,
                direction, is_live_trade, quantity, allocated_capital_inr,
                entry_reference_price, actual_entry_fill_price, entry_time,
                broker_order_id, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            )
            RETURNING trade_id;
        """

        params = (
            trade_id,
            trade_plan["signal_id"],
            symbol,
            trade_plan.get("trading_symbol") or f"{symbol}-EQ",
            category,
            trade_plan["direction"],
            bool(trade_plan.get("is_live_trade", False)),
            int(trade_plan.get("quantity", 1)),
            float(trade_plan.get("allocated_capital_inr", 10000.0)),
            float(trade_plan["entry_reference_price"]),
            float(trade_plan.get("actual_entry_fill_price", trade_plan["entry_reference_price"])),
            now_dt,
            trade_plan.get("broker_order_id"),
            now_dt
        )

        db_manager.execute_fo_write(query, params)

        # Transition signal to ACTIVE
        SignalRepository.update_signal_status(
            signal_id=trade_plan["signal_id"],
            new_status="ACTIVE",
            trigger_price=float(trade_plan.get("actual_entry_fill_price", trade_plan["entry_reference_price"])),
            event_type="ACTIVATED",
            event_payload={"trade_id": trade_id, "quantity": trade_plan.get("quantity", 1), "capital": trade_plan.get("allocated_capital_inr")},
            market_timestamp=now_dt
        )
        log.info(f"Opened trade {trade_id} for signal {trade_plan['signal_id']} ({symbol} {trade_plan['direction']})")
        return trade_id

    @classmethod
    def close_trade(
        cls,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[datetime] = None,
        broker_order_id: Optional[str] = None
    ) -> bool:
        """
        Closes an open trade in fo_trades, computes gross and net P&L with statutory charges.
        """
        now_dt = exit_time or datetime.now(IST)
        sql_fetch = "SELECT * FROM fo_trades WHERE trade_id = %s LIMIT 1;"
        rows = db_manager.execute_fo_query(sql_fetch, [trade_id])
        if not rows:
            log.warning(f"Trade {trade_id} not found for closing")
            return False

        trade = rows[0]
        entry_price = float(trade.get("actual_entry_fill_price") or trade.get("entry_reference_price"))
        quantity = int(trade.get("quantity") or 1)
        direction = trade.get("direction", "BUY").upper()
        entry_time = trade.get("entry_time")

        if direction == "BUY":
            gross_pnl = round((exit_price - entry_price) * quantity, 4)
        else:
            gross_pnl = round((entry_price - exit_price) * quantity, 4)

        capital_used = entry_price * quantity
        gross_pnl_pct = round((gross_pnl / capital_used) * 100.0, 4) if capital_used > 0 else 0.0

        turnover = (entry_price + exit_price) * quantity
        estimated_costs = round(max(10.0, turnover * 0.001), 2)
        net_pnl = round(gross_pnl - estimated_costs, 4)
        net_pnl_pct = round((net_pnl / capital_used) * 100.0, 4) if capital_used > 0 else 0.0

        duration_sec = int((now_dt - entry_time).total_seconds()) if entry_time else 0

        sql_update = """
            UPDATE fo_trades SET
                actual_exit_fill_price = %s,
                exit_reference_price = %s,
                exit_time = %s,
                holding_duration_seconds = %s,
                exit_reason = %s,
                gross_pnl = %s,
                gross_pnl_percentage = %s,
                estimated_transaction_costs = %s,
                net_pnl = %s,
                net_pnl_percentage = %s,
                broker_order_id = COALESCE(%s, broker_order_id)
            WHERE trade_id = %s;
        """
        params = (
            exit_price,
            exit_price,
            now_dt,
            duration_sec,
            exit_reason,
            gross_pnl,
            gross_pnl_pct,
            estimated_costs,
            net_pnl,
            net_pnl_pct,
            broker_order_id,
            trade_id
        )
        affected = db_manager.execute_fo_write(sql_update, params)
        log.info(f"Closed trade {trade_id} ({exit_reason}) @ {exit_price:.2f} | Net PnL: Rs {net_pnl:.2f} ({net_pnl_pct:.2f}%)")
        return affected > 0

    @classmethod
    def get_trade_by_id(cls, trade_id: str) -> Optional[Dict[str, Any]]:
        """Fetches single trade by trade_id from fo_trades."""
        sql = "SELECT * FROM fo_trades WHERE trade_id = %s LIMIT 1;"
        rows = db_manager.execute_fo_query(sql, [trade_id])
        return rows[0] if rows else None

    @classmethod
    def get_open_trades(cls, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns all active trades where exit_time IS NULL."""
        if symbol:
            query = """
                SELECT * FROM fo_trades
                WHERE exit_time IS NULL AND symbol = %s
                ORDER BY entry_time DESC;
            """
            return db_manager.execute_fo_query(query, [symbol])
        else:
            query = """
                SELECT * FROM fo_trades
                WHERE exit_time IS NULL
                ORDER BY entry_time DESC;
            """
            return db_manager.execute_fo_query(query)

    @classmethod
    def log_risk_event(
        cls,
        event_type: str,
        symbol: str,
        signal_id: Optional[str] = None,
        attempted_price: Optional[float] = None,
        market_quote_price: Optional[float] = None,
        deviation_points: Optional[float] = None,
        deviation_percentage: Optional[float] = None,
        threshold_allowed: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """Logs risk safeguards and pre-execution rejection events in fo_risk_events."""
        query = """
            INSERT INTO fo_risk_events (
                event_type, symbol, signal_id, attempted_price, market_quote_price,
                deviation_points, deviation_percentage, threshold_allowed, details
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s::jsonb
            );
        """
        details_json = json.dumps(details or {})
        params = (
            event_type,
            symbol,
            signal_id,
            attempted_price,
            market_quote_price,
            deviation_points,
            deviation_percentage,
            threshold_allowed,
            details_json
        )
        try:
            db_manager.execute_fo_write(query, params)
        except Exception as e:
            log.error(f"Failed to log risk event for {symbol} ({event_type}): {e}")
