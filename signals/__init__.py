"""
Signal modules for BTC Intelligence.

Provides market analysis signals:
- Regime detection (trending/choppy/news)
- VWAP with deviation bands
- Volatility analysis (ATR, Bollinger)
"""
from .regime import RegimeDetector, RegimeState, MarketRegime
from .vwap import VWAPCalculator, VWAPState
from .volatility import VolatilityAnalyzer, VolatilityState, Candle

__all__ = [
    "RegimeDetector",
    "RegimeState",
    "MarketRegime",
    "VWAPCalculator",
    "VWAPState",
    "VolatilityAnalyzer",
    "VolatilityState",
    "Candle",
]
