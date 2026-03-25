#!/usr/bin/env python3
"""
Spread Test - Matches exact arb_engine price check flow.

Usage:
    python test_spread.py ETH    # WETH on Base vs ETH on HL
    python test_spread.py BTC    # WBTC on Base vs BTC on HL  
    python test_spread.py SOL    # SOL on Solana vs SOL on HL
"""

import asyncio
import aiohttp
import ssl
import certifi
import json
import time
import sys
import os
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# ASSET CONFIGURATION - Easy swap
# =============================================================================
ASSETS = {
    "ETH": {
        "token_address": "0x4200000000000000000000000000000000000006",  # WETH on Base
        "chain": "base",
        "hl_symbol": "ETH",
    },
    "BTC": {
        "token_address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",  # cbBTC on Base
        "chain": "base",
        "hl_symbol": "BTC",
    },
    "SOL": {
        "token_address": "So11111111111111111111111111111111111111112",  # Native SOL
        "chain": "solana",
        "hl_symbol": "SOL",
    },
}

USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_SOLANA = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# =============================================================================
# GLOBAL STATE (mirrors arb_engine)
# =============================================================================
_hl_ws_price: float = 0.0
_hl_ws_price_time: float = 0.0
_hl_ws_connected: bool = False


def _on_hl_ws_message(msg: str, hl_symbol: str):
    """Parse HL WebSocket message - EXACT copy from arb_engine."""
    global _hl_ws_price, _hl_ws_price_time
    try:
        data = json.loads(msg)
        if data.get("channel") == "bbo":
            msg_data = data.get("data", {})
            coin = msg_data.get("coin", "")
            if coin == hl_symbol:
                bbo = msg_data.get("bbo", [])
                if len(bbo) >= 2:
                    bid = float(bbo[0].get("px", 0))
                    ask = float(bbo[1].get("px", 0))
                    if bid > 0 and ask > 0:
                        _hl_ws_price = (bid + ask) / 2
                        _hl_ws_price_time = time.time()
    except Exception:
        pass


def _get_hl_ws_price() -> float:
    """Get HL WebSocket price - EXACT copy from arb_engine."""
    global _hl_ws_connected, _hl_ws_price, _hl_ws_price_time
    if not _hl_ws_connected or _hl_ws_price == 0:
        return None
    age = time.time() - _hl_ws_price_time
    if age > 5.0:
        return None
    return _hl_ws_price


async def _run_hl_websocket(hl_symbol: str, stop_event: asyncio.Event):
    """Run HL WebSocket - mirrors arb_engine._hl_ws_task."""
    global _hl_ws_connected
    import websockets
    
    url = "wss://api.hyperliquid.xyz/ws"
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    
    try:
        async with websockets.connect(url, ssl=ssl_ctx) as ws:
            _hl_ws_connected = True
            print(f"[WS] Connected to HL WebSocket for {hl_symbol}")
            
            sub_msg = {
                "method": "subscribe",
                "subscription": {"type": "bbo", "coin": hl_symbol}
            }
            await ws.send(json.dumps(sub_msg))
            
            while not stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    _on_hl_ws_message(msg, hl_symbol)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    print(f"[WS] Error: {e}")
                    break
    except Exception as e:
        print(f"[WS] Connection error: {e}")
    finally:
        _hl_ws_connected = False


async def get_prices(session, asset_config: dict) -> tuple:
    """
    Get synchronized HL and DEF prices - EXACT flow from arb_engine.get_prices().
    
    Returns: (hl_price, def_price, price_age_ms, def_latency_ms)
    """
    global _hl_ws_price_time
    
    hl_price = None
    def_price = None
    prime_latency = 0
    
    # Determine USDC address based on chain
    usdc_address = USDC_SOLANA if asset_config["chain"] == "solana" else USDC_BASE
    
    # Fetch PRIME quote from DEF
    start = time.time()
    quote_payload = {
        "from": usdc_address,
        "to": asset_config["token_address"],
        "chain": asset_config["chain"],
        "toChain": asset_config["chain"],
        "qty": "100",  # $100 quote
        "orderSide": "buy",
        "type": "market",
        "degenMode": False,
        "executionPreference": 2,
    }
    
    try:
        async with session.post(
            "https://api.definitive.fi/v1/orders/quote",
            json=quote_payload
        ) as resp:
            prime_latency = (time.time() - start) * 1000
            
            if resp.status == 200:
                data = await resp.json()
                buy_amount = float(data.get("buyAmount", 0))
                if buy_amount > 0:
                    def_price = 100.0 / buy_amount  # USDC per token
            else:
                text = await resp.text()
                print(f"[DEF] Quote error {resp.status}: {text[:200]}")
    except Exception as e:
        prime_latency = (time.time() - start) * 1000
        print(f"[DEF] Quote exception: {e}")
    
    # Get HL price from WebSocket (instant, ~0ms gap) - RIGHT AFTER DEF returns
    hl_price = _get_hl_ws_price()
    price_age_ms = (time.time() - _hl_ws_price_time) * 1000 if _hl_ws_price_time > 0 else 9999
    
    return hl_price, def_price, price_age_ms, prime_latency


async def run_spread_test(asset: str, num_tests: int = 10):
    """Run spread tests for specified asset."""
    
    if asset not in ASSETS:
        print(f"Unknown asset: {asset}")
        print(f"Available: {list(ASSETS.keys())}")
        return
    
    asset_config = ASSETS[asset]
    hl_symbol = asset_config["hl_symbol"]
    
    print("=" * 60)
    print(f"SPREAD TEST: {asset} (DEF {asset_config['chain']} vs HL {hl_symbol})")
    print("=" * 60)
    
    # Setup session with Privy auth - matches arb_engine
    privy_token = os.getenv("PRIVY_ACCESS_TOKEN")
    privy_id_token = os.getenv("PRIVY_ID_TOKEN")
    org_id = os.getenv("DEFINITIVE_ORG_ID")
    portfolio_id = os.getenv("DEFINITIVE_PORTFOLIO_ID")
    read_token = os.getenv("DEFINITIVE_READ_TOKEN")
    
    if not privy_token:
        print("ERROR: Missing PRIVY_ACCESS_TOKEN")
        return
    
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    
    cookies = {
        "privy-token": privy_token,
        "privy-id-token": privy_id_token or "",
        "privy-session": "privy.definitive.fi",
    }
    
    headers = {
        "Content-Type": "application/json",
        "privy-token": privy_token,
        "organization-id": org_id or "",
        "portfolio-id": portfolio_id or "",
        "read-token": read_token or "",
        "origin": "https://app.definitive.fi",
        "referer": "https://app.definitive.fi/",
    }
    
    stop_event = asyncio.Event()
    
    async with aiohttp.ClientSession(connector=connector, cookies=cookies, headers=headers) as session:
        # Start WebSocket
        ws_task = asyncio.create_task(_run_hl_websocket(hl_symbol, stop_event))
        
        # Wait for first price
        print("\nWaiting for HL WebSocket price...")
        for i in range(50):
            if _get_hl_ws_price():
                print(f"[WS] First {hl_symbol} price: ${_hl_ws_price:.2f}")
                break
            await asyncio.sleep(0.1)
        
        if not _get_hl_ws_price():
            print("ERROR: No HL WebSocket price after 5s")
            stop_event.set()
            return
        
        # Run tests
        results = []
        print(f"\nRunning {num_tests} spread tests...\n")
        
        for i in range(num_tests):
            hl_price, def_price, price_age_ms, def_latency = await get_prices(session, asset_config)
            
            if hl_price and def_price:
                spread_bps = ((def_price - hl_price) / hl_price) * 10000
                print(f"Test {i+1:2d} | DEF: ${def_price:.2f} | HL: ${hl_price:.2f} | "
                      f"Spread: {spread_bps:+6.2f}bp | DEF: {def_latency:.0f}ms | HL age: {price_age_ms:.0f}ms")
                results.append({
                    "spread_bps": spread_bps,
                    "def_latency": def_latency,
                    "hl_age": price_age_ms,
                    "def_price": def_price,
                    "hl_price": hl_price,
                })
            else:
                print(f"Test {i+1:2d} | FAILED - DEF: {def_price}, HL: {hl_price}")
            
            await asyncio.sleep(2)  # Avoid rate limits
        
        # Stop WebSocket
        stop_event.set()
        await asyncio.sleep(0.5)
        
        # Summary
        if results:
            spreads = [r["spread_bps"] for r in results]
            def_latencies = [r["def_latency"] for r in results]
            hl_ages = [r["hl_age"] for r in results]
            
            print("\n" + "=" * 60)
            print("SUMMARY")
            print("=" * 60)
            print(f"Tests completed: {len(results)}/{num_tests}")
            print(f"Spread:  min={min(spreads):+.2f}bp  max={max(spreads):+.2f}bp  avg={sum(spreads)/len(spreads):+.2f}bp")
            print(f"DEF latency avg: {sum(def_latencies)/len(def_latencies):.0f}ms")
            print(f"HL age avg: {sum(hl_ages)/len(hl_ages):.0f}ms")


def main():
    asset = sys.argv[1].upper() if len(sys.argv) > 1 else "ETH"
    num_tests = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    asyncio.run(run_spread_test(asset, num_tests))


if __name__ == "__main__":
    main()
