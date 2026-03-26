"""
main.py
Async orchestrator — wires all modules together and runs the main loop.

Loop logic (runs every POLL_INTERVAL seconds):
  1. Fetch active 15-min BTC/ETH markets from Kalshi
  2. For each market, evaluate entry signals on new markets
  3. For each open position, evaluate exit signals
  4. Execute any buy/sell decisions via OrderManager
  5. Log status line to console
"""

import asyncio
import logging
import time
from datetime import datetime

from config import load_config
from kalshi_client import KalshiClient
from price_feed import PriceFeed
from signal_engine import SignalEngine, SignalType
from strategy import Strategy
from risk_manager import RiskManager
from order_manager import OrderManager
from logger import TradeLogger

# ------------------------------------------------------------------ logging setup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)-16s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log"),
    ]
)
logger = logging.getLogger(__name__)

POLL_INTERVAL = 5       # seconds between market scans
BTC_SERIES    = "KXBTC15M"  # Kalshi series ticker prefix for BTC 15-min markets
ETH_SERIES    = "KXETH15M"  # Kalshi series ticker prefix for ETH 15-min markets


async def run_bot():
    config = load_config()

    logger.info("=" * 60)
    logger.info("Kalshi Mean-Reversion Bot starting up")
    logger.info(f"DRY RUN: {config.dry_run}")
    logger.info(f"Assets: {config.assets}")
    logger.info(f"Entry threshold: {config.entry_odds_threshold:.0%}")
    logger.info(f"Exit range: {config.exit_odds_low:.0%} - {config.exit_odds_high:.0%}")
    logger.info(f"Trade size: ${config.trade_size_dollars}")
    logger.info("=" * 60)

    # Initialise all modules
    trade_logger   = TradeLogger()
    client         = KalshiClient(config)
    risk_manager   = RiskManager(config)
    price_feed     = PriceFeed(config.assets)
    signal_engine  = SignalEngine(config, price_feed)
    strategy       = Strategy(config, risk_manager)
    order_manager  = OrderManager(config, client, risk_manager, trade_logger)

    # Start price feed in background
    price_feed_task = asyncio.create_task(price_feed.start())
    logger.info("Price feed connecting...")
    await asyncio.sleep(3)  # give WebSocket time to connect

    # ---------------------------------------------------------------- main loop
    try:
        while True:
            if not risk_manager.can_run():
                logger.warning("Kill switch active — pausing trading loop")
                await asyncio.sleep(60)
                continue

            try:
                await trading_cycle(
                    config, client, signal_engine, strategy,
                    order_manager, price_feed
                )
            except Exception as e:
                logger.error(f"Trading cycle error: {e}", exc_info=True)

            # Status line
            status = risk_manager.status()
            btc_price = price_feed.get_price("BTC")
            eth_price = price_feed.get_price("ETH")
            logger.info(
                f"STATUS | BTC=${btc_price or 0:,.0f} ETH=${eth_price or 0:,.0f} | "
                f"positions={status['open_positions']} | "
                f"daily_pnl=${status['daily_pnl']:+.2f} | "
                f"kill={status['kill_switch']}"
            )

            await asyncio.sleep(POLL_INTERVAL)

    except asyncio.CancelledError:
        logger.info("Bot shutting down gracefully")
    finally:
        price_feed.stop()
        price_feed_task.cancel()


async def trading_cycle(config, client, signal_engine, strategy,
                        order_manager, price_feed):
    """Single iteration of the trading loop."""

    # Fetch active 15-min markets
    markets = []
    for series in [BTC_SERIES, ETH_SERIES]:
        try:
            batch = client.get_markets(series_ticker=series)
            markets.extend(batch)
        except Exception as e:
            logger.warning(f"Failed to fetch {series} markets: {e}")

    if not markets:
        logger.debug("No active markets found")
        return

    # Deduplicate markets by ticker — API can return same market twice
    seen = set()
    markets = [m for m in markets if m.get('ticker') not in seen and not seen.add(m.get('ticker'))]

    open_tickers = order_manager.get_open_tickers()

    # --- Evaluate entries on all markets ---
    for market in markets:
        ticker = market.get("ticker", "")

        entry_signal = signal_engine.evaluate_entry(market, open_tickers)
        if entry_signal.type == SignalType.ENTRY:
            decision = strategy.decide_entry(entry_signal)
            if decision.action == "buy":
                success = order_manager.execute_entry(decision)
                if success:
                    open_tickers.add(ticker)  # update locally

    # --- Evaluate exits on open positions ---
    for ticker in list(open_tickers):
        position = order_manager.get_position(ticker)
        if not position:
            continue

        # Find the market data for this ticker
        market = next((m for m in markets if m.get("ticker") == ticker), None)
        if not market:
            continue

        exit_signal = signal_engine.evaluate_exit(market, {
            "side": position.side,
            "position": position.contracts,
        })
        if exit_signal.type == SignalType.EXIT:
            decision = strategy.decide_exit(exit_signal, {
                "side": position.side,
                "position": position.contracts,
            })
            if decision.action == "sell":
                order_manager.execute_exit(decision)


if __name__ == "__main__":
    asyncio.run(run_bot())
