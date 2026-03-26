"""
risk_manager.py
Enforces all risk controls:
  - Max concurrent open positions
  - Daily loss limit with kill switch
  - Per-asset cooldown after a loss
  - Kill switch (manual or auto)
"""

import logging
import time
from datetime import datetime, date

from config import Config

logger = logging.getLogger(__name__)


class RiskManager:
    def __init__(self, config: Config):
        self.config = config
        self.open_positions: set[str] = set()     # set of tickers
        self.daily_pnl: float = 0.0               # dollars, resets at midnight
        self.last_reset_date: date = date.today()
        self.kill_switch: bool = False
        self.cooldowns: dict[str, float] = {}     # asset → timestamp of last loss

    # ---------------------------------------------------------------- checks

    def can_open_position(self, ticker: str) -> tuple[bool, str]:
        """Returns (allowed, reason). Call before every entry."""
        self._maybe_reset_daily()

        if self.kill_switch:
            return False, "kill switch is active"

        if len(self.open_positions) >= self.config.max_concurrent_positions:
            return False, f"max concurrent positions reached ({self.config.max_concurrent_positions})"

        if self.daily_pnl <= -self.config.daily_loss_limit_dollars:
            self._trigger_kill_switch("daily loss limit reached")
            return False, "daily loss limit reached"

        asset = self._ticker_to_asset(ticker)
        if self._in_cooldown(asset):
            remaining = self._cooldown_remaining(asset)
            return False, f"{asset} in cooldown for {remaining:.0f}s"

        return True, "ok"

    def can_run(self) -> bool:
        """Fast check — returns False if kill switch is active."""
        return not self.kill_switch

    # ---------------------------------------------------------------- updates

    def on_position_opened(self, ticker: str):
        self.open_positions.add(ticker)
        logger.info(f"Position opened: {ticker} | open={len(self.open_positions)}")

    def on_position_closed(self, ticker: str, pnl_dollars: float):
        self.open_positions.discard(ticker)
        self.daily_pnl += pnl_dollars

        logger.info(
            f"Position closed: {ticker} | pnl=${pnl_dollars:+.2f} | "
            f"daily_pnl=${self.daily_pnl:+.2f}"
        )

        # Apply cooldown on a loss
        if pnl_dollars < 0:
            asset = self._ticker_to_asset(ticker)
            self.cooldowns[asset] = time.time()
            logger.warning(f"Loss on {asset} — cooldown applied for "
                           f"{self.config.cooldown_after_loss_seconds}s")

        # Check if we've hit daily loss limit
        if self.daily_pnl <= -self.config.daily_loss_limit_dollars:
            self._trigger_kill_switch("daily loss limit reached after close")

    def reset_kill_switch(self):
        """Manually re-enable trading (use with caution)."""
        logger.warning("Kill switch manually reset")
        self.kill_switch = False

    # ---------------------------------------------------------------- status

    def status(self) -> dict:
        return {
            "kill_switch": self.kill_switch,
            "open_positions": len(self.open_positions),
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_limit": self.config.daily_loss_limit_dollars,
        }

    # ---------------------------------------------------------------- private

    def _trigger_kill_switch(self, reason: str):
        if not self.kill_switch:
            logger.critical(f"KILL SWITCH TRIGGERED: {reason}")
            self.kill_switch = True

    def _in_cooldown(self, asset: str) -> bool:
        last_loss = self.cooldowns.get(asset)
        if not last_loss:
            return False
        return time.time() - last_loss < self.config.cooldown_after_loss_seconds

    def _cooldown_remaining(self, asset: str) -> float:
        last_loss = self.cooldowns.get(asset, 0)
        return max(0, self.config.cooldown_after_loss_seconds - (time.time() - last_loss))

    def _maybe_reset_daily(self):
        today = date.today()
        if today != self.last_reset_date:
            logger.info(f"New day — resetting daily P&L (was ${self.daily_pnl:+.2f})")
            self.daily_pnl = 0.0
            self.last_reset_date = today
            if self.kill_switch:
                logger.info("Kill switch auto-reset at midnight")
                self.kill_switch = False

    def _ticker_to_asset(self, ticker: str) -> str:
        ticker_upper = ticker.upper()
        for asset in self.config.assets:
            if asset in ticker_upper:
                return asset
        return "UNKNOWN"
