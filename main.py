#!/usr/bin/env python3
"""
BTC Intelligence - Manual Trading Assistant

A semi-automated trading assistant that analyzes BTC market data
from Hyperliquid and generates recommendations for manual execution
on the Definitive platform.

Run with: python main.py
"""
from __future__ import annotations

import asyncio
import sys
import os
import signal
import logging
from datetime import datetime
from typing import Optional

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s - %(message)s',
    handlers=[
        logging.FileHandler('btc-intelligence.log'),
        logging.StreamHandler() if '--debug' in sys.argv else logging.NullHandler(),
    ]
)
logger = logging.getLogger(__name__)

# Import components
from adapters import HyperliquidDataAdapter
from signals import RegimeDetector, VWAPCalculator, VolatilityAnalyzer
from guards import TimeGuard, PositionGuard, LossGuard, SpikeGuard
from position import PositionTracker
from engine import RecommendationEngine, SizingConfig, Action
from alerts import AlertManager, AlertConfig
from ui import Dashboard, PositionInput, quick_position_entry


class BTCIntelligence:
    """
    Main application class that orchestrates all components.
    """
    
    def __init__(self):
        # Configuration
        self._sizing_config = SizingConfig(
            base_trade_size_usd=10000,
            max_position_usd=30000,
        )
        self._alert_config = AlertConfig(
            sound_enabled=True,
            macos_enabled=True,
            telegram_enabled=True,
        )
        
        # Core components
        self._adapter: Optional[HyperliquidDataAdapter] = None
        self._regime: Optional[RegimeDetector] = None
        self._vwap: Optional[VWAPCalculator] = None
        self._volatility: Optional[VolatilityAnalyzer] = None
        
        # Guards
        self._time_guard: Optional[TimeGuard] = None
        self._position_guard: Optional[PositionGuard] = None
        self._loss_guard: Optional[LossGuard] = None
        self._spike_guard: Optional[SpikeGuard] = None
        
        # Position and engine
        self._tracker: Optional[PositionTracker] = None
        self._engine: Optional[RecommendationEngine] = None
        
        # Alerts and UI
        self._alerts: Optional[AlertManager] = None
        self._dashboard: Optional[Dashboard] = None
        self._input: Optional[PositionInput] = None
        
        # State
        self._running = False
        self._last_alert_action: Optional[Action] = None
    
    async def initialize(self) -> bool:
        """Initialize all components."""
        try:
            # Data adapter
            self._adapter = HyperliquidDataAdapter(symbol="BTC")
            
            # Signal modules
            self._regime = RegimeDetector()
            self._vwap = VWAPCalculator()
            self._volatility = VolatilityAnalyzer()
            
            # Guards
            self._time_guard = TimeGuard()
            self._position_guard = PositionGuard(
                max_position_usd=self._sizing_config.max_position_usd
            )
            self._loss_guard = LossGuard(daily_loss_limit_usd=3000)
            self._spike_guard = SpikeGuard()
            
            # Position and engine
            self._tracker = PositionTracker()
            self._engine = RecommendationEngine(self._sizing_config)
            
            # Alerts and UI
            self._alerts = AlertManager(self._alert_config)
            self._dashboard = Dashboard()
            self._input = PositionInput(self._tracker)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            return False
    
    async def connect(self) -> bool:
        """Connect to data sources."""
        try:
            await self._adapter.connect()
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False
    
    async def run(self) -> None:
        """Main run loop."""
        self._running = True
        
        # Show startup
        self._dashboard.show_startup()
        
        # Initialize
        if not await self.initialize():
            self._dashboard.show_error("Failed to initialize")
            return
        
        # Connect
        if not await self.connect():
            self._dashboard.show_error("Failed to connect to Hyperliquid")
            return
        
        self._dashboard.show_ready()
        
        # Start main loop and input handler
        try:
            await asyncio.gather(
                self._main_loop(),
                self._input_loop(),
            )
        except asyncio.CancelledError:
            logger.info("Shutting down...")
        finally:
            await self._cleanup()
    
    async def _main_loop(self) -> None:
        """Main update loop."""
        while self._running:
            try:
                await self._update_cycle()
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                await asyncio.sleep(5)
    
    async def _update_cycle(self) -> None:
        """Single update cycle."""
        # Get current price
        price = self._adapter.mid_price()
        if not price:
            return
        
        # Update signals
        self._regime.update(price)
        self._vwap.update(price)
        self._volatility.update_price(price)
        
        # Update spike guard
        self._spike_guard.update(price)
        
        # Get current position
        position_state = self._tracker.get_state(price)
        
        # Update position guard
        position_usd = position_state.position.size_usd
        if position_state.position.side == "short":
            position_usd = -position_usd
        
        # Generate recommendation
        rec = self._engine.generate(
            current_price=price,
            regime_state=self._regime.get_state(),
            vwap_state=self._vwap.get_state(),
            vol_state=self._volatility.get_state(),
            position_state=position_state,
            time_guard=self._time_guard.check(),
            position_guard=self._position_guard.check(position_usd),
            loss_guard=self._loss_guard.check(),
            spike_guard=self._spike_guard.get_state(),
        )
        
        # Check for alert
        if self._should_alert(rec):
            await self._send_alert(rec)
        
        # Update dashboard
        self._dashboard.update(
            recommendation=rec,
            price=price,
            position=position_state,
            regime=self._regime.get_state(),
            vwap=self._vwap.get_state(),
            vol=self._volatility.get_state(),
            guards={
                "time": self._time_guard.check(),
                "position": self._position_guard.check(position_usd),
                "loss": self._loss_guard.check(),
                "spike": self._spike_guard.get_state(),
            },
        )
    
    def _should_alert(self, rec) -> bool:
        """Check if we should send an alert for this recommendation."""
        if rec.action == self._last_alert_action:
            return False
        
        # Alert on actionable recommendations
        if rec.action in (Action.BUY, Action.SELL, Action.CLOSE_LONG, Action.CLOSE_SHORT):
            self._last_alert_action = rec.action
            return True
        
        # Reset on wait/sit_out
        if rec.action in (Action.WAIT, Action.SIT_OUT):
            self._last_alert_action = None
        
        return False
    
    async def _send_alert(self, rec) -> None:
        """Send alert through all channels."""
        try:
            await self._alerts.trade_signal(
                action=rec.action.value,
                size_usd=rec.target_size_usd,
                entry_low=rec.entry_price_low,
                entry_high=rec.entry_price_high,
                stop_loss=rec.stop_loss,
                take_profit=rec.take_profit,
                reason=rec.reason,
                valid_minutes=rec.valid_for_minutes,
            )
        except Exception as e:
            logger.warning(f"Failed to send alert: {e}")
    
    async def _input_loop(self) -> None:
        """Handle user input asynchronously."""
        while self._running:
            try:
                # Use asyncio to check for input
                # This is a simple implementation - in practice you might
                # want a more sophisticated input handler
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error in input loop: {e}")
    
    async def _cleanup(self) -> None:
        """Clean up resources."""
        try:
            if self._adapter:
                await self._adapter.close()
        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")
    
    def stop(self) -> None:
        """Signal to stop running."""
        self._running = False


async def main():
    """Entry point."""
    app = BTCIntelligence()
    
    # Handle signals for graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, app.stop)
    
    try:
        await app.run()
    except KeyboardInterrupt:
        app.stop()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
