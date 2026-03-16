"""
Non-blocking database logger for arbitrage engine.
Uses thread-safe deque + background thread for zero latency impact.
Writes to Supabase (Postgres) for persistence.
"""

import os
import threading
import time
import requests
from collections import deque
from typing import Optional, List, Dict, Any


class ArbLogger:
    """Thread-safe, non-blocking Supabase logger."""
    
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL", "")
        self.supabase_key = os.getenv("SUPABASE_KEY", "")
        
        if not self.supabase_url or not self.supabase_key:
            print("[DB] WARNING: SUPABASE_URL or SUPABASE_KEY not set - logging disabled")
            self._enabled = False
            return
        
        self._enabled = True
        self._queue: deque = deque(maxlen=50000)
        self._running = True
        self._flush_interval = 5
        
        # Headers for Supabase REST API
        self._headers = {
            "apikey": self.supabase_key,
            "Authorization": f"Bearer {self.supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal"
        }
        
        self._thread = threading.Thread(target=self._flush_loop, daemon=True)
        self._thread.start()
        print(f"[DB] Supabase logger initialized")
    
    def _flush_loop(self):
        """Background thread: flush queue to Supabase periodically."""
        while self._running:
            time.sleep(self._flush_interval)
            try:
                self._flush()
            except Exception as e:
                print(f"[DB] Flush error (non-fatal): {e}")
    
    def _flush(self):
        """Flush pending items to Supabase."""
        if not self._enabled or not self._queue:
            return
        
        items = []
        while self._queue:
            try:
                items.append(self._queue.popleft())
            except IndexError:
                break
        
        if not items:
            return
        
        spread_rows: List[Dict] = []
        trade_rows: List[Dict] = []
        
        for table, data in items:
            if table == "spreads":
                spread_rows.append({
                    "ts": data[0],
                    "hl_price": data[1],
                    "def_price": data[2],
                    "spread_bps": data[3],
                    "mode": data[4],
                    "def_latency_ms": data[5],
                    "hl_price_age_ms": data[6],
                    "price_gap_ms": data[7]
                })
            elif table == "trades":
                trade_rows.append({
                    "ts": data[0],
                    "cycle_id": data[1],
                    "side": data[2],
                    "expected_spread_bps": data[3],
                    "actual_spread_bps": data[4],
                    "slippage_bps": data[5],
                    "hl_price": data[6],
                    "def_price": data[7],
                    "order_size_usd": data[8],
                    "def_fill_amount": data[9],
                    "hl_fill_amount": data[10],
                    "def_latency_ms": data[11],
                    "hl_latency_ms": data[12],
                    "total_exec_ms": data[13],
                    "success": data[14],
                    "error": data[15],
                    "gross_pnl": data[16],
                    "net_pnl": data[17]
                })
        
        # Batch insert spreads
        if spread_rows:
            try:
                resp = requests.post(
                    f"{self.supabase_url}/rest/v1/spreads",
                    headers=self._headers,
                    json=spread_rows,
                    timeout=10
                )
                if resp.status_code not in (200, 201):
                    print(f"[DB] Spread insert error: {resp.status_code} - {resp.text[:100]}")
            except Exception as e:
                print(f"[DB] Spread insert exception: {e}")
        
        # Batch insert trades
        if trade_rows:
            try:
                resp = requests.post(
                    f"{self.supabase_url}/rest/v1/trades",
                    headers=self._headers,
                    json=trade_rows,
                    timeout=10
                )
                if resp.status_code not in (200, 201):
                    print(f"[DB] Trade insert error: {resp.status_code} - {resp.text[:100]}")
            except Exception as e:
                print(f"[DB] Trade insert exception: {e}")
    
    def log_spread(
        self,
        hl_price: float,
        def_price: float,
        spread_bps: float,
        mode: str,
        def_latency_ms: float = 0,
        hl_price_age_ms: float = 0,
        price_gap_ms: float = 0
    ):
        """Log a spread reading. NON-BLOCKING - just appends to deque."""
        if not self._enabled:
            return
        self._queue.append(("spreads", (
            int(time.time() * 1000),
            hl_price,
            def_price,
            spread_bps,
            mode,
            def_latency_ms,
            hl_price_age_ms,
            price_gap_ms
        )))
    
    def log_trade(
        self,
        cycle_id: int,
        side: str,
        expected_spread_bps: float,
        actual_spread_bps: float,
        hl_price: float,
        def_price: float,
        order_size_usd: float,
        def_fill_amount: float,
        hl_fill_amount: float,
        def_latency_ms: float,
        hl_latency_ms: float,
        total_exec_ms: float,
        success: bool,
        error: Optional[str] = None,
        gross_pnl: float = 0,
        net_pnl: float = 0
    ):
        """Log a trade execution. NON-BLOCKING - just appends to deque."""
        if not self._enabled:
            return
        slippage = actual_spread_bps - expected_spread_bps
        self._queue.append(("trades", (
            int(time.time() * 1000),
            cycle_id,
            side,
            expected_spread_bps,
            actual_spread_bps,
            slippage,
            hl_price,
            def_price,
            order_size_usd,
            def_fill_amount,
            hl_fill_amount,
            def_latency_ms,
            hl_latency_ms,
            total_exec_ms,
            1 if success else 0,
            error,
            gross_pnl,
            net_pnl
        )))
    
    def shutdown(self):
        """Flush remaining and stop background thread."""
        self._running = False
        self._flush()


_logger: Optional[ArbLogger] = None


def init_logger(db_path: str = "data/arb.db"):
    """Initialize the global logger."""
    global _logger
    if _logger is None:
        _logger = ArbLogger(db_path)


def get_logger() -> Optional[ArbLogger]:
    """Get the global logger instance."""
    return _logger


def shutdown_logger():
    """Shutdown the global logger."""
    global _logger
    if _logger:
        _logger.shutdown()
        _logger = None


def log_spread(
    hl_price: float,
    def_price: float,
    spread_bps: float,
    mode: str,
    def_latency_ms: float = 0,
    hl_price_age_ms: float = 0,
    price_gap_ms: float = 0
):
    """Log spread to database. NON-BLOCKING."""
    if _logger:
        _logger.log_spread(
            hl_price, def_price, spread_bps, mode,
            def_latency_ms, hl_price_age_ms, price_gap_ms
        )


def log_trade(
    cycle_id: int,
    side: str,
    expected_spread_bps: float,
    actual_spread_bps: float,
    hl_price: float,
    def_price: float,
    order_size_usd: float,
    def_fill_amount: float,
    hl_fill_amount: float,
    def_latency_ms: float,
    hl_latency_ms: float,
    total_exec_ms: float,
    success: bool,
    error: Optional[str] = None,
    gross_pnl: float = 0,
    net_pnl: float = 0
):
    """Log trade to database. NON-BLOCKING."""
    if _logger:
        _logger.log_trade(
            cycle_id, side, expected_spread_bps, actual_spread_bps,
            hl_price, def_price, order_size_usd, def_fill_amount,
            hl_fill_amount, def_latency_ms, hl_latency_ms, total_exec_ms,
            success, error, gross_pnl, net_pnl
        )
