"""
Daily Loss Limit Guard for BTC Intelligence.

Tracks daily P&L and pauses trading if loss limit is exceeded.
Resets at a configurable time (default: midnight UTC).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional, List
import logging
import json
import os

logger = logging.getLogger(__name__)


@dataclass
class Trade:
    """A single completed trade."""
    timestamp: datetime
    side: str           # "buy" or "sell"
    size_usd: float
    entry_price: float
    exit_price: float
    pnl_usd: float


@dataclass
class LossGuardState:
    """Current state of loss guard."""
    daily_pnl_usd: float = 0.0
    daily_loss_limit_usd: float = 3000.0
    
    # Status
    trades_today: int = 0
    wins_today: int = 0
    losses_today: int = 0
    
    # Guard state
    is_paused: bool = False
    pause_reason: str = ""
    
    # Warnings
    warning_level: str = "none"   # "none", "caution", "warning", "critical"
    pnl_pct_of_limit: float = 0.0
    
    # Time tracking
    current_date: str = ""
    reset_at_utc: str = "00:00"
    
    def to_dict(self) -> dict:
        return {
            "daily_pnl_usd": round(self.daily_pnl_usd, 2),
            "daily_loss_limit_usd": self.daily_loss_limit_usd,
            "trades_today": self.trades_today,
            "wins_today": self.wins_today,
            "losses_today": self.losses_today,
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
            "warning_level": self.warning_level,
            "pnl_pct_of_limit": round(self.pnl_pct_of_limit * 100, 1),
            "current_date": self.current_date,
        }


class LossGuard:
    """
    Guards against excessive daily losses.
    
    Tracks realized P&L and pauses when limit is exceeded.
    Also provides warnings at thresholds.
    
    Thresholds:
    - 50% of limit used: Caution
    - 75% of limit used: Warning
    - 100% of limit: Paused
    
    Usage:
        guard = LossGuard(daily_loss_limit_usd=3000)
        
        # Record trades
        guard.record_trade(pnl=-150)
        
        # Check state
        state = guard.check()
        if state.is_paused:
            print("Stop trading - daily limit hit")
    """
    
    def __init__(
        self,
        daily_loss_limit_usd: float = 2500.0,  # Tighter limit for capital preservation
        caution_threshold_pct: float = 50.0,
        warning_threshold_pct: float = 75.0,
        persist_file: Optional[str] = None,
    ):
        self._loss_limit = daily_loss_limit_usd
        self._caution_pct = caution_threshold_pct / 100
        self._warning_pct = warning_threshold_pct / 100
        
        # Persistence
        if persist_file is None:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            persist_file = os.path.join(base_dir, "data", "daily_pnl.json")
        self._persist_file = persist_file
        
        # State
        self._state = LossGuardState(daily_loss_limit_usd=daily_loss_limit_usd)
        self._trades: List[Trade] = []
        
        # Load persisted state
        self._load_state()
    
    def record_trade(
        self,
        pnl_usd: float,
        side: str = "buy",
        size_usd: float = 0,
        entry_price: float = 0,
        exit_price: float = 0,
    ) -> LossGuardState:
        """
        Record a completed trade.
        
        Args:
            pnl_usd: Realized P&L from the trade
            side: "buy" or "sell"
            size_usd: Trade size
            entry_price: Entry price
            exit_price: Exit price
            
        Returns:
            Updated LossGuardState
        """
        # Check for day change first
        self._check_day_reset()
        
        # Record trade
        trade = Trade(
            timestamp=datetime.utcnow(),
            side=side,
            size_usd=size_usd,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl_usd=pnl_usd,
        )
        self._trades.append(trade)
        
        # Update state
        self._state.daily_pnl_usd += pnl_usd
        self._state.trades_today += 1
        
        if pnl_usd >= 0:
            self._state.wins_today += 1
        else:
            self._state.losses_today += 1
        
        # Check limits
        self._update_guard_state()
        
        # Persist
        self._save_state()
        
        # Log significant events
        if pnl_usd < -100:
            logger.warning(f"Loss recorded: ${pnl_usd:.2f} (daily total: ${self._state.daily_pnl_usd:.2f})")
        
        return self._state
    
    def check(self) -> LossGuardState:
        """Check current state (also handles day reset)."""
        self._check_day_reset()
        return self._state
    
    def _check_day_reset(self) -> None:
        """Reset if it's a new day."""
        today = date.today().isoformat()
        
        if self._state.current_date != today:
            if self._state.current_date:  # Not first run
                logger.info(f"📅 New day - resetting daily P&L (was ${self._state.daily_pnl_usd:.2f})")
            
            self._state.current_date = today
            self._state.daily_pnl_usd = 0.0
            self._state.trades_today = 0
            self._state.wins_today = 0
            self._state.losses_today = 0
            self._state.is_paused = False
            self._state.pause_reason = ""
            self._trades = []
            
            self._update_guard_state()
            self._save_state()
    
    def _update_guard_state(self) -> None:
        """Update warning levels and pause state."""
        pnl = self._state.daily_pnl_usd
        limit = self._loss_limit
        
        # Calculate percentage of limit used (for losses)
        if pnl < 0:
            pct_used = abs(pnl) / limit
        else:
            pct_used = 0
        
        self._state.pnl_pct_of_limit = pct_used
        
        # Determine warning level
        if pct_used >= 1.0:
            self._state.warning_level = "critical"
            self._state.is_paused = True
            self._state.pause_reason = f"Daily loss limit reached (${abs(pnl):.0f} / ${limit:.0f})"
            logger.error(f"🛑 DAILY LOSS LIMIT REACHED: ${pnl:.2f}")
            
        elif pct_used >= self._warning_pct:
            self._state.warning_level = "warning"
            self._state.is_paused = False
            self._state.pause_reason = ""
            
        elif pct_used >= self._caution_pct:
            self._state.warning_level = "caution"
            self._state.is_paused = False
            self._state.pause_reason = ""
            
        else:
            self._state.warning_level = "none"
            self._state.is_paused = False
            self._state.pause_reason = ""
    
    def get_remaining_risk(self) -> float:
        """Get remaining risk budget for the day."""
        pnl = self._state.daily_pnl_usd
        limit = self._loss_limit
        
        if pnl >= 0:
            return limit  # Full budget available
        else:
            return max(0, limit - abs(pnl))
    
    def override_pause(self, reason: str = "Manual override") -> None:
        """Override the pause (use with caution)."""
        if self._state.is_paused:
            logger.warning(f"⚠️ Loss limit pause overridden: {reason}")
            self._state.is_paused = False
            self._state.pause_reason = ""
    
    def get_state(self) -> LossGuardState:
        """Get current state."""
        return self._state
    
    def _load_state(self) -> None:
        """Load persisted state from file."""
        if not os.path.exists(self._persist_file):
            self._state.current_date = date.today().isoformat()
            return
        
        try:
            with open(self._persist_file, 'r') as f:
                data = json.load(f)
            
            # Only load if same day
            if data.get("date") == date.today().isoformat():
                self._state.daily_pnl_usd = data.get("pnl", 0)
                self._state.trades_today = data.get("trades", 0)
                self._state.wins_today = data.get("wins", 0)
                self._state.losses_today = data.get("losses", 0)
                self._state.current_date = data.get("date", "")
                self._update_guard_state()
                logger.info(f"Loaded daily P&L: ${self._state.daily_pnl_usd:.2f} ({self._state.trades_today} trades)")
            else:
                self._state.current_date = date.today().isoformat()
                
        except Exception as e:
            logger.warning(f"Failed to load persisted state: {e}")
            self._state.current_date = date.today().isoformat()
    
    def _save_state(self) -> None:
        """Save state to file."""
        try:
            os.makedirs(os.path.dirname(self._persist_file), exist_ok=True)
            
            data = {
                "date": self._state.current_date,
                "pnl": self._state.daily_pnl_usd,
                "trades": self._state.trades_today,
                "wins": self._state.wins_today,
                "losses": self._state.losses_today,
                "updated_at": datetime.utcnow().isoformat(),
            }
            
            with open(self._persist_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")
