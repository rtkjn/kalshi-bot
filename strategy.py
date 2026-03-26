"""
strategy.py
Core entry/exit decision logic.
Consumes signals from SignalEngine and decides whether to act,
factoring in risk manager state and current positions.
"""

import logging
from dataclasses import dataclass

from config import Config
from signal_engine import Signal, SignalType
from risk_manager import RiskManager

logger = logging.getLogger(__name__)


@dataclass
class TradeDecision:
    action: str        # 'buy', 'sell', 'hold'
    ticker: str
    side: str          # 'yes' or 'no'
    size_dollars: float
    price_cents: int   # limit price in cents (1-99)
    reason: str


class Strategy:
    def __init__(self, config: Config, risk_manager: RiskManager):
        self.config = config
        self.risk_manager = risk_manager

    def decide_entry(self, signal: Signal) -> TradeDecision:
        """
        Given an entry signal, decide whether to actually place the trade.
        Returns a TradeDecision with action='buy' or 'hold'.
        """
        if signal.type != SignalType.ENTRY:
            return self._hold(signal.ticker, signal.side, "no entry signal")

        # Check risk manager allows a new position
        allowed, reason = self.risk_manager.can_open_position(signal.ticker)
        if not allowed:
            return self._hold(signal.ticker, signal.side, f"risk blocked: {reason}")

        # Calculate limit price — bid slightly above current odds to get filled
        # e.g. if odds are at 0.37, bid at 38 cents
        price_cents = max(1, min(99, int(signal.current_odds * 100) + 1))

        # Calculate contract count from dollar size
        # Each contract costs price_cents / 100 dollars
        contract_cost = price_cents / 100
        contracts = max(1, int(self.config.trade_size_dollars / contract_cost))

        logger.info(
            f"ENTRY SIGNAL: {signal.ticker} | side={signal.side} | "
            f"odds={signal.current_odds:.2%} | {contracts} contracts @ {price_cents}¢ | "
            f"reason={signal.reason}"
        )

        return TradeDecision(
            action="buy",
            ticker=signal.ticker,
            side=signal.side,
            size_dollars=self.config.trade_size_dollars,
            price_cents=price_cents,
            reason=signal.reason,
        )

    def decide_exit(self, signal: Signal, position: dict) -> TradeDecision:
        """
        Given an exit signal, decide whether to close the position.
        Returns a TradeDecision with action='sell' or 'hold'.
        """
        if signal.type != SignalType.EXIT:
            return self._hold(signal.ticker, signal.side, "no exit signal")

        contracts = position.get("position", 1)
        # Sell at current bid — subtract 1 cent to ensure fill
        price_cents = max(1, min(99, int(signal.current_odds * 100) - 1))

        logger.info(
            f"EXIT SIGNAL: {signal.ticker} | side={signal.side} | "
            f"odds={signal.current_odds:.2%} | {contracts} contracts @ {price_cents}¢ | "
            f"reason={signal.reason}"
        )

        return TradeDecision(
            action="sell",
            ticker=signal.ticker,
            side=signal.side,
            size_dollars=0,
            price_cents=price_cents,
            reason=signal.reason,
        )

    # ---------------------------------------------------------------- helpers

    def _hold(self, ticker: str, side: str, reason: str) -> TradeDecision:
        return TradeDecision(
            action="hold",
            ticker=ticker,
            side=side,
            size_dollars=0,
            price_cents=0,
            reason=reason,
        )
