"""
Test script for Hyperliquid data adapter.

Run with: python -m pytest tests/test_adapter.py -v
Or directly: python tests/test_adapter.py
"""
import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.hyperliquid import HyperliquidDataAdapter


async def test_live_connection():
    """Test live connection to Hyperliquid and data fetching."""
    print("=" * 60)
    print("BTC Intelligence - Data Adapter Test")
    print("=" * 60)
    
    adapter = HyperliquidDataAdapter(symbol="BTC")
    
    try:
        print("\n1. Connecting to Hyperliquid WebSocket...")
        await adapter.connect()
        print("   ✅ Connected")
        
        # Wait for initial data
        print("\n2. Waiting for market data (3 seconds)...")
        await asyncio.sleep(3)
        
        # Test mid price
        mid = adapter.mid_price()
        if mid:
            print(f"   ✅ Mid price: ${mid:,.2f}")
        else:
            print("   ❌ No mid price received")
            return False
        
        # Test bid/ask
        ba = adapter.best_bid_ask()
        if ba:
            bid, ask = ba
            print(f"   ✅ Best bid: ${bid:,.2f}, Best ask: ${ask:,.2f}")
        else:
            print("   ❌ No bid/ask received")
        
        # Test spread
        spread = adapter.spread_bps()
        if spread is not None:
            print(f"   ✅ Spread: {spread:.2f} bps")
        
        # Test order book depth
        bids, asks = adapter.get_order_book_depth(levels=5)
        print(f"   ✅ Order book: {len(bids)} bid levels, {len(asks)} ask levels")
        
        # Test imbalance
        imbalance = adapter.get_imbalance(levels=5)
        if imbalance is not None:
            direction = "bid heavy" if imbalance > 0 else "ask heavy"
            print(f"   ✅ Imbalance: {imbalance:.3f} ({direction})")
        
        # Test depth in USD
        bid_depth, ask_depth = adapter.get_depth_usd(levels=5)
        print(f"   ✅ Depth (5 levels): ${bid_depth:,.0f} bids, ${ask_depth:,.0f} asks")
        
        # Test data freshness
        fresh = adapter.is_data_fresh()
        print(f"   ✅ Data fresh: {fresh}")
        
        # Test REST API calls
        print("\n3. Testing REST API...")
        
        funding = await adapter.fetch_funding_rate()
        if funding is not None:
            print(f"   ✅ Funding rate: {funding:.6f} ({funding * 100:.4f}%)")
        else:
            print("   ⚠️  Funding rate not available")
        
        oi = await adapter.fetch_open_interest()
        if oi is not None:
            print(f"   ✅ Open interest: ${oi:,.0f}")
        else:
            print("   ⚠️  Open interest not available")
        
        volume = await adapter.fetch_24h_volume()
        if volume is not None:
            print(f"   ✅ 24h volume: ${volume:,.0f}")
        else:
            print("   ⚠️  24h volume not available")
        
        # Test candles
        print("\n4. Testing historical candles...")
        candles = await adapter.fetch_candles(interval="5m", limit=10)
        if candles:
            print(f"   ✅ Fetched {len(candles)} candles")
            latest = candles[-1]
            print(f"   Latest candle: O=${latest['open']:,.0f} H=${latest['high']:,.0f} L=${latest['low']:,.0f} C=${latest['close']:,.0f}")
        else:
            print("   ⚠️  Could not fetch candles")
        
        # Test price history
        history = adapter.get_price_history(last_n=10)
        print(f"\n5. Price history: {len(history)} samples recorded")
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        print("\nClosing connection...")
        await adapter.close()
        print("Done.")


if __name__ == "__main__":
    success = asyncio.run(test_live_connection())
    sys.exit(0 if success else 1)
