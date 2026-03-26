"""
order_manager.py
Handles the full lifecycle of orders:
place → track → confirm fill → close.
Wraps KalshiClient with position state tracking.
"""

import logging
from dataclasses import dataclass, field
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
        self.config = config
        self.client = client
        self.risk_manager = risk_manager
        self.trade_logger = trade_logger
        self.positions: dict[str, OpenPosition] = {}  # ticker → position

    # ---------------------------------------------------------------- public

    def execute_entry(self, decision: TradeDecision) -> bool:
        """Place an entry order. Returns True if successful."""
        if decision.action != "buy":
            return False

        ticker = decision.ticker

        # Hard guard — never enter the same ticker twice
        if ticker in self.positions:
            logger.warning(f"Duplicate entry blocked for {ticker} — already in positions")
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

            order_id = result.get("order_id", "unknown")
            cost = contracts * decision.price_cents / 100

            position = OpenPosition(
                ticker=ticker,
                side=decision.side,
                contracts=contracts,
                entry_price_cents=decision.price_cents,
                order_id=order_id,
                entry_cost_dollars=cost,
            )
            self.positions[ticker] = position
            self.risk_manager.on_position_opened(ticker)

            self.trade_logger.log_entry(
                ticker=ticker,
                side=decision.side,
                contracts=contracts,
                price_cents=decision.price_cents,
                cost_dollars=cost,
                order_id=order_id,
                reason=decision.reason,
            )

            logger.info(f"ENTRY PLACED: {ticker} | {decision.side} | "
                        f"{contracts} contracts @ {decision.price_cents}¢ | "
                        f"cost=${cost:.2f}")
            return True

        except Exception as e:
            logger.error(f"Failed to place entry on {ticker}: {e}")
            return False

    def execute_exit(self, decision: TradeDecision) -> bool:
        """Place an exit order. Returns True if successful."""
        if decision.action != "sell":
            return False

        ticker = decision.ticker
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
            pnl = proceeds - position.entry_cost_dollars

            self.risk_manager.on_position_closed(ticker, pnl)

            self.trade_logger.log_exit(
                ticker=ticker,
                side=position.side,
                contracts=position.contracts,
                exit_price_cents=decision.price_cents,
                proceeds_dollars=proceeds,
                pnl_dollars=pnl,
                reason=decision.reason,
            )

            del self.positions[ticker]

            logger.info(f"EXIT PLACED: {ticker} | {position.side} | "
                        f"{position.contracts} contracts @ {decision.price_cents}¢ | "
                        f"pnl=${pnl:+.2f}")
            return True

        except Exception as e:
            logger.error(f"Failed to place exit on {ticker}: {e}")
            return False

    def get_open_tickers(self) -> set[str]:
        return set(self.positions.keys())

    def get_position(self, ticker: str) -> Optional[OpenPosition]:
        return self.positions.get(ticker)
