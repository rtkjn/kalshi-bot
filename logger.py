"""
logger.py
Logs every trade entry and exit to:
  1. A SQLite database (kalshi_bot.db) for querying and backtesting
  2. A CSV file (trades.csv) for easy spreadsheet review
  3. Python's logging module for live console output
"""

import csv
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH   = "kalshi_bot.db"
CSV_PATH  = "trades.csv"


class TradeLogger:
    def __init__(self):
        self._init_db()
        self._init_csv()

    # ---------------------------------------------------------------- public

    def log_entry(self, ticker: str, side: str, contracts: int,
                  price_cents: int, cost_dollars: float,
                  order_id: str, reason: str):
        now = datetime.utcnow().isoformat()
        row = {
            "timestamp": now,
            "type": "entry",
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "dollars": round(cost_dollars, 4),
            "pnl": "",
            "order_id": order_id,
            "reason": reason,
        }
        self._write_db(row)
        self._write_csv(row)
        logger.info(f"[LOG] ENTRY {ticker} {side} {contracts}x @ {price_cents}¢ "
                    f"cost=${cost_dollars:.2f}")

    def log_exit(self, ticker: str, side: str, contracts: int,
                 exit_price_cents: int, proceeds_dollars: float,
                 pnl_dollars: float, reason: str):
        now = datetime.utcnow().isoformat()
        row = {
            "timestamp": now,
            "type": "exit",
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": exit_price_cents,
            "dollars": round(proceeds_dollars, 4),
            "pnl": round(pnl_dollars, 4),
            "order_id": "",
            "reason": reason,
        }
        self._write_db(row)
        self._write_csv(row)
        logger.info(f"[LOG] EXIT  {ticker} {side} {contracts}x @ {exit_price_cents}¢ "
                    f"proceeds=${proceeds_dollars:.2f} pnl=${pnl_dollars:+.2f}")

    def get_daily_pnl(self) -> float:
        """Query today's total P&L from the database."""
        today = datetime.utcnow().date().isoformat()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.execute(
                "SELECT SUM(pnl) FROM trades WHERE type='exit' AND timestamp LIKE ?",
                (f"{today}%",)
            )
            result = cursor.fetchone()[0]
            return result or 0.0

    # ---------------------------------------------------------------- private

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT,
                    type        TEXT,
                    ticker      TEXT,
                    side        TEXT,
                    contracts   INTEGER,
                    price_cents INTEGER,
                    dollars     REAL,
                    pnl         REAL,
                    order_id    TEXT,
                    reason      TEXT
                )
            """)
            conn.commit()

    def _init_csv(self):
        if not Path(CSV_PATH).exists():
            with open(CSV_PATH, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "timestamp", "type", "ticker", "side", "contracts",
                    "price_cents", "dollars", "pnl", "order_id", "reason"
                ])
                writer.writeheader()

    def _write_db(self, row: dict):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT INTO trades
                (timestamp, type, ticker, side, contracts, price_cents, dollars, pnl, order_id, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["timestamp"], row["type"], row["ticker"], row["side"],
                row["contracts"], row["price_cents"], row["dollars"],
                row["pnl"] if row["pnl"] != "" else None,
                row["order_id"], row["reason"],
            ))
            conn.commit()

    def _write_csv(self, row: dict):
        with open(CSV_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=row.keys())
            writer.writerow(row)
