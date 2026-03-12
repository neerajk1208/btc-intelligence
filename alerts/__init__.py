"""
Alert system for BTC Intelligence.

Provides multiple notification channels:
- Sound: macOS system sounds
- macOS: Native notification center
- Telegram: Push notifications to mobile

All channels can be enabled/disabled independently.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional
from dataclasses import dataclass

from .sound import SoundAlert, get_sound_alert
from .macos import MacOSNotification, get_macos_notification
from .telegram import TelegramAlert, get_telegram_alert

logger = logging.getLogger(__name__)

__all__ = [
    "SoundAlert",
    "get_sound_alert",
    "MacOSNotification", 
    "get_macos_notification",
    "TelegramAlert",
    "get_telegram_alert",
    "AlertManager",
    "AlertConfig",
]


@dataclass
class AlertConfig:
    """Configuration for alert channels."""
    sound_enabled: bool = True
    sound_volume: float = 1.0
    macos_enabled: bool = True
    telegram_enabled: bool = True
    

class AlertManager:
    """
    Unified alert manager that sends to all configured channels.
    
    Usage:
        manager = AlertManager()
        
        # Send trade signal to all channels
        await manager.trade_signal(
            action="BUY",
            size_usd=10000,
            entry_low=67000,
            entry_high=67500,
            stop_loss=66000,
            take_profit=69000,
            reason="MR: Buy at VWAP support",
        )
    """
    
    def __init__(self, config: Optional[AlertConfig] = None):
        config = config or AlertConfig()
        
        self._sound = get_sound_alert(
            enabled=config.sound_enabled,
            volume=config.sound_volume,
        )
        self._macos = get_macos_notification(enabled=config.macos_enabled)
        self._telegram = get_telegram_alert() if config.telegram_enabled else None
    
    async def trade_signal(
        self,
        action: str,
        size_usd: float,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        take_profit: float,
        reason: str,
        valid_minutes: int = 15,
    ) -> None:
        """Send trade signal to all channels."""
        # Sound
        if action in ("BUY", "SELL"):
            self._sound.trade_signal()
        elif action in ("CLOSE_LONG", "CLOSE_SHORT"):
            self._sound.urgent()
        
        # macOS notification
        entry_mid = (entry_low + entry_high) / 2
        self._macos.trade_signal(action, size_usd, entry_mid, reason)
        
        # Telegram
        if self._telegram and self._telegram.is_configured():
            await self._telegram.send_trade_signal(
                action=action,
                size_usd=size_usd,
                entry_low=entry_low,
                entry_high=entry_high,
                stop_loss=stop_loss,
                take_profit=take_profit,
                reason=reason,
                valid_minutes=valid_minutes,
            )
    
    async def warning(self, message: str) -> None:
        """Send warning to all channels."""
        self._sound.warning()
        self._macos.warning(message)
        
        if self._telegram and self._telegram.is_configured():
            await self._telegram.send_warning(message)
    
    async def error(self, message: str) -> None:
        """Send error to all channels."""
        self._sound.error()
        self._macos.error(message)
        
        if self._telegram and self._telegram.is_configured():
            await self._telegram.send_error(message)
    
    async def status(self, status: dict) -> None:
        """Send status update (Telegram only, silent)."""
        if self._telegram and self._telegram.is_configured():
            await self._telegram.send_status(status)
    
    def sound_only(self, sound_type: str = "info") -> None:
        """Play just a sound."""
        self._sound.play(sound_type)
