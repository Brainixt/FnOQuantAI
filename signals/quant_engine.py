"""
================================================================================
FnOQuantAI: Quantitative Analysis & Math Engine
================================================================================
Deterministic quantitative calculations, tick rounding, and geometry validation:
- Enforces strict directional price coherence (BUY: sl < entry < target; SELL: target < entry < sl).
- Validates minimum reward-to-risk thresholds.
- Rounds prices to verified NSE equity contract tick sizes (standard 0.05).
- Selects and ranks top candidates per strategy category.
================================================================================
"""

import math
from typing import Dict, Any, Optional, Tuple, List

try:
    import fo_config as config
except ImportError:
    import config


class QuantEngine:
    """Core mathematical and rule-based validation engine for NSE equities."""

    @staticmethod
    def round_to_tick(price: float, tick_size: Optional[float] = None) -> float:
        """
        Rounds price to nearest valid tick increment according to contract specifications.
        Default tick size for NSE equities: 0.05.
        """
        tick = float(tick_size) if tick_size and float(tick_size) > 0 else config.DEFAULT_TICK_SIZE
        return round(round(price / tick) * tick, 4)

    @classmethod
    def validate_geometry(
        cls,
        direction: str,
        entry: float,
        stop_loss: float,
        target: float,
        min_reward_to_risk: Optional[float] = None
    ) -> Tuple[bool, Optional[str], Dict[str, float]]:
        """
        Validates stop loss and target geometry against directional rules.
        Returns:
            (is_valid, rejection_reason, metrics_dict)
        """
        min_rr = min_reward_to_risk if min_reward_to_risk is not None else config.MIN_REWARD_TO_RISK

        if entry <= 0 or stop_loss <= 0 or target <= 0:
            return False, "Non-positive price level detected", {}

        if direction == "BUY":
            if not (stop_loss < entry < target):
                return False, f"Invalid BUY geometry: Stop-loss ({stop_loss}) must be strictly below Entry ({entry}) and Target ({target}) strictly above", {}
            risk = entry - stop_loss
            reward = target - entry
        elif direction == "SELL":
            if not (target < entry < stop_loss):
                return False, f"Invalid SELL geometry: Target ({target}) must be strictly below Entry ({entry}) and Stop-loss ({stop_loss}) strictly above", {}
            risk = stop_loss - entry
            reward = entry - target
        else:
            return False, f"Unknown direction '{direction}'", {}

        if risk <= 0:
            return False, f"Invalid risk per unit ({risk:.4f})", {}

        rr_ratio = round(reward / risk, 4)
        if rr_ratio < min_rr:
            return False, f"Reward-to-risk ratio ({rr_ratio:.2f}) is below minimum threshold ({min_rr})", {}

        risk_pct = round((risk / entry) * 100.0, 4)

        metrics = {
            "entry_price": round(entry, 4),
            "stop_loss": round(stop_loss, 4),
            "target_price": round(target, 4),
            "risk_points": round(risk, 4),
            "reward_points": round(reward, 4),
            "reward_to_risk_ratio": rr_ratio,
            "risk_percentage": risk_pct,
            # Backward-compatible aliases
            "entry_reference_price": round(entry, 4),
            "stop_loss_price": round(stop_loss, 4),
            "risk_per_unit": round(risk, 4),
            "reward_per_unit": round(reward, 4)
        }
        return True, None, metrics

    @staticmethod
    def rank_candidates(
        candidates: List[Dict[str, Any]],
        max_signals: int = 3
    ) -> List[Dict[str, Any]]:
        """
        Sorts eligible candidates by total quantitative score descending
        and returns the top candidates up to max_signals.
        """
        if not candidates:
            return []

        valid = [c for c in candidates if c.get("total_score", 0.0) >= config.MIN_SIGNAL_SCORE]
        valid.sort(key=lambda x: (x.get("total_score", 0.0), x.get("reward_to_risk_ratio", 0.0)), reverse=True)
        return valid[:max_signals]
