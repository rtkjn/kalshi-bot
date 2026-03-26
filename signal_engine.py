"""
signal_engine.py
Watches active 15-min Kalshi markets and emits entry/exit signals.

Entry signal fires when:
  1. We are in the first ENTRY_WINDOW_PCT of the market's lifetime
  2. YES or NO odds drop below ENTRY_ODDS_THRESHOLD
  3. Momentum filter passes (underlying hasn't moved too much recently)
  4. We are not already in a position on this market

Exit signal fires when:
  - An open position's odds recover to EXIT_ODDS_LOW..EXIT_ODDS_HIGH
  - OR the market is approaching expiry (last 60 seconds)
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

from config import Config
from price_feed import PriceFeed

logger = logging.getLogger(__name__)


class SignalType(Enum):
    ENTRY = "entry"
    EXIT  = "exit"
    NONE  = "none"


@dataclass
class Signal:
    type: SignalType
    ticker: str
    side: str             # 'yes' or 'no'
    current_odds: float   # current price as probability (0-1)
    asset: str            # 'BTC' or 'ETH'
    reason: str           # human-readable explanation


class SignalEngine:
    def __init__(self, config: Config, price_feed: PriceFeed):
        self.config = config
        self.price_feed = price_feed

    def evaluate_entry(self, market: dict, open_tickers: set[str]) -> Signal:
        """
        Evaluate whether to enter a position on a given market.
        Returns a Signal with type ENTRY or NONE.
        """
        ticker = market.get("ticker", "")
        asset = self._extract_asset(ticker)

        # Skip if already in this market
        if ticker in open_tickers:
            return Signal(SignalType.NONE, ticker, "", 0, asset, "already in position")

        # Check entry timing window
        if not self._in_entry_window(market):
            return Signal(SignalType.NONE, ticker, "", 0, asset, "outside entry window")

        # Check momentum filter
        if not self._passes_momentum_filter(asset):
            return Signal(SignalType.NONE, ticker, "", 0, asset, "momentum filter blocked")

        # Check odds
        yes_price = market.get("yes_bid", 0) / 100  # Kalshi returns cents
        no_price  = market.get("no_bid", 0) / 100
        threshold = self.config.entry_odds_threshold

        if yes_price > 0 and yes_price < threshold:
            return Signal(SignalType.ENTRY, ticker, "yes", yes_price, asset,
                          f"YES odds {yes_price:.2%} < threshold {threshold:.2%}")

        if no_price > 0 and no_price < threshold:
            return Signal(SignalType.ENTRY, ticker, "no", no_price, asset,
                          f"NO odds {no_price:.2%} < threshold {threshold:.2%}")

        return Signal(SignalType.NONE, ticker, "", 0, asset, "odds not low enough")

    def evaluate_exit(self, market: dict, position: dict) -> Signal:
        """
        Evaluate whether to exit an open position.
        Returns a Signal with type EXIT or NONE.
        """
        ticker = market.get("ticker", "")
        asset = self._extract_asset(ticker)
        side = position.get("side", "yes")

        current_price = (
            market.get("yes_bid", 0) if side == "yes" else market.get("no_bid", 0)
        ) / 100

        # Exit if odds recovered to target range
        if self.config.exit_odds_low <= current_price <= self.config.exit_odds_high:
            return Signal(SignalType.EXIT, ticker, side, current_price, asset,
                          f"Odds recovered to {current_price:.2%} — take profit")

        # Exit if market is expiring soon (within 60 seconds)
        time_remaining = self._seconds_remaining(market)
        if time_remaining is not None and time_remaining < 60:
            return Signal(SignalType.EXIT, ticker, side, current_price, asset,
                          "Market expiring in <60s — forced exit")

        return Signal(SignalType.NONE, ticker, side, current_price, asset, "holding")

    # ---------------------------------------------------------------- helpers

    def _in_entry_window(self, market: dict) -> bool:
        """Returns True if we are within the first ENTRY_WINDOW_PCT of market life."""
        open_time  = market.get("open_time")
        close_time = market.get("close_time")
        if not open_time or not close_time:
            return False

        now = time.time()
        total_duration = close_time - open_time
        if total_duration <= 0:
            return False

        elapsed_pct = (now - open_time) / total_duration
        return elapsed_pct <= self.config.entry_window_pct

    def _passes_momentum_filter(self, asset: str) -> bool:
        """Returns True if recent price movement is below the momentum threshold."""
        momentum = self.price_feed.get_momentum_pct(asset, lookback_seconds=120)
        if momentum is None:
            # Not enough data yet — be conservative, allow entry
            return True
        return momentum < self.config.momentum_filter_pct

    def _seconds_remaining(self, market: dict) -> float | None:
        close_time = market.get("close_time")
        if not close_time:
            return None
        return close_time - time.time()

    def _extract_asset(self, ticker: str) -> str:
        """Extract asset name from ticker e.g. 'KXBTC15M-...' → 'BTC'"""
        ticker_upper = ticker.upper()
        for asset in self.config.assets:
            if asset in ticker_upper:
                return asset
        return "UNKNOWN"
