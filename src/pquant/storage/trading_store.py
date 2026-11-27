"""SQLite paper-trading store. No API secrets and no exchange order ids from a live venue."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pquant.storage.schema import SQLITE_SCHEMA


class TradingStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SQLITE_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_order_by_client_id(self, client_order_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id = ?",
                [client_order_id],
            ).fetchone()
        return dict(row) if row else None

    def get_account(self, account_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM paper_accounts WHERE account_id = ?", [account_id]).fetchone()
        return dict(row) if row else None

    def list_accounts(self) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM paper_accounts ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def list_orders(self, account_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_orders WHERE account_id = ? ORDER BY created_at",
                [account_id],
            ).fetchall()
        return [dict(row) for row in rows]

    def list_fills(self, account_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT f.* FROM paper_fills f
                JOIN paper_orders o ON o.order_id = f.order_id
                WHERE o.account_id = ?
                ORDER BY f.fill_time
                """,
                [account_id],
            ).fetchall()
        return [dict(row) for row in rows]

    def list_positions(self, account_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_positions WHERE account_id = ? ORDER BY symbol",
                [account_id],
            ).fetchall()
        return [dict(row) for row in rows]

    def list_equity(self, account_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_equity_snapshots WHERE account_id = ? ORDER BY timestamp",
                [account_id],
            ).fetchall()
        return [dict(row) for row in rows]

    def list_risk_events(self, account_id: str) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM risk_events WHERE account_id = ? ORDER BY event_time",
                [account_id],
            ).fetchall()
        return [dict(row) for row in rows]

    def transaction(self, callback):
        with self.connect() as conn:
            return callback(conn)

    def apply_transaction(self, statements: list[tuple[str, list[object]]]) -> None:
        """Run several writes in one transaction. A failure rolls all of them back."""
        with self.connect() as conn:
            for sql, params in statements:
                conn.execute(sql, params)
