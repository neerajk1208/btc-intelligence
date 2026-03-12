"""
Position-based Guards for BTC Intelligence.

Manages:
1. Maximum position size limits
2. Position-based action restrictions
3. Exposure warnings

These guard against over-leveraging and ensure risk management.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class PositionGuardState:
    """Current state of position guards."""
    current_position_usd: float = 0.0
    max_position_usd: float = 30000.0
    
    # Exposure metrics
    exposure_pct: float = 0.0           # Abs position / max position
    exposure_level: str = "none"        # "none", "low", "moderate", "high", "max"
    
    # Restrictions
    can_add_long: bool = True           # Can open/add to long
    can_add_short: bool = True          # Can open/add to short
    reduce_only: bool = False           # Only reducing trades allowed
    
    # Warnings
    warning: Optional[str] = None       # Warning message if any
    
    def to_dict(self) -> dict:
        return {
            "current_position_usd": self.current_position_usd,
            "max_position_usd": self.max_position_usd,
            "exposure_pct": round(self.exposure_pct * 100, 1),
            "exposure_level": self.exposure_level,
            "can_add_long": self.can_add_long,
            "can_add_short": self.can_add_short,
            "reduce_only": self.reduce_only,
            "warning": self.warning,
        }


class PositionGuard:
    """
    Guards against excessive position sizes.
    
    Tiers:
    - 0-30%: Low exposure - normal trading
    - 30-60%: Moderate exposure - reduce adding size
    - 60-90%: High exposure - prefer reducing trades
    - 90%+: Max exposure - reduce only
    
    Usage:
        guard = PositionGuard(max_position_usd=30000)
        
        # Update with current position
        state = guard.check(position_usd=15000)
        
        if not state.can_add_long:
            print("Cannot add to long")
    """
    
    def __init__(
        self,
        max_position_usd: float = 30000.0,
        moderate_threshold_pct: float = 30.0,
        high_threshold_pct: float = 60.0,
        max_threshold_pct: float = 90.0,
    ):
        self._max_position = max_position_usd
        self._moderate_pct = moderate_threshold_pct / 100
        self._high_pct = high_threshold_pct / 100
        self._max_pct = max_threshold_pct / 100
        
        self._state = PositionGuardState(max_position_usd=max_position_usd)
    
    def check(self, position_usd: float) -> PositionGuardState:
        """
        Check position against limits.
        
        Args:
            position_usd: Current position (positive=long, negative=short)
            
        Returns:
            PositionGuardState with restrictions
        """
        self._state.current_position_usd = position_usd
        
        # Calculate exposure
        abs_position = abs(position_usd)
        if self._max_position > 0:
            exposure_pct = abs_position / self._max_position
        else:
            exposure_pct = 0
        
        self._state.exposure_pct = exposure_pct
        
        # Determine exposure level
        if exposure_pct >= self._max_pct:
            self._state.exposure_level = "max"
            self._state.reduce_only = True
            self._state.warning = f"⚠️ MAX EXPOSURE ({exposure_pct*100:.0f}%) - Reduce only!"
            
            # Can only reduce
            if position_usd > 0:  # Long
                self._state.can_add_long = False
                self._state.can_add_short = True  # Reducing
            else:  # Short
                self._state.can_add_long = True   # Reducing
                self._state.can_add_short = False
                
        elif exposure_pct >= self._high_pct:
            self._state.exposure_level = "high"
            self._state.reduce_only = False
            self._state.warning = f"⚠️ High exposure ({exposure_pct*100:.0f}%) - Prefer reducing"
            
            # Prefer reducing but allow small adds
            self._state.can_add_long = True
            self._state.can_add_short = True
            
        elif exposure_pct >= self._moderate_pct:
            self._state.exposure_level = "moderate"
            self._state.reduce_only = False
            self._state.warning = None
            
            self._state.can_add_long = True
            self._state.can_add_short = True
            
        elif exposure_pct > 0:
            self._state.exposure_level = "low"
            self._state.reduce_only = False
            self._state.warning = None
            
            self._state.can_add_long = True
            self._state.can_add_short = True
            
        else:
            self._state.exposure_level = "none"
            self._state.reduce_only = False
            self._state.warning = None
            
            self._state.can_add_long = True
            self._state.can_add_short = True
        
        return self._state
    
    def get_size_multiplier(self) -> float:
        """Get size multiplier based on current exposure."""
        level = self._state.exposure_level
        
        if level == "max":
            return 0.0  # No adding
        elif level == "high":
            return 0.25  # Quarter size for adds
        elif level == "moderate":
            return 0.5   # Half size
        else:
            return 1.0   # Full size
    
    def get_max_add_size(self) -> float:
        """Get maximum size that can be added without exceeding limits."""
        current = abs(self._state.current_position_usd)
        remaining = max(0, self._max_position - current)
        return remaining
    
    def would_exceed_limit(self, add_size_usd: float, side: str) -> bool:
        """Check if adding this size would exceed the limit."""
        current = self._state.current_position_usd
        
        if side == "buy":
            new_position = current + add_size_usd
        else:
            new_position = current - add_size_usd
        
        return abs(new_position) > self._max_position
    
    def get_state(self) -> PositionGuardState:
        """Get current state."""
        return self._state
