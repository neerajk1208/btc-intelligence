"""
WebSocket client for Hyperliquid market data.

Adapted from trade repo - data only, no user streams needed.
Provides real-time BTC price updates via WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import ssl
from typing import Any, Awaitable, Callable, Dict, Optional

import certifi
import websockets

from adapters.utils import get_logger


# Type alias for message handler
MessageHandler = Callable[[Dict[str, Any]], Awaitable[None]]

# Hyperliquid mainnet WebSocket endpoint
HL_WS_URL = "wss://api.hyperliquid.xyz/ws"


class HyperliquidWS:
    """
    WebSocket client for Hyperliquid market data.
    
    Handles orderbook and BBO (best bid/offer) streams for BTC.
    Auto-reconnects on disconnect with exponential backoff.
    """
    
    def __init__(
        self,
        on_message: MessageHandler,
        url: str = HL_WS_URL,
        ssl_verify: bool = True,
        name: str = "ws",
    ) -> None:
        """
        Args:
            on_message: Async callback for each received message
            url: WebSocket URL (defaults to Hyperliquid mainnet)
            ssl_verify: Whether to verify SSL certificates
            name: Logger name suffix for identification
        """
        self.url = url
        self.on_message = on_message
        self.ssl_verify = ssl_verify
        self._logger = get_logger(f"ws.{name}")
        
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        
        # Send pacing: minimum 100ms between sends
        self._min_send_interval_ms = 100
        self._last_send_ms: int = 0
        
        # Store subscriptions for re-subscribing after reconnect
        self._subscriptions: list[Dict[str, Any]] = []
    
    @property
    def is_connected(self) -> bool:
        """Returns True if WebSocket is currently connected."""
        if self._ws is None:
            return False
        try:
            return self._ws.open
        except AttributeError:
            from websockets.protocol import State
            return self._ws.state == State.OPEN
    
    async def connect(self) -> None:
        """Start the WebSocket connection loop (runs forever until stop() called)."""
        asyncio.create_task(self._connection_loop())
        # Wait for first successful connection
        await self._connected.wait()
    
    async def stop(self) -> None:
        """Gracefully close the WebSocket connection."""
        self._stop.set()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
    
    async def send(self, msg: Dict[str, Any]) -> None:
        """
        Send a JSON message to the WebSocket.
        
        Enforces minimum 100ms between sends to avoid rate limiting.
        """
        if not self._ws or not self.is_connected:
            self._logger.warning("Cannot send - WebSocket not connected")
            return
        
        # Pacing: wait if we sent too recently
        now = int(asyncio.get_event_loop().time() * 1000)
        elapsed = now - self._last_send_ms
        if elapsed < self._min_send_interval_ms:
            await asyncio.sleep((self._min_send_interval_ms - elapsed) / 1000.0)
        
        self._last_send_ms = int(asyncio.get_event_loop().time() * 1000)
        await self._ws.send(json.dumps(msg))
        self._logger.debug(f"Sent: {msg}")
    
    async def subscribe_orderbook(self, symbol: str) -> None:
        """Subscribe to L2 orderbook and BBO for a symbol."""
        sub1 = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": symbol}}
        sub2 = {"method": "subscribe", "subscription": {"type": "bbo", "coin": symbol}}
        self._subscriptions.append(sub1)
        self._subscriptions.append(sub2)
        await self.send(sub1)
        await self.send(sub2)
        self._logger.info(f"Subscribed to orderbook: {symbol}")
    
    async def subscribe_trades(self, symbol: str) -> None:
        """Subscribe to trade stream for a symbol."""
        sub = {"method": "subscribe", "subscription": {"type": "trades", "coin": symbol}}
        self._subscriptions.append(sub)
        await self.send(sub)
        self._logger.info(f"Subscribed to trades: {symbol}")
    
    async def _connection_loop(self) -> None:
        """Main connection loop with exponential backoff on failures."""
        backoff_ms = 1000
        max_backoff_ms = 30000
        
        while not self._stop.is_set():
            try:
                await self._connect_and_listen()
                backoff_ms = 1000
            except Exception as e:
                self._logger.warning(f"Connection error: {e}")
            
            if self._stop.is_set():
                break
                
            self._logger.debug(f"Reconnecting in {backoff_ms}ms...")
            await asyncio.sleep(backoff_ms / 1000.0)
            backoff_ms = min(backoff_ms * 2, max_backoff_ms)
    
    async def _connect_and_listen(self) -> None:
        """Establish connection and process messages."""
        ssl_ctx = None
        if self.url.startswith("wss"):
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            if not self.ssl_verify:
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
        
        self._logger.info(f"Connecting to {self.url}")
        
        async with websockets.connect(self.url, ssl=ssl_ctx, ping_interval=20) as ws:
            self._ws = ws
            # Small delay to ensure WebSocket is fully ready
            await asyncio.sleep(0.1)
            self._connected.set()
            self._logger.info("Connected")
            
            # Re-subscribe after reconnect
            if self._subscriptions:
                self._logger.debug(f"Re-subscribing to {len(self._subscriptions)} channels...")
                for sub in self._subscriptions:
                    try:
                        await self._ws.send(json.dumps(sub))
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        self._logger.error(f"Re-subscribe failed: {e}")
                self._logger.debug("Re-subscribed successfully")
            
            try:
                async for raw in ws:
                    try:
                        data = json.loads(raw)
                        await self.on_message(data)
                    except json.JSONDecodeError:
                        self._logger.warning(f"Invalid JSON: {raw[:100]}")
                    except Exception as e:
                        self._logger.error(f"Message handler error: {e}")
            finally:
                self._ws = None
                self._connected.clear()
                self._logger.warning("Disconnected")
