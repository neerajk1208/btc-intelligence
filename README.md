# BTC Intelligence

A semi-automated BTC trading assistant that analyzes market data from Hyperliquid and generates clear recommendations for manual execution on the Definitive platform.

## Overview

This bot is designed for the EDGE token reward program on Definitive, where:
- **Reward rate**: 0.358% on trading volume
- **Trading fee**: 0.10%
- **Net margin**: 0.258% per trade (before P&L)

Target: Generate $25M weekly volume with capital preservation.

## Features

- **Real-time BTC analysis** via Hyperliquid WebSocket
- **Regime detection**: Trending up/down, Choppy, News shock
- **VWAP signals** with deviation bands
- **Volatility analysis**: ATR, Bollinger Bands
- **Guard rails**:
  - Macro event pauses (FOMC, CPI, NFP)
  - Position limits
  - Daily loss limits
  - Spike detection
- **Multi-channel alerts**: Sound, macOS notifications, Telegram
- **Simple terminal UI** with clear action instructions

## Installation

```bash
# Clone the repository
git clone https://github.com/youruser/btc-intelligence.git
cd btc-intelligence

# Install dependencies
pip install -r requirements.txt

# Copy environment template
cp .env.example .env
# Edit .env with your Telegram credentials (optional)
```

## Configuration

### Environment Variables (.env)

```bash
# Telegram alerts (optional)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### Configuration File (config.yaml)

Edit `config.yaml` to customize:
- Trading parameters (size, limits)
- Signal thresholds
- Alert preferences
- Guard rail settings

## Usage

### Run the Bot

```bash
python main.py

# With debug output
python main.py --debug
```

### Run Tests

```bash
# Test data adapter
python tests/test_adapter.py

# Test signals
python tests/test_signals.py

# Full integration test
python tests/test_integration.py
```

## Architecture

```
btc-intelligence/
├── adapters/           # Data sources (Hyperliquid)
├── signals/            # Market analysis
│   ├── regime.py       # Trend/choppy/news detection
│   ├── vwap.py         # VWAP with deviation bands
│   └── volatility.py   # ATR, Bollinger
├── guards/             # Safety mechanisms
│   ├── time_regime.py  # Macro event pauses
│   ├── position_guard.py
│   ├── loss_guard.py
│   └── spike_guard.py
├── position/           # Position tracking
├── engine/             # Recommendation generator
├── alerts/             # Notification system
├── ui/                 # Terminal dashboard
├── data/               # Persistent state
└── tests/              # Test suite
```

## Strategy

### Regime-Aware Trading

1. **Choppy/Range** → Mean Reversion
   - Buy at VWAP -1σ
   - Sell at VWAP +1σ
   - Quick in/out for volume

2. **Trending Up** → Buy Pullbacks
   - Wait for pullback to VWAP
   - Enter long, ride the trend

3. **Trending Down** → Sell Rallies
   - Wait for rally to VWAP
   - Enter short, ride the trend

4. **News Shock** → Sit Out
   - Wait for volatility to settle

### Position Sizing

| Exposure | Size Multiplier |
|----------|-----------------|
| 0-30%    | 100% (full)     |
| 30-60%   | 50%             |
| 60-90%   | 25%             |
| 90%+     | 0% (reduce only)|

## Manual Workflow

1. **Start the bot** - Dashboard shows current state
2. **Wait for signal** - Bot alerts when entry zone reached
3. **Execute on Definitive** - Follow the recommendation
4. **Update position** - Press 'P' to enter position details
5. **Monitor P&L** - Dashboard shows unrealized P&L
6. **Exit signal** - Bot recommends when to close

## Guard Rails

- **Macro Events**: Pauses 30min before/after FOMC, CPI, NFP
- **Position Limits**: Max $30,000 position
- **Daily Loss**: Stops trading at $3,000 daily loss
- **Spike Detection**: 2%+ move in 15min triggers pause

## Alerts

- **Sound**: macOS system sounds
- **Notification**: Native macOS notification center
- **Telegram**: Push notifications to mobile

## Files

- `btc-intelligence.log` - Application logs
- `data/position.json` - Position state
- `data/daily_pnl.json` - Daily P&L tracking
- `data/macro_events.json` - Scheduled events

## License

Private - Not for redistribution

## Support

For issues or questions, contact the maintainer.
