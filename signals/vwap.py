"""
VWAP (Volume-Weighted Average Price) Signal Generator.

Calculates VWAP and deviation bands for BTC trading signals.
Primary signal for mean reversion and pullback entries.

Usage:
    vwap = VWAPCalculator()
    
    # Feed price and volume data
    vwap.update(price=67500, volume=100)
    
    # Get current state
    state = vwap.get_state()
    print(f"VWAP: {state.vwap}, Deviation: {state.deviation_sigma}σ")
    
    # Check zones
    if state.in_buy_zone:
        print("Price in BUY zone")
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from collections import deque
import math


@dataclass
class VWAPSample:
    """A single price/volume sample."""
    timestamp_ms: int
    price: float
    volume: float


@dataclass
class VWAPState:
    """Current VWAP state with deviation bands."""
    vwap: float = 0.0                      # Volume-weighted average price
    
    # Deviation from VWAP
    deviation_usd: float = 0.0             # Current price - VWAP
    deviation_pct: float = 0.0             # Deviation as percentage
    deviation_sigma: float = 0.0           # Deviation in standard deviations
    
    # Standard deviation of price from VWAP
    std_dev: float = 0.0
    
    # Bands (VWAP ± N standard deviations)
    upper_1sigma: float = 0.0
    lower_1sigma: float = 0.0
    upper_2sigma: float = 0.0
    lower_2sigma: float = 0.0
    
    # Zone detection
    zone: str = "neutral"                  # "buy", "sell", "extended_buy", "extended_sell", "neutral"
    in_buy_zone: bool = False              # Below -1σ
    in_sell_zone: bool = False             # Above +1σ
    is_extended: bool = False              # Beyond ±1.5σ
    
    # Volume metrics
    total_volume: float = 0.0
    volume_pace: float = 1.0               # Current volume vs expected (1.0 = normal)
    
    # Session info
    session_start_ms: int = 0
    samples_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            "vwap": round(self.vwap, 2),
            "deviation_usd": round(self.deviation_usd, 2),
            "deviation_pct": round(self.deviation_pct, 4),
            "deviation_sigma": round(self.deviation_sigma, 2),
            "std_dev": round(self.std_dev, 2),
            "upper_1sigma": round(self.upper_1sigma, 2),
            "lower_1sigma": round(self.lower_1sigma, 2),
            "upper_2sigma": round(self.upper_2sigma, 2),
            "lower_2sigma": round(self.lower_2sigma, 2),
            "zone": self.zone,
            "in_buy_zone": self.in_buy_zone,
            "in_sell_zone": self.in_sell_zone,
            "is_extended": self.is_extended,
            "total_volume": round(self.total_volume, 2),
            "samples_count": self.samples_count,
        }


class VWAPCalculator:
    """
    Calculates VWAP and deviation bands for trading signals.
    
    Two modes:
    1. Session VWAP: Resets at session start (24h or custom)
    2. Rolling VWAP: Continuous rolling window (e.g., 4 hours)
    
    Uses rolling mode by default for crypto (24/7 market).
    """
    
    def __init__(
        self,
        rolling_window_hours: float = 4.0,    # Rolling window for VWAP
        entry_sigma: float = 0.75,            # Enter trades at ±0.75σ (tighter for more signals)
        extended_sigma: float = 1.25,         # Don't chase beyond ±1.25σ
        sample_interval_ms: int = 1000,       # Sample rate
        min_samples_for_signal: int = 20,     # Faster warmup (was 100)
    ):
        self._window_ms = int(rolling_window_hours * 3600 * 1000)
        self._entry_sigma = entry_sigma
        self._extended_sigma = extended_sigma
        self._sample_interval_ms = sample_interval_ms
        self._min_samples = min_samples_for_signal
        
        # Sample buffer
        max_samples = int(rolling_window_hours * 3600) + 100
        self._samples: deque[VWAPSample] = deque(maxlen=max_samples)
        self._last_sample_time: int = 0
        
        # Current state
        self._state = VWAPState()
        
        # Running totals for efficient calculation
        self._cumulative_pv: float = 0.0     # Cumulative (price * volume)
        self._cumulative_v: float = 0.0      # Cumulative volume
        self._cumulative_pv2: float = 0.0    # For variance calculation
    
    def update(self, price: float, volume: float = 1.0) -> VWAPState:
        """
        Update VWAP with new price and volume.
        
        Args:
            price: Current BTC price
            volume: Trade volume (can use 1.0 if volume not available)
            
        Returns:
            Current VWAPState
        """
        now_ms = int(time.time() * 1000)
        
        # Rate limiting
        if now_ms - self._last_sample_time < self._sample_interval_ms:
            return self._state
        
        # Add sample
        sample = VWAPSample(timestamp_ms=now_ms, price=price, volume=volume)
        self._samples.append(sample)
        self._last_sample_time = now_ms
        
        # Initialize session start if needed
        if self._state.session_start_ms == 0:
            self._state.session_start_ms = now_ms
        
        # Recalculate VWAP (full recalc for rolling window)
        self._calculate_vwap(price, now_ms)
        
        return self._state
    
    def _calculate_vwap(self, current_price: float, now_ms: int) -> None:
        """Calculate VWAP and bands from samples in window."""
        # Filter samples within window
        cutoff = now_ms - self._window_ms
        samples_in_window = [s for s in self._samples if s.timestamp_ms >= cutoff]
        
        if len(samples_in_window) < 10:
            # Not enough data
            self._state.vwap = current_price
            self._state.samples_count = len(samples_in_window)
            return
        
        # Calculate VWAP
        total_pv = sum(s.price * s.volume for s in samples_in_window)
        total_v = sum(s.volume for s in samples_in_window)
        
        if total_v == 0:
            self._state.vwap = current_price
            return
        
        vwap = total_pv / total_v
        self._state.vwap = vwap
        self._state.total_volume = total_v
        self._state.samples_count = len(samples_in_window)
        
        # Calculate standard deviation from VWAP
        squared_diffs = [(s.price - vwap) ** 2 * s.volume for s in samples_in_window]
        variance = sum(squared_diffs) / total_v
        std_dev = math.sqrt(variance) if variance > 0 else 0
        
        self._state.std_dev = std_dev
        
        # Calculate bands
        if std_dev > 0:
            self._state.upper_1sigma = vwap + std_dev
            self._state.lower_1sigma = vwap - std_dev
            self._state.upper_2sigma = vwap + 2 * std_dev
            self._state.lower_2sigma = vwap - 2 * std_dev
        else:
            # No variance yet
            self._state.upper_1sigma = vwap
            self._state.lower_1sigma = vwap
            self._state.upper_2sigma = vwap
            self._state.lower_2sigma = vwap
        
        # Calculate deviation
        self._state.deviation_usd = current_price - vwap
        
        if vwap > 0:
            self._state.deviation_pct = (current_price - vwap) / vwap
        
        if std_dev > 0:
            self._state.deviation_sigma = (current_price - vwap) / std_dev
        else:
            self._state.deviation_sigma = 0
        
        # Determine zone
        self._determine_zone(current_price)
    
    def _determine_zone(self, price: float) -> None:
        """Determine which zone the price is in."""
        sigma = self._state.deviation_sigma
        entry = self._entry_sigma
        extended = self._extended_sigma
        
        # Need minimum samples before signaling zones
        if self._state.samples_count < self._min_samples:
            self._state.zone = "warming_up"
            self._state.in_buy_zone = False
            self._state.in_sell_zone = False
            self._state.is_extended = False
            return
        
        # Determine zone
        if sigma <= -extended:
            self._state.zone = "extended_buy"
            self._state.in_buy_zone = True
            self._state.in_sell_zone = False
            self._state.is_extended = True
        elif sigma <= -entry:
            self._state.zone = "buy"
            self._state.in_buy_zone = True
            self._state.in_sell_zone = False
            self._state.is_extended = False
        elif sigma >= extended:
            self._state.zone = "extended_sell"
            self._state.in_buy_zone = False
            self._state.in_sell_zone = True
            self._state.is_extended = True
        elif sigma >= entry:
            self._state.zone = "sell"
            self._state.in_buy_zone = False
            self._state.in_sell_zone = True
            self._state.is_extended = False
        else:
            self._state.zone = "neutral"
            self._state.in_buy_zone = False
            self._state.in_sell_zone = False
            self._state.is_extended = False
    
    def get_state(self) -> VWAPState:
        """Get current VWAP state."""
        return self._state
    
    def get_entry_levels(self, current_price: float) -> dict:
        """
        Get recommended entry levels based on current VWAP.
        
        Returns dict with:
        - buy_zone: (low, high) price range for buy entries
        - sell_zone: (low, high) price range for sell entries
        - distance_to_buy: price must drop this much to enter buy zone
        - distance_to_sell: price must rise this much to enter sell zone
        """
        vwap = self._state.vwap
        std = self._state.std_dev
        
        if vwap == 0 or std == 0:
            return {
                "buy_zone": (0, 0),
                "sell_zone": (0, 0),
                "distance_to_buy": 0,
                "distance_to_sell": 0,
            }
        
        buy_upper = vwap - self._entry_sigma * std
        buy_lower = vwap - self._extended_sigma * std
        
        sell_lower = vwap + self._entry_sigma * std
        sell_upper = vwap + self._extended_sigma * std
        
        distance_to_buy = buy_upper - current_price if current_price > buy_upper else 0
        distance_to_sell = sell_lower - current_price if current_price < sell_lower else 0
        
        return {
            "buy_zone": (buy_lower, buy_upper),
            "sell_zone": (sell_lower, sell_upper),
            "distance_to_buy": abs(distance_to_buy),
            "distance_to_sell": abs(distance_to_sell),
        }
    
    def reset(self) -> None:
        """Reset VWAP calculator (new session)."""
        self._samples.clear()
        self._state = VWAPState()
        self._last_sample_time = 0
        self._cumulative_pv = 0.0
        self._cumulative_v = 0.0
        self._cumulative_pv2 = 0.0
