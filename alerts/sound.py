"""
Sound Alert System for BTC Intelligence.

Uses macOS system sounds for alerts.
Cross-platform fallback using beep.
"""
from __future__ import annotations

import subprocess
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SoundAlert:
    """
    Plays sound alerts for different events.
    
    Sound types:
    - trade_signal: Important - action needed
    - warning: Caution - attention needed
    - info: Informational
    - error: Problem occurred
    
    Usage:
        alert = SoundAlert()
        alert.play("trade_signal")
    """
    
    # macOS system sounds (in /System/Library/Sounds/)
    _MACOS_SOUNDS = {
        "trade_signal": "Glass",      # Distinctive - needs attention
        "warning": "Basso",           # Deep warning
        "info": "Pop",                # Gentle info
        "error": "Sosumi",            # Error sound
        "success": "Purr",            # Positive
        "urgent": "Hero",             # Very attention-grabbing
    }
    
    def __init__(self, enabled: bool = True, volume: float = 1.0):
        """
        Args:
            enabled: Whether sounds are enabled
            volume: Volume multiplier (0-1)
        """
        self._enabled = enabled
        self._volume = max(0.0, min(1.0, volume))
        self._is_macos = sys.platform == "darwin"
    
    def play(self, sound_type: str = "info", repeat: int = 1) -> bool:
        """
        Play a sound.
        
        Args:
            sound_type: Type of sound to play
            repeat: Number of times to repeat
            
        Returns:
            True if sound played successfully
        """
        if not self._enabled:
            return False
        
        try:
            for _ in range(repeat):
                if self._is_macos:
                    self._play_macos(sound_type)
                else:
                    self._play_fallback()
            return True
        except Exception as e:
            logger.warning(f"Failed to play sound: {e}")
            return False
    
    def _play_macos(self, sound_type: str) -> None:
        """Play macOS system sound."""
        sound_name = self._MACOS_SOUNDS.get(sound_type, "Pop")
        
        # Use afplay for system sounds
        sound_path = f"/System/Library/Sounds/{sound_name}.aiff"
        
        # Volume: afplay uses -v where 1.0 is normal
        vol_arg = str(self._volume)
        
        subprocess.run(
            ["afplay", "-v", vol_arg, sound_path],
            capture_output=True,
            timeout=5
        )
    
    def _play_fallback(self) -> None:
        """Fallback beep for non-macOS."""
        # Terminal bell
        print("\a", end="", flush=True)
    
    def trade_signal(self) -> None:
        """Play trade signal alert."""
        self.play("trade_signal", repeat=2)
    
    def warning(self) -> None:
        """Play warning alert."""
        self.play("warning")
    
    def error(self) -> None:
        """Play error alert."""
        self.play("error")
    
    def success(self) -> None:
        """Play success sound."""
        self.play("success")
    
    def urgent(self) -> None:
        """Play urgent alert (multiple sounds)."""
        self.play("urgent", repeat=3)
    
    def set_enabled(self, enabled: bool) -> None:
        """Enable/disable sounds."""
        self._enabled = enabled
    
    def set_volume(self, volume: float) -> None:
        """Set volume (0-1)."""
        self._volume = max(0.0, min(1.0, volume))


# Singleton
_sound_alert: Optional[SoundAlert] = None


def get_sound_alert(enabled: bool = True, volume: float = 1.0) -> SoundAlert:
    """Get or create the singleton SoundAlert instance."""
    global _sound_alert
    if _sound_alert is None:
        _sound_alert = SoundAlert(enabled=enabled, volume=volume)
    return _sound_alert
