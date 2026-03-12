"""
UI module for BTC Intelligence.

Provides:
- Terminal dashboard for real-time display
- Position input interface for manual entry
"""
from .dashboard import Dashboard, get_dashboard
from .input import PositionInput, quick_position_entry

__all__ = [
    "Dashboard",
    "get_dashboard",
    "PositionInput",
    "quick_position_entry",
]
