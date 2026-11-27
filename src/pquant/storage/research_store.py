"""SQLite research metadata: strategies, experiments, backtest runs, agent runs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pquant.storage.schema import SQLITE_SCHEMA


class ResearchStore:
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

    def upsert_strategy(
        self,
        *,
        strategy_id: str,
        name: str,
        description: str,
        asset_scope: str,
        timeframe: str,
        created_at: str,
        status: str,
        version: str,
        parameters_schema_json: str,
        code_commit: str | None,
        version_id: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO strategies (
                    strategy_id, name, description, asset_scope, timeframe, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    name = excluded.name,
                    description = excluded.description,
                    asset_scope = excluded.asset_scope,
                    timeframe = excluded.timeframe,
                    status = excluded.status
                """,
                [strategy_id, name, description, asset_scope, timeframe, created_at, status],
            )
            conn.execute(
                """
                INSERT INTO strategy_versions (
                    version_id, strategy_id, version, parameters_schema_json, code_commit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, version) DO UPDATE SET
                    parameters_schema_json = excluded.parameters_schema_json,
                    code_commit = excluded.code_commit
                """,
                [version_id, strategy_id, version, parameters_schema_json, code_commit, created_at],
            )

    def create_experiment(
        self,
        *,
        experiment_id: str,
        title: str,
        hypothesis: str,
        objective: str,
        status: str,
        created_at: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO research_experiments (
                    experiment_id, title, hypothesis, objective, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [experiment_id, title, hypothesis, objective, status, created_at],
            )

    def insert_run(self, row: dict[str, object]) -> None:
        columns = [
            "run_id",
            "experiment_id",
            "strategy_version_id",
            "strategy_id",
            "symbol",
            "interval",
            "start_time",
            "end_time",
            "dataset_hash",
            "config_json",
            "code_commit",
            "started_at",
            "completed_at",
            "status",
            "error_message",
            "segment",
        ]
        with self.connect() as conn:
            conn.execute(
                f"""
                INSERT INTO backtest_runs ({", ".join(columns)})
                VALUES ({", ".join(["?"] * len(columns))})
                """,
                [row.get(column) for column in columns],
            )

    def insert_metrics(self, rows: list[tuple[str, str, str | None, str, str]]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO backtest_metrics (run_id, metric_name, metric_value, metric_unit, segment)
                VALUES (?, ?, ?, ?, ?)
                """,
                rows,
            )

    def insert_agent_run(self, row: dict[str, object]) -> None:
        columns = [
            "agent_run_id",
            "task_type",
            "input_json",
            "output_json",
            "status",
            "started_at",
            "ended_at",
            "model_name",
            "prompt_version",
        ]
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO agent_runs ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(columns))})",
                [row.get(column) for column in columns],
            )

    def list_runs(self, limit: int = 20) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT run_id, strategy_id, symbol, interval, segment, status, started_at, completed_at
                FROM backtest_runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, object] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", [run_id]).fetchone()
            if row is None:
                return None
            metrics = conn.execute(
                "SELECT metric_name, metric_value, metric_unit, segment FROM backtest_metrics WHERE run_id = ?",
                [run_id],
            ).fetchall()
        payload = dict(row)
        payload["metrics"] = [dict(item) for item in metrics]
        return payload

    def list_strategies(self) -> list[dict[str, object]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT strategy_id, name, description, timeframe, status FROM strategies ORDER BY strategy_id"
            ).fetchall()
        return [dict(row) for row in rows]
