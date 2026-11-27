# Implementation status — Personal Quant AI v1.0

This file records what was actually built and run. Historical numbers below come from the Binance public download on 2026-11-27. They are not a forecast and they are not permission to trade.

Code commit for the backtest engine that produced `BACKTEST_REPORT.md`: `7e528ffe394e1072a8d4373bc29156602b461007`.

## Commands that were run

```text
uv sync
uv run pytest
uv run python scripts/init_db.py
uv run python scripts/collect_history.py
uv run python scripts/validate_data.py
uv run python scripts/run_backtests.py
uv run python scripts/collect_history.py   # second run, idempotency check
uv run streamlit run src/pquant/dashboard/app.py --server.headless true --server.address 127.0.0.1 --server.port 8501
```

The agent host was Linux. The README commands are the PowerShell and CMD forms for Windows 11. They call the same scripts.

## Tests

`uv run pytest`: **42 passed, 0 failed**.

That includes unit tests for timestamps, OHLC, dedupe, gaps, factors, next-open fills, fees, slippage, inventory, metrics, time splits, the judgment rule, paper-order transitions, idempotency, restart recovery, the public-endpoint allowlist, HTTP 418/429 handling, and a future-leakage regression. The integration test downloaded BTCUSDT daily bars for 2024-01-01 through 2024-01-11 from the public API, reloaded them, and wrote a JSON/CSV/PNG run. It is marked `integration` and skips if the API is unreachable; on this run it passed.

## Real market data

Source: `https://data-api.binance.vision` (`GET /api/v3/klines`, `/api/v3/exchangeInfo`, `/api/v3/time`). No API key.

| Symbol | Interval | Stored rows | Closed rows | Open (excluded from backtest) | Missing bars | Quality | Closed range (UTC) | Dataset hash |
|---|---|---:|---:|---:|---:|---|---|---|
| BTCUSDT | 1d | 3390 | 3389 | 1 | 0 | qualified | 2017-08-17 00:00:00 to 2026-11-26 23:59:59.999 | `082c50ae8d39acba97b1240a981026e6400835878b1202aa1adcd7bd72c586eb` |
| ETHUSDT | 1d | 3390 | 3389 | 1 | 0 | qualified | 2017-08-17 00:00:00 to 2026-11-26 23:59:59.999 | `fe05ccf267a13b604b5e3914338ede64a4b1dc8b5a53217e33fb53dc1887117c` |

First download used 4 kline requests per symbol. The second download used 1 request per symbol and kept the same row count and hash.

Checked prices, not assumed: BTC opened at 4261.48 on 2017-08-17 and the last closed bar closed at 60577.18. The buy-and-hold fill was at `4261.48 * 1.0005 = 4263.61074`, on that first open, with a fee of about 9.99 USDT. The locked test window is 2025-01-18 through 2026-11-26. BTC moved from an open of 104077.47 to a close of 60577.18 on that window (about -41.8% before the small entry cost). ETH moved from 3473.64 to 1689.85 (about -51.4%).

## Real backtest labels

Base cost: 0.10% fee and 0.05% slippage per side. Initial cash 10,000 USDT. Risk-free rate 0. These are research assumptions.

Full-sample total return, then the locked test-segment total return, then the pre-registered label:

| Symbol | Strategy | Full total return | Test total return | Test Sharpe | Label |
|---|---|---:|---:|---:|---|
| BTCUSDT | buy_and_hold | 13.1938 | -0.4188 | -0.4617 | REJECT |
| BTCUSDT | sma_cross | 14.6300 | 0.1010 | 0.3276 | CANDIDATE_FOR_PAPER |
| BTCUSDT | momentum | 32.5200 | -0.0997 | -0.0976 | INCONCLUSIVE |
| BTCUSDT | cash | 0 | 0 | undefined | BENCHMARK_ONLY |
| ETHUSDT | buy_and_hold | 4.6033 | -0.5143 | -0.2263 | REJECT |
| ETHUSDT | sma_cross | 32.4129 | 0.3216 | 0.5673 | CANDIDATE_FOR_PAPER |
| ETHUSDT | momentum | 41.0802 | 0.6283 | 0.8385 | CANDIDATE_FOR_PAPER |
| ETHUSDT | cash | 0 | 0 | undefined | BENCHMARK_ONLY |

Buy & Hold is rejected because the pre-registered rule rejects a test total return at or below -20% with a negative Sharpe. The large full-sample gain is the earlier history, not the test window. `CANDIDATE_FOR_PAPER` only means the paper-observation rule passed. It is not permission to trade and it is not a claim of future profit. SMA and momentum test returns on this sample were above buy-and-hold; that comparison does not by itself change the label.

Walk-forward used a 730-day train window and 29 complete 90-day test windows for each symbol. About half of the fixed-parameter windows had negative returns. Those windows are in the CSVs.

## Checklist

| Item | Status | Note |
|---|---|---|
| Installable Python 3.12 project with `uv.lock` | Completed | `uv sync` on this host |
| Windows PowerShell and CMD instructions | Completed | README. Not executed on a Windows machine in this session |
| Config, logging, safety switches | Completed | `LIVE`, leverage, short, withdrawal, and live orders raise at startup |
| DuckDB and SQLite init | Completed | `scripts/init_db.py` |
| Binance public kline pagination | Completed | Limit 1000, weight 2, verified 2026-11-27 |
| Incremental resume and dedupe | Completed | Second collect: 1 request, same hash |
| Unclosed bar excluded from backtests | Completed | 1 open bar stored, 3389 closed bars used |
| Data-quality report | Completed | Missing ratio 0, no OHLC or future-timestamp failures |
| No forward fill, no shuffled splits | Completed | Tested |
| Buy & Hold, SMA 20/50, 60-day momentum, cash | Completed | Same engine and cost model |
| Next-open fills, fees, slippage | Completed | Hand-checked against the BTC buy fill |
| Metrics, train/validation/test, walk-forward | Completed | 730-day train, 90-day test, 29 windows |
| Robustness grid and three cost scenarios | Completed | Grid did not select the test parameters |
| Report, CSV, PNG, HTML, config snapshots | Completed | `BACKTEST_REPORT.md`, `data/reports/` |
| Paper broker, no live order API | Completed | Tested, including restart |
| LangGraph whitelist workflow | Completed | Deterministic graph, no external chat model, no generated-code execution |
| Streamlit dashboard | Completed | HTTP 200, and every page rendered without an exception |
| 4h and 1h bulk download | Not run | Data layer accepts both. Default config downloads 1d only |
| WebSocket streams | Not implemented | REST history is the v1 source |
| P5 Spot Testnet order lifecycle | Not implemented | Optional. No testnet order client exists |
| P6 live trading | Not implemented | Disabled in code. Out of scope |

## Dashboard check

Streamlit answered `http://127.0.0.1:8501/` with HTTP 200. A headless Streamlit app test then opened Overview, Market, Strategy Lab, Backtesting, Paper Trading, AI Research, and Settings. Each page rendered. Overview showed the qualified BTC and ETH quality status. Settings showed cost assumptions and stated that the API secret is unused and not displayed. Market showed stored candles and the UTC candle note.
