# ETH Arbitrage Bot - Inefficiencies & Optimization Opportunities

This document outlines current inefficiencies in the arbitrage system and potential improvements to maximize profit.

---

## 1. Static Thresholds

**Current State:**
- Entry/exit thresholds are fixed (e.g., entry ≤4 bps, exit ≥16 bps)
- Same thresholds used 24/7 regardless of market conditions

**Problem:**
- Spread distributions vary throughout the day
- A 4 bps entry might be easy at 3am UTC but impossible during US market hours
- **Opportunity cost**: Sitting idle when spreads are consistently at 8 bps because you're waiting for 4 bps

**Potential Fix:**
- Dynamic thresholds based on rolling average spread
- Time-of-day adjusted targets
- Percentile-based entry (e.g., enter when spread is in bottom 20% of last 1 hour)

---

## 2. Latency Gap (~50-100ms)

**Current State:**
- Definitive quote takes ~50-80ms to return
- HL WebSocket provides real-time prices
- By the time DEF price returns, HL price used for comparison is already stale

**Problem:**
- Spread calculation compares prices from different moments in time
- During volatile moments, this gap is significant
- **Impact**: You might enter thinking spread is 4 bps when it's actually 6 bps

**Potential Fix:**
- Timestamp both prices and calculate "price age gap"
- Reject trades where gap exceeds threshold (e.g., 50ms)
- Request DEF quote and capture HL price at response time (not request time)

---

## 3. Fixed Order Size ($100)

**Current State:**
- Same $100 size regardless of spread opportunity
- No position sizing based on edge quality

**Problem:**
- A 2 bps spread opportunity should warrant smaller size (less edge)
- A -5 bps spread opportunity (rare) should warrant larger size (more edge)
- **Kelly criterion** not applied - not sizing based on edge vs. variance

**Potential Fix:**
- Scale size with spread magnitude: `size = base_size * (edge_bps / target_edge_bps)`
- Cap maximum size based on liquidity
- Minimum edge threshold before any trade

---

## 4. No Time-of-Day Awareness

**Current State:**
- Bot treats all hours identically
- No awareness of market sessions or typical spread patterns

**Problem:**
- Spreads behave differently during:
  - **Asia session**: Typically tighter spreads
  - **US market open**: Volatile, wider spreads in both directions
  - **Weekend**: Lower liquidity, unpredictable
- Trading during unfavorable hours wastes time and may result in worse fills

**Potential Fix:**
- Log spread data with timestamps
- Analyze historical patterns by hour/day
- Adjust thresholds or pause during historically unfavorable periods

---

## 5. Single Direction Only

**Current State:**
- Only executes: Buy DEF WETH + Short HL ETH (betting spread will widen)
- Never executes: Sell DEF WETH + Long HL ETH (betting spread will narrow)

**Problem:**
- If spread is consistently +15 bps, you can't profit from it reverting to +5 bps
- **~50% of opportunities ignored**

**Potential Fix:**
- Implement reverse direction trades
- When spread is abnormally HIGH (e.g., +20 bps), sell DEF + long HL
- Exit when spread normalizes (e.g., +10 bps)
- Requires holding WETH inventory on DEF to sell

---

## 6. No Spread Mean-Reversion Analysis

**Current State:**
- Fixed entry/exit thresholds with no statistical basis
- No tracking of "normal" spread distribution

**Problem:**
- If average spread is +10 bps with std dev of 5 bps:
  - Entry at +4 bps is 1.2 std devs below mean (statistically favorable)
  - Exit at +16 bps is 1.2 std devs above mean
- Without this analysis, thresholds are arbitrary

**Potential Fix:**
- Track rolling mean and standard deviation of spread
- Entry threshold = mean - (N * std_dev)
- Exit threshold = mean + (M * std_dev)
- Dynamically adjust as distribution shifts

---

## 7. Quote Expiry Risk

**Current State:**
- PRIME quote from Definitive has a validity window (seconds)
- No tracking of quote age vs. execution timing

**Problem:**
- If HL execution is slow, the DEF quote might expire before use
- Could result in quote rejection or execution at different (worse) price

**Potential Fix:**
- Track quote timestamp and TTL
- Reject/re-quote if approaching expiry
- Execute DEF leg first (since it has the quote constraint)

---

## 8. No Partial Fill Handling

**Current State:**
- Assumes all-or-nothing fills on both legs
- No logic for handling partial executions

**Problem:**
- If HL fills 80% and DEF fills 100%, you're unhedged on 20%
- Creates directional exposure and potential loss

**Potential Fix:**
- Check fill quantities after execution
- If mismatch, immediately hedge the difference
- Or use IOC (Immediate or Cancel) orders to ensure full fill or no fill

---

## 9. Fee Structure Not Optimized

**Current State:**
- Using market orders (taker) on Hyperliquid
- Paying full taker fees on every trade

**Problem:**
- Hyperliquid has maker vs. taker fee structure
- Maker fees are cheaper, sometimes negative (rebate)
- **Missing ~2-3 bps per round trip**

**Potential Fix:**
- Use limit orders on HL placed at current bid/ask
- Wait for fill (adds latency but saves fees)
- Hybrid: use limit for non-urgent exits, market for urgent entries

---

## 10. No Volatility Filter

**Current State:**
- No awareness of current market volatility
- Same behavior during calm and chaotic markets

**Problem:**
- During high volatility, spreads swing wildly
- Entry at calculated +4 bps might execute at +8 bps
- Slippage expectations not vol-adjusted

**Potential Fix:**
- Track recent price velocity/volatility
- Widen slippage tolerance during high vol
- Or pause trading entirely when vol exceeds threshold

---

## 11. Single Asset (ETH Only)

**Current State:**
- Only trading ETH-PERP vs WETH
- SOL spread tests showed +100 bps (massive, different opportunity)

**Problem:**
- Missing opportunities on other assets
- Different assets may have different optimal strategies

**Potential Fix:**
- Multi-asset scanner running in parallel
- Asset-specific parameters (thresholds, sizes, fees)
- Prioritize assets with best risk-adjusted opportunities

---

## 12. No Historical Performance Tracking

**Current State:**
- Cycle data not persisted
- No long-term analytics

**Problem:**
- Can't analyze patterns:
  - Best time of day for entries
  - Average slippage by spread level
  - Win rate by entry spread
  - Fee leakage over time

**Potential Fix:**
- Log every cycle to database/CSV:
  - Timestamps, prices, spreads, fills, P&L, latencies
- Build dashboards for analysis
- Use data to tune parameters

---

## 13. Token Refresh Downtime

**Current State:**
- Privy token expires every ~1 hour
- Manual refresh required (WebAuthn blocks automation)
- Bot pauses during refresh

**Problem:**
- Missing opportunities during refresh window
- If token expires mid-cycle, could cause issues

**Potential Fix:**
- Proactive refresh 5-10 minutes before expiry
- Hot-reload implemented but requires manual token update in Railway
- Investigate Privy API for programmatic refresh

---

## Summary - Biggest Profit Levers

| Issue | Potential Impact | Difficulty |
|-------|------------------|------------|
| **Single direction only** | Missing ~50% of opportunities | Medium |
| **Static thresholds** | Missing opportunities when market regime shifts | Low |
| **No time-of-day optimization** | Trading during unfavorable hours | Low |
| **Fixed sizing** | Not maximizing edge when it's large | Low |
| **Latency gap** | Entry/exit at worse prices than calculated | Medium |
| **No maker orders on HL** | Paying ~2-3 bps more per round trip | Medium |
| **No mean-reversion analysis** | Arbitrary thresholds, suboptimal entries | Medium |
| **Single asset** | Missing opportunities on other pairs | High |

---

## Recommended Priority

1. **Low-hanging fruit (implement first):**
   - Historical spread logging (data collection)
   - Time-of-day spread analysis
   - Dynamic threshold adjustment

2. **Medium effort, high impact:**
   - Reverse direction trades (requires WETH inventory)
   - HL maker orders for exits
   - Volatility filter

3. **Higher effort:**
   - Multi-asset support
   - Kelly criterion position sizing
   - Full backtesting infrastructure

---

*Document created: March 2026*
*Last updated: March 2026*
