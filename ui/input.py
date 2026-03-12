"""
Position Input Interface for BTC Intelligence.

Allows manual entry of position state after executing trades on Definitive.
Simple command-line interface for entering:
- Long/short position
- Size in USD or BTC
- Entry price
"""
from __future__ import annotations

import sys
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class PositionInput:
    """
    Command-line interface for position entry.
    
    Usage:
        input_handler = PositionInput(tracker)
        
        # Show input prompt
        input_handler.prompt()
    """
    
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    
    def __init__(self, position_tracker):
        """
        Args:
            position_tracker: PositionTracker instance
        """
        self._tracker = position_tracker
    
    def prompt(self) -> bool:
        """
        Show position input prompt.
        
        Returns:
            True if position was updated, False if cancelled
        """
        print(f"\n{self.CYAN}═══ Position Entry ═══{self.RESET}")
        print(f"{self.YELLOW}Enter position details or 'c' to cancel{self.RESET}\n")
        
        # Show current position
        current = self._tracker.get_position()
        if current.is_flat:
            print(f"Current: {self.BOLD}FLAT{self.RESET}")
        else:
            side_color = self.GREEN if current.side == "long" else self.RED
            print(f"Current: {side_color}{current.side.upper()}{self.RESET} "
                  f"${current.size_usd:,.0f} @ ${current.avg_entry_price:,.0f}")
        
        print()
        
        # Get action
        print("Action:")
        print(f"  {self.GREEN}1. Enter LONG{self.RESET}")
        print(f"  {self.RED}2. Enter SHORT{self.RESET}")
        print(f"  3. Close position")
        print(f"  4. Set position manually")
        print(f"  c. Cancel")
        
        choice = input("\nChoice: ").strip().lower()
        
        if choice == "c":
            return False
        elif choice == "1":
            return self._enter_position("long")
        elif choice == "2":
            return self._enter_position("short")
        elif choice == "3":
            return self._close_position()
        elif choice == "4":
            return self._set_position()
        else:
            print(f"{self.RED}Invalid choice{self.RESET}")
            return False
    
    def _enter_position(self, side: str) -> bool:
        """Enter a new position or add to existing."""
        print(f"\n{self.CYAN}Enter {side.upper()} Position{self.RESET}")
        
        # Get size
        size_str = input("Size (USD, e.g., 10000): $").strip()
        if not size_str or size_str.lower() == 'c':
            return False
        
        try:
            size_usd = float(size_str.replace(",", "").replace("$", ""))
        except ValueError:
            print(f"{self.RED}Invalid size{self.RESET}")
            return False
        
        # Get entry price
        price_str = input("Entry price (e.g., 67500): $").strip()
        if not price_str or price_str.lower() == 'c':
            return False
        
        try:
            entry_price = float(price_str.replace(",", "").replace("$", ""))
        except ValueError:
            print(f"{self.RED}Invalid price{self.RESET}")
            return False
        
        # Confirm
        print(f"\n{self.YELLOW}Confirm:{self.RESET}")
        side_color = self.GREEN if side == "long" else self.RED
        print(f"  {side_color}{side.upper()}{self.RESET} ${size_usd:,.0f} @ ${entry_price:,.0f}")
        
        confirm = input("\nProceed? (y/n): ").strip().lower()
        if confirm != 'y':
            return False
        
        # Execute
        try:
            self._tracker.add_entry(
                side=side,
                size_usd=size_usd,
                entry_price=entry_price,
            )
            print(f"\n{self.GREEN}✓ Position updated{self.RESET}")
            return True
        except Exception as e:
            print(f"\n{self.RED}✗ Error: {e}{self.RESET}")
            return False
    
    def _close_position(self) -> bool:
        """Close current position."""
        current = self._tracker.get_position()
        
        if current.is_flat:
            print(f"\n{self.YELLOW}No position to close{self.RESET}")
            return False
        
        print(f"\n{self.CYAN}Close Position{self.RESET}")
        
        side_color = self.GREEN if current.side == "long" else self.RED
        print(f"Current: {side_color}{current.side.upper()}{self.RESET} "
              f"${current.size_usd:,.0f} @ ${current.avg_entry_price:,.0f}")
        
        # Get exit price
        price_str = input("\nExit price: $").strip()
        if not price_str or price_str.lower() == 'c':
            return False
        
        try:
            exit_price = float(price_str.replace(",", "").replace("$", ""))
        except ValueError:
            print(f"{self.RED}Invalid price{self.RESET}")
            return False
        
        # Calculate P&L
        if current.side == "long":
            pnl = (exit_price - current.avg_entry_price) * current.size_btc
        else:
            pnl = (current.avg_entry_price - exit_price) * current.size_btc
        
        pnl_color = self.GREEN if pnl >= 0 else self.RED
        pnl_sign = "+" if pnl >= 0 else ""
        
        print(f"\n{self.YELLOW}Confirm:{self.RESET}")
        print(f"  Close at ${exit_price:,.0f}")
        print(f"  P&L: {pnl_color}{pnl_sign}${pnl:,.2f}{self.RESET}")
        
        confirm = input("\nProceed? (y/n): ").strip().lower()
        if confirm != 'y':
            return False
        
        # Execute
        try:
            realized = self._tracker.close_position(exit_price=exit_price)
            print(f"\n{self.GREEN}✓ Position closed. Realized P&L: ${realized:,.2f}{self.RESET}")
            return True
        except Exception as e:
            print(f"\n{self.RED}✗ Error: {e}{self.RESET}")
            return False
    
    def _set_position(self) -> bool:
        """Manually set position (override)."""
        print(f"\n{self.CYAN}Set Position Manually{self.RESET}")
        print(f"{self.YELLOW}This overwrites current position state{self.RESET}\n")
        
        # Get side
        side_str = input("Side (long/short/flat): ").strip().lower()
        if side_str == 'c':
            return False
        
        if side_str == "flat":
            self._tracker.set_position(None, 0, 0)
            print(f"\n{self.GREEN}✓ Position set to FLAT{self.RESET}")
            return True
        
        if side_str not in ("long", "short"):
            print(f"{self.RED}Invalid side{self.RESET}")
            return False
        
        # Get size
        size_str = input("Size (BTC, e.g., 0.15): ").strip()
        if not size_str or size_str.lower() == 'c':
            return False
        
        try:
            size_btc = float(size_str)
        except ValueError:
            print(f"{self.RED}Invalid size{self.RESET}")
            return False
        
        # Get entry price
        price_str = input("Average entry price: $").strip()
        if not price_str or price_str.lower() == 'c':
            return False
        
        try:
            avg_entry = float(price_str.replace(",", "").replace("$", ""))
        except ValueError:
            print(f"{self.RED}Invalid price{self.RESET}")
            return False
        
        # Confirm
        size_usd = size_btc * avg_entry
        side_color = self.GREEN if side_str == "long" else self.RED
        print(f"\n{self.YELLOW}Confirm:{self.RESET}")
        print(f"  {side_color}{side_str.upper()}{self.RESET}")
        print(f"  {size_btc:.6f} BTC (${size_usd:,.0f})")
        print(f"  @ ${avg_entry:,.0f}")
        
        confirm = input("\nProceed? (y/n): ").strip().lower()
        if confirm != 'y':
            return False
        
        # Execute
        try:
            self._tracker.set_position(side_str, size_btc, avg_entry)
            print(f"\n{self.GREEN}✓ Position set{self.RESET}")
            return True
        except Exception as e:
            print(f"\n{self.RED}✗ Error: {e}{self.RESET}")
            return False


def quick_position_entry(tracker, current_price: float = 0.0) -> Optional[Tuple[str, float, float]]:
    """
    Quick one-line position entry.
    
    Format: "long 10000 67500" or "short 5000 68000" or "flat"
    
    Returns:
        Tuple of (side, size_usd, entry_price) or None if cancelled
    """
    print(f"\nQuick entry (e.g., 'long 10000 67500', 'short 5000', 'flat'):")
    if current_price > 0:
        print(f"Current price: ${current_price:,.0f}")
    
    line = input("> ").strip().lower()
    
    if not line or line == 'c':
        return None
    
    parts = line.split()
    
    if parts[0] == "flat":
        tracker.set_position(None, 0, 0)
        return ("flat", 0, 0)
    
    if len(parts) < 2:
        print("Invalid format")
        return None
    
    side = parts[0]
    if side not in ("long", "short"):
        print("Side must be 'long', 'short', or 'flat'")
        return None
    
    try:
        size_usd = float(parts[1].replace(",", "").replace("$", ""))
    except ValueError:
        print("Invalid size")
        return None
    
    # Entry price (optional, uses current price if omitted)
    if len(parts) >= 3:
        try:
            entry_price = float(parts[2].replace(",", "").replace("$", ""))
        except ValueError:
            print("Invalid price")
            return None
    elif current_price > 0:
        entry_price = current_price
    else:
        print("Entry price required")
        return None
    
    tracker.add_entry(side=side, size_usd=size_usd, entry_price=entry_price)
    return (side, size_usd, entry_price)
