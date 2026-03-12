"""
Time-based Trading Guards for BTC Intelligence.

Handles:
1. Macro event pauses (FOMC, CPI, NFP, earnings)
2. Scheduled pause windows (optional)
3. Wind-down periods before pauses

Adapted from trade repo bot/strategies/time_regime.py - simplified for recommendations.
"""
from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MacroEvent:
    """A scheduled macro event that pauses trading."""
    name: str
    datetime_utc: datetime
    pause_before_minutes: int = 30
    pause_after_minutes: int = 30
    event_type: str = "economic"  # economic, fomc, opex, earnings_mega
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "datetime_utc": self.datetime_utc.isoformat(),
            "pause_before_minutes": self.pause_before_minutes,
            "pause_after_minutes": self.pause_after_minutes,
            "type": self.event_type,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MacroEvent":
        dt_str = data["datetime_utc"]
        if dt_str.endswith("Z"):
            dt_str = dt_str[:-1]
        dt = datetime.fromisoformat(dt_str)
        return cls(
            name=data["name"],
            datetime_utc=dt,
            pause_before_minutes=data.get("pause_before_minutes", 30),
            pause_after_minutes=data.get("pause_after_minutes", 30),
            event_type=data.get("type", "economic"),
        )
    
    @property
    def window_start(self) -> datetime:
        return self.datetime_utc - timedelta(minutes=self.pause_before_minutes)
    
    @property
    def window_end(self) -> datetime:
        return self.datetime_utc + timedelta(minutes=self.pause_after_minutes)


@dataclass
class TimeGuardState:
    """Current state of time-based guards."""
    is_paused: bool = False
    pause_reason: str = ""
    next_event_name: Optional[str] = None
    next_event_in_minutes: Optional[float] = None
    active_event: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "is_paused": self.is_paused,
            "pause_reason": self.pause_reason,
            "next_event_name": self.next_event_name,
            "next_event_in_minutes": round(self.next_event_in_minutes, 1) if self.next_event_in_minutes else None,
            "active_event": self.active_event,
        }


class TimeGuard:
    """
    Manages time-based trading pauses.
    
    Loads macro events from JSON and checks if trading should be paused.
    
    Usage:
        guard = TimeGuard()
        
        # Check periodically
        state = guard.check()
        
        if state.is_paused:
            print(f"Paused: {state.pause_reason}")
        else:
            print(f"Next event: {state.next_event_name} in {state.next_event_in_minutes} min")
    """
    
    def __init__(self, events_file: Optional[str] = None):
        """
        Args:
            events_file: Path to macro_events.json. If None, looks in default location.
        """
        if events_file is None:
            base_dir = os.path.dirname(os.path.dirname(__file__))
            events_file = os.path.join(base_dir, "data", "macro_events.json")
        
        self._events_file = events_file
        self._events: List[MacroEvent] = []
        self._load_events()
        
        self._last_state: Optional[TimeGuardState] = None
    
    def _load_events(self) -> None:
        """Load events from JSON file."""
        if not os.path.exists(self._events_file):
            logger.warning(f"Events file not found: {self._events_file}")
            return
        
        try:
            with open(self._events_file, 'r') as f:
                data = json.load(f)
            
            self._events = [MacroEvent.from_dict(e) for e in data.get("events", [])]
            
            # Filter to future events
            now = datetime.utcnow()
            self._events = [e for e in self._events if e.window_end > now]
            self._events.sort(key=lambda e: e.datetime_utc)
            
            logger.info(f"Loaded {len(self._events)} upcoming macro events")
            
        except Exception as e:
            logger.error(f"Failed to load events: {e}")
            self._events = []
    
    def reload_events(self) -> None:
        """Reload events from file."""
        self._load_events()
    
    def check(self, now: Optional[datetime] = None) -> TimeGuardState:
        """
        Check if trading should be paused.
        
        Args:
            now: Override current time (for testing)
            
        Returns:
            TimeGuardState with pause status
        """
        if now is None:
            now = datetime.utcnow()
        
        state = TimeGuardState()
        
        # Check if we're in any event window
        for event in self._events:
            if event.window_start <= now <= event.window_end:
                state.is_paused = True
                state.pause_reason = f"{event.name} ({event.pause_before_minutes}min before to {event.pause_after_minutes}min after)"
                state.active_event = event.name
                
                # Log state change
                if self._last_state is None or not self._last_state.is_paused:
                    logger.warning(f"⏸️ PAUSED: {state.pause_reason}")
                
                self._last_state = state
                return state
        
        # Find next upcoming event
        for event in self._events:
            if event.window_start > now:
                minutes_until = (event.window_start - now).total_seconds() / 60
                state.next_event_name = event.name
                state.next_event_in_minutes = minutes_until
                break
        
        # Log resumption
        if self._last_state is not None and self._last_state.is_paused and not state.is_paused:
            logger.info(f"✅ Resumed from pause")
        
        self._last_state = state
        return state
    
    def get_upcoming_events(self, limit: int = 5) -> List[Dict]:
        """Get list of upcoming events."""
        now = datetime.utcnow()
        upcoming = [e for e in self._events if e.datetime_utc > now][:limit]
        
        result = []
        for event in upcoming:
            minutes_until = (event.datetime_utc - now).total_seconds() / 60
            result.append({
                "name": event.name,
                "datetime_utc": event.datetime_utc.isoformat(),
                "type": event.event_type,
                "minutes_until": round(minutes_until, 0),
                "pause_before": event.pause_before_minutes,
                "pause_after": event.pause_after_minutes,
            })
        
        return result
    
    def add_event(self, event: MacroEvent) -> None:
        """Add an event and save to file."""
        self._events.append(event)
        self._events.sort(key=lambda e: e.datetime_utc)
        self._save_events()
    
    def _save_events(self) -> None:
        """Save events to JSON file."""
        try:
            # Load existing file to preserve metadata
            existing = {}
            if os.path.exists(self._events_file):
                with open(self._events_file, 'r') as f:
                    existing = json.load(f)
            
            # Update events
            existing["events"] = [e.to_dict() for e in self._events]
            existing["last_updated"] = datetime.utcnow().strftime("%Y-%m-%d")
            
            with open(self._events_file, 'w') as f:
                json.dump(existing, f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to save events: {e}")


# Singleton instance
_time_guard: Optional[TimeGuard] = None


def get_time_guard() -> TimeGuard:
    """Get or create the singleton TimeGuard instance."""
    global _time_guard
    if _time_guard is None:
        _time_guard = TimeGuard()
    return _time_guard
