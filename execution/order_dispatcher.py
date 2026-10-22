"""
================================================================================
FnOQuantAI: Order Dispatcher
================================================================================
Handles order dispatching for both paper trading simulation and live broker execution:
- Paper Mode: Generates simulated fills with deterministic realistic slippage (1 tick = 0.05).
- Live Mode: Connects to Kotak Neo API via CommonModule.script.neo_connection.
- Targets NSE Cash Equities:
  - exchange_segment = "nse_cm"
  - trading_symbol = "{symbol}-EQ"
  - product = "MIS" (Intraday) or "CNC" (Short-Term / Mid-Term Delivery)
  - quantity sized strictly to ?10,000 capital allocation
- Multi-tier Safety Gates:
  1. LIVE_TRADING_ENABLED guard (defaults to False).
  2. Capital allocation validation.
  3. Pre-dispatch price deviation and slippage threshold checks (0.25% / 5 ticks).
  4. Automatic session refresh and retry on expired broker tokens.
  5. Audit logging in fo_risk_events upon broker rejections.
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

COMMON_MODULE_DIR = r"D:\SM_Server\CommonModule"
if COMMON_MODULE_DIR not in sys.path:
    sys.path.insert(0, COMMON_MODULE_DIR)

log = logging.getLogger("FnOQuantAI.Execution.OrderDispatcher")

NSE_EQUITY_TICK_SIZE = 0.05


class OrderDispatcher:
    """Dispatches orders to either paper simulation or live Kotak Neo broker for NSE equities."""

    @classmethod
    def get_tick_size(cls, symbol: str) -> float:
        """Resolves standard NSE cash equity tick size (0.05)."""
        return NSE_EQUITY_TICK_SIZE

    @classmethod
    def calculate_simulated_fill(
        cls,
        symbol: str,
        direction: str,
        reference_price: float,
        slippage_ticks: float = 1.0
    ) -> Tuple[float, float]:
        """
        Calculates realistic simulated fill price with tick-based slippage.
        Returns: (fill_price, slippage_points)
        """
        tick_size = cls.get_tick_size(symbol)
        max_deviation_ticks = getattr(config, "MAX_PRICE_DEVIATION_TICKS", 5)
        effective_ticks = min(slippage_ticks, max_deviation_ticks)
        slippage_points = round(effective_ticks * tick_size, 4)

        if direction.upper() in ("BUY", "B"):
            fill_price = round(reference_price + slippage_points, 4)
        else:
            fill_price = round(reference_price - slippage_points, 4)

        return fill_price, slippage_points

    @classmethod
    def dispatch_order(cls, order_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes order according to environment configuration:
        - If config.LIVE_TRADING_ENABLED is False or order_plan['is_live'] is False:
          Routes to paper trading simulation.
        - If LIVE_TRADING_ENABLED is True and order_plan['is_live'] is True:
          Validates safety gates and routes to live broker.
        """
        is_live = bool(getattr(config, "LIVE_TRADING_ENABLED", False) and order_plan.get("is_live", True))

        if not is_live:
            return cls._execute_paper_order(order_plan)
        else:
            return cls._execute_live_order(order_plan)

    @classmethod
    def _execute_paper_order(cls, order_plan: Dict[str, Any]) -> Dict[str, Any]:
        """Simulates order fill with realistic slippage for cash equities."""
        symbol = order_plan["symbol"]
        direction = order_plan.get("direction", "BUY")
        ref_price = float(order_plan.get("reference_price", 0.0))
        current_price = float(order_plan.get("current_price", ref_price))
        quantity = int(order_plan.get("quantity", 1))

        # Slippage calculation (1 tick adverse drift = 0.05)
        fill_price, slippage_pts = cls.calculate_simulated_fill(
            symbol=symbol,
            direction=direction,
            reference_price=current_price or ref_price,
            slippage_ticks=1.0
        )

        now_ms = int(datetime.now().timestamp() * 1000)
        action = order_plan.get("action", "ENTRY")
        sim_order_id = f"SIM_FO_{action}_{symbol}_{now_ms}"

        log.info(f"[PaperExecution] {action} {symbol} {direction} filled @ {fill_price:.2f} "
                 f"(Ref: {ref_price:.2f}, Slippage: {slippage_pts:.2f} pts, Qty: {quantity}) | Order ID: {sim_order_id}")

        return {
            "success": True,
            "status": "FILLED",
            "is_live": False,
            "broker_order_id": sim_order_id,
            "fill_price": fill_price,
            "slippage": slippage_pts,
            "quantity": quantity,
            "message": "Simulated fill executed successfully"
        }

    @classmethod
    def _execute_live_order(cls, order_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Places order with Kotak Neo broker for NSE Cash Equity after executing safety gates.
        """
        symbol = order_plan["symbol"]
        trading_symbol = order_plan.get("trading_symbol") or f"{symbol}-EQ"
        if not trading_symbol.endswith("-EQ") and not trading_symbol.endswith("-BE"):
            trading_symbol = f"{symbol}-EQ"

        direction = order_plan.get("direction", "BUY").upper()
        ref_price = float(order_plan.get("reference_price", 0.0))
        current_price = float(order_plan.get("current_price", ref_price))
        quantity = int(order_plan.get("quantity", 1))
        category = order_plan.get("category", "INTRADAY").upper()
        signal_id = order_plan.get("signal_id")
        action = order_plan.get("action", "ENTRY")

        # ----------------------------------------------------------------------
        # Safety Gate 1: Capital Allocation Enforcement (Max ?15,000 threshold)
        # ----------------------------------------------------------------------
        trade_val = quantity * current_price
        max_capital = getattr(config, "CAPITAL_PER_TRADE_INR", 10000.0) * 1.5
        if trade_val > max_capital:
            msg = f"Live order rejected: trade value ?{trade_val:.2f} exceeds max allowed ?{max_capital:.2f}"
            log.error(f"[OrderDispatcher.SafetyGate] {msg}")
            RiskManager.log_risk_event(
                event_type="POSITION_LIMIT_REACHED",
                symbol=symbol,
                signal_id=signal_id,
                details={"reason": msg, "quantity": quantity, "current_price": current_price}
            )
            return {"success": False, "status": "REJECTED", "error": msg, "is_live": True}

        # ----------------------------------------------------------------------
        # Safety Gate 2: Pre-Dispatch Price Deviation & Slippage Check
        # ----------------------------------------------------------------------
        if ref_price > 0 and current_price > 0:
            dev_points = abs(current_price - ref_price)
            dev_pct = (dev_points / ref_price) * 100.0
            tick_size = cls.get_tick_size(symbol)
            dev_ticks = dev_points / tick_size

            max_ticks = getattr(config, "MAX_PRICE_DEVIATION_TICKS", 5)
            max_pct = getattr(config, "MAX_PRICE_DEVIATION_PERCENT", 0.25)

            if dev_ticks > max_ticks or dev_pct > max_pct:
                msg = f"Live order rejected: price deviation {dev_points:.2f} ({dev_pct:.2f}%, {dev_ticks:.1f} ticks) exceeds limits"
                log.warning(f"[OrderDispatcher.SafetyGate] {msg}")
                RiskManager.log_risk_event(
                    event_type="PRICE_DEVIATION_EXCEEDED",
                    symbol=symbol,
                    signal_id=signal_id,
                    attempted_price=ref_price,
                    market_quote_price=current_price,
                    deviation_points=dev_points,
                    deviation_percentage=dev_pct,
                    threshold_allowed=max_pct,
                    details={"deviation_ticks": dev_ticks, "max_ticks": max_ticks}
                )
                return {"success": False, "status": "REJECTED", "error": msg, "is_live": True}

        # ----------------------------------------------------------------------
        # Broker Order Dispatch via Kotak Neo API (NSE Cash Equity: nse_cm)
        # ----------------------------------------------------------------------
        try:
            from script import neo_connection
        except ImportError:
            log.error("CommonModule.script.neo_connection is not accessible.")
            return {"success": False, "status": "FAILED", "error": "neo_connection module not found", "is_live": True}

        # Product / Share Type selection: Normal if Buy, MIS if Sell
        product = order_plan.get("product")
        if not product:
            orig_dir = order_plan.get("original_trade_direction") or order_plan.get("trade_direction")
            ref_dir = orig_dir if (orig_dir and action == "EXIT") else direction
            if ref_dir in ("BUY", "B"):
                product = getattr(config, "SHARE_TYPE_BUY", "Normal")
            else:
                product = getattr(config, "SHARE_TYPE_SELL", "MIS")
        txn_type = "B" if direction in ("BUY", "B") else "S"
        order_type = order_plan.get("order_type", "MKT")
        limit_price = "0" if order_type == "MKT" else str(current_price)

        log.info(f"[LiveOrderDispatching] Submitting {direction} {trading_symbol} (Segment: nse_cm, {product}, Qty: {quantity}) to Kotak Neo...")

        def _place(client):
            return client.place_order(
                exchange_segment="nse_cm",
                product=product,
                price=limit_price,
                order_type=order_type,
                validity="DAY",
                trading_symbol=trading_symbol,
                transaction_type=txn_type,
                quantity=str(quantity)
            )

        client = neo_connection.NeoConnectionManager.get_write_connection()
        try:
            resp = _place(client)
        except Exception as exc:
            log.error(f"[LiveOrderDispatching] Initial broker API exception: {exc}")
            resp = {"stat": "Not_Ok", "errMsg": str(exc)}

        # Session Expiry Detection & Auto-Refresh
        is_session_expired = False
        if isinstance(resp, dict):
            err_msg = str(resp.get("errMsg", "")).lower()
            if "session" in err_msg or "token" in err_msg or "unauthorized" in err_msg or "expired" in err_msg:
                is_session_expired = True

        if is_session_expired:
            log.warning("[LiveOrderDispatching] Session token appears expired. Forcing token refresh...")
            try:
                neo_connection.NeoConnectionManager.recreate_connection(mode='write', force_login=True)
                client = neo_connection.NeoConnectionManager.get_write_connection()
                resp = _place(client)
            except Exception as refresh_exc:
                log.error(f"[LiveOrderDispatching] Session refresh failed: {refresh_exc}")
                return {"success": False, "status": "FAILED", "error": f"Session refresh failed: {refresh_exc}", "is_live": True}

        # Parse Response
        if isinstance(resp, dict) and resp.get("stat") == "Ok":
            order_id = resp.get("nOrdNo") or resp.get("orderId")
            log.info(f"[LiveOrderDispatching] Order placed successfully! Broker Order ID: {order_id}")
            return {
                "success": True,
                "status": "SUBMITTED",
                "is_live": True,
                "broker_order_id": order_id,
                "fill_price": current_price,
                "quantity": quantity,
                "raw_response": resp
            }
        else:
            err_msg = resp.get("errMsg", "Unknown broker error") if isinstance(resp, dict) else str(resp)
            log.error(f"[LiveOrderDispatching] Order submission failed by broker: {err_msg}")
            RiskManager.log_risk_event(
                event_type="BROKER_ORDER_REJECTED",
                symbol=symbol,
                signal_id=signal_id,
                details={"error": err_msg, "response": str(resp)}
            )
            return {
                "success": False,
                "status": "REJECTED",
                "error": err_msg,
                "is_live": True,
                "raw_response": resp
            }
