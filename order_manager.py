"""
order_manager.py
Handles the full lifecycle of orders:
place → track → confirm fill → close.

Bug fixes:
- sync_live_position: syncs Kalshi API positions into memory (Bug 1)
- execute_exit: only removes position from memory if order succeeds (Bug 3)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from config import Config
from kalshi_client import KalshiClient
from strategy import TradeDecision
from risk_manager import RiskManager
from logger import TradeLogger

logger = logging.getLogger(__name__)


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
        self.config       = config
        self.client       = client
        self.risk_manager = risk_manager
        self.trade_logger = trade_logger
        self.positions: dict[str, OpenPosition] = {}

    # ---------------------------------------------------------------- Bug 1 fix

    def sync_live_position(self, ticker: str, position_fp: float):
        """
        Called every cycle with live data from Kalshi API.
        If Kalshi shows a position we don't have in memory, add it.
        If Kalshi shows zero position, remove it from memory.
        This prevents the in-memory state from drifting from reality.
        """
        contracts = abs(int(position_fp))
        if position_fp == 0:
            if ticker in self.positions:
                logger.info(f"Sync: removing closed position {ticker} from memory")
                del self.positions[ticker]
            return

        if ticker not in self.positions and contracts > 0:
            # Position exists on Kalshi but not in memory — likely from a previous
            # bot session or a partially-confirmed order. Add it.
            side = "no" if position_fp < 0 else "yes"
            self.positions[ticker] = OpenPosition(
                ticker=ticker,
                side=side,
                contracts=contracts,
                entry_price_cents=0,   # unknown — synced from live
                order_id="synced",
                entry_cost_dollars=0,  # unknown
            )
            self.risk_manager.on_position_opened(ticker)
            logger.info(f"Sync: added live position {ticker} "
                        f"({side} {contracts}x) from Kalshi API")


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
        Bug 3 fix: only removes position from memory if the order succeeds.
        If the order fails, the position stays open and will be re-evaluated
        on the next cycle.
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

            # Only mark closed if the order was accepted
            order_id = result.get("order", {}).get("order_id") or result.get("order_id", "")
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

            logger.info(f"EXIT PLACED: {ticker} | {position.side} | "
                        f"{position.contracts}x @ {decision.price_cents}¢ | "
                        f"pnl=${pnl:+.2f}")
            return True

        except Exception as e:
            # Bug 3: DO NOT remove position from memory on failure
            # It stays open and the next cycle will try again
            logger.error(f"Exit order failed for {ticker}: {e} — position kept open")
            return False

    # ---------------------------------------------------------------- accessors

    def get_open_tickers(self) -> set:
        return set(self.positions.keys())

    def get_position(self, ticker: str) -> Optional[OpenPosition]:
        return self.positions.get(ticker)
