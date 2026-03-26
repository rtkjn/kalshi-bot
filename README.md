# Kalshi Mean-Reversion Bot

Automated trading bot for Kalshi's 15-minute BTC/ETH prediction markets.
Strategy: buy when odds drop below 40%, sell when they recover to 48-50%.

## Setup

### 1. Create virtual environment
```bash
cd ~/Documents/Personal\ Projects/kalshi-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Add your Kalshi API keys
- Log into kalshi.com → Settings → API → Create API Key
- Download the `.pem` file → save as `kalshi_private_key.pem` in this folder
- Copy your Key ID into `.env` as `KALSHI_API_KEY_ID`

### 3. Run in dry-run mode first (no real trades)
```bash
source venv/bin/activate
python main.py
```

### 4. When ready to go live
- Set `DRY_RUN=false` in `.env`
- Run again: `python main.py`

### 5. Run continuously with auto-restart
```bash
cp com.kalshibot.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.kalshibot.plist
launchctl start com.kalshibot
```

Monitor logs:
```bash
tail -f bot.log
```

Stop the bot:
```bash
launchctl stop com.kalshibot
```

## Project Structure

| File | Purpose |
|---|---|
| `main.py` | Async orchestrator — main loop |
| `config.py` | Loads `.env` into typed Config object |
| `kalshi_client.py` | Kalshi REST API with RSA-PSS auth |
| `price_feed.py` | Binance WebSocket for BTC/ETH prices |
| `signal_engine.py` | Entry/exit signal detection |
| `strategy.py` | Trade decision logic |
| `risk_manager.py` | Kill switch, position limits, daily loss cap |
| `order_manager.py` | Order placement and position tracking |
| `logger.py` | SQLite + CSV trade logging |
| `.env` | Your API keys and strategy parameters |
| `com.kalshibot.plist` | macOS launchd auto-restart service |

## Risk Controls

- **Kill switch**: auto-triggers if daily loss exceeds `DAILY_LOSS_LIMIT_DOLLARS`
- **Max positions**: never more than `MAX_CONCURRENT_POSITIONS` open at once
- **Cooldown**: `COOLDOWN_AFTER_LOSS_SECONDS` pause per asset after any loss
- **Momentum filter**: skips entry if BTC/ETH moved more than `MOMENTUM_FILTER_PCT` recently
- **Entry window**: only enters in first `ENTRY_WINDOW_PCT` of each market's lifetime

## ⚠️ Disclaimer
This bot trades with real money when `DRY_RUN=false`.
Always paper trade first. Never risk more than you can afford to lose.
