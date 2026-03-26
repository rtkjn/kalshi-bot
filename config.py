"""
config.py
Loads all settings from .env into a typed Config object.
Every other module imports from here — never reads .env directly.
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # --- Kalshi API ---
    api_key_id: str
    private_key_path: str
    base_url: str
    ws_url: str
    dry_run: bool

    # --- Strategy ---
    entry_odds_threshold: float   # buy when YES or NO odds drop below this (e.g. 0.40)
    exit_odds_low: float          # sell when odds recover to this (e.g. 0.48)
    exit_odds_high: float         # or this (e.g. 0.50)
    entry_window_pct: float       # only enter in first X% of market lifetime (e.g. 0.40)
    trade_size_dollars: float     # fixed dollar amount per trade (e.g. 5.00)

    # --- Risk ---
    max_concurrent_positions: int
    daily_loss_limit_dollars: float
    momentum_filter_pct: float    # skip entry if price moved more than X% recently
    cooldown_after_loss_seconds: int

    # --- Assets ---
    assets: list[str]             # e.g. ["BTC", "ETH"]


def load_config() -> Config:
    return Config(
        api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private_key.pem"),
        base_url=os.getenv("KALSHI_BASE_URL", "https://trading-api.kalshi.com/trade-api/v2"),
        ws_url=os.getenv("KALSHI_WS_URL", "wss://trading-api.kalshi.com/trade-api/ws/v2"),
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",

        entry_odds_threshold=float(os.getenv("ENTRY_ODDS_THRESHOLD", "40")) / 100,
        exit_odds_low=float(os.getenv("EXIT_ODDS_LOW", "48")) / 100,
        exit_odds_high=float(os.getenv("EXIT_ODDS_HIGH", "50")) / 100,
        entry_window_pct=float(os.getenv("ENTRY_WINDOW_PCT", "0.40")),
        trade_size_dollars=float(os.getenv("TRADE_SIZE_DOLLARS", "5.00")),

        max_concurrent_positions=int(os.getenv("MAX_CONCURRENT_POSITIONS", "3")),
        daily_loss_limit_dollars=float(os.getenv("DAILY_LOSS_LIMIT_DOLLARS", "50.0")),
        momentum_filter_pct=float(os.getenv("MOMENTUM_FILTER_PCT", "0.5")) / 100,
        cooldown_after_loss_seconds=int(os.getenv("COOLDOWN_AFTER_LOSS_SECONDS", "300")),

        assets=os.getenv("ASSETS", "BTC,ETH").split(","),
    )
