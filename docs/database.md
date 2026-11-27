# Databases

Market data lives in DuckDB (`data/duckdb/market.duckdb`): `market_candles`, `instruments`, `ingestion_runs`, `ingestion_requests`, and `data_quality_reports`. The candle primary key is exchange, market type, symbol, interval, and open time. Prices in DuckDB are DOUBLE values for queries. The Parquet snapshot keeps the exchange's decimal strings and is the backtest input.

Research and paper trading share `data/sqlite/research_trading.sqlite`. Research tables include strategies, strategy versions, experiments, backtest runs, metrics, and agent runs. Trading tables include paper accounts, orders, order events, fills, positions, equity snapshots, and risk events. `client_order_id` is unique. Foreign keys are enabled. No table stores an API secret.

Writes for a paper fill, its order status, cash, and position happen in one SQLite transaction.
