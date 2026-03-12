"""
macOS Native Notification Integration for BTC Intelligence.

Uses AppleScript to trigger native macOS notifications.
These appear in Notification Center and can be configured
for sounds, banners, etc.
"""
from __future__ import annotations

import subprocess
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MacOSNotification:
    """
    Sends native macOS notifications.
    
    Only works on macOS. Falls back gracefully on other platforms.
    
    Usage:
        notif = MacOSNotification()
        notif.send("Trade Signal", "BUY $10,000 BTC")
    """
    
    def __init__(self, enabled: bool = True, app_name: str = "BTC Intelligence"):
        """
        Args:
            enabled: Whether notifications are enabled
            app_name: App name shown in notification
        """
        self._enabled = enabled and sys.platform == "darwin"
        self._app_name = app_name
        
        if not self._enabled and sys.platform != "darwin":
            logger.info("macOS notifications not available on this platform")
    
    def is_available(self) -> bool:
        """Check if notifications are available."""
        return self._enabled
    
    def send(
        self,
        title: str,
        message: str,
        subtitle: Optional[str] = None,
        sound: bool = True,
    ) -> bool:
        """
        Send a native macOS notification.
        
        Args:
            title: Notification title
            message: Main message body
            subtitle: Optional subtitle
            sound: Whether to play a sound
            
        Returns:
            True if sent successfully
        """
        if not self._enabled:
            return False
        
        try:
            # Build AppleScript
            script_parts = [f'display notification "{self._escape(message)}"']
            script_parts.append(f'with title "{self._escape(title)}"')
            
            if subtitle:
                script_parts.append(f'subtitle "{self._escape(subtitle)}"')
            
            if sound:
                script_parts.append('sound name "Glass"')
            
            script = " ".join(script_parts)
            
            # Execute via osascript
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=5,
            )
            
            return result.returncode == 0
            
        except subprocess.TimeoutExpired:
            logger.warning("Notification timed out")
            return False
        except Exception as e:
            logger.warning(f"Failed to send notification: {e}")
            return False
    
    def _escape(self, text: str) -> str:
        """Escape text for AppleScript."""
        # Escape backslashes and quotes
        return text.replace("\\", "\\\\").replace('"', '\\"')
    
    def trade_signal(
        self,
        action: str,
        size_usd: float,
        entry_price: float,
        reason: str,
    ) -> bool:
        """Send a trade signal notification."""
        emoji = {
            "BUY": "🟢",
            "SELL": "🔴",
            "CLOSE_LONG": "📤",
            "CLOSE_SHORT": "📥",
        }.get(action, "📊")
        
        title = f"{emoji} BTC {action}"
        message = f"${size_usd:,.0f} @ ${entry_price:,.0f}"
        subtitle = reason[:50] if reason else None
        
        return self.send(title, message, subtitle)
    
    def warning(self, message: str) -> bool:
        """Send a warning notification."""
        return self.send("⚠️ BTC Warning", message, sound=True)
    
    def error(self, message: str) -> bool:
        """Send an error notification."""
        return self.send("🚨 BTC Error", message, sound=True)
    
    def info(self, title: str, message: str) -> bool:
        """Send an info notification."""
        return self.send(f"ℹ️ {title}", message, sound=False)


# Singleton
_macos_notification: Optional[MacOSNotification] = None


def get_macos_notification(enabled: bool = True) -> MacOSNotification:
    """Get or create the singleton MacOSNotification instance."""
    global _macos_notification
    if _macos_notification is None:
        _macos_notification = MacOSNotification(enabled=enabled)
    return _macos_notification
