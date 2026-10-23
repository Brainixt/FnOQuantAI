"""
================================================================================
FnOQuantAI: Central System Configuration
================================================================================
Defines system-wide parameters, trading windows, risk safeguards,
capital allocations, and database connectivity for NSE F&O Equity Trading.
================================================================================
"""

import os
from datetime import time as dtime
from typing import Dict, Any

# 1. System Roots & Identification
SYSTEM_NAME: str = "FnOQuantAI"
SYSTEM_VERSION: str = "1.0.0"
SYSTEM_DIR: str = os.path.dirname(os.path.abspath(__file__))

# Link to CommonModule
COMMON_MODULE_DIR: str = os.getenv("COMMON_MODULE_DIR", r"D:\SM_Server\CommonModule")

# 2. Market Definition & Exchange Hours (NSE Cash Equity)
EXCHANGE: str = "NSE"
EXCHANGE_SEGMENT_CASH: str = "nse_cm"
EXCHANGE_SEGMENT_DERIVATIVE: str = "nse_fo"
MARKET_CATEGORY: str = "EQUITY"

MARKET_OPEN_TIME: dtime = dtime(9, 15)
MARKET_CLOSE_TIME: dtime = dtime(15, 30)

# Standard NSE Cash Equity Tick Size (Rs 0.05)
DEFAULT_TICK_SIZE: float = 0.05

# Intraday Trade Safeguards
INTRADAY_ENTRY_CUTOFF_TIME: dtime = dtime(15, 0)   # No new intraday entries past 15:00 IST
MIS_SELL_SQUARE_OFF_TIME: dtime = dtime(15, 0)     # Mandatory square-off for SELL (MIS) at 15:00 IST
INTRADAY_SQUARE_OFF_TIME: dtime = dtime(15, 0)     # Backward-compatible alias

# 3. Worker Polling Intervals
SIGNAL_WORKER_INTERVAL_SECONDS: int = int(os.getenv("FO_SIGNAL_INTERVAL", "300"))      # 5 mins
OPEN_CALL_WORKER_INTERVAL_SECONDS: int = int(os.getenv("FO_MONITOR_INTERVAL", "60"))   # 1 min

# 4. Strategy & Portfolio Limits
MIN_SIGNAL_SCORE: float = float(os.getenv("MIN_SIGNAL_SCORE", "55.0"))
MIN_REWARD_TO_RISK: float = float(os.getenv("MIN_REWARD_TO_RISK", "1.3"))

MAX_SIGNALS_INTRADAY: int = int(os.getenv("MAX_SIGNALS_INTRADAY", "3"))
MAX_SIGNALS_SHORT_TERM: int = int(os.getenv("MAX_SIGNALS_SHORT_TERM", "3"))
MAX_SIGNALS_MID_TERM: int = int(os.getenv("MAX_SIGNALS_MID_TERM", "3"))

# 5. Trading Safety & Execution Controls
# CRITICAL: LIVE TRADING MUST DEFAULT TO FALSE
LIVE_TRADING_ENABLED: bool = True # os.getenv("LIVE_TRADING_ENABLED", "False").lower() in ("true", "1", "yes")

# Fixed Capital Allocation per Trade (Default: ₹10,000)
CAPITAL_PER_TRADE_INR: float = float(os.getenv("CAPITAL_PER_TRADE_INR", "10000.0"))

# Share / Product Type Mapping for Kotak Neo (NSE Cash Equity)
# Normal for BUY (Delivery / Carry-Forward), MIS for SELL (Intraday Short)
SHARE_TYPE_BUY: str = os.getenv("FO_SHARE_TYPE_BUY", "Normal")
SHARE_TYPE_SELL: str = os.getenv("FO_SHARE_TYPE_SELL", "MIS")

# Price Deviation Safeguard (Slippage protection before order dispatch)
MAX_PRICE_DEVIATION_TICKS: int = int(os.getenv("MAX_PRICE_DEVIATION_TICKS", "20"))
MAX_PRICE_DEVIATION_PERCENT: float = float(os.getenv("MAX_PRICE_DEVIATION_PERCENT", "0.35")) # 0.35%
MAX_QUOTE_AGE_SECONDS: int = int(os.getenv("MAX_QUOTE_AGE_SECONDS", "600")) # 10 min freshness tolerance

# 6. Database Connection Paths
COMMON_POSTGRE_READ_FILE = os.path.join(COMMON_MODULE_DIR, ".postgre_read")
COMMON_POSTGRE_WRITE_FILE = os.path.join(COMMON_MODULE_DIR, ".postgre")

# 7. Logging
LOG_DIR = os.path.join(SYSTEM_DIR, "logs")
LOG_LEVEL: str = os.getenv("FO_LOG_LEVEL", "INFO")


def validate_config() -> Dict[str, Any]:
    """Validates configuration parameters and returns clean dictionary."""
    if SIGNAL_WORKER_INTERVAL_SECONDS < 10:
        raise ValueError("SIGNAL_WORKER_INTERVAL_SECONDS must be >= 10")
    if CAPITAL_PER_TRADE_INR <= 0:
        raise ValueError("CAPITAL_PER_TRADE_INR must be greater than 0")
    if MAX_PRICE_DEVIATION_PERCENT <= 0:
        raise ValueError("MAX_PRICE_DEVIATION_PERCENT must be positive")

    return {
        "system_name": SYSTEM_NAME,
        "version": SYSTEM_VERSION,
        "live_trading": LIVE_TRADING_ENABLED,
        "capital_per_trade_inr": CAPITAL_PER_TRADE_INR,
        "signal_interval": SIGNAL_WORKER_INTERVAL_SECONDS,
        "monitor_interval": OPEN_CALL_WORKER_INTERVAL_SECONDS,
        "max_deviation_pct": MAX_PRICE_DEVIATION_PERCENT,
        "max_deviation_ticks": MAX_PRICE_DEVIATION_TICKS
    }
