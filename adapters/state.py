"""
State dataclasses for market data.

Adapted from trade repo for BTC-only data consumption.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Level:
    """Single price level in the order book."""
    px: float  # Price
    sz: float  # Size
    n: int = 0  # Number of orders (optional)


@dataclass
class OrderbookState:
    """Order book state with bids and asks."""
    bids: List[Level] = field(default_factory=list)
    asks: List[Level] = field(default_factory=list)
    ts: int = 0  # Last update timestamp
    seq: int = 0  # Sequence number

    def best_bid_ask(self) -> Optional[Tuple[float, float]]:
        """Get best bid and ask prices."""
        bid = self.bids[0].px if self.bids else None
        ask = self.asks[0].px if self.asks else None
        if bid is None or ask is None:
            return None
        return (bid, ask)

    def mid(self) -> Optional[float]:
        """Calculate mid price from best bid/ask."""
        ba = self.best_bid_ask()
        if not ba:
            return None
        b, a = ba
        return (b + a) / 2.0
    
    def spread_bps(self) -> Optional[float]:
        """Calculate spread in basis points."""
        ba = self.best_bid_ask()
        if not ba:
            return None
        bid, ask = ba
        mid = (bid + ask) / 2.0
        if mid == 0:
            return None
        return ((ask - bid) / mid) * 10000

    def depth_at_levels(self, levels: int = 5) -> Tuple[float, float]:
        """
        Calculate total depth (in USD) at top N levels.
        
        Returns:
            Tuple of (bid_depth_usd, ask_depth_usd)
        """
        bid_depth = sum(level.px * level.sz for level in self.bids[:levels])
        ask_depth = sum(level.px * level.sz for level in self.asks[:levels])
        return (bid_depth, ask_depth)


@dataclass
class MarketState:
    """Combined market state."""
    orderbook: OrderbookState = field(default_factory=OrderbookState)
    last_market_ws_ms: int = 0
    funding_rate: Optional[float] = None  # Current funding rate
    open_interest: Optional[float] = None  # Open interest in USD
    mark_price: Optional[float] = None
    index_price: Optional[float] = None
