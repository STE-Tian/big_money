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

V2 P1 is additive and lives in `src/pquant/v2`. It collects spot 1h/4h klines and USD-M funding-rate history, then writes a point-in-time feature store. Funding events are not candles and are not tradable. The feature builder does not call the backtest engine. Daily relative-strength rows are derived from six closed 4h bars and are stored under `data/parquet/v2_features/`, separate from the V1 daily candle file. The formal scope, including the parts that are specified but not built, is `docs/V2_SPEC.md`.
