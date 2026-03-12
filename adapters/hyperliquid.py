"""
Hyperliquid Data Adapter for BTC.

This adapter handles:
1. WebSocket connections for real-time BTC market data
2. REST API calls for funding rate, open interest, and historical data

NO trading functionality - this is data-only for signal generation.

Adapted from trade repo bot/venues/hyperliquid.py
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional, Tuple

from adapters.state import Level, MarketState
from adapters.utils import get_logger, now_ms
from adapters.websocket import HyperliquidWS


class HyperliquidDataAdapter:
    """
    Data adapter for Hyperliquid BTC market.
    
    Provides:
    - Real-time prices (mid, bid/ask)
    - Order book depth
    - Funding rate
    - Open interest
    - 24h volume
    
    NO trading - data only.
    """
    
    def __init__(
        self,
        symbol: str = "BTC",
        ssl_verify: bool = True,
    ) -> None:
        """
        Args:
            symbol: Trading symbol (default "BTC" for Bitcoin perpetual)
            ssl_verify: Whether to verify SSL certificates
        """
        self.symbol = symbol
        self.ssl_verify = ssl_verify
        self._logger = get_logger("adapter.hyperliquid")
        
        # State
        self._market = MarketState()
        
        # WebSocket
        self._ws: Optional[HyperliquidWS] = None
        
        # REST client (lazy init)
        self._info = None
        
        # Price history for VWAP and other calculations
        self._price_history: List[Tuple[int, float, float]] = []  # (timestamp_ms, price, volume)
        self._max_history_size = 10000  # Keep ~3 hours at 1s intervals
    
    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------
    
    async def connect(self) -> None:
        """Connect to WebSocket and start receiving data."""
        self._ws = HyperliquidWS(
            on_message=self._on_message,
            ssl_verify=self.ssl_verify,
            name="btc-data",
        )
        await self._ws.connect()
        
        # Subscribe to BTC market data
        await self._ws.subscribe_orderbook(self.symbol)
        
        self._logger.info(f"Connected and subscribed to {self.symbol}")
    
    async def close(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            await self._ws.stop()
    
    # -------------------------------------------------------------------------
    # Real-time Data (from WebSocket)
    # -------------------------------------------------------------------------
    
    def mid_price(self) -> Optional[float]:
        """Get current mid price from orderbook."""
        return self._market.orderbook.mid()
    
    def best_bid_ask(self) -> Optional[Tuple[float, float]]:
        """Get best bid and ask prices."""
        return self._market.orderbook.best_bid_ask()
    
    def spread_bps(self) -> Optional[float]:
        """Get current spread in basis points."""
        return self._market.orderbook.spread_bps()
    
    def is_data_fresh(self, stale_ms: int = 5000) -> bool:
        """Check if market data is fresh (received within stale_ms)."""
        return (now_ms() - self._market.last_market_ws_ms) < stale_ms
    
    def get_order_book_depth(
        self, 
        levels: int = 5
    ) -> Tuple[List[Tuple[float, float]], List[Tuple[float, float]]]:
        """
        Get order book depth for top N levels on each side.
        
        Returns:
            Tuple of (bids, asks) where each is a list of (price, size) tuples
        """
        bids = [(bid.px, bid.sz) for bid in self._market.orderbook.bids[:levels]]
        asks = [(ask.px, ask.sz) for ask in self._market.orderbook.asks[:levels]]
        return bids, asks
    
    def get_depth_usd(self, levels: int = 5) -> Tuple[float, float]:
        """
        Get total depth in USD at top N levels.
        
        Returns:
            Tuple of (bid_depth_usd, ask_depth_usd)
        """
        return self._market.orderbook.depth_at_levels(levels)
    
    def get_imbalance(self, levels: int = 5) -> Optional[float]:
        """
        Calculate order book imbalance at top N levels.
        
        Returns:
            Imbalance ratio: positive = bid heavy, negative = ask heavy
            Range roughly -1 to +1
        """
        bid_depth, ask_depth = self.get_depth_usd(levels)
        total = bid_depth + ask_depth
        if total == 0:
            return None
        return (bid_depth - ask_depth) / total
    
    def get_price_history(self, last_n: int = 100) -> List[Tuple[int, float]]:
        """
        Get recent price history.
        
        Returns:
            List of (timestamp_ms, price) tuples, most recent last
        """
        return [(ts, price) for ts, price, _ in self._price_history[-last_n:]]
    
    # -------------------------------------------------------------------------
    # REST API Data
    # -------------------------------------------------------------------------
    
    async def fetch_funding_rate(self) -> Optional[float]:
        """
        Fetch current funding rate for BTC.
        
        Returns:
            Funding rate as decimal (e.g., 0.0001 = 0.01%)
        """
        try:
            if self._info is None:
                from hyperliquid.info import Info
                self._info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
            
            # Fetch meta and asset contexts
            result = self._info.meta_and_asset_ctxs()
            
            meta = result[0] if isinstance(result, (list, tuple)) else result
            asset_ctxs = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
            
            # Find BTC
            universe = meta.get('universe', [])
            for i, asset in enumerate(universe):
                if asset.get('name') == self.symbol:
                    if i < len(asset_ctxs):
                        ctx = asset_ctxs[i]
                        funding = float(ctx.get('funding', 0))
                        self._market.funding_rate = funding
                        return funding
            
            return None
            
        except Exception as e:
            self._logger.warning(f"Failed to fetch funding rate: {e}")
            return None
    
    async def fetch_open_interest(self) -> Optional[float]:
        """
        Fetch open interest for BTC.
        
        Returns:
            Open interest in USD
        """
        try:
            if self._info is None:
                from hyperliquid.info import Info
                self._info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
            
            result = self._info.meta_and_asset_ctxs()
            
            meta = result[0] if isinstance(result, (list, tuple)) else result
            asset_ctxs = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
            
            universe = meta.get('universe', [])
            for i, asset in enumerate(universe):
                if asset.get('name') == self.symbol:
                    if i < len(asset_ctxs):
                        ctx = asset_ctxs[i]
                        oi = float(ctx.get('openInterest', 0))
                        mark_px = float(ctx.get('markPx', 0))
                        oi_usd = oi * mark_px
                        self._market.open_interest = oi_usd
                        self._market.mark_price = mark_px
                        return oi_usd
            
            return None
            
        except Exception as e:
            self._logger.warning(f"Failed to fetch open interest: {e}")
            return None
    
    async def fetch_24h_volume(self) -> Optional[float]:
        """
        Fetch 24-hour trading volume for BTC.
        
        Returns:
            24h volume in USD
        """
        try:
            if self._info is None:
                from hyperliquid.info import Info
                self._info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
            
            result = self._info.meta_and_asset_ctxs()
            
            meta = result[0] if isinstance(result, (list, tuple)) else result
            asset_ctxs = result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else []
            
            universe = meta.get('universe', [])
            for i, asset in enumerate(universe):
                if asset.get('name') == self.symbol:
                    if i < len(asset_ctxs):
                        ctx = asset_ctxs[i]
                        volume_24h = float(ctx.get('dayNtlVlm', 0))
                        self._logger.debug(f"24h volume for {self.symbol}: ${volume_24h:,.0f}")
                        return volume_24h
            
            return None
            
        except Exception as e:
            self._logger.warning(f"Failed to fetch 24h volume: {e}")
            return None
    
    async def fetch_candles(
        self, 
        interval: str = "5m",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical candles for BTC.
        
        Args:
            interval: Candle interval (1m, 5m, 15m, 1h, 4h, 1d)
            limit: Number of candles to fetch
            
        Returns:
            List of candle dicts with open, high, low, close, volume
        """
        try:
            if self._info is None:
                from hyperliquid.info import Info
                self._info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
            
            # Convert interval to Hyperliquid format
            interval_map = {
                "1m": "1m", "5m": "5m", "15m": "15m",
                "1h": "1h", "4h": "4h", "1d": "1d"
            }
            hl_interval = interval_map.get(interval, "5m")
            
            # Calculate start time
            interval_ms = {
                "1m": 60000, "5m": 300000, "15m": 900000,
                "1h": 3600000, "4h": 14400000, "1d": 86400000
            }
            ms_per_candle = interval_ms.get(hl_interval, 300000)
            start_time = now_ms() - (limit * ms_per_candle)
            
            result = self._info.candles_snapshot(
                self.symbol,
                hl_interval,
                start_time,
                now_ms()
            )
            
            candles = []
            for c in result:
                candles.append({
                    "timestamp": c.get("t", 0),
                    "open": float(c.get("o", 0)),
                    "high": float(c.get("h", 0)),
                    "low": float(c.get("l", 0)),
                    "close": float(c.get("c", 0)),
                    "volume": float(c.get("v", 0)),
                })
            
            return candles
            
        except Exception as e:
            self._logger.warning(f"Failed to fetch candles: {e}")
            return []
    
    # -------------------------------------------------------------------------
    # WebSocket Message Handler
    # -------------------------------------------------------------------------
    
    async def _on_message(self, data: Dict[str, Any]) -> None:
        """Process incoming WebSocket messages."""
        channel = data.get("channel") or data.get("type")
        payload = data.get("data", data)
        
        if not isinstance(payload, dict):
            return
        
        coin = payload.get("coin")
        
        if coin == self.symbol:
            if channel == "l2Book":
                self._parse_l2book(payload)
            elif channel == "bbo":
                self._parse_bbo(payload)
    
    def _parse_l2book(self, payload: Dict) -> None:
        """Parse L2 orderbook update."""
        levels = payload.get("levels", [])
        if len(levels) >= 2:
            bids = [Level(px=float(b["px"]), sz=float(b["sz"])) 
                    for b in levels[0] if "px" in b and "sz" in b]
            asks = [Level(px=float(a["px"]), sz=float(a["sz"])) 
                    for a in levels[1] if "px" in a and "sz" in a]
            
            self._market.orderbook.bids = bids
            self._market.orderbook.asks = asks
            self._market.last_market_ws_ms = now_ms()
            
            # Record price history
            mid = self._market.orderbook.mid()
            if mid:
                self._record_price(mid)
    
    def _parse_bbo(self, payload: Dict) -> None:
        """Parse BBO (best bid/offer) update."""
        bbo = payload.get("bbo", [])
        if len(bbo) >= 2:
            if bbo[0] and "px" in bbo[0]:
                self._market.orderbook.bids = [Level(px=float(bbo[0]["px"]), sz=float(bbo[0].get("sz", 0)))]
            if bbo[1] and "px" in bbo[1]:
                self._market.orderbook.asks = [Level(px=float(bbo[1]["px"]), sz=float(bbo[1].get("sz", 0)))]
            self._market.last_market_ws_ms = now_ms()
            
            # Record price history
            mid = self._market.orderbook.mid()
            if mid:
                self._record_price(mid)
    
    def _record_price(self, price: float, volume: float = 0) -> None:
        """Record price to history for signal calculations."""
        ts = now_ms()
        self._price_history.append((ts, price, volume))
        
        # Trim history if too large
        if len(self._price_history) > self._max_history_size:
            self._price_history = self._price_history[-self._max_history_size:]


# Convenience alias
DataAdapter = HyperliquidDataAdapter
