"""
================================================================================
FnOQuantAI: Execution Engine
================================================================================
Coordinates end-to-end trade execution lifecycle for NSE cash equities:
- Connects RiskManager-evaluated trade plans to OrderDispatcher.
- Dispatches Entry orders (paper simulation or live Kotak Neo broker).
- On fill, allocates trade in fo_trades and transitions signal to ACTIVE.
- Dispatches Exit orders on target/stop-loss/intraday square-off/multi-day duration.
- On exit fill, closes trade in fo_trades, computes net P&L, and updates audit events.
================================================================================
"""

import os
import sys
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

try:
    import fo_config as config
except ImportError:
    import config

from database import db_manager
from risk.risk_manager import RiskManager
from signals.signal_repository import SignalRepository
from .order_dispatcher import OrderDispatcher

log = logging.getLogger("FnOQuantAI.Execution.Engine")


class ExecutionEngine:
    """Master coordinator for algorithmic trade entry and exit execution."""

    @classmethod
    def execute_trade_entry(
        cls,
        signal: Dict[str, Any],
        current_quote: Dict[str, Any],
        current_dt: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Executes trade entry for an eligible quantitative signal:
        1. Evaluates through RiskManager safety gates.
        2. Dispatches order via OrderDispatcher (Paper or Live Kotak Neo).
        3. On success, persists trade in fo_trades and activates signal in fo_signals.
        """
        # Step 1: Risk Assessment & Sizing
        approved, rejection_reason, trade_plan = RiskManager.validate_trade_eligibility(signal, current_quote, current_dt=current_dt)
        if not approved or not trade_plan:
            log.info(f"[ExecutionEngine] Signal {signal.get('signal_id')} entry rejected by RiskManager: {rejection_reason}")
            return False, rejection_reason, None

        symbol = trade_plan["symbol"]
        category = trade_plan["strategy_category"]
        direction = trade_plan["direction"]
        signal_id = trade_plan["signal_id"]

        # Step 2: Prepare Order Plan
        ref_price = float(trade_plan["entry_reference_price"])
        current_ltp = float(current_quote.get("ltp") or ref_price)

        product_type = getattr(config, "SHARE_TYPE_BUY", "Normal") if direction.upper() in ("BUY", "B") else getattr(config, "SHARE_TYPE_SELL", "MIS")

        order_plan = {
            "signal_id": signal_id,
            "symbol": symbol,
            "trading_symbol": trade_plan.get("trading_symbol") or f"{symbol}-EQ",
            "category": category,
            "direction": direction,
            "product": product_type,
            "action": "ENTRY",
            "order_type": "MKT",
            "reference_price": ref_price,
            "current_price": current_ltp,
            "quantity": int(trade_plan["quantity"]),
            "is_live": bool(trade_plan.get("is_live_trade", False))
        }

        # Step 3: Dispatch Order
        order_res = OrderDispatcher.dispatch_order(order_plan)
        if not order_res.get("success"):
            err = order_res.get("error", "Order dispatch failed")
            log.warning(f"[ExecutionEngine] Order failed for {symbol} ({signal_id}): {err}")
            return False, err, order_res

        # Step 4: Persist Filled Trade & Activate Signal
        fill_price = float(order_res.get("fill_price") or current_ltp)
        broker_order_id = order_res.get("broker_order_id")

        trade_plan["actual_entry_fill_price"] = fill_price
        trade_plan["broker_order_id"] = broker_order_id

        trade_id = RiskManager.open_trade(trade_plan)
        log.info(f"[ExecutionEngine] Trade entry executed successfully! Trade ID: {trade_id} "
                 f"({symbol} @ {fill_price:.2f}, Live: {order_plan['is_live']}, BrokerID: {broker_order_id})")

        return True, trade_id, order_res

    @classmethod
    def execute_trade_exit(
        cls,
        trade_id: str,
        exit_reason: str,
        exit_price: float,
        current_quote: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Executes trade exit (square-off):
        1. Dispatches exit order via OrderDispatcher.
        2. Closes trade record in fo_trades via RiskManager.
        3. Updates signal status to target/stop/time exit in fo_signals.
        """
        # Fetch trade record
        sql = "SELECT * FROM fo_trades WHERE trade_id = %s LIMIT 1;"
        rows = db_manager.execute_fo_query(sql, [trade_id])
        if not rows:
            return False, f"Trade {trade_id} not found in fo_trades", None

        trade = rows[0]
        symbol = trade["symbol"]
        entry_direction = trade["direction"].upper()
        # Exit order direction is inverted (BUY -> SELL, SELL -> BUY)
        exit_direction = "SELL" if entry_direction in ("BUY", "B") else "BUY"
        is_live = bool(trade.get("is_live_trade", False))
        quantity = int(trade.get("quantity", 1))
        category = trade.get("strategy_category", "INTRADAY")
        signal_id = trade.get("signal_id")

        current_ltp = exit_price
        if current_quote and current_quote.get("ltp"):
            current_ltp = float(current_quote["ltp"])

        # Square-off order must use the same product type as entry position
        entry_product = getattr(config, "SHARE_TYPE_BUY", "Normal") if entry_direction in ("BUY", "B") else getattr(config, "SHARE_TYPE_SELL", "MIS")

        # Prepare Exit Order Plan
        order_plan = {
            "signal_id": signal_id,
            "trade_id": trade_id,
            "symbol": symbol,
            "trading_symbol": trade.get("trading_symbol") or f"{symbol}-EQ",
            "category": category,
            "direction": exit_direction,
            "product": entry_product,
            "original_trade_direction": entry_direction,
            "action": "EXIT",
            "order_type": "MKT",
            "reference_price": exit_price,
            "current_price": current_ltp,
            "quantity": quantity,
            "is_live": is_live
        }

        # Dispatch Exit Order
        order_res = OrderDispatcher.dispatch_order(order_plan)
        actual_fill_price = float(order_res.get("fill_price") or exit_price)
        broker_order_id = order_res.get("broker_order_id")

        # Close trade in database
        closed = RiskManager.close_trade(
            trade_id=trade_id,
            exit_price=actual_fill_price,
            exit_reason=exit_reason,
            broker_order_id=broker_order_id
        )

        log.info(f"[ExecutionEngine] Trade {trade_id} exit completed ({exit_reason}) @ {actual_fill_price:.2f} | Live: {is_live}")
        return closed, None, order_res
