# ETH Arbitrage Bot - Complete Documentation

**Last Updated**: March 11, 2026  
**Status**: Production (Live Trading Enabled)

---

## 1. Executive Summary

### What It Is
A **delta-neutral arbitrage bot** that captures spread differences between:
- **Definitive** (spot WETH on Base chain)
- **Hyperliquid** (ETH-PERP perpetual futures)

### The Pitch
When ETH prices diverge between spot and perp markets, we capture the spread by going long on one and short on the other simultaneously. Since we're hedged, we profit from the spread converging—regardless of which direction ETH moves.

### Key Numbers
| Metric | Value |
|--------|-------|
| Entry Threshold | ≤ 4 bps spread |
| Exit Threshold | ≥ 16 bps spread |
| Estimated Round-Trip Fees | ~13 bps |
| Minimum Profitable Spread | ~13 bps captured |
| Order Size | **$100 USDC** (minimum recommended) |

**IMPORTANT**: Always use $100+ order size. Smaller orders ($10) have inflated spreads due to fixed fees being a larger percentage of the trade.

---

## 2. Strategy Explanation

### Delta-Neutral Arbitrage

**Entry (when spread is LOW/negative):**
1. BUY spot WETH on Definitive (long exposure)
2. SHORT ETH-PERP on Hyperliquid (short exposure)
3. Net exposure = 0 (hedged)

**Exit (when spread is HIGH/positive):**
1. SELL spot WETH on Definitive
2. CLOSE short on Hyperliquid
3. Capture the spread difference minus fees

### Spread Calculation
```
spread_bps = ((DEF_price - HL_price) / HL_price) * 10000
```

- **Negative spread**: DEF is cheaper than HL → good entry
- **Positive spread**: DEF is more expensive than HL → good exit

### Profit Formula
```
Profit = (Exit_spread - Entry_spread) - Round_trip_fees - Slippage
```

Example:
- Enter at -5 bps spread
- Exit at +15 bps spread
- Spread captured: 20 bps
- Fees: ~13 bps
- Net profit: ~7 bps on position size

---

## 3. Technical Architecture

### Core Components

```
btc-intelligence/
├── arb_engine.py          # Main arbitrage engine (ALL core logic)
├── run_with_ui.py         # Launcher with Web UI integration
├── adapters/
│   ├── hl_trader.py       # Hyperliquid order placement
│   ├── websocket.py       # Hyperliquid WebSocket client
│   └── hyperliquid.py     # Legacy HL adapter
├── web/
│   ├── app.py             # Flask web server
│   └── templates/         # UI templates
├── auth/
│   ├── tokens.json        # Privy token storage (AUTO-LOADED)
│   └── privy_session.py   # Token refresh (Playwright, not working)
└── .env                   # Environment variables
```

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                       arb_engine.py                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. WARMUP (10s)                                           │
│     └── Wait, validate connections                          │
│                                                             │
│  2. ENTRY MONITORING                                        │
│     ├── Fetch DEF price (PRIME quote API)                  │
│     ├── Fetch HL price (WebSocket BBO)                     │
│     ├── Calculate spread                                    │
│     └── If spread ≤ threshold → EXECUTE ENTRY              │
│                                                             │
│  3. ENTRY EXECUTION (parallel)                             │
│     ├── DEF: POST /v1/orders (TURBO + quoteId)            │
│     └── HL: IOC limit order via SDK                        │
│                                                             │
│  4. EXIT MONITORING                                         │
│     ├── Fetch DEF SELL price (PRIME quote)                 │
│     ├── Fetch HL price (WebSocket)                         │
│     └── If spread ≥ threshold → EXECUTE EXIT               │
│                                                             │
│  5. EXIT EXECUTION (parallel)                              │
│     ├── DEF: Sell WETH (TURBO + SELL quoteId)             │
│     └── HL: Close short (buy back)                         │
│                                                             │
│  6. LOG & REPEAT                                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Price Synchronization Strategy

To ensure accurate spread measurement:
1. **Hyperliquid**: WebSocket BBO (Best Bid/Offer) subscription
   - Real-time updates, ~0ms latency
   - Mid price = (bid + ask) / 2
2. **Definitive**: REST API quote
   - Get PRIME quote → returns `quoteId` and `buyAmount`
   - Price = order_size / buyAmount
3. **Sync**: When DEF quote returns, grab latest HL WebSocket price
   - Actual price gap: typically <200ms

---

## 4. API Integration Details

### Definitive API

**Authentication:**
- `privy-token` header (access token, expires ~1 hour)
- `privy-id-token` cookie (identity token, expires ~10 hours)
- `organization-id`, `portfolio-id`, `read-token` headers

**Endpoints:**

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/v1/orders/quote` | POST | Get price quote (PRIME mode) |
| `/v1/orders` | POST | Execute order (TURBO mode) |
| `/v1/position` | GET | Get portfolio positions/balances |

**Order Types:**
- **PRIME**: Get quote first, then execute with `quoteId` (price locked)
- **TURBO**: Direct execution with `quoteId` from prior PRIME quote (fastest)

**Quote Response Fields:**
```json
{
  "quoteId": "abc123...",
  "buyAmount": "0.004879",  // Amount of WETH you'll receive
  "price": "0.0004879...",  // WETH per USDC (invert for USDC/WETH)
  "estimatedPriceImpact": "0.0001"
}
```

### Hyperliquid API

**REST API:**
- `POST https://api.hyperliquid.xyz/info` with `{"type": "allMids"}` → mid prices
- `POST https://api.hyperliquid.xyz/info` with `{"type": "clearinghouseState", "user": "0x..."}` → positions

**WebSocket:**
- `wss://api.hyperliquid.xyz/ws`
- Subscribe to BBO: `{"method": "subscribe", "subscription": {"type": "bbo", "coin": "ETH"}}`
- BBO message format:
```json
{
  "channel": "bbo",
  "data": {
    "coin": "ETH",
    "bbo": [
      {"px": "2050.50", "sz": "10.5", "n": 5},  // bid
      {"px": "2050.60", "sz": "8.2", "n": 3}   // ask
    ]
  }
}
```

**Order Placement:**
- Uses `hyperliquid-python-sdk`
- IOC (Immediate or Cancel) limit orders for taker execution
- Requires `HL_API_SECRET` (private key)

---

## 5. Token Management

### Token Files

**`.env`** - Primary token storage (loaded at startup)
```
PRIVY_ACCESS_TOKEN=eyJ...  # Expires ~1 hour
PRIVY_ID_TOKEN=eyJ...       # Expires ~10 hours
```

**`auth/tokens.json`** - Auto-reload storage (checked periodically)
```json
{
  "access_token": "eyJ...",
  "id_token": "eyJ...",
  "access_exp": 1773277507,
  "id_exp": 1773309927
}
```

### Token Refresh Flow
1. Engine checks `auth/tokens.json` every 30 seconds
2. If tokens differ from current, reload into memory
3. If token expires within 2 minutes, log warning
4. If token expired, DEF API returns 401

**IMPORTANT**: Currently, token refresh is MANUAL. Playwright automation is not working due to passkey/WebAuthn issues.

---

## 6. Environment Variables

```bash
# Privy Authentication (REQUIRED)
PRIVY_ACCESS_TOKEN=eyJ...
PRIVY_ID_TOKEN=eyJ...

# Definitive IDs (REQUIRED)
DEFINITIVE_ORG_ID=cdce88c4-...
DEFINITIVE_PORTFOLIO_ID=2fac9be7-...
DEFINITIVE_READ_TOKEN=c8725aac-...

# Definitive QuickTrade API (optional, for faster endpoint)
DEFINITIVE_API_KEY=dpka_...
DEFINITIVE_API_SECRET=dpks_...

# Hyperliquid (REQUIRED)
HL_API_SECRET=0x...           # Private key for signing orders
HL_MAIN_WALLET=0xA449...      # Main wallet address for balance queries

# Trading Mode
ENABLE_LIVE_TRADING=true      # Set to "true" for real orders
```

---

## 7. Running the Bot

### Command Line Options

```bash
python run_with_ui.py [options]

Options:
  --size FLOAT      Order size in USD (default: 100)
  --entry FLOAT     Entry threshold in bps (default: 5.0)
  --exit FLOAT      Exit threshold in bps (default: 15.0)
  --cycles INT      Number of cycles to run (default: 999)
  --prime           Use PRIME mode instead of TURBO
  --slip FLOAT      Slippage tolerance in bps (default: 5.0)
```

### Example Commands

```bash
# Test run with $10, entry ≤4 bps, exit ≥16 bps
python run_with_ui.py --size 10 --entry 4 --exit 16

# Production run with $100
python run_with_ui.py --size 100 --entry 4 --exit 16 --cycles 999
```

### Web UI

Access at: `http://localhost:5000`

Displays:
- Current spread (HL price, DEF price, spread bps)
- Position status (in position, entry time, unrealized P&L)
- Thresholds (entry/exit)
- Cycle history
- Event log
- Token expiration countdown

---

## 8. Fee Structure

| Platform | Fee Type | Rate |
|----------|----------|------|
| Definitive | Trading fee | ~2 bps |
| Hyperliquid | Taker fee | ~4.5 bps |
| **Round-trip total** | | **~13 bps** |

**Break-even**: Must capture at least 13 bps of spread to profit.

---

## 9. Position Detection

On startup, the engine checks for existing positions:
- DEF: Queries `/v1/position` for WETH balance
- HL: Queries `clearinghouseState` for ETH-PERP position

If both exist (WETH long + ETH short), the bot resumes in "in position" state and monitors for exit.

---

## 10. Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| `401 invalid token` | Privy token expired | Update tokens in `.env` AND `auth/tokens.json` |
| `'list' object has no attribute 'get'` | BBO parsing error | Fixed - BBO is list not dict |
| `Price fetch failed` | WebSocket not connected | Check WS connection, wait for BBO |
| `HL order not filled` | Price moved, IOC cancelled | Retry on next cycle |

---

## 11. Current Limitations

1. **Token Refresh**: Must be done manually (Playwright broken)
2. **Single Asset**: Only ETH/WETH supported
3. **Single Direction**: Only long DEF + short HL (not reverse)
4. **No Stop Loss**: If one leg fails, manual intervention needed

---

## 12. File Locations Summary

| File | Purpose |
|------|---------|
| `arb_engine.py` | Core engine - ALL trading logic |
| `run_with_ui.py` | Launcher with Flask UI |
| `adapters/hl_trader.py` | Hyperliquid order execution |
| `adapters/websocket.py` | HL WebSocket client |
| `web/app.py` | Flask server + state management |
| `.env` | Secrets and config |
| `auth/tokens.json` | Auto-reloaded Privy tokens |

---

## 13. Debugging

### Check Token Validity
```python
import base64, json
token = "eyJ..."
payload = token.split(".")[1] + "=="
data = json.loads(base64.urlsafe_b64decode(payload))
print(f"Expires: {data['exp']}")  # Unix timestamp
```

### Test WebSocket
```bash
python test_sol_spread.py  # Tests BBO + DEF quote
```

### Check Logs
All output goes to stdout. Look for:
- `[WS]` - WebSocket messages
- `[DEF]` - Definitive API
- `[HL]` - Hyperliquid
- `[TOKEN]` - Token status
- `[WARMUP]` - Startup validation

---

## 14. Market Conditions

### When This Works
- Spread volatility (spread swings between -10 and +20 bps)
- Normal market conditions
- Sufficient liquidity on both platforms

### When This Fails
- Consistently positive spread (never enters)
- Extreme volatility (fills at bad prices)
- Low spread movement (enters but never exits)

### Current Observation (March 2026)
- ETH spread typically +5 to +25 bps
- Entry at ≤4 bps rarely triggers
- SOL spread +100+ bps (not viable)

---

## 15. Version History

| Date | Change |
|------|--------|
| Mar 9, 2026 | Initial project (BTC Intelligence) |
| Mar 10, 2026 | Pivoted to ETH arbitrage |
| Mar 11, 2026 | Added WebSocket BBO, TURBO mode, UI |
| Mar 11, 2026 | Fixed BBO parsing (list vs dict) |
| Mar 11, 2026 | Added stdout line buffering |
