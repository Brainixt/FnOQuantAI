"""
================================================================================
FnOQuantAI: Open Call Lifecycle & Exit Monitor Worker
================================================================================
Monitors active/open NSE equity signals against incoming live market snapshots:
1. Target Hit: Price touches or exceeds profit target.
2. Stop-Loss Hit: Price touches or breaches stop-loss level.
3. Intraday Square-Off: Mandatory same-session exit before 15:15 IST.
4. Holding Duration Expiry:
   - Short-Term: Exits if position held > 5 trading days.
   - Mid-Term: Exits if position held > 30 trading days.
5. Financial Resolution: Computes exact gross P&L, turnover, estimated statutory costs,
   and net P&L based on share quantities (?10,000 capital sizing).
6. Auditing & Execution: Emits lifecycle events in fo_signal_events, closes trade in
   fo_trades via ExecutionEngine, and records execution in fo_worker_executions.
================================================================================
"""

import time
import logging
import threading
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config

from data.common_db_reader import CommonDbReader
from signals.signal_repository import SignalRepository
from database import db_manager

log = logging.getLogger("FnOQuantAI.Workers.OpenCallWorker")
IST = ZoneInfo("Asia/Kolkata")



def resolve_lot_size(symbol: str, snapshot_lot_size: Optional[int] = None) -> int:
    """Resolves equity lot multiplier (defaults to 1 share for cash equity)."""
    if snapshot_lot_size and int(snapshot_lot_size) > 0:
        return int(snapshot_lot_size)
    return 1


class OpenCallWorker:
    """Recurring open call monitor and position lifecycle resolver for NSE equities."""

    WORKER_NAME = "open_call_worker"

    @classmethod
    def run_cycle(cls, current_dt: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Executes a single monitoring cycle across all active signals.
        Returns execution summary dictionary.
        """
        started_at = current_dt or datetime.now(IST)
        active_signals = SignalRepository.get_active_signals()
        active_count = len(active_signals)

        if not active_signals:
            completed_at = datetime.now(IST)
            duration = (completed_at - started_at).total_seconds()
            db_manager.record_worker_execution(
                worker_name=cls.WORKER_NAME,
                cycle_id=None,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                active_calls_monitored=0,
                exit_events_triggered=0,
                status="SUCCESS"
            )
            return {
                "status": "SUCCESS",
                "active_calls_monitored": 0,
                "exit_events_triggered": 0,
                "message": "No active signals to monitor"
            }

        exit_events_triggered = 0
        resolved_signal_ids: List[str] = []

        try:
            for sig in active_signals:
                sig_id = sig["signal_id"]
                symbol = sig["symbol"]
                direction = sig["direction"]
                entry_price = float(sig["entry_reference_price"])
                stop_loss = float(sig["stop_loss_price"])
                target_price = float(sig["target_price"])
                risk_per_unit = float(sig["risk_per_unit"])
                category = sig["strategy_category"]
                created_at = sig.get("created_at")

                # Fetch latest market quote from CommonModule
                sql_latest = """
                    SELECT * FROM market_equity_snapshots
                    WHERE symbol = %s
                    ORDER BY id DESC LIMIT 1;
                """
                latest_rows = CommonDbReader.execute_read(sql_latest, [symbol])
                if not latest_rows:
                    continue

                latest_snap = latest_rows[0]
                ltp = float(latest_snap.get("ltp") or latest_snap.get("last_price") or 0.0)
                if ltp <= 0:
                    continue

                exit_triggered = False
                new_status = ""
                event_type = ""
                exit_price = ltp

                # 1. Target Hit Evaluation
                if direction == "BUY" and ltp >= target_price:
                    exit_triggered = True
                    new_status = "TARGET_HIT"
                    event_type = "TARGET_HIT"
                    exit_price = target_price
                elif direction == "SELL" and ltp <= target_price:
                    exit_triggered = True
                    new_status = "TARGET_HIT"
                    event_type = "TARGET_HIT"
                    exit_price = target_price

                # 2. Stop-Loss Hit Evaluation
                elif direction == "BUY" and ltp <= stop_loss:
                    exit_triggered = True
                    new_status = "STOP_LOSS_HIT"
                    event_type = "STOP_LOSS_HIT"
                    exit_price = stop_loss
                elif direction == "SELL" and ltp >= stop_loss:
                    exit_triggered = True
                    new_status = "STOP_LOSS_HIT"
                    event_type = "STOP_LOSS_HIT"
                    exit_price = stop_loss

                # 3. Mandatory Square-off Evaluation (SELL / MIS Short positions only, at 15:00 IST)
                # BUY trades are held as Normal (Delivery) and continue across sessions until Stop-Loss or Target is hit.
                elif direction == "SELL" and started_at.time() >= getattr(config, "MIS_SELL_SQUARE_OFF_TIME", config.INTRADAY_SQUARE_OFF_TIME):
                    exit_triggered = True
                    new_status = "TIME_EXIT"
                    event_type = "INTRADAY_SQUARE_OFF"
                    exit_price = ltp

                # 4. Multi-day Duration Expiry Check
                elif category in ("SHORT_TERM", "MID_TERM") and created_at:
                    max_days = config.MAX_HOLDING_DAYS_SHORT_TERM if category == "SHORT_TERM" else config.MAX_HOLDING_DAYS_MID_TERM
                    days_held = (started_at.date() - created_at.date()).days
                    if days_held > max_days:
                        exit_triggered = True
                        new_status = "TIME_EXIT"
                        event_type = "DURATION_EXPIRED"
                        exit_price = ltp

                # Execute Resolution & Update Record
                if exit_triggered:
                    # Look up active trade to get quantity and allocated capital
                    sql_find_trade = "SELECT trade_id, quantity, allocated_capital_inr FROM fo_trades WHERE signal_id = %s AND exit_time IS NULL LIMIT 1;"
                    trade_rows = db_manager.execute_fo_query(sql_find_trade, [sig_id])

                    quantity = 1
                    if trade_rows:
                        quantity = int(trade_rows[0].get("quantity") or 1)

                    pnl_points = round((exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price), 4)
                    realized_r = round(pnl_points / risk_per_unit, 4) if risk_per_unit > 0 else 0.0
                    gross_pnl_inr = round(pnl_points * quantity, 2)

                    # NSE statutory costs & brokerage estimation: ~0.10% to 0.15% of turnover (min Rs 10)
                    turnover = (entry_price + exit_price) * quantity
                    estimated_costs = round(max(10.0, turnover * 0.001), 2)
                    net_pnl_inr = round(gross_pnl_inr - estimated_costs, 2)

                    payload = {
                        "action": event_type,
                        "exit_price": exit_price,
                        "market_ltp": ltp,
                        "pnl_points": pnl_points,
                        "realized_r_multiple": realized_r,
                        "quantity": quantity,
                        "gross_pnl_inr": gross_pnl_inr,
                        "estimated_statutory_costs_inr": estimated_costs,
                        "net_pnl_inr": net_pnl_inr
                    }

                    updated = SignalRepository.update_signal_status(
                        signal_id=sig_id,
                        new_status=new_status,
                        event_type=event_type,
                        event_payload=payload,
                        trigger_price=exit_price
                    )

                    if updated:
                        exit_events_triggered += 1
                        resolved_signal_ids.append(sig_id)
                        log.info(f"[{cls.WORKER_NAME}] Resolved {sig_id} ({symbol}) -> {new_status} "
                                 f"@ {exit_price} | PnL: Rs {gross_pnl_inr:.2f} (Net: Rs {net_pnl_inr:.2f})")
                        if trade_rows:
                            active_trade_id = trade_rows[0]["trade_id"]
                            try:
                                from execution.execution_engine import ExecutionEngine
                                ExecutionEngine.execute_trade_exit(
                                    trade_id=active_trade_id,
                                    exit_reason=new_status,
                                    exit_price=exit_price,
                                    current_quote=latest_snap
                                )
                            except Exception as exit_err:
                                log.error(f"[{cls.WORKER_NAME}] Error executing trade exit for {sig_id}: {exit_err}", exc_info=True)

            completed_at = datetime.now(IST)
            duration = (completed_at - started_at).total_seconds()

            db_manager.record_worker_execution(
                worker_name=cls.WORKER_NAME,
                cycle_id=None,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                active_calls_monitored=active_count,
                exit_events_triggered=exit_events_triggered,
                status="SUCCESS"
            )

            return {
                "status": "SUCCESS",
                "active_calls_monitored": active_count,
                "exit_events_triggered": exit_events_triggered,
                "resolved_signal_ids": resolved_signal_ids,
                "duration_seconds": duration
            }

        except Exception as e:
            completed_at = datetime.now(IST)
            duration = (completed_at - started_at).total_seconds()
            log.error(f"[{cls.WORKER_NAME}] Open call monitoring cycle failed: {e}", exc_info=True)
            db_manager.record_worker_execution(
                worker_name=cls.WORKER_NAME,
                cycle_id=None,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                active_calls_monitored=active_count,
                exit_events_triggered=0,
                status="FAILURE",
                error_message=str(e)
            )
            return {
                "status": "FAILURE",
                "error": str(e)
            }

    @classmethod
    def start_loop(
        cls,
        stop_event: Optional[threading.Event] = None,
        interval: Optional[int] = None
    ) -> None:
        """Runs the signal worker continuously at configured intervals."""
        poll_interval = interval or config.SIGNAL_WORKER_INTERVAL_SECONDS
        log.info(f"[{cls.WORKER_NAME}] Background loop started (Interval: {poll_interval}s)")

        while not (stop_event and stop_event.is_set()):
            try:
                cls.run_cycle()
            except Exception as e:
                log.error(f"[{cls.WORKER_NAME}] Unhandled loop exception: {e}")

            if stop_event:
                if stop_event.wait(poll_interval):
                    break
            else:
                time.sleep(poll_interval)

        log.info(f"[{cls.WORKER_NAME}] Loop stopped.")

