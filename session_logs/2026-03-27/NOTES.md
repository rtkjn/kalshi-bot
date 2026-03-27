# Session 2026-03-27

## Summary
- Bot ran overnight unattended (forgot to kill)
- Starting balance: $130.19 (end of previous session)
- Ending balance: $91.67
- Net P&L: -$38.52

## What went wrong
1. recently_closed TTL (60s) not long enough — Kalshi API shows settled
   positions for several minutes, sync kept re-adding phantom positions
2. Exit orders on expired markets returned 404 — Bug 3 "keep position open
   on failure" then retried infinitely on dead markets
3. No daily loss limit enforced — can_open_position() never called before entries
4. Root cause identified: limit orders sit on book unfilled, bot re-evaluates
   and places duplicate orders, all fill when price hits threshold

## Root cause fix planned
- Switch from limit orders to fill_or_kill via buy_max_cost field
- Spend $5 at immediate market price, no resting orders ever
- Eliminates the entire phantom/duplicate order problem

## Git tag
v1.1-bugfixes (current HEAD before market order implementation)
