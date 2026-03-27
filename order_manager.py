"""
order_manager.py
Handles the full lifecycle of orders:
place → track → confirm fill → close.

Bug fixes:
- sync_live_position: syncs Kalshi API positions into memory (Bug 1)
  with recently_closed guard to prevent re-adding just-exited positions
- execute_exit: only removes position from memory if order succeeds (Bug 3)
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional, Dict

from config import Config
from kalshi_client import KalshiClient
from strategy import TradeDecision
from risk_manager import RiskManager
from logger import TradeLogger

logger = logging.getLogger(__name__)

RECENTLY_CLOSED_TTL = 60  # seconds to block re-sync after an exit


@dataclass
class OpenPosition:
    ticker: str
    side: str
    contracts: int
    entry_price_cents: int
    order_id: str
    entry_cost_dollars: float


class OrderManager:
    def __init__(self, config: Config, client: KalshiClient,
                 risk_manager: RiskManager, trade_logger: TradeLogger):
        self.config        = config
        self.client        = client
        self.risk_manager  = risk_manager
        self.trade_logger  = trade_logger
        self.positions: Dict[str, OpenPosition] = {}
        # Tickers we recently exited — block sync from re-adding for TTL seconds
        self._recently_closed: Dict[str, float] = {}  # ticker → exit timestamp

    # ---------------------------------------------------------------- Bug 1 fix

    def sync_live_position(self, ticker: str, position_fp: float):
        """
        Called every cycle with live data from Kalshi API.
        Adds positions that exist on Kalshi but not in memory.
        Removes positions that have been closed on Kalshi.

        IMPORTANT: Never re-adds a ticker we recently exited — this prevents
        the loop where a just-sold position is still visible in the API for
        a few seconds and gets re-added as a new position.
        """
        contracts = abs(int(position_fp))

        if position_fp == 0:
            # Position gone on Kalshi — clean up memory if needed
            if ticker in self.positions:
                logger.info(f"Sync: removing closed position {ticker} from memory")
                del self.positions[ticker]
            return

        # Check recently-closed guard
        closed_at = self._recently_closed.get(ticker)
        if closed_at and time.time() - closed_at < RECENTLY_CLOSED_TTL:
            remaining = RECENTLY_CLOSED_TTL - (time.time() - closed_at)
            logger.debug(f"Sync: skipping {ticker} — recently closed, {remaining:.0f}s remaining in guard")
            return

        # Only add if not already in memory
        if ticker not in self.positions and contracts > 0:
            side = "no" if position_fp < 0 else "yes"
            self.positions[ticker] = OpenPosition(
                ticker=ticker,
                side=side,
                contracts=contracts,
                entry_price_cents=0,
                order_id="synced",
                entry_cost_dollars=0,
            )
            self.risk_manager.on_position_opened(ticker)
            logger.info(f"Sync: added live position {ticker} "
                        f"({side} {contracts}x) from Kalshi API")

    def _mark_recently_closed(self, ticker: str):
        """Record that we just exited this ticker — blocks sync re-add for TTL."""
        self._recently_closed[ticker] = time.time()
        # Clean up old entries while we're here
        now = time.time()
        self._recently_closed = {
            t: ts for t, ts in self._recently_closed.items()
            if now - ts < RECENTLY_CLOSED_TTL
        }

    # ---------------------------------------------------------------- entries

    def execute_entry(self, decision: TradeDecision) -> bool:
        """Place an entry order. Returns True if order was accepted."""
        if decision.action != "buy":
            return False

        ticker = decision.ticker

        # Hard guard — never enter a ticker already in memory
        if ticker in self.positions:
            logger.warning(f"Duplicate entry blocked: {ticker} already in positions")
            return False

        contracts = max(1, int(
            decision.size_dollars / (decision.price_cents / 100)
        ))

        try:
            result = self.client.place_order(
                ticker=ticker,
                side=decision.side,
                count=contracts,
                price_cents=decision.price_cents,
            )

            order_id = result.get("order", {}).get("order_id") or result.get("order_id", "unknown")
            cost     = contracts * decision.price_cents / 100

            self.positions[ticker] = OpenPosition(
                ticker=ticker,
                side=decision.side,
                contracts=contracts,
                entry_price_cents=decision.price_cents,
                order_id=order_id,
                entry_cost_dollars=cost,
            )
            self.risk_manager.on_position_opened(ticker)
            self.trade_logger.log_entry(
                ticker=ticker, side=decision.side, contracts=contracts,
                price_cents=decision.price_cents, cost_dollars=cost,
                order_id=order_id, reason=decision.reason,
            )
            logger.info(f"ENTRY PLACED: {ticker} | {decision.side} | "
                        f"{contracts}x @ {decision.price_cents}¢ | cost=${cost:.2f}")
            return True

        except Exception as e:
            logger.error(f"Failed to place entry on {ticker}: {e}")
            return False

    # ---------------------------------------------------------------- exits

    def execute_exit(self, decision: TradeDecision) -> bool:
        """
        Place an exit order.
        Only removes position from memory if order succeeds.
        Marks ticker as recently closed to block sync re-add.
        """
        if decision.action != "sell":
            return False

        ticker   = decision.ticker
        position = self.positions.get(ticker)
        if not position:
            logger.warning(f"No open position found for {ticker} — skipping exit")
            return False

        try:
            result = self.client.place_order(
                ticker=ticker,
                side=position.side,
                count=position.contracts,
                price_cents=decision.price_cents,
            )

            proceeds = position.contracts * decision.price_cents / 100
            pnl      = proceeds - position.entry_cost_dollars

            self.risk_manager.on_position_closed(ticker, pnl)
            self.trade_logger.log_exit(
                ticker=ticker, side=position.side,
                contracts=position.contracts,
                exit_price_cents=decision.price_cents,
                proceeds_dollars=proceeds, pnl_dollars=pnl,
                reason=decision.reason,
            )
            del self.positions[ticker]

            # Mark as recently closed — prevents sync from re-adding it
            self._mark_recently_closed(ticker)

            logger.info(f"EXIT PLACED: {ticker} | {position.side} | "
                        f"{position.contracts}x @ {decision.price_cents}¢ | "
                        f"pnl=${pnl:+.2f}")
            return True

        except Exception as e:
            logger.error(f"Exit order failed for {ticker}: {e} — position kept open")
            return False

    # ---------------------------------------------------------------- accessors

    def get_open_tickers(self) -> set:
        return set(self.positions.keys())

    def get_position(self, ticker: str) -> Optional[OpenPosition]:
        return self.positions.get(ticker)
