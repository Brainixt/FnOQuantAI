"""
================================================================================
FnOQuantAI: Feature Engineering Engine
================================================================================
Calculates deterministic technical and F&O-derived market structure features
from validated price histories of NSE F&O-eligible underlying equities:
- Exponential Moving Averages (EMA Fast, EMA Slow) & Trend Alignment
- Relative Strength Index (RSI 14)
- Average True Range (ATR 14) & Dynamic Volatility Buffers
- Momentum / Rate of Change (ROC)
- Dynamic Support & Resistance Levels (Rolling Min/Max)
- F&O Open Interest & Volume Relative Confirmation
================================================================================
"""

import math
from typing import List, Dict, Any, Optional


class FeatureEngine:
    """Calculates standardized quantitative and structural features for NSE equities."""

    @classmethod
    def extract_features(
        cls,
        history: List[Dict[str, Any]],
        latest: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Extracts multi-dimensional features from validated historical bars + current snapshot.
        history: Chronologically sorted list of bars (oldest first).
        latest: Current snapshot dict.
        """
        ltp = float(latest.get("ltp") or latest.get("last_price") or 0.0)
        if ltp <= 0:
            return {}

        closes = [float(b.get("close") or b.get("close_price") or b.get("ltp") or b.get("last_price") or ltp) for b in history]
        highs = [float(b.get("high") or b.get("high_price") or b.get("ltp") or b.get("last_price") or ltp) for b in history]
        lows = [float(b.get("low") or b.get("low_price") or b.get("ltp") or b.get("last_price") or ltp) for b in history]

        # Ensure latest price is included at the end
        closes.append(ltp)
        latest_h = float(latest.get("high") or latest.get("high_price") or ltp)
        latest_l = float(latest.get("low") or latest.get("low_price") or ltp)
        highs.append(max(latest_h, ltp))
        lows.append(min(latest_l, ltp))

        n_bars = len(closes)

        # 1. EMAs and Trend Direction
        fast_period = min(5, max(2, n_bars // 3))
        slow_period = min(15, max(4, n_bars - 1))

        ema_fast = cls._calculate_ema(closes, fast_period)
        ema_slow = cls._calculate_ema(closes, slow_period)

        if ema_fast > ema_slow * 1.0005:
            trend = "BULLISH"
        elif ema_fast < ema_slow * 0.9995:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"

        # 2. RSI (14-period or adaptive)
        rsi_period = min(14, max(3, n_bars - 2))
        rsi_val = cls._calculate_rsi(closes, rsi_period)

        # 3. ATR (Average True Range)
        atr_period = min(14, max(3, n_bars - 2))
        atr_val = cls._calculate_atr(highs, lows, closes, atr_period)
        # Fallback ATR if history is very short: 0.5% of LTP (minimum 0.05 NSE tick)
        if atr_val <= 0:
            atr_val = max(0.05, round(ltp * 0.005, 4))

        # 4. Momentum / Rate of Change
        lookback_roc = min(5, max(1, n_bars - 1))
        past_price = closes[-lookback_roc - 1]
        roc_pct = round(((ltp - past_price) / past_price) * 100.0, 4) if past_price > 0 else 0.0

        # 5. Support & Resistance Levels (rolling min/max over lookback)
        sr_window = min(20, n_bars)
        support = min(lows[-sr_window:])
        resistance = max(highs[-sr_window:])

        # 6. F&O Open Interest Confirmation (if present)
        current_oi = latest.get("open_interest")
        oi_change_pct = None
        if current_oi is not None and len(history) > 0:
            prev_oi = history[-1].get("open_interest")
            if prev_oi is not None and float(prev_oi) > 0:
                oi_change_pct = round(((float(current_oi) - float(prev_oi)) / float(prev_oi)) * 100.0, 2)

        # 7. Volume Relative Ratio
        current_vol = float(latest.get("volume") or 0.0)
        vol_ratio = 1.0
        if len(history) > 0:
            past_vols = [float(b.get("volume") or 0.0) for b in history[-5:]]
            avg_vol = sum(past_vols) / len(past_vols) if past_vols else 0.0
            if avg_vol > 0:
                vol_ratio = round(current_vol / avg_vol, 2)

        return {
            "current_price": ltp,
            "ema_fast": round(ema_fast, 4),
            "ema_slow": round(ema_slow, 4),
            "trend": trend,
            "rsi": round(rsi_val, 2),
            "atr": round(atr_val, 4),
            "atr_pct": round((atr_val / ltp) * 100.0, 3),
            "roc_pct": roc_pct,
            "roc": roc_pct,
            "support": round(support, 4),
            "resistance": round(resistance, 4),
            "open_interest": current_oi,
            "oi_change_pct": oi_change_pct,
            "volume": current_vol,
            "volume_ratio": vol_ratio,
            "bars_count": n_bars
        }

    @staticmethod
    def _calculate_ema(series: List[float], period: int) -> float:
        """Calculates Exponential Moving Average."""
        if not series:
            return 0.0
        if len(series) <= period:
            return sum(series) / len(series)

        k = 2.0 / (period + 1.0)
        ema = sum(series[:period]) / period
        for val in series[period:]:
            ema = (val * k) + (ema * (1.0 - k))
        return ema

    @staticmethod
    def _calculate_rsi(closes: List[float], period: int = 14) -> float:
        """Calculates Relative Strength Index (RSI)."""
        if len(closes) < 2:
            return 50.0

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        if not deltas:
            return 50.0

        effective_period = min(period, len(deltas))
        gains = [d for d in deltas[-effective_period:] if d > 0]
        losses = [-d for d in deltas[-effective_period:] if d < 0]

        avg_gain = sum(gains) / effective_period if gains else 0.0
        avg_loss = sum(losses) / effective_period if losses else 0.0

        if avg_loss == 0.0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def _calculate_atr(
        highs: List[float],
        lows: List[float],
        closes: List[float],
        period: int = 14
    ) -> float:
        """Calculates Average True Range (ATR)."""
        n = len(closes)
        if n < 2:
            return 0.0

        tr_list = []
        for i in range(1, n):
            h = highs[i]
            l = lows[i]
            prev_c = closes[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            tr_list.append(tr)

        if not tr_list:
            return 0.0

        effective_period = min(period, len(tr_list))
        atr = sum(tr_list[:effective_period]) / effective_period
        for tr in tr_list[effective_period:]:
            atr = ((atr * (effective_period - 1)) + tr) / effective_period
        return atr
