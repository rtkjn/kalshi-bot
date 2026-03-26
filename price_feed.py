"""
price_feed.py
Connects to Coinbase Advanced Trade WebSocket for real-time BTC and ETH prices.
Coinbase is US-friendly and doesn't geo-block like Binance.
Maintains a rolling 2-minute price history for momentum filtering.
Reconnects automatically on disconnect.
"""

import asyncio
import json
import logging
import time
from collections import deque
from typing import Dict, Optional

import websockets

logger = logging.getLogger(__name__)

COINBASE_WS = "wss://advanced-trade-ws.coinbase.com"
PRICE_HISTORY_SECONDS = 120


class PriceFeed:
    def __init__(self, assets: list):
        # assets like ["BTC", "ETH"] → product ids like "BTC-USD"
        self.assets = assets
        self.prices: Dict[str, float] = {}
        self.history: Dict[str, deque] = {a: deque() for a in assets}
        self._running = False

    # ---------------------------------------------------------------- public

    def get_price(self, asset: str) -> Optional[float]:
        return self.prices.get(asset)

    def get_momentum_pct(self, asset: str, lookback_seconds: int = 120) -> Optional[float]:
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

        # Build subscription message for Coinbase
        product_ids = [f"{asset}-USD" for asset in self.assets]

        subscribe_msg = json.dumps({
            "type": "subscribe",
            "product_ids": product_ids,
            "channel": "ticker"
        })

        while self._running:
            try:
                logger.info(f"Connecting to Coinbase price feed: {product_ids}")
                async with websockets.connect(COINBASE_WS, ping_interval=20) as ws:
                    await ws.send(subscribe_msg)
                    async for raw in ws:
                        self._handle_message(raw)
            except Exception as e:
                logger.warning(f"Price feed disconnected: {e} — reconnecting in 3s")
                await asyncio.sleep(3)

    def stop(self):
        self._running = False

    # ---------------------------------------------------------------- private

    def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)

            # Coinbase sends events in a wrapper
            if msg.get("channel") != "ticker":
                return

            events = msg.get("events", [])
            for event in events:
                tickers = event.get("tickers", [])
                for ticker in tickers:
                    product_id = ticker.get("product_id", "")  # e.g. "BTC-USD"
                    price_str = ticker.get("price", "")

                    if not product_id or not price_str:
                        continue

                    # Map "BTC-USD" → "BTC"
                    asset = product_id.replace("-USD", "")
                    if asset not in self.assets:
                        continue

                    price = float(price_str)
                    if price <= 0:
                        continue

                    self.prices[asset] = price
                    now = time.time()
                    self.history[asset].append((now, price))

                    # Prune history older than PRICE_HISTORY_SECONDS
                    cutoff = now - PRICE_HISTORY_SECONDS
                    while self.history[asset] and self.history[asset][0][0] < cutoff:
                        self.history[asset].popleft()

                    logger.debug(f"{asset}: ${price:,.2f}")

        except Exception as e:
            logger.debug(f"Price feed parse error: {e}")
