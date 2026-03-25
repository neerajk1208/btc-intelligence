#!/usr/bin/env python3
"""
Test QuickTrade endpoint with a $100 WETH purchase.
Measures latency and compares to TURBO baseline.
"""

import asyncio
import aiohttp
import hmac
import hashlib
import json
import time
import os
import ssl
import certifi
from dotenv import load_dotenv

load_dotenv()

# Constants
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE = "0x4200000000000000000000000000000000000006"
QUICKTRADE_URL = "https://ddp.definitive.fi/v2/portfolio/quicktrade"
TURBO_URL = "https://api.definitive.fi/v1/orders"
QUOTE_URL = "https://api.definitive.fi/v1/orders/quote"

# Auth
API_KEY = os.getenv("DEFINITIVE_API_KEY")
API_SECRET = os.getenv("DEFINITIVE_API_SECRET")
PRIVY_TOKEN = os.getenv("PRIVY_ACCESS_TOKEN")
ORG_ID = os.getenv("DEFINITIVE_ORG_ID")
PORTFOLIO_ID = os.getenv("DEFINITIVE_PORTFOLIO_ID")
READ_TOKEN = os.getenv("DEFINITIVE_READ_TOKEN")

def get_quicktrade_headers(method: str, path: str, body: dict) -> dict:
    """Get QuickTrade headers with HMAC signing."""
    timestamp = str(int(time.time() * 1000))
    
    headers = {
        "x-definitive-api-key": API_KEY,
        "x-definitive-timestamp": timestamp,
    }
    
    # Sorted headers as "key:json_value" joined by comma
    sorted_headers = ",".join([
        f'{k}:{json.dumps(v)}' 
        for k, v in sorted(headers.items())
    ])
    
    # Body as JSON string
    body_str = json.dumps(body) if body else ""
    
    # Prehash: method:path?queryParams:timestamp:sortedHeaders{body}
    prehash = f"{method}:{path}?:{timestamp}:{sorted_headers}{body_str}"
    
    # Sign (strip dpks_ prefix)
    secret = API_SECRET.replace("dpks_", "") if API_SECRET else ""
    signature = hmac.new(
        secret.encode(),
        prehash.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return {
        "Content-Type": "application/json",
        "x-definitive-api-key": API_KEY,
        "x-definitive-timestamp": timestamp,
        "x-definitive-signature": signature,
    }

def get_privy_headers() -> dict:
    """Get Privy JWT headers for TURBO."""
    return {
        "Content-Type": "application/json",
        "privy-token": PRIVY_TOKEN,
        "organization-id": ORG_ID,
        "portfolio-id": PORTFOLIO_ID,
        "read-token": READ_TOKEN,
        "origin": "https://app.definitive.fi",
        "referer": "https://app.definitive.fi/",
    }

async def test_quicktrade(session: aiohttp.ClientSession, size_usd: float = 100.0, quoted_price: float = None):
    """Test QuickTrade endpoint."""
    print("\n" + "="*60)
    print("TESTING QUICKTRADE ENDPOINT")
    print("="*60)
    
    if quoted_price is None:
        print("ERROR: No quoted price provided!")
        return False, 0, "No price"
    
    path = "/v2/portfolio/quicktrade"
    payload = {
        "chain": "base",
        "targetAsset": WETH_BASE,
        "contraAsset": USDC_BASE,
        "qty": str(size_usd),
        "orderSide": "buy",
        "slippageTolerance": "0.001000",  # 10 bps
        "displayAssetPrice": str(quoted_price),  # Use actual quoted price
    }
    
    print(f"\nPayload: {json.dumps(payload, indent=2)}")
    
    headers = get_quicktrade_headers("POST", path, payload)
    print(f"\nHeaders: x-definitive-api-key={headers['x-definitive-api-key'][:20]}...")
    
    start = time.time()
    try:
        async with session.post(
            f"https://ddp.definitive.fi{path}",
            json=payload,
            headers=headers
        ) as resp:
            latency_ms = (time.time() - start) * 1000
            text = await resp.text()
            
            print(f"\n[QUICKTRADE] Status: {resp.status}")
            print(f"[QUICKTRADE] Latency: {latency_ms:.0f}ms")
            print(f"[QUICKTRADE] Response: {text[:500]}")
            
            if resp.status == 200:
                result = json.loads(text)
                order_id = result.get("orderId")
                print(f"\n[QUICKTRADE] SUCCESS! Order ID: {order_id}")
                return True, latency_ms, result
            else:
                print(f"\n[QUICKTRADE] FAILED!")
                return False, latency_ms, text
                
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        print(f"\n[QUICKTRADE] ERROR: {e}")
        return False, latency_ms, str(e)

async def test_turbo_quote(session: aiohttp.ClientSession, size_usd: float = 100.0):
    """Test TURBO quote endpoint for comparison."""
    print("\n" + "="*60)
    print("TESTING TURBO QUOTE (for baseline comparison)")
    print("="*60)
    
    payload = {
        "from": USDC_BASE,
        "to": WETH_BASE,
        "chain": "base",
        "toChain": "base",
        "qty": str(size_usd),
        "orderSide": "buy",
        "type": "market",
        "degenMode": False,
        "executionPreference": 2,
    }
    
    headers = get_privy_headers()
    
    start = time.time()
    try:
        async with session.post(QUOTE_URL, json=payload, headers=headers) as resp:
            latency_ms = (time.time() - start) * 1000
            text = await resp.text()
            
            print(f"\n[TURBO QUOTE] Status: {resp.status}")
            print(f"[TURBO QUOTE] Latency: {latency_ms:.0f}ms")
            
            if resp.status == 200:
                result = json.loads(text)
                quote_id = result.get("quoteId", "")[:30]
                buy_amount = result.get("buyAmount", "0")
                print(f"[TURBO QUOTE] quoteId: {quote_id}...")
                print(f"[TURBO QUOTE] buyAmount: {buy_amount}")
                return True, latency_ms, result
            else:
                print(f"[TURBO QUOTE] Response: {text[:300]}")
                return False, latency_ms, text
                
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        print(f"\n[TURBO QUOTE] ERROR: {e}")
        return False, latency_ms, str(e)

async def main():
    print("\n" + "#"*60)
    print("QUICKTRADE vs TURBO COMPARISON TEST")
    print("#"*60)
    
    # Check env vars
    print("\nChecking environment variables...")
    missing = []
    if not API_KEY: missing.append("DEFINITIVE_API_KEY")
    if not API_SECRET: missing.append("DEFINITIVE_API_SECRET")
    if not PRIVY_TOKEN: missing.append("PRIVY_ACCESS_TOKEN")
    if not ORG_ID: missing.append("DEFINITIVE_ORG_ID")
    if not PORTFOLIO_ID: missing.append("DEFINITIVE_PORTFOLIO_ID")
    
    if missing:
        print(f"ERROR: Missing env vars: {missing}")
        return
    
    print("All env vars present!")
    print(f"API_KEY: {API_KEY[:20]}...")
    print(f"API_SECRET: {API_SECRET[:15]}...")
    
    # Create SSL context
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        # Test TURBO quote first (doesn't execute, just gets quote)
        turbo_success, turbo_latency, turbo_result = await test_turbo_quote(session, 100)
        
        if not turbo_success:
            print("\nFailed to get TURBO quote. Cannot proceed with QuickTrade test.")
            return
        
        # Calculate actual price from quote
        buy_amount = float(turbo_result.get("buyAmount", 0))
        if buy_amount <= 0:
            print("\nInvalid buy amount from quote. Cannot proceed.")
            return
        
        quoted_price = 100.0 / buy_amount  # USDC / WETH = price per WETH
        print(f"\n[PRICE] Calculated from quote: ${quoted_price:.2f} per WETH")
        
        # Test QuickTrade (THIS WILL EXECUTE A REAL $100 TRADE!)
        print("\n" + "!"*60)
        print("WARNING: QuickTrade will execute a REAL $100 WETH purchase!")
        print(f"Using quoted price: ${quoted_price:.2f}")
        print("!"*60)
        
        confirm = input("\nProceed with QuickTrade test? (yes/no): ")
        if confirm.lower() != "yes":
            print("Aborted.")
            return
        
        qt_success, qt_latency, qt_result = await test_quicktrade(session, 100, quoted_price)
        
        # Summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"\nTURBO Quote Latency:    {turbo_latency:.0f}ms (quote only, no execution)")
        print(f"QuickTrade Latency:     {qt_latency:.0f}ms (quote + execution atomic)")
        
        if qt_success:
            print(f"\nQuickTrade executed successfully!")
            print("Check your Definitive portfolio for the WETH purchase.")
        else:
            print(f"\nQuickTrade failed. Check the error above.")

if __name__ == "__main__":
    asyncio.run(main())
