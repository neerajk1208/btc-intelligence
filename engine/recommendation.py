"""
Recommendation Engine for BTC Intelligence.

SIMPLIFIED LOGIC:
- When FLAT: Look for BUY signals
- When LONG: Look for SELL (close) signals
- No shorting. Just long or flat.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class Action(str, Enum):
    """Recommended action type."""
    BUY = "BUY"
    SELL = "SELL"  # Close position
    WAIT = "WAIT"
    SIT_OUT = "SIT_OUT"


class Urgency(str, Enum):
    """How urgent the recommendation is."""
    IMMEDIATE = "immediate"
    SOON = "soon"
    NEUTRAL = "neutral"


@dataclass
class Recommendation:
    """A trading recommendation."""
    action: Action
    urgency: Urgency
    
    target_size_usd: float = 0.0
    entry_price_low: float = 0.0
    entry_price_high: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    valid_for_minutes: int = 5
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    
    reason: str = ""
    regime: str = ""
    zone: str = ""
    
    current_position_side: Optional[str] = None
    current_position_size_usd: float = 0.0
    would_add_to_position: bool = False
    would_reduce_position: bool = False
    guard_warnings: List[str] = field(default_factory=list)
    
    def is_active(self) -> bool:
        age_ms = int(time.time() * 1000) - self.created_at_ms
        return age_ms < (self.valid_for_minutes * 60 * 1000)
    
    def time_remaining_seconds(self) -> float:
        age_ms = int(time.time() * 1000) - self.created_at_ms
        remaining_ms = (self.valid_for_minutes * 60 * 1000) - age_ms
        return max(0, remaining_ms / 1000)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "urgency": self.urgency.value,
            "target_size_usd": self.target_size_usd,
            "entry_price_low": self.entry_price_low,
            "entry_price_high": self.entry_price_high,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "valid_for_minutes": self.valid_for_minutes,
            "time_remaining_seconds": round(self.time_remaining_seconds(), 0),
            "reason": self.reason,
            "regime": self.regime,
            "zone": self.zone,
            "current_position_side": self.current_position_side,
            "current_position_size_usd": self.current_position_size_usd,
            "guard_warnings": self.guard_warnings,
        }


@dataclass
class SizingConfig:
    """Configuration for position sizing."""
    base_trade_size_usd: float = 12500.0
    max_position_usd: float = 30000.0


class RecommendationEngine:
    """
    Simple long-only recommendation engine.
    
    FLAT → BUY when conditions good
    LONG → SELL when conditions change or profit/loss targets hit
    """
    
    def __init__(self, sizing_config: Optional[SizingConfig] = None):
        self._sizing = sizing_config or SizingConfig()
        self._last_recommendation: Optional[Recommendation] = None
        self._last_alert_action: Optional[Action] = None
    
    def generate(
        self,
        current_price: float,
        regime_state,
        vwap_state,
        vol_state,
        position_state,
        time_guard,
        position_guard,
        loss_guard,
        spike_guard,
    ) -> Recommendation:
        """Generate a trading recommendation."""
        rec = Recommendation(
            action=Action.WAIT,
            urgency=Urgency.NEUTRAL,
        )
        
        warnings = []
        
        # Check guards that block all trading
        if time_guard.is_paused:
            rec.action = Action.SIT_OUT
            rec.reason = f"Macro event: {time_guard.pause_reason}"
            rec.guard_warnings.append(f"⏸️ {time_guard.pause_reason}")
            self._last_recommendation = rec
            return rec
        
        if loss_guard.is_paused:
            rec.action = Action.SIT_OUT
            rec.reason = f"Daily loss limit hit"
            rec.guard_warnings.append(f"🛑 {loss_guard.pause_reason}")
            self._last_recommendation = rec
            return rec
        
        if spike_guard.is_paused:
            rec.action = Action.SIT_OUT
            rec.reason = f"Price spike detected"
            rec.guard_warnings.append(f"⚠️ {spike_guard.pause_reason}")
            self._last_recommendation = rec
            return rec
        
        if vol_state.should_pause:
            rec.action = Action.SIT_OUT
            rec.reason = f"Extreme volatility"
            rec.guard_warnings.append(f"🌪️ Extreme volatility")
            self._last_recommendation = rec
            return rec
        
        # Add warnings
        if time_guard.next_event_in_minutes and time_guard.next_event_in_minutes < 60:
            warnings.append(f"📅 {time_guard.next_event_name} in {time_guard.next_event_in_minutes:.0f} min")
        
        if vol_state.vol_regime == "HIGH":
            warnings.append(f"🌪️ High volatility")
        
        rec.guard_warnings = warnings
        
        # Get state
        regime = regime_state.regime.value if hasattr(regime_state.regime, 'value') else str(regime_state.regime)
        zone = vwap_state.zone
        deviation = vwap_state.deviation_sigma
        position = position_state.position
        
        rec.regime = regime
        rec.zone = zone
        rec.current_position_side = position.side
        rec.current_position_size_usd = position.size_usd
        
        is_flat = position.side is None or position.size_usd == 0
        is_long = position.side == "long" and position.size_usd > 0
        
        # ============================================
        # WHEN FLAT - Look for BUY opportunities
        # ============================================
        if is_flat:
            
            # NEWS_SHOCK → Don't buy
            if regime == "news_shock":
                rec.action = Action.SIT_OUT
                rec.reason = "News shock - don't enter"
                self._last_recommendation = rec
                return rec
            
            # TRENDING_DOWN → Don't buy against trend
            if regime == "trending_down":
                rec.action = Action.WAIT
                rec.reason = "Downtrend - wait"
                self._last_recommendation = rec
                return rec
            
            # TRENDING_UP → BUY (unless extremely extended)
            if regime == "trending_up":
                if deviation > 2.0:
                    rec.action = Action.WAIT
                    rec.reason = f"Uptrend but extended ({deviation:.1f}σ) - wait for dip"
                elif position_guard.can_add_long:
                    rec.action = Action.BUY
                    rec.urgency = Urgency.IMMEDIATE
                    rec.reason = "UPTREND - BUY"
                    rec.target_size_usd = min(self._sizing.base_trade_size_usd, position_guard.get_max_add_size())
                    rec.entry_price_low = current_price * 0.999
                    rec.entry_price_high = current_price * 1.001
                    rec.stop_loss, rec.take_profit = self._calc_levels(current_price, vol_state, "BUY")
                else:
                    rec.action = Action.WAIT
                    rec.reason = "Max position"
                self._last_recommendation = rec
                return rec
            
            # CHOPPY → Buy at buy zone
            if regime == "choppy":
                if zone in ("buy", "extended_buy"):
                    if position_guard.can_add_long:
                        rec.action = Action.BUY
                        rec.urgency = Urgency.IMMEDIATE
                        rec.reason = f"CHOPPY + DIP - BUY"
                        rec.target_size_usd = min(self._sizing.base_trade_size_usd, position_guard.get_max_add_size())
                        rec.entry_price_low = current_price * 0.999
                        rec.entry_price_high = current_price * 1.001
                        rec.stop_loss, rec.take_profit = self._calc_levels(current_price, vol_state, "BUY")
                    else:
                        rec.action = Action.WAIT
                        rec.reason = "Max position"
                else:
                    rec.action = Action.WAIT
                    rec.reason = f"Choppy - wait for dip (zone: {zone})"
                self._last_recommendation = rec
                return rec
            
            # Default for flat
            rec.action = Action.WAIT
            rec.reason = "No clear signal"
            self._last_recommendation = rec
            return rec
        
        # ============================================
        # WHEN LONG - Look for SELL (close) signals
        # ============================================
        if is_long:
            
            # NEWS_SHOCK → Close immediately
            if regime == "news_shock":
                rec.action = Action.SELL
                rec.urgency = Urgency.IMMEDIATE
                rec.reason = "NEWS SHOCK - SELL NOW"
                rec.target_size_usd = position.size_usd
                rec.would_reduce_position = True
                self._last_recommendation = rec
                return rec
            
            # TRENDING_DOWN → Close (trend reversed)
            if regime == "trending_down":
                rec.action = Action.SELL
                rec.urgency = Urgency.IMMEDIATE
                rec.reason = "TREND REVERSED - SELL"
                rec.target_size_usd = position.size_usd
                rec.would_reduce_position = True
                self._last_recommendation = rec
                return rec
            
            # CHOPPY + sell zone → Take profit
            if regime == "choppy" and zone in ("sell", "extended_sell"):
                rec.action = Action.SELL
                rec.urgency = Urgency.IMMEDIATE
                rec.reason = f"CHOPPY + RIP - TAKE PROFIT"
                rec.target_size_usd = position.size_usd
                rec.would_reduce_position = True
                self._last_recommendation = rec
                return rec
            
            # TRENDING_UP + very extended → Take profit
            if regime == "trending_up" and deviation > 1.5:
                rec.action = Action.SELL
                rec.urgency = Urgency.IMMEDIATE
                rec.reason = f"EXTENDED {deviation:.1f}σ - TAKE PROFIT"
                rec.target_size_usd = position.size_usd
                rec.would_reduce_position = True
                self._last_recommendation = rec
                return rec
            
            # Otherwise hold
            rec.action = Action.WAIT
            rec.reason = f"HOLD - {regime} / {zone}"
            self._last_recommendation = rec
            return rec
        
        # Fallback
        rec.action = Action.WAIT
        rec.reason = "Unknown state"
        self._last_recommendation = rec
        return rec
    
    def _calc_levels(self, price: float, vol_state, action: str) -> tuple[float, float]:
        """Calculate stop loss and take profit."""
        atr = vol_state.atr if vol_state.atr > 0 else price * 0.005
        
        if action == "BUY":
            stop = price - (1.0 * atr)
            target = price + (1.0 * atr)
        else:
            stop = price + (1.0 * atr)
            target = price - (1.0 * atr)
        
        return round(stop, 2), round(target, 2)
    
    def get_last_recommendation(self) -> Optional[Recommendation]:
        return self._last_recommendation
    
    def should_alert(self, new_rec: Recommendation) -> bool:
        if self._last_alert_action != new_rec.action:
            if new_rec.action in (Action.BUY, Action.SELL):
                self._last_alert_action = new_rec.action
                return True
        return False
