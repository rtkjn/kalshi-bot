"""
price_feed.py
Connects to Binance WebSocket for real-time BTC and ETH prices.
Maintains a rolling 2-minute price history for momentum filtering.
Reconnects automatically on disconnect.
"""

import asyncio
import json
import logging
import time
from collections import deque

import websockets

logger = logging.getLogger(__name__)

BINANCE_WS = "wss://stream.binance.com:443/stream"

# Rolling window of (timestamp, price) tuples — last 2 minutes
PRICE_HISTORY_SECONDS = 120


class PriceFeed:
    def __init__(self, assets: list[str]):
        # assets like ["BTC", "ETH"] → streams like "btcusdt@trade"
        self.assets = assets
        self.prices: dict[str, float] = {}
        self.history: dict[str, deque] = {
            a: deque() for a in assets
        }
        self._running = False

    # ---------------------------------------------------------------- public

    def get_price(self, asset: str) -> float | None:
        return self.prices.get(asset)

    def get_momentum_pct(self, asset: str, lookback_seconds: int = 120) -> float | None:
        """
        Returns price change % over the last lookback_seconds.
        Used by strategy to filter out real momentum moves.
        Returns None if not enough data yet.
        """
        history = self.history.get(asset)
        if not history or len(history) < 2:
            return None

        now = time.time()
        cutoff = now - lookback_seconds
        old_prices = [p for ts, p in history if ts >= cutoff]

        if not old_prices:
            return None

        oldest = old_prices[0]
        current = self.prices.get(asset)
        if not oldest or not current:
            return None

        return abs((current - oldest) / oldest)

    async def start(self):
        self._running = True
        streams = "/".join(
            f"{asset.lower()}usdt@trade" for asset in self.assets
        )
        url = f"{BINANCE_WS}?streams={streams}"

        while self._running:
            try:
                logger.info(f"Connecting to Binance price feed: {self.assets}")
                async with websockets.connect(url, ping_interval=20) as ws:
                    async for raw in ws:
                        self._handle_message(raw)
            except Exception as e:
                logger.warning(f"Price feed disconnected: {e} — reconnecting in 2s")
                await asyncio.sleep(2)

    def stop(self):
        self._running = False

    # ---------------------------------------------------------------- private

    def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
            data = msg.get("data", {})
            symbol: str = data.get("s", "")  # e.g. "BTCUSDT"
            price = float(data.get("p", 0))

            # Map symbol back to asset name
            asset = symbol.replace("USDT", "")
            if asset in self.assets and price > 0:
                self.prices[asset] = price
                now = time.time()
                self.history[asset].append((now, price))
                # Prune history older than PRICE_HISTORY_SECONDS
                cutoff = now - PRICE_HISTORY_SECONDS
                while self.history[asset] and self.history[asset][0][0] < cutoff:
                    self.history[asset].popleft()

        except Exception as e:
            logger.debug(f"Price feed parse error: {e}")
