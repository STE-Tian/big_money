# Operations

Run the commands in the README from the repository root.

`scripts/init_db.py` creates both databases. `scripts/collect_history.py` resumes from the latest stored open time and upserts by the candle key. `scripts/validate_data.py` rewrites the quality report. `scripts/run_backtests.py` reads Parquet and refuses synthetic sources.

If the public API returns HTTP 418, the process stops and writes `data/reports/ingest_error.json`. Do not bypass a regional or exchange restriction with a proxy or a false identity. v1 does not need an account.

Logs go to stderr and `logs/pquant.log`. Secret-like `key=value` text is redacted. The log file is gitignored.

The Streamlit dashboard is `uv run streamlit run src/pquant/dashboard/app.py`. Empty panels show 暂无结果 instead of sample numbers.
