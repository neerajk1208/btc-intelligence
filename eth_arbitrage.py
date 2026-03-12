#!/usr/bin/env python3
"""
ETH Arbitrage: Hyperliquid vs Definitive Price Monitor

Compares ETH-PERP price on Hyperliquid with WETH spot price on Definitive (Base).
Uses Privy Bearer token authentication for Definitive API.

Usage:
    python eth_arbitrage.py
"""

import asyncio
import json
import os
import ssl
import time
from datetime import datetime
from typing import Optional

import aiohttp
import certifi
import websockets
from dotenv import load_dotenv

load_dotenv()


# =============================================================================
# Configuration
# =============================================================================

HYPERLIQUID_WS_URL = "wss://api.hyperliquid.xyz/ws"
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
DEFINITIVE_QUOTE_URL = "https://api.definitive.fi/v1/orders/quote"

# Base chain token addresses
USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
WETH_BASE = "0x4200000000000000000000000000000000000006"

# Polling interval - CONSERVATIVE to avoid rate limits
DEFINITIVE_POLL_INTERVAL = 15  # 4 requests per minute max

# Quote size in USD
QUOTE_SIZE_USD = 1000

# Alert threshold (basis points)
# Definitive fee: ~2 bps, Hyperliquid taker: 4.32 bps = ~6.5 bps round-trip
ALERT_THRESHOLD_BPS = 10  # Alert if spread > 0.10% (profitable after ~6.5bp fees)

# Rate limit protection
MAX_CONSECUTIVE_ERRORS = 3
BACKOFF_MULTIPLIER = 2
MAX_BACKOFF_SECONDS = 120


# =============================================================================
# State
# =============================================================================

class ArbitrageState:
    def __init__(self):
        self.hl_price: Optional[float] = None
        self.hl_last_update: float = 0
        self.hl_funding_rate: Optional[float] = None  # Current funding rate (hourly)
        self.hl_funding_annualized: Optional[float] = None  # Annualized funding
        self.hl_open_interest: Optional[float] = None  # Open interest in USD
        self.def_price: Optional[float] = None
        self.def_price_before_fees: Optional[float] = None  # Price before fees
        self.def_fee_bps: Optional[float] = None  # Total fee + impact in bps
        self.def_last_update: float = 0
        self.spread_history: list = []
        
    def spread_bps(self) -> Optional[float]:
        """Calculate spread in basis points (Definitive - Hyperliquid)."""
        if self.hl_price and self.def_price:
            return ((self.def_price - self.hl_price) / self.hl_price) * 10000
        return None
    
    def net_spread_bps(self) -> Optional[float]:
        """Spread minus Definitive fees (what you'd actually capture)."""
        spread = self.spread_bps()
        if spread is not None and self.def_fee_bps is not None:
            return spread - self.def_fee_bps
        return spread


state = ArbitrageState()


# =============================================================================
# Hyperliquid WebSocket (ETH-PERP price)
# =============================================================================

async def hyperliquid_stream():
    """Connect to Hyperliquid and stream ETH-PERP mid price."""
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    
    while True:
        try:
            print("[HL] Connecting to Hyperliquid WebSocket...")
            async with websockets.connect(HYPERLIQUID_WS_URL, ssl=ssl_ctx, ping_interval=20) as ws:
                print("[HL] Connected")
                
                sub = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": "ETH"}}
                await ws.send(json.dumps(sub))
                print("[HL] Subscribed to ETH-PERP orderbook")
                
                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if data.get("channel") == "l2Book":
                            payload = data.get("data", {})
                            if payload.get("coin") == "ETH":
                                levels = payload.get("levels", [])
                                if len(levels) >= 2 and levels[0] and levels[1]:
                                    best_bid = float(levels[0][0]["px"])
                                    best_ask = float(levels[1][0]["px"])
                                    mid = (best_bid + best_ask) / 2
                                    state.hl_price = mid
                                    state.hl_last_update = time.time()
                    except Exception as e:
                        print(f"[HL] Parse error: {e}")
                        
        except Exception as e:
            print(f"[HL] Connection error: {e}")
            await asyncio.sleep(2)


# =============================================================================
# Hyperliquid Funding Rate Polling
# =============================================================================

async def hyperliquid_funding_poll():
    """Poll Hyperliquid for ETH funding rate and open interest."""
    
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                # Fetch meta info (includes funding rates)
                payload = {"type": "metaAndAssetCtxs"}
                
                async with session.post(HYPERLIQUID_INFO_URL, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        # data[0] = meta info with universe
                        # data[1] = list of asset contexts
                        meta = data[0]
                        asset_ctxs = data[1]
                        
                        # Find ETH index
                        universe = meta.get("universe", [])
                        eth_idx = None
                        for i, asset in enumerate(universe):
                            if asset.get("name") == "ETH":
                                eth_idx = i
                                break
                        
                        if eth_idx is not None and eth_idx < len(asset_ctxs):
                            ctx = asset_ctxs[eth_idx]
                            
                            # Funding rate (hourly, as decimal)
                            funding_str = ctx.get("funding", "0")
                            funding_rate = float(funding_str)
                            state.hl_funding_rate = funding_rate * 100  # Convert to percentage
                            
                            # Annualized = hourly * 24 * 365
                            state.hl_funding_annualized = funding_rate * 24 * 365 * 100
                            
                            # Open interest
                            oi_str = ctx.get("openInterest", "0")
                            mark_px_str = ctx.get("markPx", "0")
                            oi_contracts = float(oi_str)
                            mark_px = float(mark_px_str)
                            state.hl_open_interest = oi_contracts * mark_px  # USD value
                    
            except Exception as e:
                print(f"[HL] Funding fetch error: {e}")
            
            # Poll every 60 seconds (funding updates hourly anyway)
            await asyncio.sleep(60)


# =============================================================================
# Definitive Quote Polling (WETH price on Base) - Bearer Token Auth
# =============================================================================

async def definitive_poll():
    """Poll Definitive for WETH quote price using Privy auth."""
    
    privy_token = os.getenv("PRIVY_ACCESS_TOKEN")
    privy_id_token = os.getenv("PRIVY_ID_TOKEN")
    org_id = os.getenv("DEFINITIVE_ORG_ID")
    portfolio_id = os.getenv("DEFINITIVE_PORTFOLIO_ID")
    read_token = os.getenv("DEFINITIVE_READ_TOKEN")
    
    missing = []
    if not privy_token:
        missing.append("PRIVY_ACCESS_TOKEN")
    if not privy_id_token:
        missing.append("PRIVY_ID_TOKEN")
    if not org_id:
        missing.append("DEFINITIVE_ORG_ID")
    if not portfolio_id:
        missing.append("DEFINITIVE_PORTFOLIO_ID")
    if not read_token:
        missing.append("DEFINITIVE_READ_TOKEN")
    
    if missing:
        print(f"[DEF] ERROR: Missing env vars: {', '.join(missing)}")
        print("[DEF] Get from: Definitive app → DevTools → Network → quote request → Headers/Cookies")
        return
    
    print(f"[DEF] Privy token: {privy_token[:20]}...{privy_token[-10:]}")
    print(f"[DEF] Org: {org_id}, Portfolio: {portfolio_id}")
    
    await asyncio.sleep(2)
    
    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    
    # Cookies required for proper routing/fees
    cookies = {
        "privy-token": privy_token,
        "privy-id-token": privy_id_token,
        "privy-session": "privy.definitive.fi",
    }
    
    consecutive_errors = 0
    current_interval = DEFINITIVE_POLL_INTERVAL
    
    async with aiohttp.ClientSession(connector=connector, cookies=cookies) as session:
        while True:
            try:
                # Exact format from Definitive frontend
                payload = {
                    "from": USDC_BASE,
                    "to": WETH_BASE,
                    "chain": "base",
                    "toChain": "base",
                    "qty": str(QUOTE_SIZE_USD),
                    "orderSide": "buy",
                    "type": "market",
                    "degenMode": False,
                    "executionPreference": 2,
                    "maxPriorityFee": None,
                    "xchainMaxSlippage": None,
                }
                
                headers = {
                    "Content-Type": "application/json",
                    "privy-token": privy_token,
                    "organization-id": org_id,
                    "portfolio-id": portfolio_id,
                    "read-token": read_token,
                    "origin": "https://app.definitive.fi",
                    "referer": "https://app.definitive.fi/",
                }
                
                body_str = json.dumps(payload, separators=(',', ':'))
                
                async with session.post(DEFINITIVE_QUOTE_URL, data=body_str, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        # Response format:
                        # buyAmount: WETH received after fees
                        # buyAmountBeforeFees: WETH before fees
                        # sellAmount: USDC spent
                        # estimatedFeeNotional: Fee in USD
                        buy_amount_str = data.get("buyAmount", "0")
                        buy_before_fees_str = data.get("buyAmountBeforeFees", "0")
                        sell_amount_str = data.get("sellAmount", "0")
                        fee_notional_str = data.get("estimatedFeeNotional", "0")
                        
                        buy_amount = float(buy_amount_str) if buy_amount_str else 0
                        buy_before_fees = float(buy_before_fees_str) if buy_before_fees_str else 0
                        sell_amount = float(sell_amount_str) if sell_amount_str else 0
                        fee_notional = float(fee_notional_str) if fee_notional_str else 0
                        
                        if buy_amount > 0 and sell_amount > 0:
                            # ETH price = USDC spent / WETH received
                            eth_price = sell_amount / buy_amount
                            state.def_price = eth_price
                            state.def_last_update = time.time()
                            
                            # Fee in bps from estimatedFeeNotional (more reliable)
                            if fee_notional > 0:
                                state.def_fee_bps = (fee_notional / sell_amount) * 10000
                            elif buy_before_fees > 0:
                                # Fallback: calculate from WETH difference
                                fee_pct = (buy_before_fees - buy_amount) / buy_before_fees
                                state.def_fee_bps = fee_pct * 10000
                        
                        consecutive_errors = 0
                        current_interval = DEFINITIVE_POLL_INTERVAL
                            
                    elif resp.status == 401:
                        text = await resp.text()
                        print(f"[DEF] AUTH ERROR (401): {text[:200]}")
                        print("[DEF] Token expired. Get fresh token from Definitive localStorage.")
                        consecutive_errors += 1
                        current_interval = min(current_interval * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)
                        
                    elif resp.status == 429:
                        consecutive_errors += 1
                        current_interval = min(current_interval * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)
                        print(f"[DEF] RATE LIMITED! Backing off to {current_interval}s")
                        
                    else:
                        text = await resp.text()
                        print(f"[DEF] HTTP {resp.status}: {text[:200]}")
                        consecutive_errors += 1
                        
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors <= 2:
                    print(f"[DEF] Error: {e}")
                
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    current_interval = min(current_interval * BACKOFF_MULTIPLIER, MAX_BACKOFF_SECONDS)
            
            await asyncio.sleep(current_interval)


# =============================================================================
# Spread Logger
# =============================================================================

async def spread_logger():
    """Log spread between Hyperliquid and Definitive."""
    
    print("\n[MONITOR] Waiting for prices from both venues...")
    while state.hl_price is None or state.def_price is None:
        await asyncio.sleep(1)
    
    print("[MONITOR] Both prices received. Starting spread logging.\n")
    print("  Fees: DEF ~2bp + HL 4.32bp = 6.32bp round-trip\n")
    print("=" * 115)
    print(f"{'Time':<10} {'HL PERP':>11} {'DEF WETH':>11} {'Spread':>8} {'Fees':>7} {'P/L':>7} {'Funding':>9} {'Ann.':>7} {'OI':>8}")
    print("=" * 115)
    
    while True:
        if state.hl_price and state.def_price:
            spread = state.spread_bps()
            net_spread = state.net_spread_bps()
            now = datetime.now().strftime("%H:%M:%S")
            
            # Track history (last hour)
            state.spread_history.append((time.time(), state.hl_price, state.def_price, spread))
            cutoff = time.time() - 3600
            state.spread_history = [(t, h, d, s) for t, h, d, s in state.spread_history if t > cutoff]
            
            # Fee and P/L display
            hl_fee_bps = 4.32
            if state.def_fee_bps is not None:
                total_fees = state.def_fee_bps + hl_fee_bps
                profit_bps = spread - total_fees
                fee_str = f"{total_fees:.1f}bp"
                pl_str = f"{profit_bps:+.1f}bp"
            else:
                fee_str = "~6bp"
                pl_str = f"{spread - 6.32:+.1f}bp" if spread else "..."
            
            # Funding rate display
            if state.hl_funding_rate is not None:
                funding_str = f"{state.hl_funding_rate:+.4f}%"
                ann_str = f"{state.hl_funding_annualized:+.1f}%"
            else:
                funding_str = "..."
                ann_str = "..."
            
            # Open interest display
            if state.hl_open_interest is not None:
                oi_millions = state.hl_open_interest / 1_000_000
                oi_str = f"${oi_millions:.0f}M"
            else:
                oi_str = "..."
            
            # Alert if profitable after all fees (spread > 6.32bp)
            profit_after_fees = spread - 6.32 if spread else 0
            alert = " *** PROFIT" if profit_after_fees > 0 else ""
            
            print(f"{now:<10} ${state.hl_price:>9,.2f} ${state.def_price:>9,.2f} {spread:>+7.1f}bp {fee_str:>7} {pl_str:>7} {funding_str:>9} {ann_str:>7} {oi_str:>8}{alert}")
            
            # Stats every 2 minutes (8 samples at 15s interval)
            if len(state.spread_history) > 0 and len(state.spread_history) % 8 == 0:
                spreads = [s for _, _, _, s in state.spread_history]
                avg = sum(spreads) / len(spreads)
                min_s = min(spreads)
                max_s = max(spreads)
                print(f"\n[STATS] {len(spreads)} samples: Avg={avg:+.2f}bp, Min={min_s:+.2f}bp, Max={max_s:+.2f}bp")
                
                # Funding context
                if state.hl_funding_rate is not None:
                    if state.hl_funding_rate > 0.01:
                        print(f"[FUNDING] Positive ({state.hl_funding_rate:+.4f}%) = longs paying shorts = perp premium expected")
                    elif state.hl_funding_rate < -0.01:
                        print(f"[FUNDING] Negative ({state.hl_funding_rate:+.4f}%) = shorts paying longs = perp discount expected")
                    else:
                        print(f"[FUNDING] Neutral ({state.hl_funding_rate:+.4f}%) = balanced market")
                print()
        
        await asyncio.sleep(DEFINITIVE_POLL_INTERVAL)


# =============================================================================
# Main
# =============================================================================

async def main():
    print("\n" + "=" * 85)
    print("  ETH ARBITRAGE MONITOR: Hyperliquid vs Definitive")
    print("=" * 85)
    print(f"\n  Hyperliquid: ETH-PERP mid price (WebSocket)")
    print(f"  Definitive:  WETH spot on Base (Quote API, ${QUOTE_SIZE_USD} size)")
    print(f"  Poll interval: {DEFINITIVE_POLL_INTERVAL}s")
    print(f"  Alert threshold: {ALERT_THRESHOLD_BPS} bps ({ALERT_THRESHOLD_BPS/100:.2f}%)")
    print("\n  Press Ctrl+C to stop\n")
    
    await asyncio.gather(
        hyperliquid_stream(),
        hyperliquid_funding_poll(),
        definitive_poll(),
        spread_logger(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nStopped.")
        
        if state.spread_history:
            spreads = [s for _, _, _, s in state.spread_history]
            print(f"\n[FINAL STATS]")
            print(f"  Samples: {len(spreads)}")
            print(f"  Average spread: {sum(spreads)/len(spreads):+.2f} bps")
            print(f"  Min spread: {min(spreads):+.2f} bps")
            print(f"  Max spread: {max(spreads):+.2f} bps")
            opportunities = len([s for s in spreads if abs(s) > ALERT_THRESHOLD_BPS])
            print(f"  Opportunities (>{ALERT_THRESHOLD_BPS}bp): {opportunities} ({100*opportunities/len(spreads):.1f}%)")
