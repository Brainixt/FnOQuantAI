"""
================================================================================
FnOQuantAI: Quantitative Signal Generation Worker
================================================================================
Periodically scans incoming NSE F&O-eligible equity snapshots from CommonModule:
1. Detects new market cycles via WatermarkManager.
2. Extracts multi-bar validated history for each equity candidate.
3. Concurrently evaluates the 3 quantitative strategy engines:
   - IntradayBreakoutMomentumStrategy (Same session exit, cutoff 15:00 IST)
   - ShortTermSwingTrendStrategy (1-5 trading days delivery)
   - MidTermMacroTrendStrategy (5-30 trading days delivery)
4. Enforces portfolio allocation limits and ranks candidates.
5. Idempotently stores winning signals into fo_signals and audits in fo_signal_events.
6. Triggers execution pipeline (paper simulation or live Kotak Neo).
7. Advances persistent watermarks and logs execution in fo_worker_executions.
================================================================================
"""

import time
import logging
import threading
from datetime import datetime
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config

from data.market_repository import MarketRepository, WatermarkManager
from signals.strategy_intraday import IntradayStrategy
from signals.strategy_short_term import ShortTermStrategy
from signals.strategy_mid_term import MidTermStrategy
from signals.quant_engine import QuantEngine
from signals.signal_repository import SignalRepository
from database import db_manager

log = logging.getLogger("FnOQuantAI.Workers.SignalWorker")
IST = ZoneInfo("Asia/Kolkata")


class SignalWorker:
    """Recurring quantitative signal engine and watermark orchestrator for NSE equities."""

    WORKER_NAME = "signal_worker"

    @classmethod
    def run_cycle(cls) -> Dict[str, Any]:
        """
        Executes a single processing cycle:
        Pulls new data, evaluates candidates across 3 strategies,
        ranks and stores qualified signals, and advances watermarks.
        Returns execution summary dictionary.
        """
        started_at = datetime.now(IST)
        target_cycle = MarketRepository.get_latest_unprocessed_cycle()

        if not target_cycle:
            completed_at = datetime.now(IST)
            duration = (completed_at - started_at).total_seconds()
            db_manager.record_worker_execution(
                worker_name=cls.WORKER_NAME,
                cycle_id=None,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                records_examined=0,
                new_data_accepted=0,
                signals_generated=0,
                status="SUCCESS"
            )
            return {
                "status": "SUCCESS",
                "cycle_id": None,
                "records_examined": 0,
                "signals_generated": 0,
                "message": "No new unprocessed cycles available"
            }

        log.info(f"[{cls.WORKER_NAME}] Commencing quantitative scan for cycle: {target_cycle}")

        try:
            fresh_observations = MarketRepository.get_fresh_observations(target_cycle)
            records_examined = len(fresh_observations)

            if not fresh_observations:
                completed_at = datetime.now(IST)
                duration = (completed_at - started_at).total_seconds()
                db_manager.record_worker_execution(
                    worker_name=cls.WORKER_NAME,
                    cycle_id=target_cycle,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_seconds=duration,
                    records_examined=0,
                    new_data_accepted=0,
                    signals_generated=0,
                    status="SUCCESS"
                )
                return {
                    "status": "SUCCESS",
                    "cycle_id": target_cycle,
                    "records_examined": 0,
                    "signals_generated": 0,
                    "message": "No observations passed freshness & watermark checks"
                }

            candidates_by_cat: Dict[str, List[Dict[str, Any]]] = {
                "INTRADAY": [],
                "SHORT_TERM": [],
                "MID_TERM": []
            }

            # 1. Feature Extraction & Strategy Evaluation for each observation
            for obs in fresh_observations:
                symbol = obs["symbol"]
                trading_symbol = obs.get("trading_symbol") or f"{symbol}-EQ"
                exchange = obs.get("exchange", "NSE")
                snapshot_id = obs.get("id")

                history = MarketRepository.get_validated_history(symbol, max_bars=30)
                eval_kwargs = {
                    "symbol": symbol,
                    "trading_symbol": trading_symbol,
                    "exchange": exchange,
                    "source_cycle_id": target_cycle,
                    "source_snapshot_id": snapshot_id,
                    "source_market_timestamp": obs.get("source_timestamp") or obs.get("created_at") or started_at
                }

                # Evaluate Intraday Strategy
                sig_intra = IntradayStrategy.evaluate(history, obs, current_dt=started_at)
                if sig_intra:
                    sig_intra.update(eval_kwargs)
                    candidates_by_cat["INTRADAY"].append(sig_intra)

                # Evaluate Short-Term Strategy
                sig_short = ShortTermStrategy.evaluate(history, obs, current_dt=started_at)
                if sig_short:
                    sig_short.update(eval_kwargs)
                    candidates_by_cat["SHORT_TERM"].append(sig_short)

                # Evaluate Mid-Term Strategy
                sig_mid = MidTermStrategy.evaluate(history, obs, current_dt=started_at)
                if sig_mid:
                    sig_mid.update(eval_kwargs)
                    candidates_by_cat["MID_TERM"].append(sig_mid)

            # 2. Portfolio Allocation, Ranking, and Persistence
            generated_signal_ids: List[str] = []

            for category, candidates in candidates_by_cat.items():
                if not candidates:
                    continue

                max_allowed = getattr(config, f"MAX_SIGNALS_{category}", 3)
                active_count = SignalRepository.count_active_signals(strategy_category=category)
                available_capacity = max(0, max_allowed - active_count)

                if available_capacity <= 0:
                    log.info(f"[{cls.WORKER_NAME}] Max allocation reached for {category} ({active_count}/{max_allowed} active). Skipping {len(candidates)} candidates.")
                    continue

                top_candidates = QuantEngine.rank_candidates(candidates, max_signals=available_capacity)
                obs_by_symbol = {obs['symbol']: obs for obs in fresh_observations}
                for cand in top_candidates:
                    saved_id = SignalRepository.save_signal(cand)
                    if saved_id:
                        generated_signal_ids.append(saved_id)
                        cand['signal_id'] = saved_id
                        cand['strategy_category'] = category
                        matching_obs = obs_by_symbol.get(cand['symbol']) or cand
                        try:
                            from execution.execution_engine import ExecutionEngine
                            exec_ok, trade_or_reason, _ = ExecutionEngine.execute_trade_entry(cand, matching_obs)
                            if exec_ok:
                                log.info(f"[{cls.WORKER_NAME}] Trade executed for signal {saved_id}: {trade_or_reason}")
                            else:
                                log.info(f"[{cls.WORKER_NAME}] Trade execution bypassed for {saved_id}: {trade_or_reason}")
                        except Exception as exec_err:
                            log.error(f"[{cls.WORKER_NAME}] Execution error for signal {saved_id}: {exec_err}", exc_info=True)

            # 3. Advance Watermarks for processed records
            accepted_count = WatermarkManager.advance_watermarks(fresh_observations)

            completed_at = datetime.now(IST)
            duration = (completed_at - started_at).total_seconds()

            db_manager.record_worker_execution(
                worker_name=cls.WORKER_NAME,
                cycle_id=target_cycle,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                records_examined=records_examined,
                new_data_accepted=accepted_count,
                signals_generated=len(generated_signal_ids),
                status="SUCCESS"
            )

            log.info(f"[{cls.WORKER_NAME}] Completed cycle {target_cycle}: {len(generated_signal_ids)} signals generated from {accepted_count} accepted bars.")

            return {
                "status": "SUCCESS",
                "cycle_id": target_cycle,
                "records_examined": records_examined,
                "new_data_accepted": accepted_count,
                "signals_generated": len(generated_signal_ids),
                "signal_ids": generated_signal_ids,
                "duration_seconds": duration
            }

        except Exception as e:
            completed_at = datetime.now(IST)
            duration = (completed_at - started_at).total_seconds()
            log.error(f"[{cls.WORKER_NAME}] Cycle failed for {target_cycle}: {e}", exc_info=True)
            db_manager.record_worker_execution(
                worker_name=cls.WORKER_NAME,
                cycle_id=target_cycle,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=duration,
                records_examined=0,
                new_data_accepted=0,
                signals_generated=0,
                status="FAILURE",
                error_message=str(e)
            )
            return {
                "status": "FAILURE",
                "cycle_id": target_cycle,
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

