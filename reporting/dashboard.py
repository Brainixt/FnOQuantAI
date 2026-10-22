"""
================================================================================
FnOQuantAI: Real-Time Terminal Status Dashboard
================================================================================
Renders a live command-line dashboard displaying:
- Current NSE market operational status and local IST clock.
- Portfolio category allocation utilization (INTRADAY, SHORT_TERM, MID_TERM).
- Active open calls with entry fill, SL, target, and holding time.
- Most recent signal generations and worker health.
================================================================================
"""

import sys
from datetime import datetime, date
from typing import Dict, Any, List
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config

from database import db_manager
from risk.risk_manager import RiskManager

IST = ZoneInfo("Asia/Kolkata")


def render_cli_dashboard() -> str:
    """Generates ASCII dashboard string for terminal monitor."""
    now_ist = datetime.now(IST)
    is_live = getattr(config, "LIVE_TRADING_ENABLED", False)

    # 1. Fetch Open Trades
    open_trades = RiskManager.get_open_trades()

    # 2. Count Active Signals by Category
    sql_sig_counts = """
        SELECT strategy_category, COUNT(*) as count
        FROM fo_signals
        WHERE status IN ('NEW', 'ACTIVE')
        GROUP BY strategy_category;
    """
    cat_counts = {r["strategy_category"]: int(r["count"]) for r in db_manager.execute_fo_query(sql_sig_counts, use_primary=True)}

    # 3. Fetch Last 5 Signals
    sql_recent_signals = """
        SELECT signal_id, symbol, strategy_category, direction,
               entry_reference_price, total_score, status, created_at
        FROM fo_signals
        ORDER BY created_at DESC LIMIT 5;
    """
    recent_signals = db_manager.execute_fo_query(sql_recent_signals, use_primary=True)

    # 4. Fetch Worker Status
    sql_workers = """
        SELECT worker_name, status, started_at, duration_seconds, signals_generated, exit_events_triggered
        FROM fo_worker_executions
        ORDER BY id DESC LIMIT 2;
    """
    recent_workers = db_manager.execute_fo_query(sql_workers, use_primary=True)

    lines = [
        "=" * 80,
        f" FNOQUANTAI TERMINAL MONITOR | {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST",
        "=" * 80,
        f" Universe:              NSE F&O-Eligible Equities (Underlying Cash Shares)",
        f" Execution Mode:        [{'LIVE BROKER TRADING' if is_live else 'SIMULATED / PAPER TRADING'}]",
        f" Capital Allocation:    ?{getattr(config, 'CAPITAL_PER_TRADE_INR', 10000):,.2f} per trade",
        f" Price Deviation Gate:  {getattr(config, 'MAX_PRICE_DEVIATION_PERCENT', 0.25)}% / {getattr(config, 'MAX_PRICE_DEVIATION_TICKS', 5)} ticks",
        "",
        "PORTFOLIO ALLOCATION USAGE",
        "-" * 80,
        f"  INTRADAY:   {cat_counts.get('INTRADAY', 0):>2} / {getattr(config, 'MAX_SIGNALS_INTRADAY', 3)} open signals",
        f"  SHORT_TERM: {cat_counts.get('SHORT_TERM', 0):>2} / {getattr(config, 'MAX_SIGNALS_SHORT_TERM', 3)} open signals",
        f"  MID_TERM:   {cat_counts.get('MID_TERM', 0):>2} / {getattr(config, 'MAX_SIGNALS_MID_TERM', 3)} open signals",
        "",
        f"ACTIVE OPEN TRADES ({len(open_trades)})",
        "-" * 80
    ]

    if open_trades:
        lines.append(f"  {'Trade ID':<25} | {'Symbol':<12} | {'Cat':<10} | {'Dir':<5} | {'Fill Price':<10} | {'Qty':<5} | {'Capital'}")
        lines.append("  " + "-" * 76)
        for t in open_trades:
            cap = float(t.get('allocated_capital_inr') or 10000.0)
            lines.append(f"  {t['trade_id']:<25} | {t['symbol']:<12} | {t['strategy_category']:<10} | {t['direction']:<5} | {float(t['actual_entry_fill_price']):>10.2f} | {t['quantity']:<5} | ?{cap:,.2f}")
    else:
        lines.append("  (No active open positions)")

    lines.extend([
        "",
        f"RECENT QUANTITATIVE SIGNALS ({len(recent_signals)})",
        "-" * 80
    ])

    if recent_signals:
        lines.append(f"  {'Signal ID':<25} | {'Symbol':<12} | {'Cat':<10} | {'Dir':<5} | {'Ref Price':<10} | {'Score':<6} | {'Status'}")
        lines.append("  " + "-" * 76)
        for s in recent_signals:
            lines.append(f"  {s['signal_id']:<25} | {s['symbol']:<12} | {s['strategy_category']:<10} | {s['direction']:<5} | {float(s['entry_reference_price']):>10.2f} | {float(s['total_score']):>5.1f} | {s['status']}")
    else:
        lines.append("  (No recent signals generated)")

    lines.extend([
        "",
        "BACKGROUND WORKER STATUS",
        "-" * 80
    ])
    if recent_workers:
        for w in recent_workers:
            s_time = str(w['started_at'])[:19]
            dur = float(w['duration_seconds'] or 0.0)
            lines.append(f"  {w['worker_name']:<18} | Status: {w['status']:<7} | Last: {s_time} | Duration: {dur:.2f}s")
    else:
        lines.append("  (No worker execution records)")

    lines.extend(["=" * 80])
    output = chr(10).join(lines)
    return output


if __name__ == "__main__":
    print(render_cli_dashboard())
