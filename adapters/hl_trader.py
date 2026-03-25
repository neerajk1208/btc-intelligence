"""
Hyperliquid Taker Order Module

Minimal module for placing taker (market-like) orders on Hyperliquid ETH perp.

Environment Variables:
- HL_API_SECRET: Private key for signing orders (hex string, with or without 0x prefix)
- ENABLE_LIVE_TRADING: Set to "true" to enable real orders (default: false = dry run)

Usage:
    from adapters.hl_trader import HLTrader
    
    trader = HLTrader()
    await trader.connect()
    
    # Place a $10 buy (taker)
    result = await trader.taker_order("buy", size_usd=10)
"""

import asyncio
import os
from typing import Optional, Dict, Any

from dotenv import load_dotenv

load_dotenv()


class HLTrader:
    """
    Minimal Hyperliquid trader for taker orders on ETH perp.
    """
    
    SYMBOL = "ETH"
    TICK_SIZE = 0.1      # ETH tick size
    LOT_SIZE = 0.001     # Minimum 0.001 ETH
    MIN_NOTIONAL = 10.0  # $10 minimum
    
    def __init__(self):
        self._sdk = None
        self._info = None
        self._address = None
        self._mid_price: Optional[float] = None
        
    async def connect(self) -> bool:
        """Initialize SDK and fetch current price."""
        pk = os.getenv("HL_API_SECRET")
        if not pk:
            print("[HL] ERROR: HL_API_SECRET not set")
            return False
        
        # Ensure proper format
        if not pk.startswith("0x"):
            pk = "0x" + pk
            
        try:
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.info import Info
            
            account = Account.from_key(pk)
            self._address = account.address
            print(f"[HL] Wallet: {self._address}")
            
            self._sdk = Exchange(account, base_url="https://api.hyperliquid.xyz")
            self._info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
            
            # Fetch current mid price
            await self._update_price()
            
            if self._is_live():
                print("[HL] LIVE TRADING ENABLED")
            else:
                print("[HL] Dry-run mode (set ENABLE_LIVE_TRADING=true for real orders)")
            
            return True
            
        except ImportError as e:
            print(f"[HL] Missing dependency: {e}")
            print("[HL] Run: pip install hyperliquid-python-sdk eth-account")
            return False
        except Exception as e:
            print(f"[HL] Connection failed: {e}")
            return False
    
    async def _update_price(self) -> Optional[float]:
        """Fetch current ETH mid price from REST API."""
        try:
            result = self._info.meta_and_asset_ctxs()
            meta = result[0]
            asset_ctxs = result[1]
            
            universe = meta.get("universe", [])
            for i, asset in enumerate(universe):
                if asset.get("name") == self.SYMBOL:
                    if i < len(asset_ctxs):
                        ctx = asset_ctxs[i]
                        mark = float(ctx.get("markPx", 0))
                        self._mid_price = mark
                        return mark
            return None
        except Exception as e:
            print(f"[HL] Price fetch error: {e}")
            return None
    
    def _is_live(self) -> bool:
        """Check if live trading is enabled."""
        return os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"
    
    def _round_price(self, price: float) -> float:
        """Round price to tick size."""
        return round(price / self.TICK_SIZE) * self.TICK_SIZE
    
    def _round_size(self, size: float) -> float:
        """Round size to lot size."""
        return round(size / self.LOT_SIZE) * self.LOT_SIZE
    
    async def taker_order(
        self,
        side: str,
        size_usd: float = 10.0,
        slippage_bps: float = 50.0,
        price_hint: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Place a taker (market-like) order.
        
        Args:
            side: "buy" or "sell"
            size_usd: Order size in USD (default $10)
            slippage_bps: Max slippage in basis points (default 50 = 0.5%)
            price_hint: Optional price to use (skips price fetch if provided)
        
        Returns:
            Dict with order result: {success, oid, fill_price, size, side, error}
        """
        result = {
            "success": False,
            "oid": None,
            "fill_price": None,
            "size": None,
            "side": side,
            "error": None,
        }
        
        # Use price hint if provided, otherwise fetch
        if price_hint:
            mid = price_hint
        else:
            await self._update_price()
            if not self._mid_price:
                result["error"] = "Could not get current price"
                return result
            mid = self._mid_price
        
        # Calculate size in ETH
        size_eth = size_usd / mid
        size_eth = self._round_size(size_eth)
        
        # Check minimum
        if mid * size_eth < self.MIN_NOTIONAL:
            result["error"] = f"Order too small: ${mid * size_eth:.2f} < ${self.MIN_NOTIONAL}"
            return result
        
        # Calculate aggressive limit price
        slippage_mult = slippage_bps / 10000
        if side == "buy":
            limit_price = mid * (1 + slippage_mult)
        else:
            limit_price = mid * (1 - slippage_mult)
        
        limit_price = self._round_price(limit_price)
        
        print(f"[HL] Taker {side.upper()}: {size_eth:.4f} ETH (~${size_usd:.2f}) @ limit {limit_price:.2f} (mid={mid:.2f})")
        
        if not self._is_live():
            print(f"[HL] DRY-RUN - order not sent")
            result["success"] = True
            result["fill_price"] = mid
            result["size"] = size_eth
            result["dry_run"] = True
            return result
        
        if not self._sdk:
            result["error"] = "SDK not initialized"
            return result
        
        try:
            # IOC = Immediate or Cancel (taker behavior)
            order_type = {"limit": {"tif": "Ioc"}}
            
            # Run SDK call in thread pool to avoid blocking asyncio event loop
            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: self._sdk.order(
                    name=self.SYMBOL,
                    is_buy=(side == "buy"),
                    sz=size_eth,
                    limit_px=limit_price,
                    order_type=order_type,
                    reduce_only=False,
                )
            )
            
            print(f"[HL] Response: {resp}")
            
            # Parse response
            if isinstance(resp, dict) and resp.get("status") == "ok":
                statuses = resp.get("response", {}).get("data", {}).get("statuses", [])
                for status in statuses:
                    if "filled" in status:
                        result["success"] = True
                        result["oid"] = status["filled"]["oid"]
                        result["fill_price"] = float(status["filled"].get("avgPx", limit_price))
                        result["size"] = size_eth
                        print(f"[HL] FILLED: oid={result['oid']}, price={result['fill_price']:.2f}")
                        return result
                    elif "resting" in status:
                        # Shouldn't happen with IOC but handle it
                        result["success"] = True
                        result["oid"] = status["resting"]["oid"]
                        result["size"] = size_eth
                        result["error"] = "Order resting (unexpected for IOC)"
                        return result
                    elif "error" in status:
                        result["error"] = status["error"]
                        return result
            else:
                result["error"] = f"Unexpected response: {resp}"
                
        except Exception as e:
            result["error"] = str(e)
        
        return result
    
    async def get_position(self) -> Dict[str, Any]:
        """Get current ETH position (queries MAIN wallet, not API wallet)."""
        try:
            main_wallet = os.getenv("HL_MAIN_WALLET", self._address)
            state = self._info.user_state(main_wallet)
            for p in state.get("assetPositions", []):
                pos = p.get("position", {})
                if pos.get("coin") == self.SYMBOL:
                    return {
                        "size": float(pos.get("szi", 0)),
                        "entry_price": float(pos.get("entryPx", 0)),
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0)),
                        "margin_used": float(pos.get("marginUsed", 0)),
                    }
            return {"size": 0, "entry_price": 0, "unrealized_pnl": 0, "margin_used": 0}
        except Exception as e:
            print(f"[HL] Position fetch error: {e}")
            return {"error": str(e)}
    
    async def get_balance(self) -> Dict[str, Any]:
        """Get account balance."""
        try:
            state = self._info.user_state(self._address)
            margin = state.get("marginSummary", {})
            return {
                "account_value": float(margin.get("accountValue", 0)),
                "total_margin_used": float(margin.get("totalMarginUsed", 0)),
                "available": float(margin.get("accountValue", 0)) - float(margin.get("totalMarginUsed", 0)),
            }
        except Exception as e:
            print(f"[HL] Balance fetch error: {e}")
            return {"error": str(e)}


# Quick test
async def test_taker():
    """Test a $10 taker order (dry-run by default)."""
    trader = HLTrader()
    
    if not await trader.connect():
        return
    
    print(f"\n[TEST] Current ETH price: ${trader._mid_price:,.2f}")
    
    # Check balance
    balance = await trader.get_balance()
    print(f"[TEST] Balance: {balance}")
    
    # Check position
    position = await trader.get_position()
    print(f"[TEST] Position: {position}")
    
    # Test buy
    print("\n[TEST] Placing $10 BUY taker...")
    result = await trader.taker_order("buy", size_usd=10)
    print(f"[TEST] Result: {result}")


if __name__ == "__main__":
    asyncio.run(test_taker())
