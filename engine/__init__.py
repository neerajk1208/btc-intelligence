"""
Engine module for BTC Intelligence.

The recommendation engine is the core brain that combines:
- Market signals (regime, VWAP, volatility)
- Guards (time, position, loss, spike)
- Current position

To generate actionable recommendations.
"""
from .recommendation import (
    RecommendationEngine,
    Recommendation,
    Action,
    Urgency,
    SizingConfig,
)

__all__ = [
    "RecommendationEngine",
    "Recommendation",
    "Action",
    "Urgency",
    "SizingConfig",
]
