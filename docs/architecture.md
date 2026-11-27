# Architecture

v1 is a modular local application.

- `adapters/exchanges/binance` speaks the public market-data protocol only.
- `ingestion` pages historical klines, deduplicates them, and writes quality reports.
- `storage` keeps DuckDB for analytical candles, Parquet decimal strings for backtests, and SQLite for research plus paper trading.
- `research` owns factors, strategies, the event-driven backtest, validation, and reports.
- `execution` owns the paper broker. It does not import the Binance client.
- `agents` is a LangGraph workflow over a fixed tool whitelist.
- `dashboard` calls local services and does not open an exchange connection.

Signals are target weights from 0 to 1. Fills are applied by one accounting function shared by the backtest and the paper broker.
