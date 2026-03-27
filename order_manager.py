"""
order_manager.py
Handles the full lifecycle of orders using fill-or-kill market orders.

v2.0: switched from limit orders to buy_max_cost FoK orders.
- price_cents passed as ceiling price to Kalshi
- count calculated from budget / price
- buy_max_cost caps total spend and triggers FoK behavior
- No resting orders ever left on the book
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

RECENTLY_CLOSED_TTL = 120  # seconds — covers Kalshi's settlement lag


@dataclass
class OpenPosition:
    ticker: str
    side: str
    contracts: int
    entry_cost_dollars: float
    order_id: str


class OrderManager:
    def __init__(self, config: Config, client: KalshiClient,
                 risk_manager: RiskManager, trade_logger: TradeLogger):
        self.config        = config
        self.client        = client
        self.risk_manager  = risk_manager
        self.trade_logger  = trade_logger
        self.positions: Dict[str, OpenPosition] = {}
        self._recently_closed: Dict[str, float] = {}

    # ---------------------------------------------------------------- sync

    def sync_live_position(self, ticker: str, position_fp: float):
        """Sync live Kalshi positions into memory. Never re-adds recently closed tickers."""
        contracts = abs(int(float(position_fp)))

        if float(position_fp) == 0 or contracts == 0:
            if ticker in self.positions:
                logger.info(f"Sync: removing closed position {ticker} from memory")
                del self.positions[ticker]
            return

        closed_at = self._recently_closed.get(ticker)
        if closed_at and time.time() - closed_at < RECENTLY_CLOSED_TTL:
            remaining = RECENTLY_CLOSED_TTL - (time.time() - closed_at)
            logger.debug(f"Sync: skipping {ticker} — recently closed ({remaining:.0f}s left)")
            return

        if ticker not in self.positions:
            side = "no" if float(position_fp) < 0 else "yes"
            self.positions[ticker] = OpenPosition(
                ticker=ticker, side=side, contracts=contracts,
                entry_cost_dollars=0, order_id="synced",
            )
            self.risk_manager.on_position_opened(ticker)
            logger.info(f"Sync: added live position {ticker} ({side} {contracts}x)")

    def _mark_recently_closed(self, ticker: str):
        self._recently_closed[ticker] = time.time()
        now = time.time()
        self._recently_closed = {
            t: ts for t, ts in self._recently_closed.items()
            if now - ts < RECENTLY_CLOSED_TTL
        }

    # ---------------------------------------------------------------- entries

    def execute_entry(self, decision: TradeDecision) -> bool:
        """Place a FoK entry. Price ceiling from signal, spend capped at trade_size_dollars."""
        if decision.action != "buy":
            return False

        ticker = decision.ticker

        if ticker in self.positions:
            logger.warning(f"Duplicate entry blocked: {ticker} already in positions")
            return False

        closed_at = self._recently_closed.get(ticker)
        if closed_at and time.time() - closed_at < RECENTLY_CLOSED_TTL:
            logger.warning(f"Entry blocked: {ticker} in recently_closed guard")
            return False

        allowed, reason = self.risk_manager.can_open_position(ticker)
        if not allowed:
            logger.info(f"Entry blocked by risk manager: {reason}")
            return False

        max_cost = self.config.trade_size_dollars
        # Price ceiling: current odds + 2c so we're competitive but not reckless
        price_ceil = min(99, (decision.price_cents or 50) + 2)

        try:
            result = self.client.place_order(
                ticker=ticker,
                side=decision.side,
                price_cents=price_ceil,
                max_cost_dollars=max_cost,
            )

            order    = result.get("order", {})
            order_id = order.get("order_id", "unknown")
            status   = order.get("status", "")

            if status == "canceled":
                logger.warning(f"Entry FoK canceled (no liquidity at {price_ceil}c): {ticker}")
                return False

            fill_cost     = float(order.get("taker_fill_cost_dollars") or max_cost)
            est_contracts = max(1, int(fill_cost / (price_ceil / 100)))

            self.positions[ticker] = OpenPosition(
                ticker=ticker, side=decision.side, contracts=est_contracts,
                entry_cost_dollars=fill_cost, order_id=order_id,
            )
            self.risk_manager.on_position_opened(ticker)
            self.trade_logger.log_entry(
                ticker=ticker, side=decision.side, contracts=est_contracts,
                price_cents=price_ceil, cost_dollars=fill_cost,
                order_id=order_id, reason=decision.reason,
            )
            logger.info(f"ENTRY PLACED: {ticker} | {decision.side} | "
                        f"FoK {est_contracts}x@{price_ceil}c | ~${fill_cost:.2f}")
            return True

        except Exception as e:
            logger.error(f"Failed to place entry on {ticker}: {e}")
            return False

    # ---------------------------------------------------------------- exits

    def execute_exit(self, decision: TradeDecision) -> bool:
        """Sell position using FoK sell with reduce_only. Keeps position open on failure."""
        if decision.action != "sell":
            return False

        ticker   = decision.ticker
        position = self.positions.get(ticker)
        if not position:
            logger.warning(f"No open position found for {ticker} — skipping exit")
            return False

        try:
            price_field  = "yes_price" if position.side == "yes" else "no_price"
            exit_price   = max(1, (decision.price_cents or 50) - 2)
            body = {
                "ticker": ticker,
                "action": "sell",
                "side": position.side,
                "count": position.contracts,
                "reduce_only": True,
                "time_in_force": "fill_or_kill",
                price_field: exit_price,
            }
            result = self.client._request("POST", "/portfolio/orders", body)
            order  = result.get("order", {})
            status = order.get("status", "")

            if status == "canceled":
                logger.warning(f"Exit FoK canceled for {ticker} — will retry next cycle")
                return False

            proceeds = position.contracts * (decision.price_cents or 50) / 100
            pnl      = proceeds - position.entry_cost_dollars

            self.risk_manager.on_position_closed(ticker, pnl)
            self.trade_logger.log_exit(
                ticker=ticker, side=position.side, contracts=position.contracts,
                exit_price_cents=decision.price_cents or 50,
                proceeds_dollars=proceeds, pnl_dollars=pnl, reason=decision.reason,
            )
            del self.positions[ticker]
            self._mark_recently_closed(ticker)

            logger.info(f"EXIT PLACED: {ticker} | {position.side} | "
                        f"{position.contracts}x@{exit_price}c | pnl=~${pnl:+.2f}")
            return True

        except Exception as e:
            logger.error(f"Exit order failed for {ticker}: {e} — position kept open")
            return False

    # ---------------------------------------------------------------- accessors

    def get_open_tickers(self) -> set:
        return set(self.positions.keys())

    def get_position(self, ticker: str) -> Optional[OpenPosition]:
        return self.positions.get(ticker)
