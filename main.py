"""
main.py
Async orchestrator — wires all modules together and runs the main loop.

Loop logic (runs every POLL_INTERVAL seconds):
  1. Fetch active 15-min BTC/ETH markets from Kalshi
  2. Pull LIVE open positions from Kalshi API (Bug 1 fix — no stale in-memory state)
  3. For each market, evaluate entry signals
  4. For each open position, evaluate exit signals
  5. Log status line with true P&L from balance delta (Bug 2 fix)
"""

import asyncio
import logging
import sqlite3

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

POLL_INTERVAL = 5
BTC_SERIES    = "KXBTC15M"
ETH_SERIES    = "KXETH15M"
DB_PATH       = "kalshi_bot.db"


def _load_start_balance(client: KalshiClient) -> float:
    """
    Bug 2 fix: Load or record the starting balance for this session.
    Persists to SQLite so restarts within the same calendar day don't reset P&L.
    """
    from datetime import date
    today = date.today().isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                date TEXT PRIMARY KEY,
                start_balance REAL
            )
        """)
        row = conn.execute(
            "SELECT start_balance FROM sessions WHERE date = ?", (today,)
        ).fetchone()

        if row:
            start = row[0]
            logger.info(f"Resuming session — start balance from DB: ${start:.2f}")
        else:
            start = client.get_balance()
            conn.execute(
                "INSERT INTO sessions (date, start_balance) VALUES (?, ?)",
                (today, start)
            )
            conn.commit()
            logger.info(f"New session — recording start balance: ${start:.2f}")

    return start


async def run_bot():
    config = load_config()

    logger.info("=" * 60)
    logger.info("Kalshi Mean-Reversion Bot starting up")
    logger.info(f"DRY RUN:   {config.dry_run}")
    logger.info(f"Assets:    {config.assets}")
    logger.info(f"Entry:     < {config.entry_odds_threshold:.0%}")
    logger.info(f"Exit:      >= {config.exit_odds_low:.0%}")
    logger.info(f"Trade size: ${config.trade_size_dollars}")
    logger.info(f"Max positions: {config.max_concurrent_positions}")
    logger.info("=" * 60)

    trade_logger  = TradeLogger()
    client        = KalshiClient(config)
    risk_manager  = RiskManager(config)
    price_feed    = PriceFeed(config.assets)
    signal_engine = SignalEngine(config, price_feed)
    strategy      = Strategy(config, risk_manager)
    order_manager = OrderManager(config, client, risk_manager, trade_logger)

    # Bug 2: record starting balance once, persists across restarts
    start_balance = _load_start_balance(client)

    price_feed_task = asyncio.create_task(price_feed.start())
    logger.info("Price feed connecting...")
    await asyncio.sleep(3)

    try:
        while True:
            if not risk_manager.can_run():
                logger.warning("Kill switch active — pausing trading loop")
                await asyncio.sleep(60)
                continue

            markets_snapshot = []
            try:
                markets_snapshot = await trading_cycle(
                    config, client, signal_engine, strategy,
                    order_manager, price_feed
                ) or []
            except Exception as e:
                logger.error(f"Trading cycle error: {e}", exc_info=True)

            # Build odds string
            odds_parts = []
            for m in markets_snapshot:
                asset = "BTC" if "BTC" in m.get("ticker", "").upper() else "ETH"
                yes = float(m.get("yes_bid_dollars") or 0)
                no  = float(m.get("no_bid_dollars") or 0)
                if yes > 0 or no > 0:
                    odds_parts.append(f"{asset} YES={yes:.0%} NO={no:.0%}")
            odds_str = " | ".join(odds_parts) if odds_parts else "no markets"

            # Bug 2: true P&L = current balance minus start balance
            try:
                current_balance = client.get_balance()
                true_pnl = current_balance - start_balance
            except Exception:
                current_balance = 0
                true_pnl = 0

            btc_price = price_feed.get_price("BTC")
            eth_price = price_feed.get_price("ETH")
            status    = risk_manager.status()

            logger.info(
                f"STATUS | BTC=${btc_price or 0:,.0f} ETH=${eth_price or 0:,.0f} | "
                f"{odds_str} | "
                f"positions={status['open_positions']} | "
                f"balance=${current_balance:.2f} | "
                f"session_pnl=${true_pnl:+.2f} | "
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

    # Fetch active markets
    markets = []
    for series in [BTC_SERIES, ETH_SERIES]:
        try:
            batch = client.get_markets(series_ticker=series)
            markets.extend(batch)
        except Exception as e:
            logger.warning(f"Failed to fetch {series} markets: {e}")

    if not markets:
        logger.debug("No active markets found")
        return []

    # Deduplicate by ticker
    seen = set()
    markets = [m for m in markets
               if m.get("ticker") not in seen and not seen.add(m.get("ticker"))]

    # Bug 1 fix: pull live positions from Kalshi API every cycle
    # This is the ground truth — never stale, prevents all duplicate entries
    live_open_tickers = set()
    try:
        live_positions = client.get_positions()
        for p in live_positions:
            ticker = p.get("ticker", "")
            fp = float(p.get("position_fp") or 0)
            if ticker and fp != 0:
                live_open_tickers.add(ticker)
                # Sync into order_manager memory if not already there
                order_manager.sync_live_position(ticker, fp)
    except Exception as e:
        logger.warning(f"Could not fetch live positions: {e} — using in-memory fallback")
        live_open_tickers = order_manager.get_open_tickers()

    # Union: live API positions + in-memory (covers positions placed this cycle)
    open_tickers = live_open_tickers | order_manager.get_open_tickers()

    # --- Entry evaluation ---
    for market in markets:
        ticker = market.get("ticker", "")
        entry_signal = signal_engine.evaluate_entry(market, open_tickers)
        if entry_signal.type == SignalType.ENTRY:
            decision = strategy.decide_entry(entry_signal)
            if decision.action == "buy":
                success = order_manager.execute_entry(decision)
                if success:
                    open_tickers.add(ticker)

    # --- Exit evaluation ---
    for ticker in list(open_tickers):
        position = order_manager.get_position(ticker)
        if not position:
            continue
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

    return markets


if __name__ == "__main__":
    asyncio.run(run_bot())
