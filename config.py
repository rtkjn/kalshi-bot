"""
config.py
Loads all settings from .env into a typed Config object.
Supports MODE=demo and MODE=live — switches all credentials automatically.
"""

import os
from dataclasses import dataclass
from typing import List
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
    mode: str             # 'demo' or 'live'

    # --- Strategy ---
    entry_odds_threshold: float
    entry_odds_floor: float
    exit_odds_low: float
    exit_odds_high: float
    entry_window_pct: float
    trade_size_dollars: float

    # --- Risk ---
    max_concurrent_positions: int
    daily_loss_limit_dollars: float
    momentum_filter_pct: float
    cooldown_after_loss_seconds: int

    # --- Assets ---
    assets: List[str]


def load_config() -> Config:
    mode = os.getenv("MODE", "live").lower()
    prefix = "DEMO" if mode == "demo" else "LIVE"

    return Config(
        mode=mode,
        api_key_id=os.getenv(f"{prefix}_API_KEY_ID", ""),
        private_key_path=os.getenv(f"{prefix}_PRIVATE_KEY_PATH", "./kalshi_private_key.pem"),
        base_url=os.getenv(f"{prefix}_BASE_URL", "https://api.elections.kalshi.com/trade-api/v2"),
        ws_url=os.getenv(f"{prefix}_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2"),
        dry_run=os.getenv("DRY_RUN", "true").lower() == "true",

        entry_odds_threshold=float(os.getenv("ENTRY_ODDS_THRESHOLD", "40")) / 100,
        entry_odds_floor=float(os.getenv("ENTRY_ODDS_FLOOR", "35")) / 100,
        exit_odds_low=float(os.getenv("EXIT_ODDS_LOW", "48")) / 100,
        exit_odds_high=float(os.getenv("EXIT_ODDS_HIGH", "50")) / 100,
        entry_window_pct=float(os.getenv("ENTRY_WINDOW_PCT", "0.40")),
        trade_size_dollars=float(os.getenv("TRADE_SIZE_DOLLARS", "3.00")),

        max_concurrent_positions=int(os.getenv("MAX_CONCURRENT_POSITIONS", "2")),
        daily_loss_limit_dollars=float(os.getenv("DAILY_LOSS_LIMIT_DOLLARS", "10.0")),
        momentum_filter_pct=float(os.getenv("MOMENTUM_FILTER_PCT", "0.5")) / 100,
        cooldown_after_loss_seconds=int(os.getenv("COOLDOWN_AFTER_LOSS_SECONDS", "300")),

        assets=os.getenv("ASSETS", "BTC,ETH").split(","),
    )
