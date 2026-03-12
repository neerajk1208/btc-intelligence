"""
Volatility Analysis Module for BTC Intelligence.

Provides multiple volatility metrics:
- ATR (Average True Range)
- Bollinger Band width
- Historical volatility percentile
- Range compression/expansion detection

Used to:
1. Adjust position sizing (reduce size in high vol)
2. Detect regime changes (compression → expansion = breakout coming)
3. Pause trading during extreme volatility
"""
from __future__ import annotations

import time
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from collections import deque


@dataclass
class Candle:
    """OHLCV candle data."""
    timestamp_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class VolatilityState:
    """Current volatility metrics."""
    # ATR metrics
    atr: float = 0.0                       # Average True Range in USD
    atr_pct: float = 0.0                   # ATR as percentage of price
    atr_bps: float = 0.0                   # ATR in basis points
    
    # ATR ratio (current vs historical)
    atr_baseline: float = 0.0              # Historical average ATR
    atr_ratio: float = 1.0                 # Current ATR / baseline
    
    # Bollinger metrics
    bb_upper: float = 0.0                  # Upper band (20 SMA + 2 std)
    bb_middle: float = 0.0                 # Middle (20 SMA)
    bb_lower: float = 0.0                  # Lower band (20 SMA - 2 std)
    bb_width: float = 0.0                  # Band width as % of middle
    bb_percentile: float = 50.0            # Where price is within bands (0-100)
    
    # Historical volatility
    realized_vol_daily: float = 0.0        # Annualized realized volatility
    vol_percentile_7d: float = 50.0        # Current vol percentile over 7 days
    
    # Squeeze detection
    is_squeeze: bool = False               # Bollinger squeeze (low vol compression)
    squeeze_strength: float = 0.0          # 0-1, higher = tighter squeeze
    
    # Volatility regime
    vol_regime: str = "NORMAL"             # "LOW", "NORMAL", "HIGH", "EXTREME"
    
    # Actionable signals
    size_multiplier: float = 1.0           # Adjust size based on volatility
    spread_multiplier: float = 1.0         # Adjust spread/entry based on vol
    should_pause: bool = False             # True if vol too extreme
    
    def to_dict(self) -> dict:
        return {
            "atr": round(self.atr, 2),
            "atr_pct": round(self.atr_pct, 4),
            "atr_bps": round(self.atr_bps, 2),
            "atr_ratio": round(self.atr_ratio, 2),
            "bb_upper": round(self.bb_upper, 2),
            "bb_middle": round(self.bb_middle, 2),
            "bb_lower": round(self.bb_lower, 2),
            "bb_width": round(self.bb_width, 4),
            "bb_percentile": round(self.bb_percentile, 1),
            "is_squeeze": self.is_squeeze,
            "squeeze_strength": round(self.squeeze_strength, 2),
            "vol_regime": self.vol_regime,
            "size_multiplier": round(self.size_multiplier, 2),
            "spread_multiplier": round(self.spread_multiplier, 2),
            "should_pause": self.should_pause,
        }


class VolatilityAnalyzer:
    """
    Analyzes volatility from price data and provides trading adjustments.
    
    Can work with:
    1. Raw price ticks (builds candles internally)
    2. Pre-built candles (from API)
    
    Usage:
        analyzer = VolatilityAnalyzer()
        
        # Feed prices continuously
        state = analyzer.update_price(price=67500)
        
        # Or feed candles
        state = analyzer.update_candle(candle)
        
        # Use for sizing
        if state.vol_regime == "HIGH":
            size *= state.size_multiplier  # Reduce size
    """
    
    def __init__(
        self,
        atr_period: int = 14,
        bb_period: int = 20,
        bb_std_dev: float = 2.0,
        candle_interval_ms: int = 300000,     # 5-minute candles
        squeeze_bb_width_threshold: float = 0.02,  # BB width < 2% = squeeze
        extreme_atr_ratio: float = 1.75,      # ATR > 1.75x baseline = extreme (conservative)
        high_atr_ratio: float = 1.3,          # ATR > 1.3x baseline = high
        low_atr_ratio: float = 0.6,           # ATR < 0.6x baseline = low
        baseline_lookback_hours: int = 168,   # 7 days for baseline
    ):
        self._atr_period = atr_period
        self._bb_period = bb_period
        self._bb_std = bb_std_dev
        self._candle_interval = candle_interval_ms
        self._squeeze_threshold = squeeze_bb_width_threshold
        self._extreme_ratio = extreme_atr_ratio
        self._high_ratio = high_atr_ratio
        self._low_ratio = low_atr_ratio
        
        # Candle buffer
        candles_needed = max(atr_period, bb_period) + 50
        self._candles: deque[Candle] = deque(maxlen=candles_needed)
        
        # Current candle being built
        self._current_candle: Optional[Candle] = None
        self._candle_start_ms: int = 0
        
        # ATR history for baseline
        baseline_candles = (baseline_lookback_hours * 3600 * 1000) // candle_interval_ms
        self._atr_history: deque[float] = deque(maxlen=baseline_candles)
        
        # BB width history for percentile
        self._bb_width_history: deque[float] = deque(maxlen=baseline_candles)
        
        # State
        self._state = VolatilityState()
    
    def update_price(self, price: float, volume: float = 0.0) -> VolatilityState:
        """
        Update with a price tick. Builds candles internally.
        
        Args:
            price: Current price
            volume: Trade volume (optional)
            
        Returns:
            Current VolatilityState
        """
        now_ms = int(time.time() * 1000)
        
        # Start new candle if needed
        if self._current_candle is None or \
           now_ms - self._candle_start_ms >= self._candle_interval:
            
            # Close current candle
            if self._current_candle is not None:
                self._candles.append(self._current_candle)
                self._recalculate()
            
            # Start new candle
            self._candle_start_ms = now_ms
            self._current_candle = Candle(
                timestamp_ms=now_ms,
                open=price,
                high=price,
                low=price,
                close=price,
                volume=volume
            )
        else:
            # Update current candle
            self._current_candle.high = max(self._current_candle.high, price)
            self._current_candle.low = min(self._current_candle.low, price)
            self._current_candle.close = price
            self._current_candle.volume += volume
        
        return self._state
    
    def update_candle(self, candle: Candle) -> VolatilityState:
        """
        Update with a completed candle (from API).
        
        Args:
            candle: Completed OHLCV candle
            
        Returns:
            Current VolatilityState
        """
        self._candles.append(candle)
        self._recalculate()
        return self._state
    
    def load_candles(self, candles: List[Candle]) -> VolatilityState:
        """
        Load historical candles (for initialization).
        
        Args:
            candles: List of historical candles, oldest first
            
        Returns:
            Current VolatilityState
        """
        for candle in candles:
            self._candles.append(candle)
        self._recalculate()
        return self._state
    
    def _recalculate(self) -> None:
        """Recalculate all volatility metrics."""
        if len(self._candles) < 2:
            return
        
        self._calculate_atr()
        self._calculate_bollinger()
        self._calculate_squeeze()
        self._determine_regime()
        self._calculate_adjustments()
    
    def _calculate_atr(self) -> None:
        """Calculate Average True Range."""
        candles = list(self._candles)
        if len(candles) < self._atr_period + 1:
            return
        
        # Calculate True Ranges
        true_ranges = []
        for i in range(1, len(candles)):
            prev_close = candles[i - 1].close
            current = candles[i]
            
            # True Range = max(H-L, |H-prev_close|, |L-prev_close|)
            tr = max(
                current.high - current.low,
                abs(current.high - prev_close),
                abs(current.low - prev_close)
            )
            true_ranges.append(tr)
        
        # ATR = SMA of True Ranges
        recent_trs = true_ranges[-self._atr_period:]
        atr = sum(recent_trs) / len(recent_trs)
        
        self._state.atr = atr
        
        # ATR as percentage/bps
        current_price = candles[-1].close
        if current_price > 0:
            self._state.atr_pct = atr / current_price
            self._state.atr_bps = self._state.atr_pct * 10000
        
        # Update ATR history and baseline
        self._atr_history.append(atr)
        
        if len(self._atr_history) >= 10:
            self._state.atr_baseline = sum(self._atr_history) / len(self._atr_history)
            if self._state.atr_baseline > 0:
                self._state.atr_ratio = atr / self._state.atr_baseline
    
    def _calculate_bollinger(self) -> None:
        """Calculate Bollinger Bands."""
        candles = list(self._candles)
        if len(candles) < self._bb_period:
            return
        
        # Get recent closes
        closes = [c.close for c in candles[-self._bb_period:]]
        
        # SMA
        sma = sum(closes) / len(closes)
        
        # Standard deviation
        variance = sum((c - sma) ** 2 for c in closes) / len(closes)
        std_dev = math.sqrt(variance) if variance > 0 else 0
        
        # Bands
        self._state.bb_middle = sma
        self._state.bb_upper = sma + self._bb_std * std_dev
        self._state.bb_lower = sma - self._bb_std * std_dev
        
        # Band width
        if sma > 0:
            self._state.bb_width = (self._state.bb_upper - self._state.bb_lower) / sma
        
        # Price percentile within bands
        current_price = closes[-1]
        band_range = self._state.bb_upper - self._state.bb_lower
        if band_range > 0:
            percentile = (current_price - self._state.bb_lower) / band_range * 100
            self._state.bb_percentile = max(0, min(100, percentile))
        
        # Update BB width history
        self._bb_width_history.append(self._state.bb_width)
    
    def _calculate_squeeze(self) -> None:
        """Detect Bollinger squeeze (volatility compression)."""
        if len(self._bb_width_history) < 20:
            self._state.is_squeeze = False
            self._state.squeeze_strength = 0
            return
        
        current_width = self._state.bb_width
        
        # Squeeze if width below threshold
        self._state.is_squeeze = current_width < self._squeeze_threshold
        
        # Squeeze strength = how tight relative to recent history
        if self._bb_width_history:
            avg_width = sum(self._bb_width_history) / len(self._bb_width_history)
            if avg_width > 0:
                # Lower ratio = tighter squeeze
                ratio = current_width / avg_width
                # Invert so higher = tighter squeeze
                self._state.squeeze_strength = max(0, min(1, 1 - ratio))
    
    def _determine_regime(self) -> None:
        """Determine volatility regime."""
        ratio = self._state.atr_ratio
        
        if ratio >= self._extreme_ratio:
            self._state.vol_regime = "EXTREME"
        elif ratio >= self._high_ratio:
            self._state.vol_regime = "HIGH"
        elif ratio <= self._low_ratio:
            self._state.vol_regime = "LOW"
        else:
            self._state.vol_regime = "NORMAL"
    
    def _calculate_adjustments(self) -> None:
        """Calculate size and spread adjustments based on volatility."""
        regime = self._state.vol_regime
        
        if regime == "EXTREME":
            self._state.size_multiplier = 0.25    # Quarter size
            self._state.spread_multiplier = 2.0   # Double spread requirement
            self._state.should_pause = True
        elif regime == "HIGH":
            self._state.size_multiplier = 0.5     # Half size
            self._state.spread_multiplier = 1.5   # 1.5x spread
            self._state.should_pause = False
        elif regime == "LOW":
            self._state.size_multiplier = 1.25    # Slightly larger (good for MR)
            self._state.spread_multiplier = 0.8   # Tighter entries OK
            self._state.should_pause = False
        else:  # NORMAL
            self._state.size_multiplier = 1.0
            self._state.spread_multiplier = 1.0
            self._state.should_pause = False
    
    def get_state(self) -> VolatilityState:
        """Get current volatility state."""
        return self._state
    
    def reset(self) -> None:
        """Reset analyzer."""
        self._candles.clear()
        self._atr_history.clear()
        self._bb_width_history.clear()
        self._current_candle = None
        self._candle_start_ms = 0
        self._state = VolatilityState()
