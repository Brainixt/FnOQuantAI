"""
================================================================================
FnOQuantAI Unit Tests: Quant Engine, Features, and Strategies
================================================================================
Covers:
1. FeatureEngine: EMA, RSI, ATR, ROC, Trend detection.
2. QuantEngine: Tick rounding, geometry validation (BUY/SELL), R:R metrics.
3. IntradayStrategy: Cutoff window, breakout triggers, score checks.
4. ShortTermStrategy: Expiry buffer (<= 2 days), swing trend triggers.
5. MidTermStrategy: Expiry buffer (<= 7 days), macro trend triggers.
================================================================================
"""

import unittest
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo

from signals.feature_engine import FeatureEngine
from signals.quant_engine import QuantEngine
from signals.strategy_intraday import IntradayStrategy
from signals.strategy_short_term import ShortTermStrategy
from signals.strategy_mid_term import MidTermStrategy

IST = ZoneInfo("Asia/Kolkata")


class TestFeatureAndQuantEngine(unittest.TestCase):
    """Tests technical feature calculation and quant order geometry validation."""

    def setUp(self):
        # Create synthetic realistic snapshot sequence for RELIANCE with natural oscillation
        self.base_price = 6000.0
        increments = [0, 15, 25, 10, 20, 35, 25, 40, 30, 45, 55, 45, 60, 50, 65, 75]
        self.snapshots = []
        for inc in increments:
            price = self.base_price + inc
            self.snapshots.append({
                "symbol": "RELIANCE",
                "exchange": "NSE",
                "last_price": price,
                "open_price": price - 10.0,
                "high_price": price + 15.0,
                "low_price": price - 15.0,
                "close_price": price - 2.0,
                "open_interest": 10000 + inc,
                "volume": 5000 + inc,
                "tick_size": 1.0,
                "expiry": (date.today() + timedelta(days=25)).isoformat()
            })

    def test_feature_extraction(self):
        """Validates FeatureEngine indicators and values."""
        history = self.snapshots[:-1]
        latest = self.snapshots[-1]

        features = FeatureEngine.extract_features(history, latest)
        self.assertIsNotNone(features)
        self.assertEqual(features["bars_count"], len(self.snapshots))
        self.assertAlmostEqual(features["current_price"], latest["last_price"])
        self.assertGreater(features["atr"], 0.0)
        self.assertEqual(features["trend"], "BULLISH")
        self.assertGreater(features["rsi"], 50.0)
        self.assertGreater(features["roc"], 0.0)

    def test_tick_rounding(self):
        """Verifies price rounding to exact exchange tick sizes."""
        self.assertEqual(QuantEngine.round_to_tick(6012.34, 1.0), 6012.0)
        self.assertEqual(QuantEngine.round_to_tick(6012.78, 1.0), 6013.0)
        self.assertEqual(QuantEngine.round_to_tick(75432.4, 1.0), 75432.0)
        self.assertEqual(QuantEngine.round_to_tick(123.42, 0.05), 123.40)
        self.assertEqual(QuantEngine.round_to_tick(123.44, 0.05), 123.45)

    def test_valid_buy_geometry(self):
        """Ensures valid BUY geometry: SL < Entry < Target with appropriate R:R."""
        is_valid, reason, metrics = QuantEngine.validate_geometry(
            direction="BUY",
            entry=6000.0,
            stop_loss=5960.0,
            target=6080.0,
            min_reward_to_risk=1.5
        )
        self.assertTrue(is_valid)
        self.assertEqual(metrics["risk_points"], 40.0)
        self.assertEqual(metrics["reward_points"], 80.0)
        self.assertEqual(metrics["reward_to_risk_ratio"], 2.0)

    def test_invalid_buy_geometry(self):
        """Rejects inverted BUY stops and targets."""
        is_valid, reason, _ = QuantEngine.validate_geometry(
            direction="BUY",
            entry=6000.0,
            stop_loss=6050.0,
            target=6100.0
        )
        self.assertFalse(is_valid)
        self.assertIn("must be strictly below", reason)

        is_valid, reason, _ = QuantEngine.validate_geometry(
            direction="BUY",
            entry=6000.0,
            stop_loss=5950.0,
            target=5980.0
        )
        self.assertFalse(is_valid)
        self.assertIn("strictly above", reason)

        is_valid, reason, _ = QuantEngine.validate_geometry(
            direction="BUY",
            entry=6000.0,
            stop_loss=5900.0,
            target=6100.0,
            min_reward_to_risk=1.5
        )
        self.assertFalse(is_valid)
        self.assertIn("below minimum threshold", reason)

    def test_valid_sell_geometry(self):
        """Ensures valid SELL geometry: Target < Entry < SL."""
        is_valid, reason, metrics = QuantEngine.validate_geometry(
            direction="SELL",
            entry=6000.0,
            stop_loss=6050.0,
            target=5900.0,
            min_reward_to_risk=1.5
        )
        self.assertTrue(is_valid)
        self.assertEqual(metrics["risk_points"], 50.0)
        self.assertEqual(metrics["reward_points"], 100.0)
        self.assertEqual(metrics["reward_to_risk_ratio"], 2.0)


class TestQuantitativeStrategies(unittest.TestCase):
    """Tests evaluation logic across the three strategy categories."""

    def setUp(self):
        self.base_price = 5000.0
        increments = [0, 15, 25, 10, 20, 35, 25, 40, 30, 45, 55, 45, 60, 50, 65, 75]
        self.snapshots = []
        for inc in increments:
            price = self.base_price + inc
            self.snapshots.append({
                "symbol": "TCS26NOVFUT",
                "exchange": "NSE",
                "last_price": price,
                "open_price": price - 5.0,
                "high_price": price + 10.0,
                "low_price": price - 10.0,
                "close_price": price - 1.0,
                "open_interest": 50000,
                "volume": 20000,
                "tick_size": 0.1,
                "expiry": (date.today() + timedelta(days=20)).isoformat()
            })

    def test_intraday_cutoff_enforcement(self):
        """Intraday strategy enforces 15:00 IST entry cutoff for same-session equity exits."""
        history = self.snapshots[:-1]
        latest = self.snapshots[-1]

        # Evaluation at 14:30 IST (before 15:00 cutoff) - accepted
        valid_dt = datetime.combine(date.today(), dtime(14, 30), tzinfo=IST)
        signal = IntradayStrategy.evaluate(history, latest, current_dt=valid_dt)
        self.assertIsNotNone(signal, "Signals at 14:30 IST must be generated")
        self.assertEqual(signal["strategy_category"], "INTRADAY")

        # Evaluation at 15:10 IST (past 15:00 cutoff) - rejected
        cutoff_dt = datetime.combine(date.today(), dtime(15, 10), tzinfo=IST)
        sig_rejected = IntradayStrategy.evaluate(history, latest, current_dt=cutoff_dt)
        self.assertIsNone(sig_rejected, "Signals past 15:00 IST cutoff must be rejected")
        self.assertEqual(signal["direction"], "BUY")
        self.assertGreaterEqual(signal["reward_to_risk_ratio"], 1.5)

    def test_short_term_expiry_rejection(self):
        """Short-term strategy rejects contracts expiring within 2 days."""
        history = self.snapshots[:-1]
        expiring_latest = dict(self.snapshots[-1])
        expiring_latest["expiry"] = (date.today() + timedelta(days=1)).isoformat()

        signal = ShortTermStrategy.evaluate(history, expiring_latest)
        self.assertIsNone(signal, "Contracts expiring <= 2 days must be rejected")

        # Contract expiring in 15 days should pass
        valid_latest = dict(self.snapshots[-1])
        valid_latest["expiry"] = (date.today() + timedelta(days=15)).isoformat()
        signal = ShortTermStrategy.evaluate(history, valid_latest)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["strategy_category"], "SHORT_TERM")
        self.assertGreaterEqual(signal["reward_to_risk_ratio"], 1.6)

    def test_mid_term_expiry_rejection(self):
        """Mid-term strategy rejects contracts expiring within 7 days."""
        history = self.snapshots[:-1]
        expiring_latest = dict(self.snapshots[-1])
        expiring_latest["expiry"] = (date.today() + timedelta(days=5)).isoformat()

        signal = MidTermStrategy.evaluate(history, expiring_latest)
        self.assertIsNone(signal, "Contracts expiring <= 7 days must be rejected for mid-term holding")

        # Contract expiring in 35 days should pass
        valid_latest = dict(self.snapshots[-1])
        valid_latest["expiry"] = (date.today() + timedelta(days=35)).isoformat()
        signal = MidTermStrategy.evaluate(history, valid_latest)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["strategy_category"], "MID_TERM")
        self.assertGreaterEqual(signal["reward_to_risk_ratio"], 1.8)


if __name__ == "__main__":
    unittest.main()
