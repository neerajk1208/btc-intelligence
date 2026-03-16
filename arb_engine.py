"""
ETH Arbitrage Engine

Executes delta-neutral arbitrage between:
- Definitive (spot WETH on Base)
- Hyperliquid (ETH-PERP)

Strategy:
1. ENTRY: When spread <= ENTRY_THRESHOLD_BPS
   - Buy WETH on Definitive
   - Short ETH-PERP on Hyperliquid
2. HOLD: Monitor spread, track unrealized P&L
3. EXIT: When spread >= EXIT_THRESHOLD_BPS
   - Sell WETH on Definitive
   - Close short on Hyperliquid
4. LOG: Record cycle P&L
5. REPEAT

Usage:
    python arb_engine.py --size 10 --cycles 1  # Test with $10, 1 cycle
    python arb_engine.py --size 100 --cycles 5  # Run with $100, 5 cycles
"""

import asyncio
import os
import ssl
import json
import time
import hmac
import hashlib

from db.logger import init_logger, log_spread, log_trade, shutdown_logger
import random
import aiohttp
import certifi
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple, Callable
from dataclasses import dataclass
from dotenv import load_dotenv

from adapters.websocket import HyperliquidWS

load_dotenv()

# UI callback - set by web server if running with UI
ui_callback: Optional[Callable] = None

# Stop flag - checked by engine to know when to stop
_stop_check: Optional[Callable] = None

def set_stop_check(check_fn: Callable):
    """Set function to check if engine should stop."""
    global _stop_check
    _stop_check = check_fn

def should_stop() -> bool:
    """Check if engine should stop."""
    if _stop_check:
        return _stop_check()
    return False

# Global pause flag for consecutive failures
_is_paused = False
_pause_reason = ""

def get_pause_status() -> tuple:
    """Get current pause status and reason."""
    return _is_paused, _pause_reason

def set_pause_status(paused: bool, reason: str = ""):
    """Set pause status."""
    global _is_paused, _pause_reason
    _is_paused = paused
    _pause_reason = reason

def set_ui_callback(callback: Callable):
    """Set callback for UI updates."""
    global ui_callback
    ui_callback = callback

def notify_ui(event_type: str, data: dict):
    """Send update to UI if callback is set."""
    if ui_callback:
        try:
            ui_callback(event_type, data)
        except Exception as e:
            print(f"[UI] Error: {e}")


@dataclass
class CycleLog:
    """Log for a single arbitrage cycle."""
    cycle_num: int
    entry_time: str
    exit_time: str
    entry_spread_bps: float
    exit_spread_bps: float
    size_usd: float
    
    # Definitive
    def_entry_price: float
    def_exit_price: float
    def_weth_amount: float
    def_fee_entry: float
    def_fee_exit: float
    
    # Hyperliquid
    hl_entry_price: float
    hl_exit_price: float
    hl_size_eth: float
    hl_fee_entry: float
    hl_fee_exit: float
    
    # P&L
    def_pnl: float
    hl_pnl: float
    total_fees: float
    net_pnl: float


class ArbEngine:
    """ETH Arbitrage Engine."""
    
    # Base chain addresses
    USDC_BASE = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    WETH_BASE = "0x4200000000000000000000000000000000000006"
    
    # Thresholds (in basis points)
    ENTRY_THRESHOLD_BPS = 5.0    # Enter when spread <= 5 bps
    EXIT_THRESHOLD_BPS = 15.0    # Exit when spread >= 15 bps
    MIN_PROFIT_BPS = 2.0         # Minimum net profit to exit
    
    # Fee estimates (bps)
    DEF_FEE_BPS = 2.0
    HL_FEE_BPS = 4.5
    ROUND_TRIP_FEES = (DEF_FEE_BPS + HL_FEE_BPS) * 2  # ~13 bps
    
    # Polling
    POLL_INTERVAL_SEC = 3
    
    # TURBO settings
    USE_TURBO = True  # Use TURBO instead of PRIME
    SLIPPAGE_TOLERANCE = "0.000500"  # 5 bps slippage tolerance for TURBO
    
    def __init__(self, size_usd: float = 100.0, min_size: float = None, max_size: float = None):
        # Size configuration
        self.min_size = min_size
        self.max_size = max_size
        self.size_usd = size_usd  # Will be randomized per cycle if range is set
        self._use_random_size = min_size is not None and max_size is not None
        self.session: Optional[aiohttp.ClientSession] = None
        self.hl_trader = None
        
        # Position state
        self.in_position = False
        self.entry_spread_bps = 0.0
        self.def_weth_amount = 0.0
        self.def_weth_amount_raw = "0"  # Raw string for precision on sell
        self.hl_size_eth = 0.0
        self.def_entry_price = 0.0
        self.hl_entry_price = 0.0
        
        # Logs
        self.cycle_logs: list[CycleLog] = []
        self.cycle_count = 0
        
        # Auth - Privy (for legacy endpoints)
        self.privy_token = os.getenv("PRIVY_ACCESS_TOKEN")
        self.privy_id_token = os.getenv("PRIVY_ID_TOKEN")
        self.org_id = os.getenv("DEFINITIVE_ORG_ID")
        self.portfolio_id = os.getenv("DEFINITIVE_PORTFOLIO_ID")
        self.read_token = os.getenv("DEFINITIVE_READ_TOKEN")
        
        # Auth - QuickTrade API (faster endpoint)
        self.def_api_key = os.getenv("DEFINITIVE_API_KEY")
        self.def_api_secret = os.getenv("DEFINITIVE_API_SECRET")
        if self.def_api_secret:
            self._def_secret_clean = self.def_api_secret.replace("dpks_", "")
        else:
            self._def_secret_clean = ""
        
        # Token refresh tracking
        self._tokens_file = Path(__file__).parent / "auth" / "tokens.json"
        self._last_token_check = 0
        self._token_check_interval = 30  # Check every 30 seconds
        
        # Price sync tracking (WebSocket eliminates need for latency calibration)
        self._calibration_count = 0
        
        # PRIME quote storage for TURBO execution (BUY for entry)
        self._last_prime_quote_id = ""
        self._last_prime_buy_amount = ""
        
        # PRIME SELL quote storage for TURBO exit
        self._last_prime_sell_quote_id = ""
        self._last_prime_sell_amount = ""
        
        # Warmup tracking
        self._engine_start_time = 0
        self._warmup_seconds = 10
        
        # Token validity tracking
        self._token_valid_until = 0
        self._token_refresh_buffer = 120  # Refresh 2 min before expiry
        self._refreshing_tokens = False
        
        # HL WebSocket for real-time prices (0ms gap)
        self._hl_ws: Optional[HyperliquidWS] = None
        self._hl_ws_price: float = 0.0
        self._hl_ws_price_time: float = 0.0
        self._hl_ws_connected = False
        
        # Latency tracking (per-cycle)
        self._def_quote_latency_ms: float = 0.0
        self._hl_ws_price_age_ms: float = 0.0
        self._price_gap_ms: float = 0.0  # Time between DEF response and HL price capture
        self._def_exec_latency_ms: float = 0.0
        self._hl_exec_latency_ms: float = 0.0
        
        # BPS/Slippage tracking (per-cycle)
        self._expected_entry_spread: float = 0.0
        self._actual_entry_spread: float = 0.0
        self._expected_exit_spread: float = 0.0
        self._actual_exit_spread: float = 0.0
        
        # Position confirmation
        self._position_confirmed: bool = False
        self._position_mismatch: bool = False
        self._position_mismatch_detail: str = ""
        
        # Service health
        self._service_health = {
            "def_api": "unknown",
            "def_auth": "unknown",
            "hl_rest": "unknown",
            "hl_websocket": "unknown"
        }
        
        # Token check cadence (separate from refresh buffer)
        self._token_cadence_interval = 60  # Check display every 60 seconds
        self._last_token_cadence_check = 0
        
        # Consecutive failure tracking
        self._consecutive_def_failures = 0
        self._max_consecutive_failures = 3  # Pause after 3 consecutive failures
    
    async def _on_hl_ws_message(self, data: Dict[str, Any]) -> None:
        """Handle HL WebSocket messages - update latest ETH price."""
        try:
            channel = data.get("channel")
            msg_data = data.get("data")
            
            if channel == "bbo" and msg_data:
                coin = msg_data.get("coin", "")
                if coin == "ETH":
                    bbo = msg_data.get("bbo", [])
                    # BBO is a list: [{"px": bid, ...}, {"px": ask, ...}]
                    if isinstance(bbo, list) and len(bbo) >= 2:
                        bid = float(bbo[0].get("px", 0))
                        ask = float(bbo[1].get("px", 0))
                        if bid > 0 and ask > 0:
                            self._hl_ws_price = (bid + ask) / 2
                            self._hl_ws_price_time = time.time()
        except Exception as e:
            pass  # Silently ignore parse errors
    
    async def _start_hl_websocket(self) -> bool:
        """Start HL WebSocket connection."""
        try:
            self._hl_ws = HyperliquidWS(
                on_message=self._on_hl_ws_message,
                name="hl_eth"
            )
            await self._hl_ws.connect()
            await self._hl_ws.subscribe_orderbook("ETH")
            self._hl_ws_connected = True
            print("[WS] Connected to Hyperliquid WebSocket for ETH")
            return True
        except Exception as e:
            print(f"[WS] Failed to connect: {e}")
            return False
    
    async def _stop_hl_websocket(self) -> None:
        """Stop HL WebSocket connection."""
        if self._hl_ws:
            await self._hl_ws.stop()
            self._hl_ws_connected = False
    
    def _get_hl_ws_price(self) -> Optional[float]:
        """Get latest HL price from WebSocket. Returns None if stale (>5s)."""
        if not self._hl_ws_connected or self._hl_ws_price == 0:
            return None
        age = time.time() - self._hl_ws_price_time
        if age > 5.0:  # Price older than 5 seconds is stale
            return None
        return self._hl_ws_price
    
    def _decode_jwt_exp(self, token: str) -> int:
        """Extract expiry timestamp from JWT."""
        try:
            import base64
            parts = token.split(".")
            if len(parts) != 3:
                return 0
            payload = parts[1]
            payload += "=" * (4 - len(payload) % 4)
            decoded = base64.urlsafe_b64decode(payload)
            data = json.loads(decoded)
            return data.get("exp", 0)
        except:
            return 0
    
    def _load_tokens_from_file(self) -> bool:
        """Load tokens from file and update session. Returns True if loaded."""
        try:
            if not self._tokens_file.exists():
                return False
            
            with open(self._tokens_file) as f:
                tokens = json.load(f)
            
            new_access = tokens.get("access_token")
            new_id = tokens.get("id_token")
            
            if not new_access or not new_id:
                return False
            
            # Check if tokens are actually new
            if new_access == self.privy_token:
                # Same token, just update expiry tracking
                self._token_valid_until = self._decode_jwt_exp(new_access)
                return False
            
            # New tokens - update everything
            self.privy_token = new_access
            self.privy_id_token = new_id
            self._token_valid_until = self._decode_jwt_exp(new_access)
            
            # Update session cookies
            if self.session:
                from yarl import URL
                self.session.cookie_jar.update_cookies(
                    {"privy-token": new_access, "privy-id-token": new_id},
                    URL("https://api.definitive.fi")
                )
                self.session.cookie_jar.update_cookies(
                    {"privy-token": new_access, "privy-id-token": new_id},
                    URL("https://client-api.definitive.fi")
                )
            
            exp_time = time.strftime("%H:%M:%S", time.localtime(self._token_valid_until))
            print(f"[TOKEN] Loaded fresh tokens, expires at {exp_time}")
            return True
        except Exception as e:
            print(f"[TOKEN] Error loading tokens: {e}")
            return False
    
    async def _ensure_valid_token(self) -> bool:
        """Ensure we have a valid token before executing. Blocks if refreshing."""
        now = time.time()
        
        # Check if token expires soon
        time_until_expiry = self._token_valid_until - now
        
        if time_until_expiry < self._token_refresh_buffer:
            print(f"[TOKEN] Token expires in {time_until_expiry:.0f}s, checking for refresh...")
            self._refreshing_tokens = True
            notify_ui("token_status", {"expires_in_sec": time_until_expiry, "refreshing": True})
            notify_ui("event", {"type": "WARNING", "message": f"Token expiring in {time_until_expiry:.0f}s, waiting for refresh..."})
            
            # Try to load fresh tokens (Playwright should have refreshed)
            for attempt in range(10):  # Wait up to 10 seconds
                if self._load_tokens_from_file():
                    new_expiry = self._token_valid_until - time.time()
                    if new_expiry > self._token_refresh_buffer:
                        print(f"[TOKEN] Refreshed! New token valid for {new_expiry:.0f}s")
                        self._refreshing_tokens = False
                        notify_ui("token_status", {"expires_in_sec": new_expiry, "refreshing": False})
                        notify_ui("event", {"type": "INFO", "message": f"Token refreshed! Valid for {new_expiry/60:.0f} min"})
                        return True
                await asyncio.sleep(1)
            
            self._refreshing_tokens = False
            notify_ui("token_status", {"expires_in_sec": time_until_expiry, "refreshing": False})
            
            # Check if token is actually expired
            if self._token_valid_until < now:
                print(f"[TOKEN] ERROR: Token expired! Cannot execute trades.")
                notify_ui("event", {"type": "ERROR", "message": "Token EXPIRED! Cannot trade."})
                return False
            else:
                print(f"[TOKEN] Warning: Token expiring soon ({time_until_expiry:.0f}s left)")
                notify_ui("event", {"type": "WARNING", "message": f"Token expiring soon ({time_until_expiry:.0f}s left)"})
        
        return True
    
    def _maybe_reload_tokens(self) -> bool:
        """Check for updated tokens from env vars or file (non-blocking).
        
        Checks env vars first (for Railway hot-reload), then falls back to file.
        Returns True if tokens were reloaded.
        """
        now = time.time()
        if now - self._last_token_check < self._token_check_interval:
            return False
        
        self._last_token_check = now
        
        # Check env vars first (Railway updates these live)
        new_access = os.environ.get("PRIVY_ACCESS_TOKEN", "")
        new_id = os.environ.get("PRIVY_ID_TOKEN", "")
        
        if new_access and new_access != self.privy_token:
            # New tokens from env vars - update everything
            self.privy_token = new_access
            self.privy_id_token = new_id
            self._token_valid_until = self._decode_jwt_exp(new_access)
            
            # Update session cookies
            if self.session:
                from yarl import URL
                self.session.cookie_jar.update_cookies(
                    {"privy-token": new_access, "privy-id-token": new_id},
                    URL("https://api.definitive.fi")
                )
                self.session.cookie_jar.update_cookies(
                    {"privy-token": new_access, "privy-id-token": new_id},
                    URL("https://client-api.definitive.fi")
                )
            
            exp_time = time.strftime("%H:%M:%S", time.localtime(self._token_valid_until))
            print(f"[TOKEN] Reloaded from env vars, expires at {exp_time}")
            return True
        
        # Fall back to file-based loading
        return self._load_tokens_from_file()
    
    def _maybe_reload_tokens_legacy(self) -> bool:
        """Legacy method - kept for compatibility."""
        return False
    
    def _sign_quicktrade(self, method: str, path: str, body_str: str) -> Tuple[str, str]:
        """Sign a QuickTrade API request. Returns (timestamp, signature)."""
        timestamp = str(int(time.time() * 1000))
        
        headers_for_sign = {
            'x-definitive-api-key': self.def_api_key,
            'x-definitive-timestamp': timestamp,
        }
        sorted_headers = ','.join([
            f'{k}:{json.dumps(v)}' 
            for k, v in sorted(headers_for_sign.items())
        ])
        
        prehash = f'{method}:{path}?:{timestamp}:{sorted_headers}{body_str}'
        signature = hmac.new(
            self._def_secret_clean.encode('utf-8'),
            prehash.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        return timestamp, signature
    
    async def connect(self) -> bool:
        """Initialize connections."""
        print(f"\n{'='*60}")
        print(f"ETH ARBITRAGE ENGINE")
        print(f"Order size: ${self.size_usd}")
        print(f"Entry threshold: <= {self.ENTRY_THRESHOLD_BPS} bps")
        print(f"Exit threshold: >= {self.EXIT_THRESHOLD_BPS} bps")
        print(f"Expected fees: ~{self.ROUND_TRIP_FEES} bps round-trip")
        print(f"{'='*60}\n")
        
        # Initialize database logger (non-blocking background thread)
        init_logger()
        
        # Definitive session
        ssl_ctx = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        
        cookies = {
            "privy-token": self.privy_token,
            "privy-id-token": self.privy_id_token,
            "privy-session": "privy.definitive.fi",
        }
        
        self.session = aiohttp.ClientSession(connector=connector, cookies=cookies)
        
        # Hyperliquid trader
        from adapters.hl_trader import HLTrader
        self.hl_trader = HLTrader()
        
        if not await self.hl_trader.connect():
            print("[ERROR] Failed to connect to Hyperliquid")
            return False
        
        # Start HL WebSocket for real-time prices
        if not await self._start_hl_websocket():
            print("[WARNING] HL WebSocket failed, falling back to REST")
        
        print("[OK] Connected to Definitive and Hyperliquid\n")
        
        # Token loading priority: ENV VARS first, file as fallback only
        # Railway env vars are set at deploy time and should take priority
        if self.privy_token:
            # Already have token from env vars - use it, just decode expiry
            self._token_valid_until = self._decode_jwt_exp(self.privy_token)
            print(f"[TOKEN] Using token from env vars")
        else:
            # No env var token - try loading from file as fallback
            self._load_tokens_from_file()
            print(f"[TOKEN] Loaded token from file (no env var set)")
        
        token_remaining = max(0, self._token_valid_until - time.time())
        notify_ui("token_status", {"expires_in_sec": token_remaining, "expires_at": self._token_valid_until, "refreshing": False})
        print(f"[TOKEN] Initial token expires in {token_remaining/60:.0f} min")
        
        # STARTUP VALIDATION: Test DEF API with a single quote request
        print("[STARTUP] Testing DEF API with quote request...")
        try:
            test_payload = {
                "from": self.USDC_BASE,
                "to": self.WETH_BASE,
                "chain": "base",
                "toChain": "base",
                "qty": "100",
                "orderSide": "buy",
                "type": "market",
                "degenMode": False,
                "executionPreference": 2,
            }
            async with self.session.post(
                "https://api.definitive.fi/v1/orders/quote",
                json=test_payload,
                headers=self._def_headers()
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("quoteId"):
                        print(f"[STARTUP] DEF API OK - got quote")
                        self._service_health["def_api"] = "healthy"
                        self._service_health["def_auth"] = "healthy"
                    else:
                        print(f"[STARTUP] DEF API returned no quoteId - token may be invalid")
                        notify_ui("event", {"type": "ERROR", "message": "STARTUP FAILED: DEF API returned no quote"})
                        return False
                else:
                    text = await resp.text()
                    print(f"[STARTUP] DEF API failed: {resp.status} - {text[:200]}")
                    notify_ui("event", {"type": "ERROR", "message": f"STARTUP FAILED: DEF API error {resp.status}"})
                    self._service_health["def_api"] = "error"
                    self._service_health["def_auth"] = "error"
                    return False
        except Exception as e:
            print(f"[STARTUP] DEF API test failed: {e}")
            notify_ui("event", {"type": "ERROR", "message": f"STARTUP FAILED: DEF API exception"})
            return False
        
        # Emit initial service health
        self._service_health["hl_websocket"] = "healthy" if self._hl_ws_connected else "fallback"
        self._emit_service_health()
        
        return True
    
    async def close(self):
        """Cleanup connections."""
        await self._stop_hl_websocket()
        if self.session:
            await self.session.close()
        shutdown_logger()
    
    def _def_headers(self) -> Dict[str, str]:
        """Get Definitive API headers."""
        return {
            "Content-Type": "application/json",
            "privy-token": self.privy_token,
            "organization-id": self.org_id,
            "portfolio-id": self.portfolio_id,
            "read-token": self.read_token,
            "origin": "https://app.definitive.fi",
            "referer": "https://app.definitive.fi/",
        }
    
    async def get_prices(self) -> Tuple[Optional[float], Optional[float]]:
        """Get synchronized HL and DEF prices.
        
        Strategy: 
        - HL price from WebSocket (real-time, always fresh)
        - DEF price from PRIME quote (gives valid quoteId for execution)
        - When PRIME returns, grab latest HL WebSocket price = ~0ms gap
        """
        import time
        
        self._maybe_reload_tokens()
        
        hl_price = None
        def_price = None
        prime_latency = 0
        
        # Fetch PRIME quote from DEF
        start = time.time()
        quote_payload = {
            "from": self.USDC_BASE,
            "to": self.WETH_BASE,
            "chain": "base",
            "toChain": "base",
            "qty": str(self.size_usd),
            "orderSide": "buy",
            "type": "market",
            "degenMode": False,
            "executionPreference": 2,
        }
        
        try:
            async with self.session.post(
                "https://api.definitive.fi/v1/orders/quote",
                json=quote_payload,
                headers=self._def_headers()
            ) as resp:
                prime_latency = (time.time() - start) * 1000
                if resp.status == 200:
                    data = await resp.json()
                    quote_id = data.get("quoteId", "")
                    buy_amount = float(data.get("buyAmount", 0))
                    if buy_amount > 0:
                        self._last_prime_quote_id = quote_id
                        self._last_prime_buy_amount = str(buy_amount)
                        def_price = self.size_usd / buy_amount  # USDC per WETH
                else:
                    text = await resp.text()
                    print(f"[DEF] PRIME quote error: {resp.status} - {text[:100]}")
        except Exception as e:
            print(f"[DEF] PRIME error: {e}")
        
        # Get HL price from WebSocket (instant, ~0ms gap)
        hl_price = self._get_hl_ws_price()
        price_age_ms = (time.time() - self._hl_ws_price_time) * 1000 if self._hl_ws_price_time > 0 else 9999
        
        # Fallback to REST if WebSocket price is stale
        if hl_price is None:
            print("[WS] HL WebSocket price stale, falling back to REST")
            try:
                hl_start = time.time()
                async with self.session.post(
                    "https://api.hyperliquid.xyz/info",
                    json={"type": "allMids"}
                ) as resp:
                    mids = await resp.json()
                    hl_price = float(mids.get("ETH", 0))
                    price_age_ms = (time.time() - hl_start) * 1000
            except Exception as e:
                print(f"[HL] REST fallback error: {e}")
        
        # Store latency metrics
        self._def_quote_latency_ms = prime_latency
        self._hl_ws_price_age_ms = price_age_ms
        # Price gap: HL price age when we grab it right after DEF returns
        # This shows how synchronized the prices are
        self._price_gap_ms = price_age_ms
        
        # Emit latency to UI (non-blocking callback, no latency impact)
        self._emit_latency_update()
        
        # Track consecutive DEF failures
        if def_price is None or def_price == 0:
            self._consecutive_def_failures += 1
            print(f"[DEF] Quote failed ({self._consecutive_def_failures}/{self._max_consecutive_failures} consecutive)")
        else:
            self._consecutive_def_failures = 0  # Reset on success
        
        # Update service health based on results
        self._service_health["def_api"] = "healthy" if def_price else "error"
        self._service_health["def_auth"] = "healthy" if def_price else "error"
        self._service_health["hl_websocket"] = "healthy" if self._hl_ws_connected else "fallback"
        self._service_health["hl_rest"] = "healthy" if hl_price else "error"
        
        # Token cadence check (no latency impact)
        self._check_token_cadence()
        
        # Log periodically with actual gap measurement
        self._calibration_count += 1
        if self._calibration_count % 20 == 1:
            ws_status = "WS" if self._hl_ws_connected else "REST"
            print(f"[SYNC] DEF PRIME: {prime_latency:.0f}ms | HL ({ws_status}): {price_age_ms:.0f}ms old | ACTUAL GAP: {price_age_ms:.0f}ms")
            self._emit_service_health()
        
        return hl_price, def_price
    
    async def get_exit_prices(self, weth_amount: str) -> Tuple[Optional[float], Optional[float]]:
        """Get synchronized HL and DEF SELL prices for exit.
        
        Uses WebSocket for HL (instant) + PRIME SELL quote for DEF.
        """
        import time
        
        self._maybe_reload_tokens()
        
        hl_price = None
        def_price = None
        prime_latency = 0
        
        # Fetch PRIME SELL quote from DEF
        start = time.time()
        quote_payload = {
            "from": self.WETH_BASE,
            "to": self.USDC_BASE,
            "chain": "base",
            "toChain": "base",
            "qty": weth_amount,
            "orderSide": "sell",
            "type": "market",
            "degenMode": False,
            "executionPreference": 2,
        }
        
        try:
            async with self.session.post(
                "https://api.definitive.fi/v1/orders/quote",
                json=quote_payload,
                headers=self._def_headers()
            ) as resp:
                prime_latency = (time.time() - start) * 1000
                if resp.status == 200:
                    data = await resp.json()
                    quote_id = data.get("quoteId", "")
                    # For SELL: buyAmount is USDC we receive for selling WETH
                    sell_usdc = float(data.get("buyAmount", 0))
                    weth_qty = float(weth_amount)
                    if sell_usdc > 0 and weth_qty > 0:
                        self._last_prime_sell_quote_id = quote_id
                        self._last_prime_sell_amount = str(sell_usdc)
                        def_price = sell_usdc / weth_qty  # USDC per WETH
                else:
                    text = await resp.text()
                    print(f"[DEF] SELL quote error: {resp.status} - {text[:100]}")
        except Exception as e:
            print(f"[DEF] SELL quote error: {e}")
        
        # Get HL price from WebSocket (instant, ~0ms gap)
        hl_price = self._get_hl_ws_price()
        price_age_ms = (time.time() - self._hl_ws_price_time) * 1000 if self._hl_ws_price_time > 0 else 9999
        
        # Fallback to REST if WebSocket price is stale
        if hl_price is None:
            try:
                hl_start = time.time()
                async with self.session.post(
                    "https://api.hyperliquid.xyz/info",
                    json={"type": "allMids"}
                ) as resp:
                    mids = await resp.json()
                    hl_price = float(mids.get("ETH", 0))
                    price_age_ms = (time.time() - hl_start) * 1000
            except Exception as e:
                print(f"[HL] REST fallback error: {e}")
        
        # Store latency metrics (exit prices)
        self._def_quote_latency_ms = prime_latency
        self._hl_ws_price_age_ms = price_age_ms
        self._price_gap_ms = price_age_ms  # HL price age when grabbed right after DEF
        
        # Emit latency to UI (non-blocking callback, no latency impact)
        self._emit_latency_update()
        
        # Track consecutive DEF failures
        if def_price is None or def_price == 0:
            self._consecutive_def_failures += 1
            print(f"[DEF] SELL quote failed ({self._consecutive_def_failures}/{self._max_consecutive_failures} consecutive)")
        else:
            self._consecutive_def_failures = 0  # Reset on success
        
        # Token cadence check (no latency impact)
        self._check_token_cadence()
        
        return hl_price, def_price
    
    def _pick_cycle_size(self) -> float:
        """Pick random order size for this cycle (called before cycle starts).
        
        If min_size and max_size are set, picks random value in range.
        Otherwise uses fixed size_usd.
        """
        if self._use_random_size:
            new_size = random.uniform(self.min_size, self.max_size)
            # Round to nearest dollar for cleaner orders
            new_size = round(new_size)
            print(f"[SIZE] Random size for this cycle: ${new_size} (range: ${self.min_size}-${self.max_size})")
            notify_ui("event", {"type": "INFO", "message": f"Cycle size: ${new_size}"})
            return new_size
        return self.size_usd
    
    def calc_spread(self, hl_price: float, def_price: float) -> float:
        """Calculate spread in basis points."""
        return ((def_price - hl_price) / hl_price) * 10000
    
    async def _confirm_entry_positions(self) -> bool:
        """Confirm entry execution by querying actual positions.
        
        Called AFTER entry execution. HOLDS cycle until confirmed.
        Returns True if positions match expected, False if mismatch.
        """
        print("\n[CONFIRM] Verifying entry positions...")
        
        # Query actual positions
        def_weth = await self.get_def_weth_balance()
        hl_pos = await self.hl_trader.get_position()
        hl_short = abs(hl_pos.get("size", 0))  # Short is negative, take abs
        
        # Expected values
        expected_def = self.def_weth_amount
        expected_hl = self.hl_size_eth
        
        # Calculate mismatch
        def_diff_pct = abs(def_weth - expected_def) / expected_def * 100 if expected_def > 0 else 0
        hl_diff_pct = abs(hl_short - expected_hl) / expected_hl * 100 if expected_hl > 0 else 0
        
        # Tolerance: 1% difference allowed
        tolerance_pct = 1.0
        
        if def_diff_pct <= tolerance_pct and hl_diff_pct <= tolerance_pct:
            # Also check DEF vs HL match
            cross_diff_pct = abs(def_weth - hl_short) / max(def_weth, hl_short) * 100 if max(def_weth, hl_short) > 0 else 0
            
            if cross_diff_pct <= tolerance_pct:
                print(f"[CONFIRM] ✓ Entry confirmed: DEF {def_weth:.6f} WETH, HL {hl_short:.4f} short")
                self._position_confirmed = True
                self._position_mismatch = False
                self._position_mismatch_detail = ""
                notify_ui("position_confirmed", {
                    "confirmed": True,
                    "def_amount": def_weth,
                    "hl_amount": hl_short,
                    "phase": "entry"
                })
                return True
            else:
                detail = f"DEF: {def_weth:.6f}, HL: {hl_short:.4f} (diff: {cross_diff_pct:.1f}%)"
                print(f"[CONFIRM] ⚠️ Position mismatch! {detail}")
                self._position_confirmed = False
                self._position_mismatch = True
                self._position_mismatch_detail = detail
                notify_ui("position_mismatch", {
                    "def_amount": def_weth,
                    "hl_amount": hl_short,
                    "diff_pct": cross_diff_pct,
                    "phase": "entry"
                })
                notify_ui("event", {"type": "ERROR", "message": f"Position mismatch: {detail}"})
                return False
        else:
            detail = f"DEF expected {expected_def:.6f} got {def_weth:.6f}, HL expected {expected_hl:.4f} got {hl_short:.4f}"
            print(f"[CONFIRM] ⚠️ Fill mismatch! {detail}")
            self._position_confirmed = False
            self._position_mismatch = True
            self._position_mismatch_detail = detail
            notify_ui("position_mismatch", {
                "def_amount": def_weth,
                "hl_amount": hl_short,
                "expected_def": expected_def,
                "expected_hl": expected_hl,
                "phase": "entry"
            })
            notify_ui("event", {"type": "ERROR", "message": f"Fill mismatch: {detail}"})
            return False
    
    async def _confirm_exit_positions(self) -> bool:
        """Confirm exit execution by verifying positions are flat.
        
        Called AFTER exit execution. HOLDS cycle until confirmed.
        Returns True if flat, False if residual position.
        """
        print("\n[CONFIRM] Verifying exit positions (should be flat)...")
        
        # Query actual positions
        def_weth = await self.get_def_weth_balance()
        hl_pos = await self.hl_trader.get_position()
        hl_short = abs(hl_pos.get("size", 0))
        
        # Tolerance: less than 0.0001 ETH ($0.20 at $2000/ETH)
        flat_tolerance = 0.0001
        
        if def_weth <= flat_tolerance and hl_short <= flat_tolerance:
            print(f"[CONFIRM] ✓ Exit confirmed: Positions flat (DEF: {def_weth:.6f}, HL: {hl_short:.6f})")
            self._position_confirmed = True
            self._position_mismatch = False
            self._position_mismatch_detail = ""
            notify_ui("position_confirmed", {
                "confirmed": True,
                "def_amount": def_weth,
                "hl_amount": hl_short,
                "phase": "exit",
                "flat": True
            })
            return True
        else:
            detail = f"Residual position: DEF {def_weth:.6f} WETH, HL {hl_short:.4f} short"
            print(f"[CONFIRM] ⚠️ Not flat! {detail}")
            self._position_confirmed = False
            self._position_mismatch = True
            self._position_mismatch_detail = detail
            notify_ui("position_mismatch", {
                "def_amount": def_weth,
                "hl_amount": hl_short,
                "phase": "exit",
                "flat": False
            })
            notify_ui("event", {"type": "WARNING", "message": f"Exit not flat: {detail}"})
            return False
    
    def _emit_latency_update(self):
        """Emit current latency metrics to UI."""
        notify_ui("latency", {
            "def_quote_ms": self._def_quote_latency_ms,
            "hl_ws_age_ms": self._hl_ws_price_age_ms,
            "price_gap_ms": self._price_gap_ms,
            "def_exec_ms": self._def_exec_latency_ms,
            "hl_exec_ms": self._hl_exec_latency_ms
        })
    
    def _emit_cycle_bps(self):
        """Emit cycle BPS/slippage metrics to UI."""
        entry_slip = self._actual_entry_spread - self._expected_entry_spread
        exit_slip = self._actual_exit_spread - self._expected_exit_spread
        total_slip = entry_slip + exit_slip
        
        notify_ui("cycle_bps", {
            "expected_entry": self._expected_entry_spread,
            "actual_entry": self._actual_entry_spread,
            "entry_slippage": entry_slip,
            "expected_exit": self._expected_exit_spread,
            "actual_exit": self._actual_exit_spread,
            "exit_slippage": exit_slip,
            "total_slippage": total_slip
        })
    
    def _emit_service_health(self):
        """Emit service health status to UI."""
        notify_ui("service_health", self._service_health)
    
    def _check_token_cadence(self):
        """Check token status on cadence (60s) - no latency impact."""
        now = time.time()
        if now - self._last_token_cadence_check >= self._token_cadence_interval:
            self._last_token_cadence_check = now
            expires_in = max(0, self._token_valid_until - now)
            notify_ui("token_checked", {
                "expires_in_sec": expires_in,
                "expires_at": self._token_valid_until,  # Send timestamp so UI can count down locally
                "last_checked_at": now
            })
    
    async def _check_position_balance(self) -> bool:
        """Check if positions are balanced. Returns True if OK to proceed, False if HALT needed.
        
        BALANCED means:
          - Both flat (DEF WETH ≈ 0 AND HL short ≈ 0), OR
          - Both have matching positions (DEF WETH > 0 AND HL short ≈ DEF WETH)
        
        IMBALANCED means:
          - HL has short but no DEF WETH
          - DEF has WETH but no HL short
          - Sizes don't match (>5% difference)
        
        If imbalanced → HALT, don't continue.
        """
        print("[BALANCE CHECK] Verifying position balance...")
        
        # Get actual positions
        def_weth = await self.get_def_weth_balance()
        hl_pos = await self.hl_trader.get_position()
        hl_size = abs(hl_pos.get("size", 0))  # HL short is negative, take abs
        
        # Thresholds
        flat_threshold = 0.0001  # Consider flat if < 0.0001 ETH
        match_tolerance = 0.05  # 5% tolerance for matching
        
        def_has_position = def_weth > flat_threshold
        hl_has_position = hl_size > flat_threshold
        
        # Case 1: Both flat - BALANCED
        if not def_has_position and not hl_has_position:
            print(f"[BALANCE CHECK] ✓ Both flat (DEF: {def_weth:.6f}, HL: {hl_size:.6f})")
            notify_ui("event", {"type": "INFO", "message": "Position check: Both flat ✓"})
            return True
        
        # Case 2: Both have positions - check if they match
        if def_has_position and hl_has_position:
            diff_pct = abs(def_weth - hl_size) / max(def_weth, hl_size)
            if diff_pct <= match_tolerance:
                print(f"[BALANCE CHECK] ✓ Positions match (DEF: {def_weth:.6f}, HL: {hl_size:.6f}, diff: {diff_pct*100:.1f}%)")
                notify_ui("event", {"type": "INFO", "message": f"Position check: Matched ✓ (DEF: {def_weth:.4f}, HL: {hl_size:.4f})"})
                return True
            else:
                print(f"[BALANCE CHECK] ✗ IMBALANCED - Size mismatch! DEF: {def_weth:.6f}, HL: {hl_size:.6f}, diff: {diff_pct*100:.1f}%")
                notify_ui("event", {"type": "ERROR", "message": f"IMBALANCED: Size mismatch! DEF: {def_weth:.4f}, HL: {hl_size:.4f}"})
                notify_ui("position_mismatch", {"def_amount": def_weth, "hl_amount": hl_size, "diff_pct": diff_pct * 100})
                return False
        
        # Case 3: Only HL has position - IMBALANCED
        if hl_has_position and not def_has_position:
            print(f"[BALANCE CHECK] ✗ IMBALANCED - HL has {hl_size:.4f} short but DEF has no WETH!")
            notify_ui("event", {"type": "ERROR", "message": f"IMBALANCED: HL has {hl_size:.4f} short but no DEF WETH!"})
            notify_ui("position_mismatch", {"def_amount": def_weth, "hl_amount": hl_size, "diff_pct": 100})
            return False
        
        # Case 4: Only DEF has position - IMBALANCED
        if def_has_position and not hl_has_position:
            print(f"[BALANCE CHECK] ✗ IMBALANCED - DEF has {def_weth:.4f} WETH but HL has no short!")
            notify_ui("event", {"type": "ERROR", "message": f"IMBALANCED: DEF has {def_weth:.4f} WETH but no HL short!"})
            notify_ui("position_mismatch", {"def_amount": def_weth, "hl_amount": hl_size, "diff_pct": 100})
            return False
        
        return True
    
    async def get_def_balance(self) -> float:
        """Get USDC balance on Definitive."""
        try:
            url = "https://client-api.definitive.fi/v1/position?includeDust=false&includeSpamAssets=false&limit=50"
            async with self.session.get(url, headers=self._def_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for pos in data.get("data", []):
                        asset = pos.get("asset", {})
                        if asset.get("symbol") == "USDC" and asset.get("chain", {}).get("name") == "base":
                            return float(pos.get("amount", 0))
        except Exception as e:
            print(f"[DEF] Balance error: {e}")
        return 0.0
    
    async def get_def_weth_balance(self) -> float:
        """Get WETH balance on Definitive (as float for calculations)."""
        try:
            url = "https://client-api.definitive.fi/v1/position?includeDust=false&includeSpamAssets=false&limit=50"
            async with self.session.get(url, headers=self._def_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for pos in data.get("data", []):
                        asset = pos.get("asset", {})
                        if asset.get("symbol") == "WETH" and asset.get("chain", {}).get("name") == "base":
                            return float(pos.get("amount", 0))
        except Exception as e:
            print(f"[DEF] WETH Balance error: {e}")
        return 0.0
    
    async def get_def_weth_balance_raw(self) -> str:
        """Get WETH balance on Definitive as RAW STRING (preserves precision for orders)."""
        try:
            url = "https://client-api.definitive.fi/v1/position?includeDust=false&includeSpamAssets=false&limit=50"
            async with self.session.get(url, headers=self._def_headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for pos in data.get("data", []):
                        asset = pos.get("asset", {})
                        if asset.get("symbol") == "WETH" and asset.get("chain", {}).get("name") == "base":
                            return pos.get("amount", "0")
        except Exception as e:
            print(f"[DEF] WETH Balance error: {e}")
        return "0"
    
    async def get_hl_balance(self) -> float:
        """Get USDC balance on Hyperliquid (main wallet)."""
        try:
            main_wallet = os.getenv("HL_MAIN_WALLET")
            async with self.session.post(
                "https://api.hyperliquid.xyz/info",
                json={"type": "clearinghouseState", "user": main_wallet}
            ) as resp:
                state = await resp.json()
                margin = state.get("marginSummary", {})
                return float(margin.get("accountValue", 0))
        except Exception as e:
            print(f"[HL] Balance error: {e}")
        return 0.0
    
    async def execute_entry(self, hl_price: float, def_price: float, spread_bps: float, usdc_before: float, weth_before: float = 0.0) -> bool:
        """Execute entry trades on both platforms.
        
        Args:
            usdc_before: Cached USDC balance from start of cycle (no API call here)
            weth_before: Cached WETH balance from start of cycle (to calculate delta)
        """
        import time
        
        # NO BALANCE CHECKS HERE - execute immediately!
        
        print(f"\n{'='*40}")
        print(f"ENTRY SIGNAL: Spread = {spread_bps:+.1f} bps")
        print(f"{'='*40}")
        
        weth_to_buy = self.size_usd / def_price
        mode = "TURBO" if self.USE_TURBO else "PRIME"
        
        print(f"\n[ENTRY] Executing both legs simultaneously ({mode})...")
        print(f"  DEF: BUY {weth_to_buy:.6f} WETH @ ${def_price:.2f}")
        if self.USE_TURBO and self._last_prime_quote_id:
            print(f"  DEF: Using BUY quoteId: {self._last_prime_quote_id[:20]}... quotedOut: {self._last_prime_buy_amount}")
        elif self.USE_TURBO:
            print(f"  DEF: WARNING - No BUY quoteId available!")
        print(f"  HL:  SHORT {weth_to_buy:.4f} ETH-PERP @ ${hl_price:.2f}")
        
        # usdc_before passed in from cached value - NO API CALL
        
        def_success = False
        def_result = {}
        hl_success = False
        hl_result = {}
        amount_out = None
        def_latency_ms = 0
        hl_latency_ms = 0
        
        try:
            if self.USE_TURBO:
                # TURBO: Direct order with PRIME quoteId for fast execution
                async def def_order():
                    start = time.time()
                    order_payload = {
                        "from": self.USDC_BASE,
                        "to": self.WETH_BASE,
                        "chain": "base",
                        "toChain": "base",
                        "qty": str(self.size_usd),
                        "orderSide": "buy",
                        "type": "market",
                        "orderSourceClient": "ORDER_SOURCE_CLIENT_WEB_APP",
                        "orderSourceProduct": "ORDER_SOURCE_PRODUCT_TURBO",
                        "quickTrade": True,
                        "slippageTolerance": self.SLIPPAGE_TOLERANCE,
                        "displayedAssetPrice": str(def_price),
                        "quoteId": self._last_prime_quote_id,
                        "quotedAmountOut": self._last_prime_buy_amount,
                    }
                    
                    async with self.session.post(
                        "https://api.definitive.fi/v1/orders",
                        json=order_payload,
                        headers=self._def_headers()
                    ) as resp:
                        latency = (time.time() - start) * 1000
                        result = await resp.json()
                        return resp.status == 200, result, latency
                
                async def hl_order():
                    start = time.time()
                    result = await self.hl_trader.taker_order("sell", size_usd=self.size_usd, price_hint=hl_price)
                    latency = (time.time() - start) * 1000
                    return result, latency
                
                def_task = asyncio.create_task(def_order())
                hl_task = asyncio.create_task(hl_order())
                
                (def_success, def_result, def_latency_ms), (hl_result, hl_latency_ms) = await asyncio.gather(def_task, hl_task)
                hl_success = hl_result.get("success", False)
                
                # For TURBO, estimate amount_out from price
                amount_out = str(self.size_usd / def_price)
                
            else:
                # PRIME: Quote first, then order
                quote_start = time.time()
                quote_payload = {
                    "from": self.USDC_BASE,
                    "to": self.WETH_BASE,
                    "chain": "base",
                    "toChain": "base",
                    "qty": str(self.size_usd),
                    "orderSide": "buy",
                    "type": "market",
                    "degenMode": False,
                    "executionPreference": 2,
                }
                
                async with self.session.post(
                    "https://api.definitive.fi/v1/orders/quote",
                    json=quote_payload,
                    headers=self._def_headers()
                ) as resp:
                    if resp.status != 200:
                        print(f"[DEF] Quote failed: {resp.status}")
                        return False
                    quote = await resp.json()
                
                quote_latency = (time.time() - quote_start) * 1000
                
                quote_id = quote.get("quoteId")
                amount_out = quote.get("buyAmount")
                price_impact = quote.get("estimatedPriceImpact")
                
                # Validate price impact before executing ANY orders
                price_impact_bps = float(price_impact or 0) * 10000  # Convert to bps
                max_impact_bps = float(self.SLIPPAGE_TOLERANCE) * 10000
                
                if price_impact_bps > max_impact_bps:
                    print(f"[ENTRY REJECTED] Price impact {price_impact_bps:.1f} bps > threshold {max_impact_bps:.1f} bps")
                    print(f"[ENTRY REJECTED] NOT executing DEF or HL orders")
                    notify_ui("event", {"type": "REJECTED", "message": f"Entry rejected: price impact {price_impact_bps:.1f} bps > {max_impact_bps:.1f} bps threshold"})
                    return False
                
                print(f"[QUOTE OK] Price impact {price_impact_bps:.1f} bps <= {max_impact_bps:.1f} bps - proceeding")
                
                async def def_order():
                    start = time.time()
                    order_payload = {
                        "from": self.USDC_BASE,
                        "to": self.WETH_BASE,
                        "chain": "base",
                        "toChain": "base",
                        "qty": str(self.size_usd),
                        "orderSide": "buy",
                        "type": "market",
                        "degenMode": False,
                        "maxPriorityFee": None,
                        "slippageTolerance": None,
                        "bridgeQuoteId": "",
                        "quoteId": quote_id,
                        "quotedAmountOut": amount_out,
                        "quotedPriceImpact": price_impact,
                        "orderSourceClient": "ORDER_SOURCE_CLIENT_WEB_APP",
                    }
                    
                    async with self.session.post(
                        "https://api.definitive.fi/v1/orders",
                        json=order_payload,
                        headers=self._def_headers()
                    ) as resp:
                        latency = (time.time() - start) * 1000
                        return resp.status == 200, await resp.json(), latency + quote_latency
                
                async def hl_order():
                    start = time.time()
                    result = await self.hl_trader.taker_order("sell", size_usd=self.size_usd, price_hint=hl_price)
                    latency = (time.time() - start) * 1000
                    return result, latency
                
                def_task = asyncio.create_task(def_order())
                hl_task = asyncio.create_task(hl_order())
                
                (def_success, def_result, def_latency_ms), (hl_result, hl_latency_ms) = await asyncio.gather(def_task, hl_task)
                hl_success = hl_result.get("success", False)
            
            print(f"\n[LATENCY] DEF ({mode}): {def_latency_ms:.0f}ms | HL: {hl_latency_ms:.0f}ms")
            print(f"[DEF RESPONSE] {def_result}")
            
            # Check DEF response for errors (even if HTTP 200)
            if def_result.get("error") or def_result.get("message"):
                print(f"[DEF ORDER ERROR] {def_result.get('error') or def_result.get('message')}")
                def_success = False
            
        except Exception as e:
            print(f"[ERROR] Entry execution: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        if def_success and hl_success:
            # HL is confirmed filled from response
            hl_fill_price = hl_result.get("fill_price")
            hl_size = hl_result.get("size", 0)
            
            if not hl_fill_price or hl_size == 0:
                print(f"[ERROR] HL order not filled properly: {hl_result}")
                return False
            
            # Poll for DEF settlement (WETH must increase from weth_before)
            print(f"[WAITING] Confirming DEF settlement... (weth_before: {weth_before:.6f})")
            max_wait = 15
            poll_interval = 1
            waited = 0
            weth_total = weth_before
            weth_received = 0
            weth_raw = "0"
            usdc_after = usdc_before
            
            while waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                weth_total = await self.get_def_weth_balance()
                weth_received = weth_total - weth_before  # DELTA, not total
                if weth_received > 0.0001:
                    weth_raw = await self.get_def_weth_balance_raw()
                    usdc_after = await self.get_def_balance()
                    print(f"[CONFIRMED] DEF settled after {waited}s - WETH received: {weth_received:.6f} (total: {weth_total:.6f})")
                    break
                print(f"  ... waiting ({waited}s) - WETH delta: {weth_received:.6f}")
            
            if weth_received < 0.0001:
                print(f"[ERROR] DEF order did not settle after {max_wait}s!")
                print(f"[CRITICAL] HL is SHORT but DEF has no WETH - UNHEDGED POSITION!")
                return False
            
            # Calculate actual fill price from balance change
            usdc_spent = usdc_before - usdc_after
            actual_def_price = usdc_spent / weth_received if weth_received > 0 else def_price
            
            # Store position state
            self.in_position = True
            self.entry_spread_bps = spread_bps
            self.def_weth_amount = weth_received
            self.def_weth_amount_raw = weth_raw
            self.def_entry_price = actual_def_price
            self.hl_size_eth = hl_size
            self.hl_entry_price = hl_fill_price
            
            # Store latency metrics
            self._def_exec_latency_ms = def_latency_ms
            self._hl_exec_latency_ms = hl_latency_ms
            
            # Calculate actual entry spread (based on fill prices)
            self._expected_entry_spread = spread_bps
            self._actual_entry_spread = self.calc_spread(hl_fill_price, actual_def_price)
            entry_slip = self._actual_entry_spread - self._expected_entry_spread
            
            print(f"\n[ENTRY COMPLETE]")
            print(f"  DEF: USDC spent ${usdc_spent:.2f}, WETH received {weth_received:.6f}")
            print(f"  DEF: Bought {self.def_weth_amount:.6f} WETH @ ${self.def_entry_price:.2f} (quoted: ${def_price:.2f})")
            print(f"  HL:  Shorted {self.hl_size_eth:.4f} ETH @ ${self.hl_entry_price:.2f}")
            print(f"  Entry spread: {spread_bps:+.1f} bps (expected) → {self._actual_entry_spread:+.1f} bps (actual)")
            print(f"  Entry slippage: {entry_slip:+.1f} bps")
            print(f"  Latency: DEF {def_latency_ms:.0f}ms, HL {hl_latency_ms:.0f}ms")
            
            notify_ui("event", {"type": "ENTRY", "message": f"Entry complete: DEF ${usdc_spent:.2f} spent, HL shorted {self.hl_size_eth:.4f} ETH"})
            notify_ui("position", {"in_position": True, "entry_spread_bps": spread_bps, "status": "IN_POSITION"})
            self._emit_latency_update()
            
            # Log trade to DB (non-blocking)
            log_trade(
                cycle_id=self.cycle_count,
                side="ENTRY",
                expected_spread_bps=spread_bps,
                actual_spread_bps=self._actual_entry_spread,
                hl_price=hl_price,
                def_price=def_price,
                order_size_usd=self.size_usd,
                def_fill_amount=self.def_weth_amount,
                hl_fill_amount=self.hl_size_eth,
                def_latency_ms=def_latency_ms,
                hl_latency_ms=hl_latency_ms,
                total_exec_ms=def_latency_ms + hl_latency_ms,
                success=True
            )
            
            return True
        else:
            print(f"\n[ENTRY FAILED]")
            print(f"  DEF success: {def_success}, result: {def_result}")
            print(f"  HL success: {hl_success}, error: {hl_result.get('error')}")
            notify_ui("event", {"type": "ERROR", "message": f"Entry failed: DEF={def_success}, HL={hl_success}"})
            
            # Log failed trade to DB (non-blocking)
            log_trade(
                cycle_id=self.cycle_count,
                side="ENTRY",
                expected_spread_bps=spread_bps,
                actual_spread_bps=0,
                hl_price=hl_price,
                def_price=def_price,
                order_size_usd=self.size_usd,
                def_fill_amount=0,
                hl_fill_amount=0,
                def_latency_ms=0,
                hl_latency_ms=0,
                total_exec_ms=0,
                success=False,
                error=f"DEF={def_success}, HL={hl_success}"
            )
            
            return False
    
    async def execute_exit(self, hl_price: float, def_price: float, spread_bps: float, usdc_before_exit: float) -> bool:
        """Execute exit trades on both platforms.
        
        Args:
            usdc_before_exit: Cached USDC balance from after entry (no API call here)
        """
        import time
        
        # NO BALANCE CHECKS HERE - execute immediately!
        
        print(f"\n{'='*40}")
        print(f"EXIT SIGNAL: Spread = {spread_bps:+.1f} bps")
        print(f"{'='*40}")
        
        mode = "TURBO" if self.USE_TURBO else "PRIME"
        
        # Use stored raw string from entry (no latency on exit)
        exact_weth_balance_str = self.def_weth_amount_raw
        exact_weth_balance = self.def_weth_amount
        
        print(f"\n[EXIT] Executing both legs simultaneously ({mode})...")
        print(f"  DEF: SELL {exact_weth_balance_str} WETH @ ${def_price:.2f}")
        if self._last_prime_sell_quote_id:
            print(f"  DEF: Using SELL quoteId: {self._last_prime_sell_quote_id[:20]}... quotedOut: {self._last_prime_sell_amount}")
        else:
            print(f"  DEF: WARNING - No SELL quoteId available!")
        print(f"  HL:  CLOSE SHORT {self.hl_size_eth:.4f} ETH-PERP @ ${hl_price:.2f}")
        
        def_success = False
        def_result = {}
        hl_success = False
        hl_result = {}
        def_fee = 0
        def_latency_ms = 0
        hl_latency_ms = 0
        
        try:
            if self.USE_TURBO:
                # TURBO: Direct order with PRIME SELL quoteId for price locking
                async def def_order():
                    start = time.time()
                    order_payload = {
                        "from": self.WETH_BASE,
                        "to": self.USDC_BASE,
                        "chain": "base",
                        "toChain": "base",
                        "qty": exact_weth_balance_str,
                        "qtyPct": "1",
                        "orderSide": "sell",
                        "type": "market",
                        "orderSourceClient": "ORDER_SOURCE_CLIENT_WEB_APP",
                        "orderSourceProduct": "ORDER_SOURCE_PRODUCT_TURBO",
                        "quickTrade": True,
                        "slippageTolerance": self.SLIPPAGE_TOLERANCE,
                        "displayedAssetPrice": str(def_price),
                        "quoteId": self._last_prime_sell_quote_id,
                        "quotedAmountOut": self._last_prime_sell_amount,
                    }
                    
                    async with self.session.post(
                        "https://api.definitive.fi/v1/orders",
                        json=order_payload,
                        headers=self._def_headers()
                    ) as resp:
                        latency = (time.time() - start) * 1000
                        text = await resp.text()
                        try:
                            result = json.loads(text)
                        except:
                            result = {"error": text}
                        return resp.status == 200, result, latency
                
                async def hl_order():
                    start = time.time()
                    result = await self.hl_trader.taker_order("buy", size_usd=self.hl_size_eth * hl_price, price_hint=hl_price)
                    latency = (time.time() - start) * 1000
                    return result, latency
                
                # Run orders in parallel - NO balance fetch, use cached value passed in
                def_task = asyncio.create_task(def_order())
                hl_task = asyncio.create_task(hl_order())
                
                (def_success, def_result, def_latency_ms), (hl_result, hl_latency_ms) = await asyncio.gather(def_task, hl_task)
                hl_success = hl_result.get("success", False)
                # usdc_before_exit passed in as parameter
                
            else:
                # PRIME: Quote first, then order
                quote_start = time.time()
                quote_payload = {
                    "from": self.WETH_BASE,
                    "to": self.USDC_BASE,
                    "chain": "base",
                    "toChain": "base",
                    "qty": exact_weth_balance_str,
                    "orderSide": "sell",
                    "type": "market",
                    "degenMode": False,
                    "executionPreference": 2,
                }
                
                async with self.session.post(
                    "https://api.definitive.fi/v1/orders/quote",
                    json=quote_payload,
                    headers=self._def_headers()
                ) as resp:
                    if resp.status != 200:
                        print(f"[DEF] Quote failed: {resp.status}")
                        return False
                    quote = await resp.json()
                
                quote_latency = (time.time() - quote_start) * 1000
                
                quote_id = quote.get("quoteId")
                amount_out = quote.get("buyAmount")
                price_impact = quote.get("estimatedPriceImpact")
                def_fee = float(quote.get("estimatedFeeNotional", 0))
                
                # Validate price impact before executing ANY orders
                price_impact_bps = float(price_impact or 0) * 10000  # Convert to bps
                max_impact_bps = float(self.SLIPPAGE_TOLERANCE) * 10000
                
                if price_impact_bps > max_impact_bps:
                    print(f"[EXIT REJECTED] Price impact {price_impact_bps:.1f} bps > threshold {max_impact_bps:.1f} bps")
                    print(f"[EXIT REJECTED] NOT executing DEF or HL orders - position still open")
                    notify_ui("event", {"type": "REJECTED", "message": f"Exit rejected: price impact {price_impact_bps:.1f} bps > {max_impact_bps:.1f} bps threshold"})
                    return False
                
                print(f"[QUOTE OK] Price impact {price_impact_bps:.1f} bps <= {max_impact_bps:.1f} bps - proceeding")
                
                async def def_order():
                    start = time.time()
                    order_payload = {
                        "from": self.WETH_BASE,
                        "to": self.USDC_BASE,
                        "chain": "base",
                        "toChain": "base",
                        "qty": exact_weth_balance_str,
                        "orderSide": "sell",
                        "type": "market",
                        "degenMode": False,
                        "maxPriorityFee": None,
                        "slippageTolerance": None,
                        "bridgeQuoteId": "",
                        "quoteId": quote_id,
                        "quotedAmountOut": amount_out,
                        "quotedPriceImpact": price_impact,
                        "orderSourceClient": "ORDER_SOURCE_CLIENT_WEB_APP",
                    }
                    
                    async with self.session.post(
                        "https://api.definitive.fi/v1/orders",
                        json=order_payload,
                        headers=self._def_headers()
                    ) as resp:
                        latency = (time.time() - start) * 1000
                        text = await resp.text()
                        try:
                            result = json.loads(text)
                        except:
                            result = {"error": text}
                        return resp.status == 200, result, latency + quote_latency
                
                async def hl_order():
                    start = time.time()
                    result = await self.hl_trader.taker_order("buy", size_usd=self.hl_size_eth * hl_price, price_hint=hl_price)
                    latency = (time.time() - start) * 1000
                    return result, latency
                
                # Run orders in parallel - NO balance fetch, use cached value passed in
                def_task = asyncio.create_task(def_order())
                hl_task = asyncio.create_task(hl_order())
                
                (def_success, def_result, def_latency_ms), (hl_result, hl_latency_ms) = await asyncio.gather(def_task, hl_task)
                hl_success = hl_result.get("success", False)
                # usdc_before_exit passed in as parameter
            
            print(f"\n[LATENCY] DEF ({mode}): {def_latency_ms:.0f}ms | HL: {hl_latency_ms:.0f}ms")
            print(f"[DEF RESPONSE] {def_result}")
            
            # Check DEF response for errors (even if HTTP 200)
            if def_result.get("error") or def_result.get("message"):
                print(f"[DEF ORDER ERROR] {def_result.get('error') or def_result.get('message')}")
                def_success = False
            
        except Exception as e:
            print(f"[ERROR] Exit execution: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        if def_success and hl_success:
            # HL is confirmed filled from response
            hl_exit_price = hl_result.get("fill_price")
            
            if not hl_exit_price:
                print(f"[ERROR] HL exit order not filled properly: {hl_result}")
                return False
            
            # Poll for DEF settlement (WETH must be gone from balance)
            print(f"[WAITING] Confirming DEF exit settlement...")
            max_wait = 15
            poll_interval = 1
            waited = 0
            weth_after_exit = exact_weth_balance
            usdc_after_exit = usdc_before_exit
            
            while waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                weth_after_exit = await self.get_def_weth_balance()
                if weth_after_exit < 0.0001:  # WETH sold
                    usdc_after_exit = await self.get_def_balance()
                    print(f"[CONFIRMED] DEF exit settled after {waited}s - WETH remaining: {weth_after_exit:.6f}")
                    break
                print(f"  ... waiting ({waited}s) - WETH remaining: {weth_after_exit:.6f}")
            
            if weth_after_exit > 0.0001:
                print(f"[ERROR] DEF exit did not settle after {max_wait}s!")
                print(f"[CRITICAL] HL closed but DEF still has WETH - UNHEDGED POSITION!")
                return False
            
            # Calculate actual fill price from balance change
            usdc_received = usdc_after_exit - usdc_before_exit
            weth_sold = exact_weth_balance - weth_after_exit
            
            if weth_sold > 0:
                def_exit_price = usdc_received / weth_sold
            else:
                def_exit_price = def_price
            
            print(f"  DEF: USDC received ${usdc_received:.2f}, WETH sold {weth_sold:.6f}")
            
            # DEF P&L: (exit - entry) * amount
            def_pnl = (def_exit_price - self.def_entry_price) * self.def_weth_amount
            
            # HL P&L: (entry - exit) * amount (short position)
            hl_pnl = (self.hl_entry_price - hl_exit_price) * self.hl_size_eth
            
            # Estimate fees
            total_fees = (self.size_usd * (self.DEF_FEE_BPS + self.HL_FEE_BPS) * 2) / 10000
            
            net_pnl = def_pnl + hl_pnl - total_fees
            
            self.cycle_count += 1
            
            # Store exit latency metrics
            self._def_exec_latency_ms = def_latency_ms
            self._hl_exec_latency_ms = hl_latency_ms
            
            # Calculate actual exit spread (based on fill prices)
            self._expected_exit_spread = spread_bps
            self._actual_exit_spread = self.calc_spread(hl_exit_price, def_exit_price)
            exit_slip = self._actual_exit_spread - self._expected_exit_spread
            total_slip = (self._actual_entry_spread - self._expected_entry_spread) + exit_slip
            
            print(f"\n[EXIT COMPLETE]")
            print(f"  DEF: Sold @ ${def_exit_price:.2f} (quoted: ${def_price:.2f}) | P&L: ${def_pnl:+.4f}")
            print(f"  HL:  Closed @ ${hl_exit_price:.2f} | P&L: ${hl_pnl:+.4f}")
            print(f"  Fees: ~${total_fees:.4f}")
            print(f"  Exit spread: {spread_bps:+.1f} bps (expected) → {self._actual_exit_spread:+.1f} bps (actual)")
            print(f"  Exit slippage: {exit_slip:+.1f} bps | Total cycle slippage: {total_slip:+.1f} bps")
            print(f"  Latency: DEF {def_latency_ms:.0f}ms, HL {hl_latency_ms:.0f}ms")
            
            # Send detailed cycle summary to UI
            notify_ui("event", {"type": "CYCLE_COMPLETE", "message": f"Cycle complete: Net P&L ${net_pnl:+.4f} (fees ${total_fees:.4f})"})
            notify_ui("cycle_complete", {
                "realized_pnl": net_pnl,
                "fees": total_fees,
                "def_pnl": def_pnl,
                "hl_pnl": hl_pnl,
                "entry_spread": self.entry_spread_bps,
                "exit_spread": spread_bps,
                "def_latency_ms": def_latency_ms,
                "hl_latency_ms": hl_latency_ms,
                "def_entry_price": self.def_entry_price,
                "def_exit_price": def_exit_price,
                "hl_entry_price": self.hl_entry_price,
                "hl_exit_price": hl_exit_price
            })
            notify_ui("position", {"in_position": False, "status": "IDLE"})
            self._emit_latency_update()
            self._emit_cycle_bps()
            
            # Log trade to DB (non-blocking)
            log_trade(
                cycle_id=self.cycle_count,
                side="EXIT",
                expected_spread_bps=spread_bps,
                actual_spread_bps=self._actual_exit_spread,
                hl_price=hl_price,
                def_price=def_price,
                order_size_usd=self.size_usd,
                def_fill_amount=self.def_weth_amount,
                hl_fill_amount=self.hl_size_eth,
                def_latency_ms=def_latency_ms,
                hl_latency_ms=hl_latency_ms,
                total_exec_ms=def_latency_ms + hl_latency_ms,
                success=True,
                gross_pnl=def_pnl + hl_pnl,
                net_pnl=net_pnl
            )
            
            print(f"  NET P&L: ${net_pnl:+.4f}")
            print(f"  Spread captured: {spread_bps - self.entry_spread_bps:.1f} bps")
            
            # Reset ALL state for clean next cycle
            self.in_position = False
            self.entry_spread_bps = 0
            self.def_weth_amount = 0
            self.def_weth_amount_raw = "0"
            self.hl_size_eth = 0
            self.def_entry_price = 0.0
            self.hl_entry_price = 0.0
            
            # Clear quote IDs to avoid using stale values
            self._last_prime_quote_id = ""
            self._last_prime_buy_amount = ""
            self._last_prime_sell_quote_id = ""
            self._last_prime_sell_amount = ""
            
            return True
        else:
            print(f"\n[EXIT FAILED]")
            print(f"  DEF success: {def_success}, result: {def_result}")
            print(f"  HL success: {hl_success}, error: {hl_result.get('error')}")
            notify_ui("event", {"type": "ERROR", "message": f"Exit failed: DEF={def_success}, HL={hl_success}"})
            
            # Log failed trade to DB (non-blocking)
            log_trade(
                cycle_id=self.cycle_count,
                side="EXIT",
                expected_spread_bps=spread_bps,
                actual_spread_bps=0,
                hl_price=hl_price,
                def_price=def_price,
                order_size_usd=self.size_usd,
                def_fill_amount=0,
                hl_fill_amount=0,
                def_latency_ms=0,
                hl_latency_ms=0,
                total_exec_ms=0,
                success=False,
                error=f"DEF={def_success}, HL={hl_success}"
            )
            
            return False
    
    async def run_cycle(self) -> bool:
        """Run a single arbitrage cycle (entry → hold → exit)."""
        print(f"\n{'#'*60}")
        print(f"CYCLE {self.cycle_count + 1}: Waiting for entry signal...")
        print(f"{'#'*60}")
        
        # Send thresholds to UI at cycle start
        notify_ui("thresholds", {"entry_bps": self.ENTRY_THRESHOLD_BPS, "exit_bps": self.EXIT_THRESHOLD_BPS})
        
        # Warmup check - don't trade for first N seconds
        if self._engine_start_time == 0:
            self._engine_start_time = time.time()
        
        warmup_remaining = self._warmup_seconds - (time.time() - self._engine_start_time)
        if warmup_remaining > 0:
            notify_ui("warmup", {"remaining_sec": warmup_remaining})
            print(f"[WARMUP] Waiting {warmup_remaining:.0f}s before trading...")
            notify_ui("event", {"type": "INFO", "message": f"Warmup: {warmup_remaining:.0f}s remaining"})
            await asyncio.sleep(warmup_remaining)
        
        notify_ui("warmup", {"remaining_sec": 0})
        print(f"[WARMUP] Complete.")
        
        # CRITICAL: Check position balance before ANY trading
        if not await self._check_position_balance():
            print("[HALT] Position imbalance detected. STOPPING ENGINE.")
            notify_ui("event", {"type": "ERROR", "message": "HALTED: Position imbalance! Manual intervention required."})
            ui_state_update = {"status": "HALTED", "mode": "HALTED"}
            notify_ui("status", ui_state_update)
            return False  # Don't proceed with this cycle
        
        # Pick random size for this cycle (no latency impact - done before entry detection)
        self.size_usd = self._pick_cycle_size()
        print(f"[OK] Position balanced. Trading enabled.")
        notify_ui("event", {"type": "INFO", "message": "Position check passed. Trading enabled."})
        
        # Load initial token expiry and notify UI
        print(f"[DEBUG] Loading tokens...")
        self._load_tokens_from_file()
        token_remaining = self._token_valid_until - time.time()
        print(f"[DEBUG] Token expires in {token_remaining:.0f}s")
        notify_ui("token_status", {"expires_in_sec": token_remaining, "refreshing": False})
        
        # Get balances ONCE at start of cycle (cached for entry execution)
        print(f"[DEBUG] Fetching DEF balances...")
        cached_usdc_before = await self.get_def_balance()
        cached_weth_before = await self.get_def_weth_balance()
        print(f"[CYCLE START] Cached DEF USDC: ${cached_usdc_before:,.2f}, WETH: {cached_weth_before:.6f}")
        
        # DETECT EXISTING POSITIONS - resume if we have open positions
        if not self.in_position:
            def_weth = await self.get_def_weth_balance()
            def_weth_raw = await self.get_def_weth_balance_raw()
            hl_position = await self.hl_trader.get_position()
            hl_size = hl_position.get("size", 0)
            
            if def_weth > 0.0001 and hl_size < -0.0001:
                print(f"\n{'='*60}")
                print(f"[RESUME] Detected existing position!")
                print(f"  DEF WETH: {def_weth:.6f}")
                print(f"  HL ETH-PERP: {hl_size:.4f} (short)")
                print(f"{'='*60}")
                
                # Get current prices to estimate entry prices (best effort)
                hl_price, def_price = await self.get_prices()
                
                self.in_position = True
                self.def_weth_amount = def_weth
                self.def_weth_amount_raw = def_weth_raw
                self.hl_size_eth = abs(hl_size)
                self.def_entry_price = def_price if def_price else 2000.0
                self.hl_entry_price = hl_price if hl_price else 2000.0
                self.entry_spread_bps = 0  # Unknown, just track from here
                
                notify_ui("event", {"type": "RESUME", "message": f"Resumed position: {def_weth:.4f} WETH, {abs(hl_size):.4f} ETH short"})
                notify_ui("position", {"in_position": True, "entry_spread_bps": 0, "status": "RESUMED"})
                print(f"[RESUME] Position loaded. Skipping to exit monitoring...")
            elif def_weth > 0.0001 and hl_size >= 0:
                print(f"\n[WARNING] DEF has WETH ({def_weth:.6f}) but no HL short - UNHEDGED!")
                notify_ui("event", {"type": "WARNING", "message": f"Unhedged: DEF has {def_weth:.4f} WETH but no HL short"})
            elif def_weth <= 0.0001 and hl_size < -0.0001:
                print(f"\n[WARNING] HL has short ({hl_size:.4f}) but no DEF WETH - UNHEDGED!")
                notify_ui("event", {"type": "WARNING", "message": f"Unhedged: HL has {abs(hl_size):.4f} ETH short but no DEF WETH"})
        
        # Phase 1: Wait for entry
        while not self.in_position:
            # Check if we should stop
            if should_stop():
                print("[ENGINE] Stop requested - halting entry monitoring")
                notify_ui("event", {"type": "WARNING", "message": "Engine stopped by user"})
                return False
            
            # Get synchronized prices (BUY quote for entry)
            hl_price, def_price = await self.get_prices()
            
            # Check for consecutive DEF failures (likely token expired) - PAUSE and STOP
            if self._consecutive_def_failures >= self._max_consecutive_failures:
                print(f"\n[PAUSE] {self._consecutive_def_failures} consecutive DEF quote failures - token likely expired")
                print(f"[PAUSE] ENGINE PAUSED. Manual restart required after fixing token.")
                notify_ui("event", {"type": "ERROR", "message": f"PAUSED: {self._consecutive_def_failures} consecutive DEF failures - check token"})
                set_pause_status(True, "DEF quote failures - token likely expired")
                notify_ui("status", {"status": "PAUSED", "mode": "PAUSED", "pause_reason": "DEF quote failures - token likely expired"})
                self._emit_service_health()
                return False  # Stop completely - no retry loop
            
            if hl_price and def_price:
                spread = self.calc_spread(hl_price, def_price)
                now = datetime.now().strftime("%H:%M:%S")
                
                notify_ui("spread", {"hl_price": hl_price, "def_price": def_price, "spread_bps": spread, "status": "WAITING_ENTRY"})
                
                # Log spread to DB (non-blocking)
                log_spread(hl_price, def_price, spread, "WAITING_ENTRY",
                           self._def_quote_latency_ms, self._hl_ws_price_age_ms, self._price_gap_ms)
                
                if spread <= self.ENTRY_THRESHOLD_BPS:
                    # Ensure valid token before executing
                    if not await self._ensure_valid_token():
                        print(f"[ERROR] Cannot execute entry - token invalid")
                        await asyncio.sleep(5)
                        continue
                    
                    print(f"\n{now} | HL: ${hl_price:.2f} | DEF: ${def_price:.2f} | Spread: {spread:+.1f}bp >>> ENTRY")
                    notify_ui("event", {"type": "ENTRY", "message": f"Entry signal at {spread:+.1f} bps"})
                    
                    if await self.execute_entry(hl_price, def_price, spread, cached_usdc_before, cached_weth_before):
                        # POST-ENTRY CONFIRMATION: Hold until positions verified
                        if not await self._confirm_entry_positions():
                            print(f"[PAUSE] Entry confirmation failed - position mismatch!")
                            notify_ui("event", {"type": "ERROR", "message": "PAUSED: Entry confirmation failed - position mismatch!"})
                            set_pause_status(True, "Entry confirmation failed - position mismatch")
                            notify_ui("status", {"status": "PAUSED", "mode": "PAUSED", "pause_reason": "Entry confirmation failed - position mismatch"})
                            return False  # PAUSE - don't continue
                        
                        cached_usdc_before = await self.get_def_balance()
                        cached_weth_before = await self.get_def_weth_balance()
                        print(f"[POST-ENTRY] Updated DEF USDC: ${cached_usdc_before:,.2f}, WETH: {cached_weth_before:.6f}")
                        break
                else:
                    print(f"{now} | HL: ${hl_price:.2f} | DEF: ${def_price:.2f} | Spread: {spread:+.1f}bp | waiting for <={self.ENTRY_THRESHOLD_BPS}bp")
            else:
                print(f"[WARN] Price fetch failed - HL: {hl_price}, DEF: {def_price}")
                notify_ui("event", {"type": "WARNING", "message": "Price fetch failed, retrying..."})
            
            await asyncio.sleep(self.POLL_INTERVAL_SEC)
        
        # Phase 2: Hold and wait for exit
        print(f"\n--- IN POSITION: Waiting for exit signal (>={self.EXIT_THRESHOLD_BPS}bp) ---\n")
        
        while self.in_position:
            # Check if we should stop (WARNING: position still open!)
            if should_stop():
                print("[ENGINE] Stop requested - WARNING: POSITION STILL OPEN!")
                print(f"[ENGINE] DEF WETH: {self.def_weth_amount:.6f}, HL ETH short: {self.hl_size_eth:.4f}")
                notify_ui("event", {"type": "ERROR", "message": f"Engine stopped with OPEN POSITION! WETH: {self.def_weth_amount:.4f}, HL short: {self.hl_size_eth:.4f}"})
                return False
            
            # Use SELL quote for exit spread detection (get valid quoteId for exit)
            hl_price, def_price = await self.get_exit_prices(self.def_weth_amount_raw)
            
            # Check for consecutive DEF failures (likely token expired) - CRITICAL during position hold
            if self._consecutive_def_failures >= self._max_consecutive_failures:
                print(f"\n[PAUSE] {self._consecutive_def_failures} consecutive DEF quote failures - token likely expired")
                print(f"[PAUSE] WARNING: POSITION STILL OPEN! WETH: {self.def_weth_amount:.4f}, HL short: {self.hl_size_eth:.4f}")
                print(f"[PAUSE] ENGINE PAUSED. Manual restart required after fixing token.")
                notify_ui("event", {"type": "ERROR", "message": f"PAUSED: {self._consecutive_def_failures} DEF failures - OPEN POSITION - check token"})
                set_pause_status(True, "DEF quote failures - token likely expired - POSITION OPEN")
                notify_ui("status", {"status": "PAUSED", "mode": "PAUSED", "pause_reason": "DEF quote failures - POSITION OPEN"})
                self._emit_service_health()
                return False  # Stop completely - no retry loop
            
            if hl_price and def_price:
                spread = self.calc_spread(hl_price, def_price)
                now = datetime.now().strftime("%H:%M:%S")
                
                # Calculate unrealized P&L
                def_unrealized = (def_price - self.def_entry_price) * self.def_weth_amount
                hl_unrealized = (self.hl_entry_price - hl_price) * self.hl_size_eth
                total_unrealized = def_unrealized + hl_unrealized
                
                notify_ui("position", {
                    "in_position": True,
                    "spread_bps": spread,
                    "unrealized_pnl": total_unrealized,
                    "entry_spread_bps": self.entry_spread_bps,
                    "status": "IN_POSITION"
                })
                notify_ui("spread", {"hl_price": hl_price, "def_price": def_price, "spread_bps": spread, "status": "IN_POSITION"})
                
                # Log spread to DB (non-blocking)
                log_spread(hl_price, def_price, spread, "IN_POSITION",
                           self._def_quote_latency_ms, self._hl_ws_price_age_ms, self._price_gap_ms)
                
                if spread >= self.EXIT_THRESHOLD_BPS:
                    # Ensure valid token before executing
                    if not await self._ensure_valid_token():
                        print(f"[ERROR] Cannot execute exit - token invalid")
                        await asyncio.sleep(5)
                        continue
                    
                    print(f"\n{now} | Spread: {spread:+.1f}bp | Unrealized: ${total_unrealized:+.4f} >>> EXIT")
                    notify_ui("event", {"type": "EXIT", "message": f"Exit signal at {spread:+.1f} bps, unrealized ${total_unrealized:+.4f}"})
                    
                    if await self.execute_exit(hl_price, def_price, spread, cached_usdc_before):
                        # POST-EXIT CONFIRMATION: Hold until flat positions verified
                        if not await self._confirm_exit_positions():
                            print(f"[PAUSE] Exit confirmation failed - residual position detected!")
                            notify_ui("event", {"type": "ERROR", "message": "PAUSED: Exit confirmation failed - residual position!"})
                            set_pause_status(True, "Exit confirmation failed - residual position")
                            notify_ui("status", {"status": "PAUSED", "mode": "PAUSED", "pause_reason": "Exit confirmation failed - residual position"})
                            return False  # PAUSE - don't continue
                        
                        return True
                else:
                    print(f"{now} | Spread: {spread:+.1f}bp | Unrealized: ${total_unrealized:+.4f} | waiting for >={self.EXIT_THRESHOLD_BPS}bp")
            
            await asyncio.sleep(self.POLL_INTERVAL_SEC)
        
        return True
    
    async def run(self, num_cycles: int = 1):
        """Run multiple arbitrage cycles."""
        if not await self.connect():
            return
        
        try:
            # Get starting balances
            def_start = await self.get_def_balance()
            hl_start = await self.get_hl_balance()
            total_start = def_start + hl_start
            
            print(f"\n{'='*60}")
            print(f"STARTING BALANCES")
            print(f"  Definitive (USDC): ${def_start:,.2f}")
            print(f"  Hyperliquid (USDC): ${hl_start:,.2f}")
            print(f"  TOTAL: ${total_start:,.2f}")
            print(f"{'='*60}")
            notify_ui("balances", {"def_usdc": def_start, "hl_usdc": hl_start})
            notify_ui("event", {"type": "INFO", "message": f"Engine started. Total balance: ${total_start:,.2f}"})
            
            # CRITICAL: Initial position balance check before ANY cycles
            print("\n[STARTUP] Running initial position balance check...")
            if not await self._check_position_balance():
                print("[HALT] Position imbalance detected at startup. ENGINE WILL NOT RUN.")
                notify_ui("event", {"type": "ERROR", "message": "STARTUP HALTED: Position imbalance! Manual intervention required."})
                return  # Exit run() entirely - don't start any cycles
            
            # Run cycles
            for i in range(num_cycles):
                cycle_completed = await self.run_cycle()
                
                # Check if stopped or cycle failed
                if should_stop() or not cycle_completed:
                    print("[ENGINE] Exiting run loop (stop requested or cycle incomplete)")
                    break
                
                if i < num_cycles - 1:
                    print(f"\n[Cycle {i+1} complete. Starting next cycle in 3s...]\n")
                    await asyncio.sleep(3)
            
            # Get ending balances
            def_end = await self.get_def_balance()
            hl_end = await self.get_hl_balance()
            total_end = def_end + hl_end
            
            print(f"\n{'='*60}")
            print(f"FINAL BALANCES")
            print(f"  Definitive (USDC): ${def_end:,.2f} ({def_end - def_start:+.2f})")
            print(f"  Hyperliquid (USDC): ${hl_end:,.2f} ({hl_end - hl_start:+.2f})")
            print(f"  TOTAL: ${total_end:,.2f}")
            print(f"  NET P&L: ${total_end - total_start:+.4f}")
            print(f"{'='*60}")
            
        finally:
            await self.close()


async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="ETH Arbitrage Engine")
    parser.add_argument("--size", type=float, default=100, help="Order size in USD")
    parser.add_argument("--cycles", type=int, default=1, help="Number of cycles to run")
    parser.add_argument("--entry", type=float, default=5.0, help="Entry threshold (bps)")
    parser.add_argument("--exit", type=float, default=15.0, help="Exit threshold (bps)")
    parser.add_argument("--turbo", action="store_true", help="Use TURBO mode (default)")
    parser.add_argument("--prime", action="store_true", help="Use PRIME mode (with quote)")
    parser.add_argument("--slip", type=float, default=5.0, help="Slippage tolerance in bps (TURBO only)")
    
    args = parser.parse_args()
    
    engine = ArbEngine(size_usd=args.size)
    engine.ENTRY_THRESHOLD_BPS = args.entry
    engine.EXIT_THRESHOLD_BPS = args.exit
    
    # Default to TURBO, use PRIME only if explicitly set
    engine.USE_TURBO = not args.prime
    engine.SLIPPAGE_TOLERANCE = f"{args.slip / 10000:.6f}"  # Convert bps to decimal
    
    await engine.run(num_cycles=args.cycles)


if __name__ == "__main__":
    asyncio.run(main())
