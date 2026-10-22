"""
================================================================================
FnOQuantAI: Observation & Quote Freshness Validator
================================================================================
Validates raw market data snapshots before quantitative feature extraction:
- Rejects zero, negative, or missing LTP values.
- Rejects inverted bid/ask spreads (ask < bid).
- Enforces strict staleness limits against MAX_QUOTE_AGE_SECONDS.
- Ensures cycle_id, symbol, and exchange (NSE) integrity.
================================================================================
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, Optional
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config

log = logging.getLogger("FnOQuantAI.Data.Freshness")
IST = ZoneInfo("Asia/Kolkata")


class ObservationValidator:
    """Pre-quant gatekeeper ensuring only fresh, uncorrupted equity data is processed."""

    @classmethod
    def validate_snapshot(
        cls,
        snapshot: Dict[str, Any],
        max_age_seconds: Optional[int] = None,
        current_dt: Optional[datetime] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates snapshot data integrity and freshness.
        Returns:
            (is_valid: bool, error_reason: Optional[str])
        """
        if not snapshot or not isinstance(snapshot, dict):
            return False, "Snapshot record is empty or invalid"

        symbol = snapshot.get("symbol")
        if not symbol or not str(symbol).strip():
            return False, "Missing or invalid symbol identifier"

        # Exchange Validation (Strictly NSE)
        exchange = snapshot.get("exchange", "NSE")
        if exchange and exchange.upper() not in ("NSE", "NFO"):
            return False, f"FnOQuantAI strictly operates on NSE (got {exchange})"

        # Cycle ID Validation
        cycle_id = snapshot.get("cycle_id")
        if not cycle_id or not str(cycle_id).strip():
            return False, f"Invalid or missing cycle_id: '{cycle_id}'"

        # Price Integrity (LTP must be strictly positive)
        ltp = snapshot.get("ltp")
        if ltp is None:
            return False, f"Missing LTP for {symbol}"
        try:
            ltp_val = float(ltp)
            if ltp_val <= 0.0:
                return False, f"Non-positive LTP ({ltp_val}) for {symbol}"
        except (ValueError, TypeError):
            return False, f"Non-numeric LTP '{ltp}' for {symbol}"

        # Bid / Ask Spread Sanity (if present)
        bid = snapshot.get("bid_price")
        ask = snapshot.get("ask_price")
        if bid is not None and ask is not None:
            try:
                b_val = float(bid)
                a_val = float(ask)
                if b_val > 0 and a_val > 0 and a_val < b_val:
                    return False, f"Inverted spread: ask_price ({a_val}) < bid_price ({b_val}) for {symbol}"
            except (ValueError, TypeError):
                pass

        # Age / Freshness Evaluation
        max_age = max_age_seconds or getattr(config, "MAX_QUOTE_AGE_SECONDS", 360)
        now_dt = current_dt or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        ts_val = snapshot.get("retrieved_at") or snapshot.get("source_timestamp") or snapshot.get("created_at")
        if not ts_val:
            return False, f"Missing timestamp (created_at/retrieved_at) for {symbol}"

        if isinstance(ts_val, str):
            try:
                dt_obj = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
            except ValueError:
                return False, f"Unparseable ISO timestamp: {ts_val}"
        elif isinstance(ts_val, datetime):
            dt_obj = ts_val
        else:
            return False, f"Unsupported timestamp type: {type(ts_val)}"

        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=timezone.utc)

        age_seconds = (now_dt - dt_obj).total_seconds()
        if age_seconds < -120:
            return False, f"Future timestamp detected for {symbol} (skew: {age_seconds:.1f}s)"

        if age_seconds > max_age:
            return False, f"Stale quote for {symbol}: age {int(age_seconds)}s exceeds limit {max_age}s"

        return True, None
