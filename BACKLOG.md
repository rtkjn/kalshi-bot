# Kalshi Bot — Backlog

Items to improve once the core strategy is proven profitable over 500+ trades.

---

## 🔢 Sizing Improvements

### Dynamic position sizing based on odds dislocation
**Current behavior:** Always bets flat $5 regardless of entry odds.
**Proposed improvement:** Bet more when odds are more dislocated — e.g. a 19¢ entry
has much higher expected value than a 39¢ entry, so it deserves a larger bet.

Example sizing scale:
| Entry odds | Bet size |
|---|---|
| < 20¢ | $15 |
| 20-29¢ | $10 |
| 30-39¢ | $5 |

**Why wait:** Need to confirm the mean-reversion edge is real before scaling up.
500+ dry-run trades with positive EV is the trigger to revisit this.

---

## 🏗️ Infrastructure

### Move to cloud VM (DigitalOcean / Hetzner)
**Current:** Running on local Mac — stops when machine sleeps or internet drops.
**Proposed:** $4-6/mo Hetzner VPS with systemd auto-restart, runs 24/7.
**Trigger:** Once strategy is proven and we're ready to scale.

### Telegram / Discord alerts
Real-time push notifications on phone for:
- Every entry and exit with P&L
- Kill switch trigger
- Bot crash / reconnect events

---

## 📊 Strategy

### WebSocket-based exit monitoring
**Current:** Exit checks run every 5 seconds (poll-based).
**Proposed:** Subscribe to Kalshi WebSocket orderbook deltas for real-time
odds updates — catch the 48¢ exit window the moment it appears, not up to 5s late.

### Backtest on historical odds data
Pull Kalshi historical trade data and replay the strategy to measure:
- True win rate
- Average profit per trade
- Max drawdown
- Sharpe ratio

---

## 🐛 Known Issues / Tech Debt

- `EXIT_ODDS_HIGH` config variable is now unused after the exit logic fix — can be
  removed from `.env` and `config.py` in a cleanup pass.
- `trades.csv` and `kalshi_bot.db` both grow unbounded — add a log rotation or
  archiving strategy before running for weeks continuously.
