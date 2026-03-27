"""
kalshi_client.py
Handles all communication with the Kalshi REST API.
RSA-PSS authentication, rate limiting, retries, and typed responses.
"""

import time
import base64
import json
import logging
from pathlib import Path

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend

from config import Config

logger = logging.getLogger(__name__)


class KalshiClient:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.base_url
        self.session = requests.Session()
        self._private_key = self._load_private_key()
        self._last_api_call = 0.0
        self._min_interval = 0.1  # max 10 requests/sec

    # ------------------------------------------------------------------ auth

    def _load_private_key(self):
        key_path = Path(self.config.private_key_path)
        if not key_path.exists():
            raise FileNotFoundError(f"Private key not found at {key_path}")
        with open(key_path, "rb") as f:
            return serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )

    def _sign_request(self, method: str, path: str) -> dict:
        ts = str(int(time.time() * 1000))
        path_without_query = path.split("?")[0]
        msg = ts + method.upper() + path_without_query
        signature = self._private_key.sign(
            msg.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.config.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ http

    def _rate_limit(self):
        elapsed = time.time() - self._last_api_call
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_api_call = time.time()

    def _request(self, method: str, endpoint: str, body: dict = None, retries: int = 3):
        self._rate_limit()
        path = f"/trade-api/v2{endpoint}"
        body_str = json.dumps(body) if body else ""
        headers = self._sign_request(method, path)
        url = self.base_url + endpoint

        for attempt in range(retries):
            try:
                resp = self.session.request(
                    method, url, headers=headers,
                    data=body_str if body_str else None,
                    timeout=10,
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"Rate limited — waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.error(f"Request failed (attempt {attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    # ------------------------------------------------------------------ markets

    def get_markets(self, series_ticker: str = None, status: str = "open") -> list[dict]:
        """Fetch active markets, optionally filtered by series ticker."""
        params = f"?status={status}"
        if series_ticker:
            params += f"&series_ticker={series_ticker}"
        response = self._request("GET", f"/markets{params}")
        return response.get("markets", [])

    def get_market(self, ticker: str) -> dict:
        """Fetch a single market by ticker."""
        return self._request("GET", f"/markets/{ticker}")

    def get_orderbook(self, ticker: str, depth: int = 5) -> dict:
        """Fetch orderbook for a market."""
        return self._request("GET", f"/markets/{ticker}/orderbook?depth={depth}")

    # ------------------------------------------------------------------ orders

    def place_order(self, ticker: str, side: str, max_cost_dollars: float) -> dict:
        """
        Place a fill-or-kill market order using buy_max_cost.

        Spends up to max_cost_dollars at the current best available price.
        No limit price, no resting order on the book — fills immediately or
        cancels entirely. This eliminates the duplicate order problem where
        resting limit orders accumulate and all fill when price hits the limit.

        side: 'yes' or 'no'
        max_cost_dollars: maximum dollars to spend (e.g. 3.00)
        """
        max_cost_cents = int(max_cost_dollars * 100)

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Would place {side.upper()} FoK order: "
                        f"up to ${max_cost_dollars:.2f} on {ticker}")
            return {"order": {"order_id": "dry_run"}, "status": "simulated"}

        # buy_max_cost automatically triggers Fill-or-Kill per Kalshi docs.
        # No price field or count needed — Kalshi fills at best available ask.
        body = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "buy_max_cost": max_cost_cents,
        }
        return self._request("POST", "/portfolio/orders", body)

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order."""
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Would cancel order {order_id}")
            return {"status": "simulated"}
        return self._request("DELETE", f"/portfolio/orders/{order_id}")

    # ------------------------------------------------------------------ portfolio

    def get_balance(self) -> float:
        """Return current balance in dollars."""
        resp = self._request("GET", "/portfolio/balance")
        return resp.get("balance", 0) / 100  # Kalshi returns cents

    def get_positions(self) -> list[dict]:
        """Return all open positions."""
        resp = self._request("GET", "/portfolio/positions")
        return resp.get("market_positions", [])
