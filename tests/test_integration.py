#!/usr/bin/env python3
"""
Integration test for BTC Intelligence.

Tests all components working together with live data.
Run with: python tests/test_integration.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters import HyperliquidDataAdapter
from signals import RegimeDetector, VWAPCalculator, VolatilityAnalyzer
from guards import TimeGuard, PositionGuard, LossGuard, SpikeGuard
from position import PositionTracker
from engine import RecommendationEngine, SizingConfig, Action


async def test_integration():
    """Full integration test."""
    print("=" * 60)
    print("BTC Intelligence - Integration Test")
    print("=" * 60)
    
    # Initialize all components
    print("\n1. Initializing components...")
    
    adapter = HyperliquidDataAdapter(symbol="BTC")
    regime = RegimeDetector()
    vwap = VWAPCalculator()
    volatility = VolatilityAnalyzer()
    
    time_guard = TimeGuard()
    position_guard = PositionGuard(max_position_usd=30000)
    loss_guard = LossGuard(daily_loss_limit_usd=3000)
    spike_guard = SpikeGuard()
    
    tracker = PositionTracker()
    engine = RecommendationEngine(SizingConfig())
    
    print("   ✅ All components initialized")
    
    # Connect
    print("\n2. Connecting to Hyperliquid...")
    try:
        await adapter.connect()
        print("   ✅ Connected")
    except Exception as e:
        print(f"   ❌ Connection failed: {e}")
        return False
    
    try:
        # Collect data
        print("\n3. Running 10 update cycles...")
        
        for i in range(10):
            await asyncio.sleep(1)
            
            price = adapter.mid_price()
            if not price:
                print(f"   Cycle {i+1}: No price data")
                continue
            
            # Update signals
            regime.update(price)
            vwap.update(price)
            volatility.update_price(price)
            spike_guard.update(price)
            
            # Get states
            position_state = tracker.get_state(price)
            position_usd = position_state.position.size_usd
            
            # Generate recommendation
            rec = engine.generate(
                current_price=price,
                regime_state=regime.get_state(),
                vwap_state=vwap.get_state(),
                vol_state=volatility.get_state(),
                position_state=position_state,
                time_guard=time_guard.check(),
                position_guard=position_guard.check(position_usd),
                loss_guard=loss_guard.check(),
                spike_guard=spike_guard.get_state(),
            )
            
            action = rec.action.value if hasattr(rec.action, 'value') else str(rec.action)
            print(f"   Cycle {i+1}: ${price:,.0f} → {action}")
        
        # Print final state
        print("\n4. Final State:")
        print("-" * 40)
        
        price = adapter.mid_price()
        if price:
            # Final recommendation
            position_state = tracker.get_state(price)
            rec = engine.generate(
                current_price=price,
                regime_state=regime.get_state(),
                vwap_state=vwap.get_state(),
                vol_state=volatility.get_state(),
                position_state=position_state,
                time_guard=time_guard.check(),
                position_guard=position_guard.check(position_state.position.size_usd),
                loss_guard=loss_guard.check(),
                spike_guard=spike_guard.get_state(),
            )
            
            print(f"\n   Price: ${price:,.2f}")
            print(f"   Regime: {regime.get_state().regime.value}")
            print(f"   VWAP: ${vwap.get_state().vwap:,.0f} (zone: {vwap.get_state().zone})")
            print(f"   Volatility: {volatility.get_state().vol_regime}")
            
            print(f"\n   RECOMMENDATION: {rec.action.value}")
            print(f"   Reason: {rec.reason}")
            
            if rec.action in (Action.BUY, Action.SELL):
                print(f"\n   Size: ${rec.target_size_usd:,.0f}")
                print(f"   Entry: ${rec.entry_price_low:,.0f} - ${rec.entry_price_high:,.0f}")
                print(f"   Stop: ${rec.stop_loss:,.0f}")
                print(f"   Target: ${rec.take_profit:,.0f}")
        
        # Test position entry
        print("\n5. Testing Position Tracking...")
        
        # Simulate entering a position
        tracker.add_entry("long", size_usd=10000, entry_price=price)
        pos_state = tracker.get_state(price)
        
        print(f"   Added LONG $10,000 @ ${price:,.0f}")
        print(f"   Position: {pos_state.position.side} ${pos_state.position.size_usd:,.0f}")
        print(f"   P&L: ${pos_state.unrealized_pnl_usd:.2f}")
        
        # Reset position
        tracker.set_position(None, 0, 0)
        print("   Position reset to flat")
        
        print("\n" + "=" * 60)
        print("✅ Integration test PASSED")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        await adapter.close()


if __name__ == "__main__":
    success = asyncio.run(test_integration())
    sys.exit(0 if success else 1)
