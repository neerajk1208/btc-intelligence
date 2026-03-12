"""
Telegram Bot Integration for BTC Intelligence.

Sends push notifications to mobile via Telegram bot.

Setup:
1. Create bot via @BotFather on Telegram
2. Get bot token
3. Start chat with bot, get chat_id
4. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
"""
from __future__ import annotations

import os
import asyncio
import logging
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# Try to import telegram library
try:
    from telegram import Bot
    from telegram.error import TelegramError
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Telegram alerts disabled.")


class TelegramAlert:
    """
    Sends alerts via Telegram bot.
    
    Usage:
        alert = TelegramAlert()
        
        # Check if configured
        if alert.is_configured():
            await alert.send("Trade signal: BUY $10,000")
    """
    
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
        enabled: bool = True,
    ):
        """
        Args:
            bot_token: Telegram bot token (or from TELEGRAM_BOT_TOKEN env)
            chat_id: Chat ID to send to (or from TELEGRAM_CHAT_ID env)
            enabled: Whether alerts are enabled
        """
        self._bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = enabled and TELEGRAM_AVAILABLE
        
        self._bot: Optional[Bot] = None
        if self._enabled and self._bot_token:
            try:
                self._bot = Bot(token=self._bot_token)
            except Exception as e:
                logger.warning(f"Failed to create Telegram bot: {e}")
                self._enabled = False
    
    def is_configured(self) -> bool:
        """Check if Telegram is properly configured."""
        return bool(self._enabled and self._bot and self._chat_id)
    
    async def send(self, message: str, silent: bool = False) -> bool:
        """
        Send a message via Telegram.
        
        Args:
            message: Message to send
            silent: If True, sends silently (no notification sound)
            
        Returns:
            True if sent successfully
        """
        if not self.is_configured():
            return False
        
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode="Markdown",
                disable_notification=silent,
            )
            return True
        except TelegramError as e:
            logger.warning(f"Telegram send failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"Telegram error: {e}")
            return False
    
    def send_sync(self, message: str, silent: bool = False) -> bool:
        """Synchronous wrapper for send()."""
        if not self.is_configured():
            return False
        
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule in running loop
                asyncio.create_task(self.send(message, silent))
                return True
            else:
                return loop.run_until_complete(self.send(message, silent))
        except RuntimeError:
            # No event loop - create one
            return asyncio.run(self.send(message, silent))
    
    async def send_trade_signal(
        self,
        action: str,
        size_usd: float,
        entry_low: float,
        entry_high: float,
        stop_loss: float,
        take_profit: float,
        reason: str,
        valid_minutes: int = 15,
    ) -> bool:
        """Send a formatted trade signal."""
        emoji = {
            "BUY": "🟢",
            "SELL": "🔴",
            "CLOSE_LONG": "📤",
            "CLOSE_SHORT": "📥",
            "SIT_OUT": "⏸️",
            "WAIT": "⏳",
        }.get(action, "📊")
        
        message = f"""
{emoji} *BTC SIGNAL: {action}*

💰 Size: *${size_usd:,.0f}*
📈 Entry: ${entry_low:,.0f} - ${entry_high:,.0f}
🛑 Stop: ${stop_loss:,.0f}
🎯 Target: ${take_profit:,.0f}

📝 {reason}

⏱️ Valid for {valid_minutes} minutes
🕐 {datetime.utcnow().strftime("%H:%M UTC")}
"""
        return await self.send(message.strip())
    
    async def send_warning(self, warning: str) -> bool:
        """Send a warning message."""
        message = f"⚠️ *BTC Intelligence Warning*\n\n{warning}"
        return await self.send(message)
    
    async def send_error(self, error: str) -> bool:
        """Send an error message."""
        message = f"🚨 *BTC Intelligence Error*\n\n{error}"
        return await self.send(message)
    
    async def send_status(self, status: dict) -> bool:
        """Send a status update."""
        position = status.get("position", {})
        pnl = status.get("pnl", 0)
        regime = status.get("regime", "unknown")
        
        pos_str = "Flat"
        if position.get("side"):
            pos_str = f"{position['side'].upper()} ${position.get('size_usd', 0):,.0f}"
        
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        
        message = f"""
📊 *BTC Intelligence Status*

Position: {pos_str}
{pnl_emoji} P&L: ${pnl:,.2f}
📍 Regime: {regime}

🕐 {datetime.utcnow().strftime("%H:%M UTC")}
"""
        return await self.send(message.strip(), silent=True)


# Singleton
_telegram_alert: Optional[TelegramAlert] = None


def get_telegram_alert() -> TelegramAlert:
    """Get or create the singleton TelegramAlert instance."""
    global _telegram_alert
    if _telegram_alert is None:
        _telegram_alert = TelegramAlert()
    return _telegram_alert
