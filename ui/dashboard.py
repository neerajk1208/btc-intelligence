"""
Terminal Dashboard for BTC Intelligence.

"Stupid simple" UI that clearly shows:
1. Current recommendation (BUY/SELL/WAIT)
2. Entry price range
3. Size to trade
4. Stop loss / Take profit
5. Time remaining
6. Current position and P&L
7. Guard status

Designed for quick glance decision making.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


def clear_screen():
    """Clear terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


def move_cursor_top():
    """Move cursor to top of screen (for updates without flicker)."""
    print("\033[H", end="")


class Dashboard:
    """
    Terminal dashboard for BTC Intelligence.
    
    Usage:
        dash = Dashboard()
        
        # Update with current state
        dash.update(
            recommendation=engine.get_last_recommendation(),
            price=adapter.mid_price(),
            position=tracker.get_state(price),
            regime=regime.get_state(),
            vwap=vwap.get_state(),
            vol=vol.get_state(),
            guards={
                "time": time_guard.check(),
                "position": pos_guard.check(...),
                "loss": loss_guard.check(),
                "spike": spike_guard.get_state(),
            },
        )
    """
    
    # ANSI colors
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    
    def __init__(self, title: str = "BTC INTELLIGENCE"):
        self._title = title
        self._last_update = 0
        self._update_interval = 1.0  # Seconds between visual updates
    
    def update(
        self,
        recommendation,
        price: float,
        position,
        regime,
        vwap,
        vol,
        guards: Dict[str, Any],
    ) -> None:
        """Update and redraw the dashboard."""
        # Throttle updates
        now = time.time()
        if now - self._last_update < self._update_interval:
            return
        self._last_update = now
        
        # Build display
        lines = []
        
        # Header
        lines.append(self._header())
        lines.append("")
        
        # Main recommendation box
        lines.extend(self._recommendation_box(recommendation, price))
        lines.append("")
        
        # Position and P&L
        lines.extend(self._position_box(position, price))
        lines.append("")
        
        # Market state
        lines.extend(self._market_box(price, regime, vwap, vol))
        lines.append("")
        
        # Guard status
        lines.extend(self._guards_box(guards))
        lines.append("")
        
        # Footer
        lines.append(self._footer())
        
        # Render
        move_cursor_top()
        print("\n".join(lines))
        sys.stdout.flush()
    
    def _header(self) -> str:
        """Render header."""
        now = datetime.utcnow().strftime("%H:%M:%S UTC")
        title = f"{self.BOLD}{self.CYAN}═══ {self._title} ═══{self.RESET}"
        return f"{title}  {self.GRAY}[{now}]{self.RESET}"
    
    def _footer(self) -> str:
        """Render footer."""
        return f"{self.GRAY}[P]osition input  [R]efresh  [Q]uit{self.RESET}"
    
    def _recommendation_box(self, rec, price: float) -> list:
        """Render the main recommendation box."""
        lines = []
        
        if rec is None:
            lines.append(f"{self.GRAY}Loading...{self.RESET}")
            return lines
        
        action = rec.action.value if hasattr(rec.action, 'value') else str(rec.action)
        urgency = rec.urgency.value if hasattr(rec.urgency, 'value') else str(rec.urgency)
        
        # Action with appropriate color
        if action in ("BUY",):
            color = self.GREEN
            emoji = "🟢"
        elif action in ("SELL",):
            color = self.RED
            emoji = "🔴"
        elif action in ("CLOSE_LONG",):
            color = self.RED
            emoji = "📤"
        elif action in ("CLOSE_SHORT",):
            color = self.GREEN
            emoji = "📥"
        elif action == "SIT_OUT":
            color = self.YELLOW
            emoji = "⏸️"
        else:  # WAIT
            color = self.GRAY
            emoji = "⏳"
        
        # Main action line
        action_line = f"{self.BOLD}{color}{emoji} {action}{self.RESET}"
        
        if urgency == "immediate":
            action_line += f"  {self.YELLOW}[ACT NOW]{self.RESET}"
        elif urgency == "soon":
            action_line += f"  {self.CYAN}[PREPARE]{self.RESET}"
        
        lines.append(f"┌{'─'*60}┐")
        lines.append(f"│ {action_line:<70}│")
        
        # Details if actionable
        if action in ("BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT"):
            size_str = f"${rec.target_size_usd:,.0f}"
            entry_str = f"${rec.entry_price_low:,.0f} - ${rec.entry_price_high:,.0f}"
            stop_str = f"${rec.stop_loss:,.0f}"
            tp_str = f"${rec.take_profit:,.0f}"
            
            lines.append(f"│{'─'*60}│")
            lines.append(f"│ 💰 Size:    {self.BOLD}{size_str:<48}{self.RESET}│")
            lines.append(f"│ 📈 Entry:   {entry_str:<48}│")
            lines.append(f"│ 🛑 Stop:    {stop_str:<48}│")
            lines.append(f"│ 🎯 Target:  {tp_str:<48}│")
            
            # Time remaining
            time_left = rec.time_remaining_seconds()
            if time_left > 0:
                mins = int(time_left // 60)
                secs = int(time_left % 60)
                time_str = f"{mins}m {secs}s"
                lines.append(f"│ ⏱️ Valid:   {self.CYAN}{time_str:<48}{self.RESET}│")
        
        # Reason
        if rec.reason:
            reason = rec.reason[:55]
            lines.append(f"│{'─'*60}│")
            lines.append(f"│ 📝 {reason:<56}│")
        
        # Warnings
        if rec.guard_warnings:
            lines.append(f"│{'─'*60}│")
            for warn in rec.guard_warnings[:3]:
                warn = warn[:55]
                lines.append(f"│ {self.YELLOW}{warn:<58}{self.RESET}│")
        
        lines.append(f"└{'─'*60}┘")
        
        return lines
    
    def _position_box(self, position_state, price: float) -> list:
        """Render current position box."""
        lines = []
        
        if position_state is None:
            return [f"{self.GRAY}No position data{self.RESET}"]
        
        pos = position_state.position
        
        if pos.is_flat:
            lines.append(f"📊 Position: {self.GRAY}FLAT{self.RESET}")
        else:
            side = pos.side.upper()
            side_color = self.GREEN if pos.side == "long" else self.RED
            size_str = f"${pos.size_usd:,.0f}"
            entry_str = f"${pos.avg_entry_price:,.0f}"
            
            # P&L
            pnl = position_state.unrealized_pnl_usd
            pnl_pct = position_state.unrealized_pnl_pct
            pnl_color = self.GREEN if pnl >= 0 else self.RED
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_str = f"{pnl_sign}${pnl:,.0f} ({pnl_sign}{pnl_pct:.1f}%)"
            
            lines.append(
                f"📊 Position: {side_color}{self.BOLD}{side}{self.RESET} "
                f"{size_str} @ {entry_str} "
                f"→ {pnl_color}{pnl_str}{self.RESET}"
            )
        
        return lines
    
    def _market_box(self, price: float, regime, vwap, vol) -> list:
        """Render market state."""
        lines = []
        
        # Price
        price_str = f"${price:,.2f}" if price else "---"
        lines.append(f"💹 BTC: {self.BOLD}{price_str}{self.RESET}")
        
        # Regime
        if regime:
            regime_val = regime.regime.value if hasattr(regime.regime, 'value') else str(regime.regime)
            regime_color = {
                "trending_up": self.GREEN,
                "trending_down": self.RED,
                "choppy": self.YELLOW,
                "news_shock": self.RED,
            }.get(regime_val, self.WHITE)
            lines.append(f"📍 Regime: {regime_color}{regime_val.upper()}{self.RESET}")
        
        # VWAP
        if vwap and vwap.vwap > 0:
            vwap_str = f"${vwap.vwap:,.0f}"
            dev_str = f"{vwap.deviation_sigma:+.2f}σ"
            zone = vwap.zone
            lines.append(f"📏 VWAP: {vwap_str}  |  Price: {dev_str}  |  Zone: {zone}")
        
        # Volatility
        if vol:
            vol_regime = vol.vol_regime
            vol_color = {
                "LOW": self.CYAN,
                "NORMAL": self.WHITE,
                "HIGH": self.YELLOW,
                "EXTREME": self.RED,
            }.get(vol_regime, self.WHITE)
            atr_str = f"${vol.atr:.0f}" if vol.atr > 0 else "---"
            lines.append(f"🌪️ Vol: {vol_color}{vol_regime}{self.RESET} (ATR: {atr_str})")
        
        return lines
    
    def _guards_box(self, guards: Dict[str, Any]) -> list:
        """Render guard status."""
        lines = []
        statuses = []
        
        # Time guard
        time_g = guards.get("time")
        if time_g:
            if time_g.is_paused:
                statuses.append(f"{self.RED}⏸️ MACRO{self.RESET}")
            elif time_g.next_event_in_minutes and time_g.next_event_in_minutes < 60:
                statuses.append(f"{self.YELLOW}📅 {time_g.next_event_in_minutes:.0f}m{self.RESET}")
            else:
                statuses.append(f"{self.GREEN}✓ Time{self.RESET}")
        
        # Position guard
        pos_g = guards.get("position")
        if pos_g:
            level = pos_g.exposure_level
            if level == "max":
                statuses.append(f"{self.RED}🛑 MAX EXP{self.RESET}")
            elif level == "high":
                statuses.append(f"{self.YELLOW}⚠️ High{self.RESET}")
            else:
                statuses.append(f"{self.GREEN}✓ Position{self.RESET}")
        
        # Loss guard
        loss_g = guards.get("loss")
        if loss_g:
            if loss_g.is_paused:
                statuses.append(f"{self.RED}🛑 LOSS LIM{self.RESET}")
            elif loss_g.warning_level in ("caution", "warning"):
                pct = loss_g.pnl_pct_of_limit * 100
                statuses.append(f"{self.YELLOW}⚠️ Loss {pct:.0f}%{self.RESET}")
            else:
                statuses.append(f"{self.GREEN}✓ Loss{self.RESET}")
        
        # Spike guard
        spike_g = guards.get("spike")
        if spike_g:
            if spike_g.is_paused:
                statuses.append(f"{self.RED}⚠️ SPIKE{self.RESET}")
            else:
                statuses.append(f"{self.GREEN}✓ Stable{self.RESET}")
        
        lines.append(f"🛡️ Guards: {' | '.join(statuses)}")
        
        return lines
    
    def show_startup(self) -> None:
        """Show startup screen."""
        clear_screen()
        print(f"""
{self.CYAN}╔══════════════════════════════════════════════════════════════╗
║                                                                ║
║   {self.BOLD}██████╗ ████████╗ ██████╗    ██╗███╗   ██╗████████╗{self.RESET}{self.CYAN}       ║
║   {self.BOLD}██╔══██╗╚══██╔══╝██╔════╝    ██║████╗  ██║╚══██╔══╝{self.RESET}{self.CYAN}       ║
║   {self.BOLD}██████╔╝   ██║   ██║         ██║██╔██╗ ██║   ██║   {self.RESET}{self.CYAN}       ║
║   {self.BOLD}██╔══██╗   ██║   ██║         ██║██║╚██╗██║   ██║   {self.RESET}{self.CYAN}       ║
║   {self.BOLD}██████╔╝   ██║   ╚██████╗    ██║██║ ╚████║   ██║   {self.RESET}{self.CYAN}       ║
║   {self.BOLD}╚═════╝    ╚═╝    ╚═════╝    ╚═╝╚═╝  ╚═══╝   ╚═╝   {self.RESET}{self.CYAN}       ║
║                                                                ║
║            BTC Intelligence - Manual Trading Assistant         ║
║                                                                ║
╚══════════════════════════════════════════════════════════════╝{self.RESET}

   Connecting to Hyperliquid...
""")
    
    def show_ready(self) -> None:
        """Show ready message."""
        print(f"""
   {self.GREEN}✓ Connected{self.RESET}
   {self.GREEN}✓ Signals initialized{self.RESET}
   {self.GREEN}✓ Guards active{self.RESET}
   
   Starting dashboard...
""")
        time.sleep(1)
        clear_screen()
    
    def show_error(self, error: str) -> None:
        """Show error message."""
        print(f"\n   {self.RED}✗ Error: {error}{self.RESET}\n")


# Global instance
_dashboard: Optional[Dashboard] = None


def get_dashboard() -> Dashboard:
    """Get or create dashboard instance."""
    global _dashboard
    if _dashboard is None:
        _dashboard = Dashboard()
    return _dashboard
