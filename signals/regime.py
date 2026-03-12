"""
Market Regime Detection for BTC Intelligence.

Detects market regime and provides regime-aware signals for manual trading.

Regimes:
- TRENDING_UP: Price making higher highs/lows, buy pullbacks
- TRENDING_DOWN: Price making lower lows/highs, sell rallies
- CHOPPY: No clear direction, mean reversion
- NEWS_SHOCK: Extreme volatility, sit out

Adapted from trade repo bot/strategies/regime.py - simplified for recommendations.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from collections import deque
from enum import Enum


class MarketRegime(str, Enum):
    """Market regime states."""
    CHOPPY = "choppy"               # Ranging, no clear direction
    TRENDING_UP = "trending_up"     # Price trending up
    TRENDING_DOWN = "trending_down" # Price trending down
    NEWS_SHOCK = "news_shock"       # Extreme volatility, sit out


@dataclass
class PriceSample:
    """A single price sample with timestamp."""
    timestamp_ms: int
    price: float


@dataclass
class SwingPoint:
    """A detected swing high or low."""
    timestamp_ms: int
    price: float
    is_high: bool  # True = swing high, False = swing low


@dataclass
class RegimeState:
    """Current market regime state with all relevant metrics."""
    regime: MarketRegime = MarketRegime.CHOPPY
    
    # Momentum metrics
    momentum_bps: float = 0.0           # Price change over lookback (in bps)
    momentum_direction: Optional[str] = None  # "up" or "down" or None
    
    # Volatility metrics
    atr_bps: float = 0.0                # ATR in basis points
    atr_ratio: float = 1.0              # Current ATR / baseline ATR
    volatility_mode: str = "NORMAL"     # "LOW", "NORMAL", "HIGH", "EXTREME"
    
    # Price range metrics
    range_bps: float = 0.0              # High-low range in lookback (in bps)
    
    # Structure metrics (HH/HL/LL/LH)
    recent_highs: List[float] = field(default_factory=list)  # Last 3-5 swing highs
    recent_lows: List[float] = field(default_factory=list)   # Last 3-5 swing lows
    structure_signal: str = "neutral"   # "bullish", "bearish", or "neutral"
    
    # Confidence
    confidence: float = 0.5             # 0-1 confidence in regime detection
    regime_age_seconds: float = 0.0     # How long current regime has been active
    
    # Regime change tracking
    last_regime_change: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "momentum_bps": round(self.momentum_bps, 2),
            "momentum_direction": self.momentum_direction,
            "atr_bps": round(self.atr_bps, 2),
            "atr_ratio": round(self.atr_ratio, 2),
            "volatility_mode": self.volatility_mode,
            "range_bps": round(self.range_bps, 2),
            "structure_signal": self.structure_signal,
            "confidence": round(self.confidence, 2),
            "regime_age_seconds": round(self.regime_age_seconds, 0),
        }


class RegimeDetector:
    """
    Detects market regime for BTC trading recommendations.
    
    Key differences from trade repo version:
    - No momentum gate (we're not auto-trading)
    - Focused on regime classification for recommendations
    - Includes swing high/low detection for structure
    - Simpler configuration
    
    Usage:
        detector = RegimeDetector()
        
        # In main loop, feed prices:
        state = detector.update(current_price)
        
        if state.regime == MarketRegime.TRENDING_UP:
            # Recommend: buy pullbacks to VWAP
        elif state.regime == MarketRegime.CHOPPY:
            # Recommend: mean reversion at bands
        elif state.regime == MarketRegime.NEWS_SHOCK:
            # Recommend: sit out
    """
    
    def __init__(
        self,
        momentum_threshold_bps: float = 20.0,   # Threshold for trending (higher = more CHOPPY time)
        lookback_seconds: int = 300,            # 5 minute lookback
        atr_lookback_candles: int = 14,         # Standard ATR period
        sample_interval_ms: int = 1000,         # Sample every 1 second
        structure_swing_count: int = 3,         # Need 3+ HH/HL or LL/LH for trend
        atr_baseline_hours: int = 24,           # Baseline for ATR ratio
        news_shock_atr_ratio: float = 1.75,     # ATR > 1.75x baseline = news shock (conservative)
        min_regime_duration_seconds: int = 45,  # Minimum time in a regime (faster adaptation)
    ):
        self._momentum_threshold = momentum_threshold_bps
        self._lookback_seconds = lookback_seconds
        self._atr_lookback = atr_lookback_candles
        self._sample_interval_ms = sample_interval_ms
        self._structure_count = structure_swing_count
        self._atr_baseline_hours = atr_baseline_hours
        self._news_shock_ratio = news_shock_atr_ratio
        self._min_regime_duration = min_regime_duration_seconds
        
        # Price buffer
        max_samples = (lookback_seconds * 1000) // sample_interval_ms + 100
        self._prices: deque[PriceSample] = deque(maxlen=max_samples)
        self._last_sample_time: int = 0
        
        # Swing point detection
        self._swing_points: deque[SwingPoint] = deque(maxlen=20)
        self._swing_detection_lookback = 5  # Look at last 5 samples for swing
        
        # ATR baseline (rolling average of ATR values)
        self._atr_history: deque[float] = deque(maxlen=atr_baseline_hours * 12)  # 5-min samples
        self._last_atr_sample_time: float = 0
        
        # Current state
        self._state = RegimeState()
    
    def update(self, price: float) -> RegimeState:
        """
        Update with new price and recalculate regime.
        
        Args:
            price: Current BTC price
            
        Returns:
            Current RegimeState
        """
        now_ms = int(time.time() * 1000)
        now = time.time()
        
        # Sample rate limiting
        if now_ms - self._last_sample_time < self._sample_interval_ms:
            return self._state
        
        # Add sample
        self._prices.append(PriceSample(timestamp_ms=now_ms, price=price))
        self._last_sample_time = now_ms
        
        # Need enough data
        if len(self._prices) < 20:
            return self._state
        
        # Calculate all metrics
        self._calculate_momentum()
        self._calculate_range()
        self._calculate_atr()
        self._detect_swings()
        self._analyze_structure()
        
        # Determine regime
        self._determine_regime(now)
        
        # Update regime age
        self._state.regime_age_seconds = now - self._state.last_regime_change
        
        return self._state
    
    def _calculate_momentum(self) -> None:
        """Calculate momentum (directional price change)."""
        lookback_ms = self._lookback_seconds * 1000
        now_ms = self._last_sample_time
        cutoff = now_ms - lookback_ms
        
        recent = [p for p in self._prices if p.timestamp_ms >= cutoff]
        if len(recent) < 5:
            return
        
        oldest_price = recent[0].price
        newest_price = recent[-1].price
        
        if oldest_price > 0:
            momentum_bps = (newest_price - oldest_price) / oldest_price * 10000
            self._state.momentum_bps = momentum_bps
            
            if momentum_bps > self._momentum_threshold:
                self._state.momentum_direction = "up"
            elif momentum_bps < -self._momentum_threshold:
                self._state.momentum_direction = "down"
            else:
                self._state.momentum_direction = None
    
    def _calculate_range(self) -> None:
        """Calculate price range in lookback window."""
        lookback_ms = self._lookback_seconds * 1000
        now_ms = self._last_sample_time
        cutoff = now_ms - lookback_ms
        
        recent = [p for p in self._prices if p.timestamp_ms >= cutoff]
        if len(recent) < 5:
            return
        
        high = max(p.price for p in recent)
        low = min(p.price for p in recent)
        mid = (high + low) / 2
        
        if mid > 0:
            range_bps = (high - low) / mid * 10000
            self._state.range_bps = range_bps
    
    def _calculate_atr(self) -> None:
        """Calculate ATR-like volatility metric from price samples."""
        if len(self._prices) < self._atr_lookback + 1:
            return
        
        # Simplified ATR: average of |price_change| over lookback
        prices = list(self._prices)[-self._atr_lookback - 1:]
        
        true_ranges = []
        for i in range(1, len(prices)):
            prev_close = prices[i - 1].price
            current = prices[i].price
            
            # True range (simplified - just |close - prev_close|)
            tr = abs(current - prev_close)
            true_ranges.append(tr)
        
        if not true_ranges:
            return
        
        atr = sum(true_ranges) / len(true_ranges)
        current_price = prices[-1].price
        
        if current_price > 0:
            atr_bps = atr / current_price * 10000
            self._state.atr_bps = atr_bps
            
            # Update ATR history (every 5 minutes)
            now = time.time()
            if now - self._last_atr_sample_time >= 300:
                self._atr_history.append(atr_bps)
                self._last_atr_sample_time = now
            
            # Calculate ATR ratio
            if self._atr_history:
                baseline_atr = sum(self._atr_history) / len(self._atr_history)
                if baseline_atr > 0:
                    self._state.atr_ratio = atr_bps / baseline_atr
                else:
                    self._state.atr_ratio = 1.0
            
            # Determine volatility mode
            ratio = self._state.atr_ratio
            if ratio < 0.7:
                self._state.volatility_mode = "LOW"
            elif ratio <= 1.2:
                self._state.volatility_mode = "NORMAL"
            elif ratio <= self._news_shock_ratio:
                self._state.volatility_mode = "HIGH"
            else:
                self._state.volatility_mode = "EXTREME"
    
    def _detect_swings(self) -> None:
        """Detect swing highs and lows in price data."""
        if len(self._prices) < self._swing_detection_lookback * 2 + 1:
            return
        
        prices = list(self._prices)
        lookback = self._swing_detection_lookback
        
        # Check most recent possible swing point
        idx = len(prices) - lookback - 1
        if idx < lookback:
            return
        
        pivot = prices[idx]
        before = prices[idx - lookback:idx]
        after = prices[idx + 1:idx + lookback + 1]
        
        # Check for swing high
        is_swing_high = all(pivot.price > p.price for p in before) and \
                        all(pivot.price > p.price for p in after)
        
        # Check for swing low
        is_swing_low = all(pivot.price < p.price for p in before) and \
                       all(pivot.price < p.price for p in after)
        
        if is_swing_high:
            # Avoid duplicates
            if not self._swing_points or \
               self._swing_points[-1].timestamp_ms != pivot.timestamp_ms:
                self._swing_points.append(SwingPoint(
                    timestamp_ms=pivot.timestamp_ms,
                    price=pivot.price,
                    is_high=True
                ))
        
        elif is_swing_low:
            if not self._swing_points or \
               self._swing_points[-1].timestamp_ms != pivot.timestamp_ms:
                self._swing_points.append(SwingPoint(
                    timestamp_ms=pivot.timestamp_ms,
                    price=pivot.price,
                    is_high=False
                ))
    
    def _analyze_structure(self) -> None:
        """Analyze market structure from swing points."""
        # Get recent swing highs and lows
        highs = [sp for sp in self._swing_points if sp.is_high][-5:]
        lows = [sp for sp in self._swing_points if not sp.is_high][-5:]
        
        self._state.recent_highs = [h.price for h in highs]
        self._state.recent_lows = [l.price for l in lows]
        
        if len(highs) < self._structure_count or len(lows) < self._structure_count:
            self._state.structure_signal = "neutral"
            return
        
        # Check for higher highs and higher lows (bullish)
        recent_highs = [h.price for h in highs[-self._structure_count:]]
        recent_lows = [l.price for l in lows[-self._structure_count:]]
        
        # Count HH/HL
        hh_count = sum(1 for i in range(1, len(recent_highs)) 
                      if recent_highs[i] > recent_highs[i-1])
        hl_count = sum(1 for i in range(1, len(recent_lows)) 
                      if recent_lows[i] > recent_lows[i-1])
        
        # Count LL/LH
        ll_count = sum(1 for i in range(1, len(recent_lows)) 
                      if recent_lows[i] < recent_lows[i-1])
        lh_count = sum(1 for i in range(1, len(recent_highs)) 
                      if recent_highs[i] < recent_highs[i-1])
        
        # Determine structure signal
        bullish_score = hh_count + hl_count
        bearish_score = ll_count + lh_count
        
        threshold = self._structure_count - 1  # Need N-1 confirmations
        
        if bullish_score >= threshold and bearish_score < threshold:
            self._state.structure_signal = "bullish"
        elif bearish_score >= threshold and bullish_score < threshold:
            self._state.structure_signal = "bearish"
        else:
            self._state.structure_signal = "neutral"
    
    def _determine_regime(self, now: float) -> None:
        """Determine the current market regime."""
        time_in_regime = now - self._state.last_regime_change
        can_change = time_in_regime >= self._min_regime_duration
        
        # Determine target regime
        if self._state.volatility_mode == "EXTREME":
            target_regime = MarketRegime.NEWS_SHOCK
            confidence = 0.9
        elif self._state.momentum_direction == "up" and \
             self._state.structure_signal == "bullish":
            target_regime = MarketRegime.TRENDING_UP
            confidence = 0.8
        elif self._state.momentum_direction == "down" and \
             self._state.structure_signal == "bearish":
            target_regime = MarketRegime.TRENDING_DOWN
            confidence = 0.8
        elif self._state.momentum_direction == "up":
            target_regime = MarketRegime.TRENDING_UP
            confidence = 0.6
        elif self._state.momentum_direction == "down":
            target_regime = MarketRegime.TRENDING_DOWN
            confidence = 0.6
        else:
            target_regime = MarketRegime.CHOPPY
            confidence = 0.7
        
        # Update regime if allowed
        if target_regime != self._state.regime and can_change:
            self._state.regime = target_regime
            self._state.last_regime_change = now
        
        self._state.confidence = confidence
    
    def get_state(self) -> RegimeState:
        """Get current regime state."""
        return self._state
    
    def get_strategy_recommendation(self) -> str:
        """Get a simple strategy recommendation based on current regime."""
        regime = self._state.regime
        
        if regime == MarketRegime.NEWS_SHOCK:
            return "SIT_OUT"
        elif regime == MarketRegime.TRENDING_UP:
            return "BUY_PULLBACKS"
        elif regime == MarketRegime.TRENDING_DOWN:
            return "SELL_RALLIES"
        else:  # CHOPPY
            return "MEAN_REVERSION"
    
    def reset(self) -> None:
        """Reset detector state."""
        self._prices.clear()
        self._swing_points.clear()
        self._state = RegimeState()
        self._last_sample_time = 0
