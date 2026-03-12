"""
Position Tracker for BTC Intelligence.

Tracks manually entered BTC positions on Definitive.
Since this bot doesn't execute trades, position is entered manually
and used to calibrate recommendations.

Persists state to JSON for crash recovery.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class PositionEntry:
    """A single trade/position entry."""
    timestamp_ms: int
    side: str                    # "long" or "short"
    size_btc: float
    size_usd: float
    entry_price: float
    notes: str = ""
    
    def to_dict(self) -> dict:
        return {
            "timestamp_ms": self.timestamp_ms,
            "side": self.side,
            "size_btc": self.size_btc,
            "size_usd": self.size_usd,
            "entry_price": self.entry_price,
            "notes": self.notes,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "PositionEntry":
        return cls(**data)


@dataclass 
class Position:
    """Current aggregate position."""
    side: Optional[str] = None   # "long", "short", or None (flat)
    size_btc: float = 0.0        # Absolute size
    size_usd: float = 0.0        # Dollar value at entry
    avg_entry_price: float = 0.0 # Weighted average entry
    entries: List[PositionEntry] = field(default_factory=list)
    
    @property
    def is_flat(self) -> bool:
        return abs(self.size_btc) < 0.0001
    
    @property
    def is_long(self) -> bool:
        return self.side == "long" and not self.is_flat
    
    @property
    def is_short(self) -> bool:
        return self.side == "short" and not self.is_flat
    
    def to_dict(self) -> dict:
        return {
            "side": self.side,
            "size_btc": self.size_btc,
            "size_usd": self.size_usd,
            "avg_entry_price": self.avg_entry_price,
            "entries": [e.to_dict() for e in self.entries],
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Position":
        entries = [PositionEntry.from_dict(e) for e in data.get("entries", [])]
        return cls(
            side=data.get("side"),
            size_btc=data.get("size_btc", 0),
            size_usd=data.get("size_usd", 0),
            avg_entry_price=data.get("avg_entry_price", 0),
            entries=entries,
        )


@dataclass
class PositionState:
    """State for UI display."""
    position: Position
    current_price: float = 0.0
    unrealized_pnl_usd: float = 0.0
    unrealized_pnl_pct: float = 0.0
    value_at_current: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "side": self.position.side,
            "size_btc": round(self.position.size_btc, 6),
            "size_usd": round(self.position.size_usd, 2),
            "avg_entry": round(self.position.avg_entry_price, 2),
            "current_price": round(self.current_price, 2),
            "unrealized_pnl": round(self.unrealized_pnl_usd, 2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 2),
            "value_at_current": round(self.value_at_current, 2),
            "is_flat": self.position.is_flat,
        }


class PositionTracker:
    """
    Tracks BTC position with manual entry.
    
    Since trades are executed manually on Definitive, this tracker
    requires manual position updates. State is persisted to JSON.
    
    Usage:
        tracker = PositionTracker()
        
        # Enter a position
        tracker.add_entry("long", size_btc=0.15, entry_price=67500)
        
        # Check state with current price
        state = tracker.get_state(current_price=68000)
        print(f"P&L: ${state.unrealized_pnl_usd:.2f}")
        
        # Close position
        tracker.close_position()
    """
    
    def __init__(self, persist_file: Optional[str] = None):
        if persist_file is None:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            persist_file = os.path.join(base_dir, "data", "position.json")
        
        self._persist_file = persist_file
        self._position = Position()
        
        # Realized P&L tracking
        self._realized_pnl_usd = 0.0
        self._trade_history: List[Dict] = []
        
        self._load()
    
    def add_entry(
        self,
        side: str,
        size_btc: float = 0.0,
        size_usd: float = 0.0,
        entry_price: float = 0.0,
        notes: str = "",
    ) -> Position:
        """
        Add a position entry.
        
        Can specify either size_btc or size_usd (will calculate the other).
        
        Args:
            side: "long" or "short"
            size_btc: Size in BTC
            size_usd: Size in USD
            entry_price: Entry price
            notes: Optional notes
            
        Returns:
            Updated Position
        """
        side = side.lower()
        if side not in ("long", "short"):
            raise ValueError("Side must be 'long' or 'short'")
        
        if entry_price <= 0:
            raise ValueError("Entry price must be positive")
        
        # Calculate missing size
        if size_btc > 0 and size_usd <= 0:
            size_usd = size_btc * entry_price
        elif size_usd > 0 and size_btc <= 0:
            size_btc = size_usd / entry_price
        
        if size_btc <= 0:
            raise ValueError("Size must be positive")
        
        # Create entry
        entry = PositionEntry(
            timestamp_ms=int(time.time() * 1000),
            side=side,
            size_btc=size_btc,
            size_usd=size_usd,
            entry_price=entry_price,
            notes=notes,
        )
        
        # Update aggregate position
        if self._position.is_flat:
            # New position
            self._position.side = side
            self._position.size_btc = size_btc
            self._position.size_usd = size_usd
            self._position.avg_entry_price = entry_price
            
        elif self._position.side == side:
            # Adding to existing position - average in
            old_size = self._position.size_btc
            new_size = old_size + size_btc
            
            # Weighted average entry
            self._position.avg_entry_price = (
                (old_size * self._position.avg_entry_price + size_btc * entry_price)
                / new_size
            )
            self._position.size_btc = new_size
            self._position.size_usd += size_usd
            
        else:
            # Reducing/flipping position
            if size_btc >= self._position.size_btc:
                # Closing or flipping
                realized = self._calculate_realized_pnl(
                    close_size=self._position.size_btc,
                    close_price=entry_price
                )
                self._realized_pnl_usd += realized
                
                remaining = size_btc - self._position.size_btc
                if remaining > 0.0001:
                    # Flip to other side
                    self._position.side = side
                    self._position.size_btc = remaining
                    self._position.size_usd = remaining * entry_price
                    self._position.avg_entry_price = entry_price
                else:
                    # Flat
                    self._position = Position()
            else:
                # Partial close
                realized = self._calculate_realized_pnl(
                    close_size=size_btc,
                    close_price=entry_price
                )
                self._realized_pnl_usd += realized
                self._position.size_btc -= size_btc
                self._position.size_usd = self._position.size_btc * self._position.avg_entry_price
        
        self._position.entries.append(entry)
        self._save()
        
        logger.info(f"Position updated: {self._position.side} {self._position.size_btc:.6f} BTC @ ${self._position.avg_entry_price:.2f}")
        
        return self._position
    
    def _calculate_realized_pnl(self, close_size: float, close_price: float) -> float:
        """Calculate realized P&L for closing a portion."""
        if self._position.is_flat:
            return 0.0
        
        entry = self._position.avg_entry_price
        
        if self._position.side == "long":
            pnl = (close_price - entry) * close_size
        else:  # short
            pnl = (entry - close_price) * close_size
        
        # Record trade
        self._trade_history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "side": self._position.side,
            "size_btc": close_size,
            "entry_price": entry,
            "exit_price": close_price,
            "pnl_usd": pnl,
        })
        
        return pnl
    
    def close_position(self, exit_price: float = 0.0, notes: str = "") -> float:
        """
        Close entire position.
        
        Args:
            exit_price: Exit price (required)
            notes: Optional notes
            
        Returns:
            Realized P&L in USD
        """
        if self._position.is_flat:
            logger.info("Position already flat")
            return 0.0
        
        if exit_price <= 0:
            raise ValueError("Exit price required")
        
        # Calculate P&L
        pnl = self._calculate_realized_pnl(
            close_size=self._position.size_btc,
            close_price=exit_price
        )
        self._realized_pnl_usd += pnl
        
        # Log
        logger.info(f"Position closed @ ${exit_price:.2f} - P&L: ${pnl:.2f}")
        
        # Reset position
        self._position = Position()
        self._save()
        
        return pnl
    
    def set_position(
        self,
        side: Optional[str],
        size_btc: float,
        avg_entry: float,
    ) -> Position:
        """
        Manually set position (override).
        
        Use when syncing with Definitive state.
        """
        if side is None or size_btc <= 0:
            self._position = Position()
        else:
            self._position = Position(
                side=side,
                size_btc=size_btc,
                size_usd=size_btc * avg_entry,
                avg_entry_price=avg_entry,
            )
        
        self._save()
        return self._position
    
    def get_state(self, current_price: float = 0.0) -> PositionState:
        """
        Get current position state with P&L.
        
        Args:
            current_price: Current BTC price
            
        Returns:
            PositionState with P&L calculations
        """
        state = PositionState(
            position=self._position,
            current_price=current_price,
        )
        
        if not self._position.is_flat and current_price > 0:
            entry = self._position.avg_entry_price
            size = self._position.size_btc
            
            if self._position.side == "long":
                state.unrealized_pnl_usd = (current_price - entry) * size
            else:  # short
                state.unrealized_pnl_usd = (entry - current_price) * size
            
            if self._position.size_usd > 0:
                state.unrealized_pnl_pct = (state.unrealized_pnl_usd / self._position.size_usd) * 100
            
            state.value_at_current = size * current_price
        
        return state
    
    def get_position(self) -> Position:
        """Get current position."""
        return self._position
    
    def get_realized_pnl(self) -> float:
        """Get total realized P&L."""
        return self._realized_pnl_usd
    
    def get_trade_history(self) -> List[Dict]:
        """Get trade history."""
        return self._trade_history.copy()
    
    def _load(self) -> None:
        """Load state from file."""
        if not os.path.exists(self._persist_file):
            return
        
        try:
            with open(self._persist_file, 'r') as f:
                data = json.load(f)
            
            if "position" in data:
                self._position = Position.from_dict(data["position"])
            
            self._realized_pnl_usd = data.get("realized_pnl", 0)
            self._trade_history = data.get("trade_history", [])
            
            if not self._position.is_flat:
                logger.info(
                    f"Loaded position: {self._position.side} "
                    f"{self._position.size_btc:.6f} BTC @ ${self._position.avg_entry_price:.2f}"
                )
                
        except Exception as e:
            logger.warning(f"Failed to load position: {e}")
    
    def _save(self) -> None:
        """Save state to file."""
        try:
            os.makedirs(os.path.dirname(self._persist_file), exist_ok=True)
            
            data = {
                "position": self._position.to_dict(),
                "realized_pnl": self._realized_pnl_usd,
                "trade_history": self._trade_history[-100:],  # Keep last 100 trades
                "updated_at": datetime.utcnow().isoformat(),
            }
            
            with open(self._persist_file, 'w') as f:
                json.dump(data, f, indent=2)
                
        except Exception as e:
            logger.warning(f"Failed to save position: {e}")
