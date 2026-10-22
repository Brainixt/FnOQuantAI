"""
================================================================================
FnOQuantAI: Short-Term Swing Quantitative Strategy Module
================================================================================
Strategy: ShortTermSwingTrendStrategy (v1.0.0)
Universe: NSE F&O-eligible underlying equity shares
Holding Period: 1 to 5 trading days (Delivery CNC)
Risk Framework:
- Wider ATR buffers (2.5x ATR) to safely withstand overnight gap risk.
- Higher profit target (4.5x ATR) ensuring minimum 1.8 R:R ratio.
- Regulatory Compliance: Indian cash equity overnight delivery is BUY only (Long).
================================================================================
"""

import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional
from zoneinfo import ZoneInfo

try:
    import fo_config as config
except ImportError:
    import config
from signals.feature_engine import FeatureEngine
from signals.quant_engine import QuantEngine

log = logging.getLogger("FnOQuantAI.Strategy.ShortTerm")
IST = ZoneInfo("Asia/Kolkata")


class ShortTermStrategy:
    """Quantitative swing trend strategy for 1-5 trading days holding period."""

    STRATEGY_NAME = "ShortTermSwingTrend"
    STRATEGY_VERSION = "1.0.0"
    CATEGORY = "SHORT_TERM"
    INTENDED_HOLDING = "1-5_TRADING_DAYS"

    @classmethod
    def evaluate(
        cls,
        history: list,
        latest_snapshot: Dict[str, Any],
        current_dt: Optional[datetime] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates an NSE equity candidate for 1-5 day swing trading.
        Returns a signal candidate dictionary if qualified, or None.
        """
        now = current_dt or datetime.now(IST)
        today = now.date()

        # Contract Expiry Check: Reject contracts expiring within 2 days
        expiry_val = latest_snapshot.get("expiry")
        if expiry_val:
            if isinstance(expiry_val, str):
                try:
                    expiry_date = datetime.strptime(expiry_val, "%Y-%m-%d").date()
                except ValueError:
                    expiry_date = None
            elif isinstance(expiry_val, date):
                expiry_date = expiry_val
            else:
                expiry_date = None

            if expiry_date and (expiry_date - today).days <= 2:
                log.debug(f"[{cls.CATEGORY}] Rejected {latest_snapshot.get('symbol')}: Expiry {expiry_date} is too close (<= 2 days).")
                return None

        # 1. Extract Features
        features = FeatureEngine.extract_features(history, latest_snapshot)
        if not features or features.get("bars_count", 0) < 4:
            return None

        ltp = features["current_price"]
        trend = features["trend"]
        rsi = features["rsi"]
        atr = features["atr"]
        tick_size = float(latest_snapshot.get("tick_size") or config.DEFAULT_TICK_SIZE)

        direction = "NO_TRADE"
        score = 50.0
        contributions = {}

        # 2. Swing Trend Scoring (Long only for Cash Equity Delivery)
        if trend == "BULLISH" and 48.0 <= rsi <= 72.0:
            direction = "BUY"
            score += 25.0
            contributions["trend_alignment"] = 25.0

            if 52.0 <= rsi <= 65.0:
                score += 15.0
                contributions["rsi_sweet_spot"] = 15.0
            else:
                score += 8.0
                contributions["rsi_sweet_spot"] = 8.0

            # F&O Open Interest increase signals long buildup
            oi_change = features.get("oi_change_pct")
            if oi_change is not None and oi_change > 1.0:
                score += 10.0
                contributions["oi_long_buildup"] = 10.0

            # SL = 2.5x ATR, Target = 4.5x ATR (R:R = 1.8)
            sl_raw = ltp - (2.5 * atr)
            target_raw = ltp + (4.5 * atr)

        else:
            return None

        if direction == "NO_TRADE" or score < config.MIN_SIGNAL_SCORE:
            return None

        # 3. Round to valid contract tick sizes
        sl_rounded = QuantEngine.round_to_tick(sl_raw, tick_size)
        target_rounded = QuantEngine.round_to_tick(target_raw, tick_size)
        entry_rounded = QuantEngine.round_to_tick(ltp, tick_size)

        # 4. Geometry and Risk/Reward Validation
        is_valid, reject_reason, metrics = QuantEngine.validate_geometry(
            direction=direction,
            entry=entry_rounded,
            stop_loss=sl_rounded,
            target=target_rounded,
            min_reward_to_risk=1.8
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
