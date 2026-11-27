"""DDL for DuckDB (market) and SQLite (research + trading)."""

DUCKDB_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_candles (
    exchange VARCHAR NOT NULL,
    market_type VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    close_time TIMESTAMPTZ NOT NULL,
    open DOUBLE NOT NULL,
    high DOUBLE NOT NULL,
    low DOUBLE NOT NULL,
    close DOUBLE NOT NULL,
    volume DOUBLE NOT NULL,
    quote_volume DOUBLE NOT NULL,
    trade_count BIGINT NOT NULL,
    taker_buy_base_volume DOUBLE NOT NULL,
    taker_buy_quote_volume DOUBLE NOT NULL,
    is_closed BOOLEAN NOT NULL,
    source VARCHAR NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL,
    content_hash VARCHAR NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, interval, open_time)
);

CREATE TABLE IF NOT EXISTS instruments (
    exchange VARCHAR NOT NULL,
    market_type VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    base_asset VARCHAR NOT NULL,
    quote_asset VARCHAR NOT NULL,
    price_tick DOUBLE NOT NULL,
    qty_step DOUBLE NOT NULL,
    min_qty DOUBLE NOT NULL,
    min_notional DOUBLE NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol)
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id VARCHAR PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    exchange VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    request_count INTEGER NOT NULL,
    rows_returned INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    revised_count INTEGER NOT NULL,
    data_start TIMESTAMPTZ,
    data_end TIMESTAMPTZ,
    status VARCHAR NOT NULL,
    error_message VARCHAR,
    base_url VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_requests (
    request_id VARCHAR PRIMARY KEY,
    run_id VARCHAR NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL,
    base_url VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    start_time_ms BIGINT,
    end_time_ms BIGINT,
    http_status INTEGER,
    rows_returned INTEGER NOT NULL,
    data_open_first TIMESTAMPTZ,
    data_open_last TIMESTAMPTZ,
    used_weight_1m VARCHAR,
    elapsed_ms INTEGER,
    error_message VARCHAR
);

CREATE TABLE IF NOT EXISTS data_quality_reports (
    report_id VARCHAR PRIMARY KEY,
    generated_at TIMESTAMPTZ NOT NULL,
    exchange VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    interval VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    dataset_hash VARCHAR NOT NULL,
    total_rows INTEGER NOT NULL,
    closed_rows INTEGER NOT NULL,
    missing_count INTEGER NOT NULL,
    missing_ratio DOUBLE NOT NULL,
    revised_candle_count INTEGER NOT NULL,
    report_json VARCHAR NOT NULL
);
"""

SQLITE_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS strategies (
    strategy_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    asset_scope TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    version_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL REFERENCES strategies(strategy_id),
    version TEXT NOT NULL,
    parameters_schema_json TEXT NOT NULL,
    code_commit TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (strategy_id, version)
);

CREATE TABLE IF NOT EXISTS research_experiments (
    experiment_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    experiment_id TEXT REFERENCES research_experiments(experiment_id),
    strategy_version_id TEXT,
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    dataset_hash TEXT,
    config_json TEXT NOT NULL,
    code_commit TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    segment TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    run_id TEXT NOT NULL REFERENCES backtest_runs(run_id),
    metric_name TEXT NOT NULL,
    metric_value TEXT,
    metric_unit TEXT NOT NULL,
    segment TEXT NOT NULL,
    PRIMARY KEY (run_id, metric_name, segment)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    agent_run_id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    model_name TEXT NOT NULL,
    prompt_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_accounts (
    account_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    initial_cash TEXT NOT NULL,
    current_cash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    peak_equity TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id TEXT PRIMARY KEY,
    client_order_id TEXT NOT NULL UNIQUE,
    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
    strategy_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    order_type TEXT NOT NULL,
    quantity TEXT NOT NULL,
    limit_price TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_signal_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_order_events (
    event_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
    event_time TEXT NOT NULL,
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
    fill_time TEXT NOT NULL,
    fill_price TEXT NOT NULL,
    fill_quantity TEXT NOT NULL,
    fee_amount TEXT NOT NULL,
    fee_asset TEXT NOT NULL,
    slippage_amount TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
    symbol TEXT NOT NULL,
    quantity TEXT NOT NULL,
    average_entry_price TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (account_id, symbol)
);

CREATE TABLE IF NOT EXISTS paper_equity_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
    timestamp TEXT NOT NULL,
    cash TEXT NOT NULL,
    market_value TEXT NOT NULL,
    total_equity TEXT NOT NULL,
    unrealized_pnl TEXT NOT NULL,
    realized_pnl TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_events (
    event_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES paper_accounts(account_id),
    event_time TEXT NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL,
    action_taken TEXT NOT NULL
);
"""
