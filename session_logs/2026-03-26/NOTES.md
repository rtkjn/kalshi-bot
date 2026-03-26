# Session 2026-03-26
## Summary
- First live trading session
- Starting balance: $79.55
- Ending balance: $121.93
- Net P&L: +$42.38 (+53%)
- Win rate: 8/9 (89%)
- Windows traded: 6 (18:45, 19:00, 19:15, 19:30 + earlier dry run)

## Known bugs at this version
- Duplicate entry bug (caused inflated BTC position — got lucky, worked in our favor)
- P&L log underreports (shows +$12.49 vs actual +$42.38 due to partial fills not tracked)
- Fees not tracked in CSV
- daily_pnl resets to zero on bot restart

## How to revert to this version
git checkout v1.0-working
