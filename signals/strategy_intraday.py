"""
================================================================================
FnOQuantAI: Intraday Quantitative Strategy Module
================================================================================
Strategy: IntradayBreakoutMomentumStrategy (v1.0.0)
Universe: NSE F&O-eligible underlying equity shares
Holding Period: Intraday (Same-session exit, mandatory square-off before 15:15 IST)
Risk Framework:
- Tight ATR-based stops (1.5x ATR) and targets (2.5x ATR) with minimum 1.6 R:R.
- Market noise protection: Stop loss must be >= 3 tick increments (0.15).
- Entry Cutoff: Strict cutoff at 15:00 IST (no new entries after 15:00 IST).
================================================================================
"""

import logging
from datetime import datetime, date, time as dtime
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config
from signals.feature_engine import FeatureEngine
from signals.quant_engine import QuantEngine

log = logging.getLogger("FnOQuantAI.Strategy.Intraday")
IST = ZoneInfo("Asia/Kolkata")


class IntradayStrategy:
    """Quantitative intraday momentum and breakout strategy for NSE equities."""

    STRATEGY_NAME = "IntradayBreakoutMomentum"
    STRATEGY_VERSION = "1.0.0"
    CATEGORY = "INTRADAY"
    INTENDED_HOLDING = "SAME_SESSION"

    @classmethod
    def evaluate(
        cls,
        history: list,
        latest_snapshot: Dict[str, Any],
        current_dt: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates an NSE equity candidate for intraday trading eligibility.
        Returns a signal candidate dictionary if qualified, or None.
        """
        now = current_dt or datetime.now(IST)
        if now.tzinfo is None:
            now = now.replace(tzinfo=IST)

        # 1. Entry Cutoff Check (No new intraday positions after 15:00 IST)
        if now.time() >= config.INTRADAY_ENTRY_CUTOFF_TIME:
            log.debug(f"[{cls.CATEGORY}] Rejected {latest_snapshot.get('symbol')}: Time {now.time()} past 15:00 IST entry cutoff.")
            return None

        # 2. Extract Features
        features = FeatureEngine.extract_features(history, latest_snapshot)
        if not features or features.get("bars_count", 0) < 3:
            return None

        ltp = features["current_price"]
        trend = features["trend"]
        rsi = features["rsi"]
        atr = features["atr"]
        roc = features["roc_pct"]
        tick_size = float(latest_snapshot.get("tick_size") or config.DEFAULT_TICK_SIZE)

        # Minimum noise filter: ATR must exceed 3 ticks
        if atr < (tick_size * 3):
            return None

        direction = "NO_TRADE"
        score = 50.0
        contributions = {}

        # 3. Directional Scoring Logic
        if trend == "BULLISH" and 50.0 <= rsi <= 76.0 and roc > 0:
            direction = "BUY"
            score += 20.0  # Trend confirmation
            contributions["trend"] = 20.0

            if 55.0 <= rsi <= 68.0:
                score += 15.0  # Optimal momentum band
                contributions["rsi_momentum"] = 15.0
            else:
                score += 8.0
                contributions["rsi_momentum"] = 8.0

            if roc > 0.15:
                score += 10.0
                contributions["roc_strength"] = 10.0

            # Proposed geometry: SL = 1.5x ATR, Target = 2.5x ATR
            sl_raw = ltp - (1.5 * atr)
            target_raw = ltp + (2.5 * atr)

        elif trend == "BEARISH" and 24.0 <= rsi <= 50.0 and roc < 0:
            direction = "SELL"
            score += 20.0
            contributions["trend"] = 20.0

            if 32.0 <= rsi <= 45.0:
                score += 15.0
                contributions["rsi_momentum"] = 15.0
            else:
                score += 8.0
                contributions["rsi_momentum"] = 8.0

            if roc < -0.15:
                score += 10.0
                contributions["roc_strength"] = 10.0

            sl_raw = ltp + (1.5 * atr)
            target_raw = ltp - (2.5 * atr)

        else:
            return None

        # F&O Open Interest confirmation bonus
        oi_change = features.get("oi_change_pct")
        if oi_change is not None:
            if (direction == "BUY" and oi_change > 0) or (direction == "SELL" and oi_change > 0):
                score += 5.0
                contributions["oi_confirmation"] = 5.0

        if direction == "NO_TRADE" or score < config.MIN_SIGNAL_SCORE:
            return None

        # 4. Round to valid contract tick sizes
        sl_rounded = QuantEngine.round_to_tick(sl_raw, tick_size)
        target_rounded = QuantEngine.round_to_tick(target_raw, tick_size)
        entry_rounded = QuantEngine.round_to_tick(ltp, tick_size)

        # 5. Geometry and Risk/Reward Validation
        is_valid, reject_reason, metrics = QuantEngine.validate_geometry(
            direction=direction,
            entry=entry_rounded,
            stop_loss=sl_rounded,
            target=target_rounded,
            min_reward_to_risk=1.6
        )

        if not is_valid:
            log.debug(f"[{cls.CATEGORY}] Rejected {latest_snapshot.get('symbol')}: {reject_reason}")
            return None

        symbol = latest_snapshot.get("symbol")
        trading_symbol = latest_snapshot.get("trading_symbol") or f"{symbol}-EQ"

        return {
            "symbol": symbol,
            "trading_symbol": trading_symbol,
            "exchange": "NSE",
            "strategy_name": cls.STRATEGY_NAME,
            "strategy_version": cls.STRATEGY_VERSION,
            "strategy_category": cls.CATEGORY,
            "intended_holding_period": cls.INTENDED_HOLDING,
            "direction": direction,
            "total_score": round(score, 2),
            "feature_contributions": contributions,
            "features_summary": features,
            "source_cycle_id": latest_snapshot.get("cycle_id", "MANUAL"),
            "source_snapshot_id": latest_snapshot.get("id", 0),
            "source_market_timestamp": latest_snapshot.get("retrieved_at"),
            **metrics
        }
