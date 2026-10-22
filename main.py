"""
================================================================================
FnOQuantAI: Main Command Line Interface & Daemon Orchestrator
================================================================================
Entrypoint for the FnOQuantAI quantitative trading & risk management system
tailored for NSE F&O-eligible underlying cash equities:
Subcommands:
- status:    Displays real-time terminal monitor dashboard, active calls, and allocations.
- scan:      Executes a single quantitative evaluation cycle (SignalWorker).
- monitor:   Executes a single open position and exit monitoring cycle (OpenCallWorker).
- daemon:    Runs concurrent background worker threads with graceful signal handling.
- report:    Generates formatted daily activity summaries and JSON exports.
- analytics: Computes lifetime or date-filtered quantitative performance metrics.
================================================================================
"""

import os
import sys
import time
import json
import signal
import logging
import argparse
import threading
from datetime import datetime, date
from typing import Optional
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

try:
    import fo_config as config
except ImportError:
    import config

from workers.signal_worker import SignalWorker
from workers.open_call_worker import OpenCallWorker
from reporting.dashboard import render_cli_dashboard
from reporting.daily_summary import DailySummaryReporter
from reporting.performance_analytics import PerformanceAnalytics

IST = ZoneInfo("Asia/Kolkata")
LOCK_FILE = os.path.join(BASE_DIR, "fno_quant_ai.lock")


def setup_cli_logging(log_level: str = "INFO", log_to_file: bool = False) -> None:
    """Configures logging handlers for console and optional file logging."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]

    if log_to_file:
        logs_dir = os.path.join(BASE_DIR, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        log_file = os.path.join(logs_dir, "fno_quant_ai.log")
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True
    )


class SingleInstanceLock:
    """Guarantees only one daemon instance runs concurrently on the system."""

    def __init__(self, lock_path: str = LOCK_FILE):
        self.lock_path = lock_path
        self.acquired = False

    def acquire(self) -> bool:
        if os.path.exists(self.lock_path):
            try:
                with open(self.lock_path, "r", encoding="utf-8") as f:
                    old_pid = int(f.read().strip())
                if self._is_pid_running(old_pid):
                    print(f"ERROR: Another FnOQuantAI daemon is already running (PID: {old_pid}).")
                    return False
            except (ValueError, OSError):
                pass

        try:
            with open(self.lock_path, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
            self.acquired = True
            return True
        except Exception as e:
            print(f"ERROR: Failed to acquire lock file: {e}")
            return False

    def release(self) -> None:
        if self.acquired and os.path.exists(self.lock_path):
            try:
                os.remove(self.lock_path)
                self.acquired = False
            except Exception:
                pass

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        """Checks if process ID exists on Windows."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            process = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if process != 0:
                kernel32.CloseHandle(process)
                return True
            return False
        except Exception:
            return False


def run_daemon_command() -> int:
    """Orchestrates multi-threaded daemon execution for SignalWorker and OpenCallWorker."""
    setup_cli_logging("INFO", log_to_file=True)
    log = logging.getLogger("FnOQuantAI.Daemon")

    lock = SingleInstanceLock()
    if not lock.acquire():
        return 1

    stop_event = threading.Event()

    def signal_handler(sig, frame):
        log.info("Shutdown signal received. Gracefully stopping daemon threads...")
        stop_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log.info("=" * 70)
    log.info(f"Starting FnOQuantAI Daemon (PID: {os.getpid()})")
    log.info(f"Target Universe:         NSE F&O-Eligible Equities (~213 Underlyings)")
    log.info(f"Execution Instrument:    NSE Cash Equities (nse_cm, {config.DEFAULT_TICK_SIZE} tick)")
    log.info(f"Capital Allocation:      ?{config.CAPITAL_PER_TRADE_INR:,.2f} per trade")
    log.info(f"Execution Mode:          {'LIVE TRADING' if config.LIVE_TRADING_ENABLED else 'SIMULATED / PAPER TRADING'}")
    log.info(f"Signal Scan Interval:    {config.SIGNAL_WORKER_INTERVAL_SECONDS}s")
    log.info(f"Open Call Poll Interval: {config.OPEN_CALL_WORKER_INTERVAL_SECONDS}s")
    log.info("=" * 70)

    # Thread 1: Signal Generation Worker
    signal_thread = threading.Thread(
        target=SignalWorker.start_loop,
        args=(stop_event, config.SIGNAL_WORKER_INTERVAL_SECONDS),
        name="SignalWorkerThread",
        daemon=True
    )

    # Thread 2: Open Position Monitor Worker
    monitor_thread = threading.Thread(
        target=OpenCallWorker.start_loop,
        args=(stop_event, config.OPEN_CALL_WORKER_INTERVAL_SECONDS),
        name="OpenCallWorkerThread",
        daemon=True
    )

    signal_thread.start()
    monitor_thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Keyboard interrupt received.")
        stop_event.set()
    finally:
        signal_thread.join(timeout=5)
        monitor_thread.join(timeout=5)
        lock.release()
        log.info("FnOQuantAI daemon successfully stopped.")

    return 0


def main() -> int:
    """CLI Argument Parser & Dispatcher for FnOQuantAI."""
    parser = argparse.ArgumentParser(
        prog="FnOQuantAI",
        description="NSE Equity Quantitative Analysis & Algorithmic Trading System"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Subcommand: status
    subparsers.add_parser("status", help="Displays real-time terminal dashboard")

    # Subcommand: scan
    subparsers.add_parser("scan", help="Executes a single quantitative signal scan cycle")

    # Subcommand: monitor
    subparsers.add_parser("monitor", help="Executes a single open-call monitor cycle")

    # Subcommand: daemon
    subparsers.add_parser("daemon", help="Launches background worker daemon threads")

    # Subcommand: report
    parser_report = subparsers.add_parser("report", help="Generates daily activity and performance report")
    parser_report.add_argument("--date", type=str, default=None, help="Target date in YYYY-MM-DD format (default: today)")
    parser_report.add_argument("--export", choices=["text", "json"], default="text", help="Output format (default: text)")
    parser_report.add_argument("--output", type=str, default=None, help="Filepath to write report to")
    parser_report.add_argument("--json", action="store_true", help="Alias for --export json")

    # Subcommand: analytics
    parser_analytics = subparsers.add_parser("analytics", help="Calculates lifetime or filtered performance analytics")
    parser_analytics.add_argument("--start", type=str, default=None, help="Start date (YYYY-MM-DD)")
    parser_analytics.add_argument("--end", type=str, default=None, help="End date (YYYY-MM-DD)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "status":
        print(render_cli_dashboard())
        return 0

    elif args.command == "scan":
        setup_cli_logging("INFO", log_to_file=False)
        print("Executing single quantitative signal scan cycle...")
        result = SignalWorker.run_cycle()
        print("\nScan Result Summary:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        return 0 if result.get("status") == "SUCCESS" else 1

    elif args.command == "monitor":
        setup_cli_logging("INFO", log_to_file=False)
        print("Executing single open call monitor cycle...")
        result = OpenCallWorker.run_cycle()
        print("\nMonitor Result Summary:")
        for k, v in result.items():
            print(f"  {k}: {v}")
        return 0 if result.get("status") == "SUCCESS" else 1

    elif args.command == "daemon":
        return run_daemon_command()

    elif args.command == "report":
        target_dt = None
        if args.date:
            try:
                target_dt = datetime.strptime(args.date, "%Y-%m-%d").date()
            except ValueError:
                print(f"ERROR: Invalid date format '{args.date}'. Expected YYYY-MM-DD.")
                return 1

        summary = DailySummaryReporter.generate_summary(target_date=target_dt)
        is_json = (getattr(args, "json", False) or args.export == "json")

        if is_json:
            json_text = DailySummaryReporter.export_json(summary, filepath=args.output)
            if not args.output:
                print(json_text)
            else:
                print(f"Report exported to JSON: {args.output}")
        else:
            text_rep = DailySummaryReporter.format_text_report(summary)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(text_rep)
                print(f"Report written to: {args.output}")
            else:
                print(text_rep)
        return 0

    elif args.command == "analytics":
        start_d = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
        end_d = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None
        metrics = PerformanceAnalytics.compute_metrics(start_dt=start_d, end_dt=end_d)

        print("=" * 60)
        print(" FNOQUANTAI PERFORMANCE ANALYTICS")
        print("=" * 60)
        for k, v in metrics.items():
            if isinstance(v, dict):
                print(f"\n{k.upper()}:")
                for sub_k, sub_v in v.items():
                    print(f"  {sub_k}: {sub_v}")
            else:
                print(f"{k:<35}: {v}")
        print("=" * 60)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
