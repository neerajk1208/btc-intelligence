"""
Guard modules for BTC Intelligence.

Guards are safety mechanisms that pause or restrict trading based on:
- Time/macro events (FOMC, CPI, etc.)
- Position limits
- Daily loss limits
- Volatility spikes/news events
"""
from .time_regime import TimeGuard, get_time_guard, TimeGuardState, MacroEvent
from .position_guard import PositionGuard, PositionGuardState
from .loss_guard import LossGuard, LossGuardState
from .spike_guard import SpikeGuard, SpikeGuardState

__all__ = [
    "TimeGuard",
    "get_time_guard",
    "TimeGuardState",
    "MacroEvent",
    "PositionGuard",
    "PositionGuardState",
    "LossGuard",
    "LossGuardState",
    "SpikeGuard",
    "SpikeGuardState",
]
