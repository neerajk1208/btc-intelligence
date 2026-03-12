"""
News/Volatility Spike Guard for BTC Intelligence.

Detects sudden price moves that indicate news events.
Pauses trading during these volatile periods.

This is a reactive guard - it detects spikes after they happen
and pauses to avoid chasing or getting caught in whipsaw.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional
from collections import deque
import logging

logger = logging.getLogger(__name__)


@dataclass
class PriceTick:
    """A price sample with timestamp."""
    timestamp_ms: int
    price: float


@dataclass
class SpikeGuardState:
    """Current state of spike detection."""
    # Spike detection
    spike_detected: bool = False
    spike_direction: Optional[str] = None  # "up" or "down"
    spike_magnitude_pct: float = 0.0
    
    # Pause state
    is_paused: bool = False
    pause_reason: str = ""
    pause_remaining_seconds: float = 0.0
    
    # Recent move stats
    move_15min_pct: float = 0.0
    move_5min_pct: float = 0.0
    move_1min_pct: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "spike_detected": self.spike_detected,
            "spike_direction": self.spike_direction,
            "spike_magnitude_pct": round(self.spike_magnitude_pct, 3),
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
            "pause_remaining_seconds": round(self.pause_remaining_seconds, 0),
            "move_15min_pct": round(self.move_15min_pct, 3),
            "move_5min_pct": round(self.move_5min_pct, 3),
            "move_1min_pct": round(self.move_1min_pct, 3),
        }


class SpikeGuard:
    """
    Detects sudden price spikes and pauses trading.
    
    A "spike" is defined as a price move exceeding a threshold
    within a short time window. This typically indicates:
    - Breaking news
    - Large liquidation cascade
    - Flash crash/rally
    
    During these events, normal signals are unreliable.
    
    Usage:
        guard = SpikeGuard()
        
        # Feed prices continuously
        state = guard.update(price=67500)
        
        if state.is_paused:
            print(f"Spike detected! Paused for {state.pause_remaining_seconds}s")
    """
    
    def __init__(
        self,
        spike_threshold_pct: float = 1.5,    # 1.5% move = spike (more conservative)
        spike_window_minutes: float = 15.0,  # Within 15 minutes
        pause_duration_minutes: float = 15.0, # Pause for 15 minutes (shorter recovery)
        sample_interval_ms: int = 1000,
    ):
        self._threshold = spike_threshold_pct / 100
        self._window_ms = int(spike_window_minutes * 60 * 1000)
        self._pause_duration_ms = int(pause_duration_minutes * 60 * 1000)
        self._sample_interval = sample_interval_ms
        
        # Price buffer (store ~20 minutes of data)
        max_samples = int(spike_window_minutes * 60 * 1.5)
        self._prices: deque[PriceTick] = deque(maxlen=max_samples)
        self._last_sample_time: int = 0
        
        # Pause tracking
        self._pause_start_time: Optional[int] = None
        
        # State
        self._state = SpikeGuardState()
    
    def update(self, price: float) -> SpikeGuardState:
        """
        Update with new price.
        
        Args:
            price: Current BTC price
            
        Returns:
            SpikeGuardState with detection results
        """
        now_ms = int(time.time() * 1000)
        
        # Rate limiting
        if now_ms - self._last_sample_time < self._sample_interval:
            # Still update pause timer
            self._update_pause_timer(now_ms)
            return self._state
        
        # Add sample
        self._prices.append(PriceTick(timestamp_ms=now_ms, price=price))
        self._last_sample_time = now_ms
        
        # Calculate recent moves
        self._calculate_moves(now_ms, price)
        
        # Check for spike
        self._check_spike(now_ms)
        
        # Update pause timer
        self._update_pause_timer(now_ms)
        
        return self._state
    
    def _calculate_moves(self, now_ms: int, current_price: float) -> None:
        """Calculate price moves over various windows."""
        windows = [
            (1, "move_1min_pct"),
            (5, "move_5min_pct"),
            (15, "move_15min_pct"),
        ]
        
        for minutes, attr in windows:
            cutoff = now_ms - (minutes * 60 * 1000)
            old_prices = [p for p in self._prices if p.timestamp_ms <= cutoff]
            
            if old_prices:
                old_price = old_prices[-1].price
                if old_price > 0:
                    move_pct = (current_price - old_price) / old_price
                    setattr(self._state, attr, move_pct)
    
    def _check_spike(self, now_ms: int) -> None:
        """Check if a spike has occurred."""
        # Skip if already paused
        if self._state.is_paused:
            return
        
        # Get price from start of window
        cutoff = now_ms - self._window_ms
        old_prices = [p for p in self._prices if p.timestamp_ms <= cutoff]
        
        if not old_prices:
            return
        
        old_price = old_prices[-1].price
        current_price = self._prices[-1].price if self._prices else 0
        
        if old_price <= 0 or current_price <= 0:
            return
        
        # Calculate move
        move = (current_price - old_price) / old_price
        
        # Check threshold
        if abs(move) >= self._threshold:
            self._state.spike_detected = True
            self._state.spike_direction = "up" if move > 0 else "down"
            self._state.spike_magnitude_pct = abs(move)
            
            # Trigger pause
            self._pause_start_time = now_ms
            self._state.is_paused = True
            self._state.pause_reason = f"Price spike {self._state.spike_direction} {abs(move)*100:.1f}% in {self._window_ms/60000:.0f} min"
            self._state.pause_remaining_seconds = self._pause_duration_ms / 1000
            
            logger.warning(f"⚠️ SPIKE DETECTED: {self._state.pause_reason}")
    
    def _update_pause_timer(self, now_ms: int) -> None:
        """Update pause timer and release if expired."""
        if not self._pause_start_time:
            self._state.is_paused = False
            self._state.pause_remaining_seconds = 0
            return
        
        elapsed = now_ms - self._pause_start_time
        remaining = self._pause_duration_ms - elapsed
        
        if remaining <= 0:
            # Pause expired
            self._pause_start_time = None
            self._state.is_paused = False
            self._state.pause_reason = ""
            self._state.pause_remaining_seconds = 0
            self._state.spike_detected = False
            self._state.spike_direction = None
            self._state.spike_magnitude_pct = 0
            logger.info("✅ Spike pause ended")
        else:
            self._state.pause_remaining_seconds = remaining / 1000
    
    def force_pause(self, duration_minutes: float, reason: str = "Manual pause") -> None:
        """Manually trigger a pause."""
        now_ms = int(time.time() * 1000)
        self._pause_start_time = now_ms
        self._state.is_paused = True
        self._state.pause_reason = reason
        
        # Temporarily override pause duration
        self._pause_duration_ms = int(duration_minutes * 60 * 1000)
        self._state.pause_remaining_seconds = duration_minutes * 60
        
        logger.warning(f"⏸️ Manual pause: {reason} ({duration_minutes} min)")
    
    def cancel_pause(self) -> None:
        """Cancel current pause."""
        if self._state.is_paused:
            logger.info("Pause cancelled manually")
            self._pause_start_time = None
            self._state.is_paused = False
            self._state.pause_reason = ""
            self._state.pause_remaining_seconds = 0
    
    def get_state(self) -> SpikeGuardState:
        """Get current state."""
        return self._state
    
    def reset(self) -> None:
        """Reset guard state."""
        self._prices.clear()
        self._pause_start_time = None
        self._state = SpikeGuardState()
        self._last_sample_time = 0
