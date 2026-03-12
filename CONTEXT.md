# BTC Intelligence - Project Context

**Created**: March 9, 2026  
**Purpose**: Manual trading assistant for BTC on Definitive, using Hyperliquid for signals  
**Original conversation**: Referenced from `/Users/neerajk/.cursor/projects/Users-neerajk-Desktop-trade/agent-transcripts/`

---

## 1. Project Goal

Build a **regime-aware recommendation engine** that:
1. Pulls real-time BTC data from Hyperliquid (prices, order book, funding)
2. Detects market regime (TRENDING UP / TRENDING DOWN / CHOPPY / NEWS SHOCK)
3. Generates clear buy/sell recommendations with size, entry, stop, target
4. Adjusts recommendations based on current position exposure
5. Alerts via sound, macOS notification, and Telegram
6. Tracks volume and P&L for reward farming optimization

**NOT automated trading** - recommendations are executed manually on Definitive.

---

## 2. Trading Economics

### Definitive Rewards Program
- Fee: 0.10% per volume
- Slippage: 0.05-0.07% on BTC
- Reward rate: ~0.358% in EDGE tokens per volume
- **Net margin**: ~0.258% per $1M volume (before trading P&L)

### Weekly Target
- Volume goal: Variable based on market conditions
- Capital: $100K on Definitive
- Max position: $30,000 one direction

### Break-even Calculation
At $25M weekly volume:
- Fees: -$25,000
- Slippage: -$15,000 (at 0.06%)
- Rewards: +$89,500
- **Buffer for trading losses**: $49,500

---

## 3. Macro Environment (March 2026)

**CRITICAL**: Active US-Iran conflict as of late February 2026.
- US-Israel strikes on Iran (Feb 28, 2026)
- Iran closed Strait of Hormuz
- BTC moved 5% in one hour on March 2
- $300M+ in liquidations
- Trump states conflict could last "up to 4 weeks"

**Implication**: Pure mean reversion is HIGH RISK. Using regime-aware approach instead.

---

## 4. Strategy Selection by Regime

### Regime Detection Criteria

| Regime | ATR Condition | Structure | Strategy |
|--------|---------------|-----------|----------|
| TRENDING UP | > 1.2x baseline | Higher highs confirmed | Buy pullbacks to VWAP |
| TRENDING DOWN | > 1.2x baseline | Lower lows confirmed | Sell rallies to VWAP |
| CHOPPY | < 1.2x baseline | No clear HH/HL pattern | Mean reversion ±1σ |
| NEWS SHOCK | > 2.0x baseline | Any | SIT OUT |

### Entry Timing
- **Not breakout trading** (requires speed)
- **Pullback entries** in trends (5-10 minute windows)
- **Zone entries** in chop (2-5 minute windows)

---

## 5. Signal Definitions

### Primary Signals

1. **VWAP Deviation**
   - VWAP = Volume-Weighted Average Price (4hr rolling or session)
   - Deviation measured in standard deviations (σ)
   - Buy zone: < -1σ from VWAP
   - Sell zone: > +1σ from VWAP
   - Neutral: between ±0.5σ

2. **ATR (Average True Range)**
   - 14-period on 5-minute candles
   - Baseline = 7-day average ATR
   - Elevated: > 1.2x baseline
   - Extreme: > 2.0x baseline

3. **Market Structure**
   - Higher High (HH): Price peak > previous peak
   - Higher Low (HL): Price trough > previous trough
   - Lower Low (LL): Price trough < previous trough
   - Lower High (LH): Price peak < previous peak
   - 3+ consecutive HH/HL = uptrend confirmed
   - 3+ consecutive LL/LH = downtrend confirmed

### Bias Modifiers (Not Primary Triggers)
- Funding rate extremes (>0.03% or <-0.03%)
- Open Interest divergence from price
- Order book imbalance (observation only)

---

## 6. Position-Aware Sizing

```
exposure_pct = abs(current_position) / max_position ($30K)

If exposure_pct >= 90%:
  → REDUCE_ONLY mode
  → No new adding trades

If exposure_pct >= 60%:
  → Half size on adding trades
  → Only on very strong signals (>7/10)

If exposure_pct >= 30%:
  → Normal sizing

If exposure_pct < 30%:
  → Can be slightly aggressive on strong signals
```

---

## 7. Guard Rails

### From Trade Repo (Reuse)
- **Time Regime**: Macro events (FOMC, CPI, NFP, etc.) pause trading
- **Danger Zones**: US market open hours (6 AM - 12 PM PST)

### New Guards
- **Daily Loss Limit**: If P&L < -$3,000, pause for day
- **Position Cap**: Max $30,000 exposure one direction
- **News Spike Pause**: If >2% move in 15 min, pause 30 min
- **Volatility Gate**: Reduce size when ATR > 2x baseline

---

## 8. UI Requirements

### Waiting State
- Current price vs VWAP
- Current regime
- Position exposure and P&L
- Volume progress vs target
- Next scheduled macro event

### Action State
- **CLEAR**: BUY/SELL in large text
- **SIZE**: Exact dollar amount
- **ENTRY**: Price range to execute
- **STOP**: Where to cut loss
- **TARGET**: Where to take profit
- **WINDOW**: How long signal is valid

### After Execution
- User presses Enter
- Inputs fill price
- System updates position tracker
- Sets stop/target alerts

---

## 9. Alert Priorities

1. **Sound** (immediate) - Different tones for buy/sell/stop
2. **macOS Notification** (visual) - Summary with action
3. **Telegram** (mobile) - Full details for remote awareness

---

## 10. Files Reused from Trade Repo

| Source | Destination | Modifications |
|--------|-------------|---------------|
| `bot/venues/hyperliquid.py` | `adapters/hyperliquid.py` | Remove order placement, keep data only |
| `bot/strategies/regime.py` | `signals/regime.py` | Adapt for BTC symbol |
| `bot/strategies/time_regime.py` | `guards/time_regime.py` | Use as-is |
| `bot/data/macro_events.json` | `data/macro_events.json` | Copy directly |

---

## 11. Configuration

All settings in `config.yaml`:
- Trading parameters (sizes, limits)
- Signal thresholds
- Alert settings
- API endpoints

Secrets in `.env` (not committed):
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- (No Hyperliquid API key needed for public data)

---

## 12. Testing Strategy

1. **Unit tests**: Each signal module independently
2. **Integration tests**: Engine with mock data
3. **Live data tests**: Connect to Hyperliquid, verify signals
4. **Manual validation**: Run locally, verify recommendations make sense

---

## 13. Key Decisions Made

1. **No breakout trading** - Too fast for manual execution
2. **Pullback entries** - 5-10 minute windows, manageable pace
3. **Regime-first approach** - Detect regime before choosing strategy
4. **Position-aware sizing** - Recalculate on every signal
5. **Conservative in volatility** - Sit out during news shocks
6. **Simple UI** - One clear action at a time
7. **No order placement API** - All execution is manual

---

## 14. Risk Awareness

### What Can Go Wrong
- Reward rate decay as more farmers join
- Extended trending market crushes mean reversion
- News gaps through stops
- Manual execution fatigue

### Mitigations
- Track weekly reward rate, shutdown if < 0.10% net
- Regime detection shifts strategy automatically
- Hard % stops, not just σ-based
- Daily loss limit prevents compounding bad days

---

## 15. Definitive API Integration (NEXT PRIORITY)

### Problem
Currently, the bot doesn't know your actual position on Definitive. You have to manually tell it when you trade. This means:
- No automatic entry price tracking
- No real-time P&L awareness
- Can't give "cut loss" vs "take profit" advice

### Solution
Definitive has a full REST API that provides everything we need.

### API Details

**Endpoint**: `GET https://ddp.definitive.fi/v2/portfolio/positions`

**Authentication**: HMAC signature with API key/secret
- Header: `x-definitive-api-key`
- Header: `x-definitive-timestamp`
- Header: `x-definitive-signature` (HMAC-SHA256 of prehash)

**Response fields per position**:
| Field | Description |
|-------|-------------|
| `balance` | Token quantity (e.g., 0.5 BTC) |
| `balanceExact` | Precise balance string |
| `notional` | USD value of position |
| `entryPrice` | Average entry price |
| `profitAndLoss` | Dollar P&L |
| `profitAndLossPercent` | Percentage P&L |
| `asset.ticker` | Token symbol (look for "WBTC" or "BTC") |

### What We Need to Build

1. **`adapters/definitive.py`** - Definitive API client
   - HMAC signature generation (same pattern as their docs)
   - `get_positions()` method
   - `get_btc_position()` helper that filters for BTC

2. **Auto-sync loop** - Poll every 10-30 seconds
   - Fetch positions from Definitive
   - Update `position/tracker.py` with real data
   - No more manual position entry needed

3. **P&L-aware recommendations**
   - If `profitAndLossPercent > 1.0` and in sell zone → "CLOSE_LONG - Take profit"
   - If `profitAndLossPercent < -0.5` → "CLOSE_LONG - Cut loss"
   - Show entry price and current P&L in UI

4. **Environment variables needed**
   ```
   DEFINITIVE_API_KEY=dpka_...
   DEFINITIVE_API_SECRET=dpks_...
   DEFINITIVE_PORTFOLIO_ID=... (optional, inferred from key)
   ```

### Implementation Notes

- API secret has `dpks_` prefix that must be stripped before signing
- Prehash format: `${method}:${path}?${queryParams}:${timestamp}:${sortedHeaders}${body}`
- Signature must be submitted within 2 minutes of timestamp
- Positions endpoint supports pagination but we likely only need first page

### Smart Recommendations After Integration

| Condition | Current P&L | Zone | Recommendation |
|-----------|-------------|------|----------------|
| LONG | > +1% | sell zone | **CLOSE_LONG** - Take profit |
| LONG | > +0.5% | neutral | Hold or scale out |
| LONG | < -0.5% | any | **CLOSE_LONG** - Cut loss |
| LONG | < -1% | any | **CLOSE_LONG** - Hard stop |
| FLAT | - | buy zone | **BUY** |
| FLAT | - | sell zone | **SELL** (if shorting enabled) |

### Benefits
- Eliminates manual position entry
- Real entry price = accurate stop/target placement
- P&L awareness = smarter exit recommendations
- True position size = correct exposure calculations

---

## 16. Future Considerations (Not for MVP)

- Railway deployment for always-on monitoring
- Historical signal backtesting
- Multi-asset support (ETH, SOL with separate instances)
