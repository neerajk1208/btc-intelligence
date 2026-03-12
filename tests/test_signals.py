"""
Test script for all signal modules.

Run with: python tests/test_signals.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.hyperliquid import HyperliquidDataAdapter
from signals.regime import RegimeDetector, MarketRegime
from signals.vwap import VWAPCalculator
from signals.volatility import VolatilityAnalyzer


async def test_signals_live():
    """Test all signal modules with live BTC data."""
    print("=" * 60)
    print("BTC Intelligence - Signal Modules Test")
    print("=" * 60)
    
    # Initialize components
    adapter = HyperliquidDataAdapter(symbol="BTC")
    regime = RegimeDetector()
    vwap = VWAPCalculator()
    volatility = VolatilityAnalyzer()
    
    try:
        # Connect to data source
        print("\n1. Connecting to Hyperliquid...")
        await adapter.connect()
        print("   ✅ Connected")
        
        # Collect data for 10 seconds
        print("\n2. Collecting price data (10 seconds)...")
        samples_collected = 0
        
        for i in range(10):
            await asyncio.sleep(1)
            
            price = adapter.mid_price()
            if price:
                # Feed to all signal modules
                regime.update(price)
                vwap.update(price)
                volatility.update_price(price)
                samples_collected += 1
                print(f"   Sample {i+1}: ${price:,.2f}")
        
        print(f"\n   ✅ Collected {samples_collected} samples")
        
        # Get current states
        print("\n3. Signal States:")
        print("-" * 40)
        
        # Regime
        regime_state = regime.get_state()
        print(f"\n   REGIME DETECTOR:")
        print(f"   Regime: {regime_state.regime.value}")
        print(f"   Momentum: {regime_state.momentum_bps:.2f} bps")
        print(f"   Direction: {regime_state.momentum_direction or 'none'}")
        print(f"   Structure: {regime_state.structure_signal}")
        print(f"   Volatility Mode: {regime_state.volatility_mode}")
        print(f"   Strategy: {regime.get_strategy_recommendation()}")
        
        # VWAP
        vwap_state = vwap.get_state()
        print(f"\n   VWAP CALCULATOR:")
        print(f"   VWAP: ${vwap_state.vwap:,.2f}")
        print(f"   Deviation: {vwap_state.deviation_sigma:.2f}σ ({vwap_state.deviation_pct*100:.3f}%)")
        print(f"   Zone: {vwap_state.zone}")
        print(f"   Upper 1σ: ${vwap_state.upper_1sigma:,.2f}")
        print(f"   Lower 1σ: ${vwap_state.lower_1sigma:,.2f}")
        print(f"   Samples: {vwap_state.samples_count}")
        
        # Volatility
        vol_state = volatility.get_state()
        print(f"\n   VOLATILITY ANALYZER:")
        print(f"   ATR: ${vol_state.atr:.2f} ({vol_state.atr_bps:.1f} bps)")
        print(f"   ATR Ratio: {vol_state.atr_ratio:.2f}x baseline")
        print(f"   Vol Regime: {vol_state.vol_regime}")
        print(f"   BB Width: {vol_state.bb_width*100:.2f}%")
        print(f"   Squeeze: {vol_state.is_squeeze} (strength: {vol_state.squeeze_strength:.2f})")
        print(f"   Size Mult: {vol_state.size_multiplier:.2f}x")
        print(f"   Should Pause: {vol_state.should_pause}")
        
        # Combined recommendation
        print("\n4. Combined Analysis:")
        print("-" * 40)
        
        current_price = adapter.mid_price()
        if current_price:
            entry_levels = vwap.get_entry_levels(current_price)
            
            print(f"\n   Current Price: ${current_price:,.2f}")
            print(f"   Buy Zone: ${entry_levels['buy_zone'][0]:,.2f} - ${entry_levels['buy_zone'][1]:,.2f}")
            print(f"   Sell Zone: ${entry_levels['sell_zone'][0]:,.2f} - ${entry_levels['sell_zone'][1]:,.2f}")
            print(f"   Distance to Buy: ${entry_levels['distance_to_buy']:,.2f}")
            print(f"   Distance to Sell: ${entry_levels['distance_to_sell']:,.2f}")
            
            # Simple recommendation logic
            strategy = regime.get_strategy_recommendation()
            zone = vwap_state.zone
            
            print(f"\n   RECOMMENDATION:")
            if vol_state.should_pause:
                print(f"   ⏸️  SIT OUT - Extreme volatility")
            elif strategy == "SIT_OUT":
                print(f"   ⏸️  SIT OUT - News shock regime")
            elif strategy == "BUY_PULLBACKS" and zone == "buy":
                print(f"   🟢 BUY - Trending up + at buy zone")
            elif strategy == "SELL_RALLIES" and zone == "sell":
                print(f"   🔴 SELL - Trending down + at sell zone")
            elif strategy == "MEAN_REVERSION":
                if zone == "buy":
                    print(f"   🟢 BUY - Mean reversion at buy zone")
                elif zone == "sell":
                    print(f"   🔴 SELL - Mean reversion at sell zone")
                else:
                    print(f"   ⏳ WAIT - Choppy but not at entry zone")
            else:
                print(f"   ⏳ WAIT - {strategy}, zone={zone}")
        
        print("\n" + "=" * 60)
        print("✅ All signal modules working!")
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
    success = asyncio.run(test_signals_live())
    sys.exit(0 if success else 1)
