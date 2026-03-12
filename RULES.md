# Operational Rules & AI Guidelines

**CRITICAL**: Read this entire document before making ANY changes.

---

## PART 1: LAUNCHING THE BOT

### Pre-Launch Checklist

1. **Verify tokens are fresh**
   ```bash
   # Check token expiration in auth/tokens.json
   cat auth/tokens.json | grep "access_exp"
   # Compare to current Unix timestamp
   date +%s
   ```

2. **Kill any existing processes**
   ```bash
   pkill -9 -f "python.*run_with_ui"
   pkill -9 -f "python.*arb_engine"
   ps aux | grep python  # Verify nothing running
   ```

3. **Update tokens if needed**
   - Update BOTH `.env` AND `auth/tokens.json`
   - The engine loads from `auth/tokens.json` primarily
   - `.env` is backup/initial load

4. **Launch command**
   ```bash
   cd /Users/neerajk/Desktop/btc-intelligence
   python run_with_ui.py --entry 4 --exit 16 --size 100
   ```
   
   **IMPORTANT**: Always use --size 100 (not $10). Smaller orders have inflated spreads due to fixed fees.

5. **Verify startup sequence**
   Look for these logs IN ORDER:
   ```
   [WEB UI] Starting at http://localhost:5000
   [ENGINE] Starting arbitrage engine...
   [HL] Wallet: 0x...
   [HL] LIVE TRADING ENABLED
   [WS] Connected to Hyperliquid WebSocket
   [WS] Subscribed to orderbook: ETH
   [OK] Connected to Definitive and Hyperliquid
   STARTING BALANCES
   CYCLE 1: Waiting for entry signal...
   [WARMUP] Waiting 10s...
   [WARMUP] Complete. Trading enabled.
   [DEBUG] Token expires in XXXXs  (MUST BE POSITIVE)
   [CYCLE START] Cached DEF USDC balance: $X,XXX.XX
   HH:MM:SS | HL: $XXXX.XX | DEF: $XXXX.XX | Spread: +XX.Xbp
   ```

6. **If any step fails, STOP and diagnose**

---

## PART 2: TOKEN MANAGEMENT

### When Tokens Expire

**Symptoms:**
- `[DEF] PRIME quote error: 401 - {"message":"invalid token"}`
- `[DEBUG] Token expires in -XXXXs` (negative = expired)
- `[CYCLE START] Cached DEF USDC balance: $0.00`

**Solution:**
1. User provides new tokens from Definitive web app
2. Update BOTH locations:
   - `.env`: Update `PRIVY_ACCESS_TOKEN` and `PRIVY_ID_TOKEN`
   - `auth/tokens.json`: Update `access_token`, `id_token`, `access_exp`, `id_exp`
3. Kill and restart the bot

### Token Fields

```json
{
  "access_token": "eyJ...",    // The "token" field from user
  "id_token": "eyJ...",        // The "identity_token" field from user
  "access_exp": 1773277507,    // Unix timestamp (decode from JWT exp)
  "id_exp": 1773309927         // Unix timestamp
}
```

---

## PART 3: COMMON ERRORS & FIXES

| Error | Cause | Fix |
|-------|-------|-----|
| `401 invalid token` | Token expired | Update both `.env` and `auth/tokens.json` |
| `'list' object has no attribute 'get'` | BBO parsing wrong | BBO is list: `bbo[0].get("px")` |
| `Price fetch failed - HL: X, DEF: None` | DEF API failed | Check token, check network |
| `WebSocket price stale` | WS not receiving | Check subscription, reconnect |
| `Order not filled` | Price moved | Normal, will retry next cycle |
| Process hangs after `Subscribed to orderbook` | Stdout buffering | Use `sys.stdout.reconfigure(line_buffering=True)` |

---

## PART 4: RULES FOR THE AI

### ABSOLUTE RULES (NEVER VIOLATE)

1. **DO NOT make changes without explicit user permission**
   - Ask first: "Do you want me to change X?"
   - Wait for "yes" before editing

2. **DO NOT change working code to "improve" it**
   - If it works, don't touch it
   - No refactoring without permission

3. **DO NOT implement features the user didn't ask for**
   - Stick to exactly what was requested
   - No "while I'm here, I'll also..."

4. **DO NOT revert changes without permission**
   - Even if you think previous code was better
   - Ask first

5. **ALWAYS read the file before editing**
   - Use the Read tool first
   - Understand current state

6. **ALWAYS show the exact change you're making**
   - Show old_string and new_string
   - Explain what changes and why

### DEBUGGING RULES

1. **Add logging BEFORE guessing**
   - Don't assume you know the problem
   - Add print statements to trace execution
   - Use `flush=True` for immediate output

2. **Test incrementally**
   - Make ONE change at a time
   - Run and verify before next change

3. **Check the obvious first**
   - Token expired?
   - Process still running?
   - Network issue?

4. **When price fetch fails:**
   - Check WebSocket connected (`_hl_ws_connected`)
   - Check price received (`_hl_ws_price > 0`)
   - Check DEF API response (token valid?)

### COMMUNICATION RULES

1. **Listen to the user**
   - They know their system better than you
   - If they say "this was working before", believe them

2. **Don't argue**
   - If user says stop, stop
   - If user says revert, revert
   - If user is frustrated, slow down

3. **Be concise**
   - Don't explain things the user already knows
   - Get to the point
   - Action > explanation

4. **Admit when you don't know**
   - "I'm not sure, let me investigate"
   - Better than guessing and breaking things

### BEFORE ANY CODE CHANGE

Ask yourself:
1. Did the user ask for this specific change?
2. Have I read the current code?
3. Do I understand what it currently does?
4. Is my change minimal and targeted?
5. Could this break something else?

If any answer is "no", STOP and clarify with user.

---

## PART 5: CRITICAL FILES - DO NOT BREAK

### arb_engine.py

This file contains ALL core logic:
- WebSocket handling (`_on_hl_ws_message`)
- Price fetching (`get_prices`, `get_exit_prices`)
- Order execution (`execute_entry`, `execute_exit`)
- Cycle management (`run_cycle`)
- Token handling (`_load_tokens_from_file`)

**Never change without full understanding.**

### Key Functions

| Function | Purpose | Critical Details |
|----------|---------|------------------|
| `_on_hl_ws_message` | Parse BBO | BBO is LIST: `bbo[0].get("px")` |
| `get_prices` | Entry prices | Uses `buyAmount` field |
| `get_exit_prices` | Exit prices | Uses SELL quote |
| `_start_hl_websocket` | Connect WS | Uses `subscribe_orderbook("ETH")` |
| `execute_entry` | Entry trades | Parallel DEF + HL |
| `execute_exit` | Exit trades | Parallel DEF + HL |

### auth/tokens.json

- Auto-loaded by engine every 30 seconds
- MUST contain valid tokens
- Update this when user provides new tokens

### .env

- Loaded once at startup
- Also update when tokens change
- Contains all secrets

---

## PART 6: TESTING CHANGES

### Before Deploying Any Change

1. **Syntax check**
   ```bash
   python -m py_compile arb_engine.py
   ```

2. **Test run**
   ```bash
   python run_with_ui.py --size 100 --entry 4 --exit 16
   ```
   Note: Always use $100 minimum - smaller sizes give inaccurate spreads.

3. **Watch for these success indicators:**
   - Token expires in positive seconds
   - DEF balance shows (not $0.00)
   - Spread values appear (not "Price fetch failed")
   - No repeated errors in log

4. **If errors, kill immediately:**
   ```bash
   pkill -9 -f python
   ```

---

## PART 7: RECOVERY PROCEDURES

### If Bot is Stuck with Open Position

1. **Check positions manually:**
   - Definitive: Check WETH balance in web app
   - Hyperliquid: Check ETH-PERP position in web app

2. **If hedged (both positions exist):**
   - Restart bot, it will detect and resume exit monitoring

3. **If unhedged (only one position):**
   - **MANUAL INTERVENTION REQUIRED**
   - Close the orphaned position manually
   - Do NOT let bot trade until flat

### If Code is Broken

1. **Check git status** (if committed):
   ```bash
   git diff
   git checkout -- <file>  # Revert specific file
   ```

2. **Restore from known working state**
   - Check conversation history for working code
   - User may have backup

3. **Minimal restore**
   - Focus on `_on_hl_ws_message` and `_start_hl_websocket`
   - These are most commonly broken

---

## PART 8: WHAT NOT TO DO (LESSONS LEARNED)

### Past Mistakes

1. **Changed BBO handler format without testing**
   - Assumed format, broke parsing
   - Always log raw messages first

2. **Changed subscription type without updating handler**
   - Subscribed to BBO but handler expected allMids
   - Keep subscription and handler in sync

3. **Removed working code while "improving"**
   - Broke price fetching
   - Don't touch working code

4. **Didn't flush stdout**
   - Logs didn't appear, couldn't debug
   - Always use `flush=True` for important prints

5. **Updated .env but not tokens.json**
   - Engine loads from tokens.json
   - Always update both

### Golden Rule

**If the user says "this was working before" - the bug is in YOUR recent changes, not in their system.**

---

## PART 9: QUICK REFERENCE

### Launch Command
```bash
python run_with_ui.py --entry 4 --exit 16 --size 100
```

### Kill Command
```bash
pkill -9 -f "python.*run_with_ui"
```

### Check Running
```bash
ps aux | grep python
```

### Token Locations
- `.env` → `PRIVY_ACCESS_TOKEN`, `PRIVY_ID_TOKEN`
- `auth/tokens.json` → `access_token`, `id_token`

### Success Log Pattern
```
[DEBUG] Token expires in XXXXs  (positive number)
[CYCLE START] Cached DEF USDC balance: $X,XXX.XX  (not $0.00)
HH:MM:SS | HL: $XXXX.XX | DEF: $XXXX.XX | Spread: +XX.Xbp
```

### Error Log Pattern
```
[DEF] PRIME quote error: 401  (token expired)
[WARN] Price fetch failed  (check WS + token)
Token expires in -XXXXs  (negative = expired)
```
