"""
Quick SOL spread test - DEF vs HL
"""
import asyncio
import aiohttp
import os
import time
import json
import websockets
from dotenv import load_dotenv

load_dotenv()

# Token addresses on Base
SOL_BASE = "0x1C61629598e4a901136a81BC138E5828dc150d67"  # SOL on Base (wrapped)
WETH_BASE = "0x4200000000000000000000000000000000000006"  # WETH on Base
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# Which asset to test
TEST_ASSET = "ETH"  # "SOL" or "ETH"
USE_BBO = True  # Use BBO instead of allMids
TOKEN_ADDRESS = WETH_BASE if TEST_ASSET == "ETH" else SOL_BASE

async def get_def_sol_price(session, headers):
    """Get SOL price from Definitive PRIME quote."""
    quote_payload = {
        "from": USDC_BASE,
        "to": TOKEN_ADDRESS,
        "chain": "base",
        "toChain": "base",
        "qty": "100",  # $100 quote
        "orderSide": "buy",
        "type": "market",
        "degenMode": False,
        "executionPreference": 2,
    }
    
    start = time.time()
    try:
        async with session.post(
            "https://api.definitive.fi/v1/orders/quote",
            headers=headers,
            json=quote_payload
        ) as resp:
            latency = (time.time() - start) * 1000
            text = await resp.text()
            if resp.status == 200:
                data = json.loads(text)
                # price field is SOL per USDC, so invert to get USDC per SOL
                price_raw = float(data.get("price", 0))
                if price_raw > 0:
                    price = 1 / price_raw  # USDC per SOL
                    return price, latency
                else:
                    print(f"DEF no price: {text[:300]}")
            else:
                print(f"DEF error {resp.status}: {text[:300]}")
    except Exception as e:
        print(f"DEF exception: {e}")
    return None, 0

async def get_hl_price_rest(session):
    """Get price from HL REST API."""
    start = time.time()
    try:
        async with session.post(
            "https://api.hyperliquid.xyz/info",
            json={"type": "allMids"}
        ) as resp:
            latency = (time.time() - start) * 1000
            mids = await resp.json()
            price = float(mids.get(TEST_ASSET, 0))
            return price, latency
    except Exception as e:
        print(f"HL REST exception: {e}")
    return None, 0

async def get_hl_price_ws():
    """Get price from HL WebSocket (single snapshot)."""
    try:
        start = time.time()
        async with websockets.connect("wss://api.hyperliquid.xyz/ws") as ws:
            connect_time = (time.time() - start) * 1000
            
            # Subscribe to allMids
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "allMids"}}))
            
            # Wait for first message with target asset
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                data = json.loads(msg)
                if data.get("channel") == "allMids":
                    mids = data.get("data", {}).get("mids", {})
                    if TEST_ASSET in mids:
                        price = float(mids[TEST_ASSET])
                        total_time = (time.time() - start) * 1000
                        return price, total_time, connect_time
    except Exception as e:
        print(f"HL WS exception: {e}")
    return None, 0, 0

async def run_spread_test(test_num, session, headers, hl_ws_price_holder):
    """Run a single spread test."""
    print(f"\n--- Test {test_num} ---")
    
    # Fetch DEF price
    def_start = time.time()
    def_price, def_latency = await get_def_sol_price(session, headers)
    
    # Get HL price from WebSocket cache (should be nearly instant)
    hl_price = hl_ws_price_holder.get("price")
    hl_age = (time.time() - hl_ws_price_holder.get("time", 0)) * 1000
    
    if def_price and hl_price:
        spread_bps = ((def_price - hl_price) / hl_price) * 10000
        print(f"DEF SOL: ${def_price:.4f} (latency: {def_latency:.0f}ms)")
        print(f"HL  SOL: ${hl_price:.4f} (age: {hl_age:.0f}ms)")
        print(f"SPREAD: {spread_bps:+.2f} bps")
        return spread_bps, def_latency, hl_age
    else:
        print(f"Failed - DEF: {def_price}, HL: {hl_price}")
        return None, def_latency, hl_age

async def ws_price_updater(price_holder, stop_event):
    """Background task to keep HL WebSocket price updated using BBO."""
    try:
        async with websockets.connect("wss://api.hyperliquid.xyz/ws") as ws:
            # Subscribe to BBO for the specific asset
            await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "bbo", "coin": TEST_ASSET}}))
            print(f"[WS] Connected to HL WebSocket (BBO) for {TEST_ASSET}")
            
            first_msg = True
            while not stop_event.is_set():
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1)
                    data = json.loads(msg)
                    
                    # Log subscription confirmation only
                    if data.get("channel") == "subscriptionResponse":
                        print(f"[WS] Subscribed successfully")
                    
                    if data.get("channel") == "bbo":
                        msg_data = data.get("data", {})
                        coin = msg_data.get("coin", "")
                        if coin == TEST_ASSET:
                            bbo = msg_data.get("bbo", [])
                            if len(bbo) >= 2:
                                bid = float(bbo[0].get("px", 0))
                                ask = float(bbo[1].get("px", 0))
                                if bid > 0 and ask > 0:
                                    price_holder["price"] = (bid + ask) / 2
                                    price_holder["time"] = time.time()
                except asyncio.TimeoutError:
                    continue
    except Exception as e:
        print(f"[WS] Error: {e}")

async def main():
    print("="*60)
    print(f"{TEST_ASSET} SPREAD TEST: Definitive vs Hyperliquid")
    print("="*60)
    
    # Setup session with Privy auth
    access_token = os.getenv("PRIVY_ACCESS_TOKEN")
    id_token = os.getenv("PRIVY_ID_TOKEN")
    portfolio_id = os.getenv("DEFINITIVE_PORTFOLIO_ID")
    
    if not access_token or not id_token:
        print(f"ERROR: Missing tokens. access={bool(access_token)}, id={bool(id_token)}")
        return
    
    headers = {
        "Content-Type": "application/json",
        "privy-token": access_token,
        "Authorization": f"Bearer {id_token}",
        "x-portfolio-id": portfolio_id,
    }
    
    # Setup cookies
    cookies = {
        "privy-token": access_token,
        "privy-id-token": id_token,
    }
    
    # Price holder for WebSocket
    price_holder = {"price": None, "time": 0}
    stop_event = asyncio.Event()
    
    # Create cookie jar with auth cookies
    jar = aiohttp.CookieJar()
    async with aiohttp.ClientSession(cookie_jar=jar) as session:
        # Set cookies on the definitive domain
        session.cookie_jar.update_cookies(cookies, response_url=aiohttp.client.URL("https://api.definitive.fi"))
        # Start WebSocket updater
        ws_task = asyncio.create_task(ws_price_updater(price_holder, stop_event))
        
        # Wait for first price (BBO updates on activity, might take longer)
        print("\nWaiting for WebSocket price...")
        for i in range(100):  # 10 second timeout
            if price_holder["price"]:
                print(f"[WS] First {TEST_ASSET} price: ${price_holder['price']:.4f}")
                break
            if i == 30:
                print("[WS] Still waiting for BBO update...")
            await asyncio.sleep(0.1)
        
        if not price_holder["price"]:
            print("ERROR: Could not get HL WebSocket price")
            stop_event.set()
            return
        
        # Run 10 tests with 2 second gaps to avoid rate limiting
        results = []
        for i in range(10):
            result = await run_spread_test(i+1, session, headers, price_holder)
            results.append(result)
            await asyncio.sleep(2)  # 2 seconds between tests to avoid rate limit
        
        # Stop WebSocket
        stop_event.set()
        await asyncio.sleep(0.5)
        
        # Summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        
        valid_results = [r for r in results if r[0] is not None]
        if valid_results:
            spreads = [r[0] for r in valid_results]
            def_latencies = [r[1] for r in valid_results]
            hl_ages = [r[2] for r in valid_results]
            
            print(f"Tests completed: {len(valid_results)}/5")
            print(f"Spread range: {min(spreads):+.2f} to {max(spreads):+.2f} bps")
            print(f"Spread avg: {sum(spreads)/len(spreads):+.2f} bps")
            print(f"DEF latency avg: {sum(def_latencies)/len(def_latencies):.0f}ms")
            print(f"HL WS age avg: {sum(hl_ages)/len(hl_ages):.0f}ms")
        else:
            print("No valid results")

if __name__ == "__main__":
    asyncio.run(main())
