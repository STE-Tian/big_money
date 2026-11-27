# Personal Quant AI

个人量化研究、历史回测、模拟交易和受限 AI 研究工作台。它不是自动盈利的交易机器人，也不会在 v1 发送真实订单。

Personal Quant AI is a local research workbench for Binance Spot public market data, historical backtests, paper trading, and a restricted research workflow. Live trading is disabled in code.

默认 `TRADING_MODE=research`。公开行情不需要 API Key，也不要填写 API Secret。

## 环境

- Python 3.11 或 3.12
- [uv](https://docs.astral.sh/uv/)
- 已在 Windows 11 的命令形式上核对；开发机可以是 Linux

安装 uv（PowerShell）：

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.12
```

安装 uv（CMD，调用 PowerShell 安装器）：

```cmd
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv python install 3.12
```

## 首次运行

在仓库根目录执行。下面两组命令等价，优先使用 PowerShell。

### PowerShell

```powershell
uv sync
Copy-Item .env.example .env
# 如需缩短下载范围，编辑 configs\app.yaml 里的 market_data.history_start。
# 默认起点是 2017-08-17T00:00:00Z，对应 Binance 现货 BTCUSDT / ETHUSDT 日线的起点。
# history_start 为空时程序会拒绝下载，不会自行拉取无限历史。
uv run python scripts/init_db.py
uv run python scripts/collect_history.py
uv run python scripts/validate_data.py
uv run python scripts/run_backtests.py
uv run pytest
uv run streamlit run src/pquant/dashboard/app.py
```

### CMD

```cmd
uv sync
copy .env.example .env
uv run python scripts/init_db.py
uv run python scripts/collect_history.py
uv run python scripts/validate_data.py
uv run python scripts/run_backtests.py
uv run pytest
uv run streamlit run src/pquant/dashboard/app.py
```

`uv sync` 会创建 `.venv` 并按 `uv.lock` 安装依赖。`.env` 已被 git 忽略。程序只读取 `TRADING_MODE`、`LOG_LEVEL`、`PQUANT_CONFIG_DIR`，其它行（包括任何 Secret）不会进入配置。

采集命令只访问公开行情：

- 优先 `https://data-api.binance.vision`
- 主站连接失败或返回 5xx 时才改用 `https://api.binance.com`
- `GET /api/v3/klines`、`/api/v3/exchangeInfo`、`/api/v3/time`、`/api/v3/ticker/price`、`/api/v3/ticker/24hr`

这些限制以 2026-11-27 对照官方 `binance-spot-api-docs` 为准：K 线 weight 为 2，`limit` 最大 1000。HTTP 418 会停止请求。HTTP 429 读取 `Retry-After`。

## 结果在哪里

- 数据质量：`data/reports/data_quality/`
- 回测图表、交易明细、指标和配置快照：`data/reports/runs/`
- 汇总：`data/reports/summary.json` 和仓库根目录 `BACKTEST_REPORT.md`
- 本地数据库和原始 K 线在 `data/duckdb`、`data/sqlite`、`data/parquet`、`data/raw`，这些目录不提交。

回测规则：t 日收盘形成信号，t+1 日开盘成交。买入价 = 开盘价 × (1 + 滑点)，卖出价 = 开盘价 × (1 - 滑点)，手续费按成交名义金额扣除。基准成本是单边 0.10% 手续费和 0.05% 滑点，另有两档压力成本。这些是研究假设，不是你的 Binance 实际费率。

未收盘 K 线不进入历史回测。缺失 K 线不会用未来数据填补。Train / Validation / Test 按时间顺序切分，不随机打乱。

`CANDIDATE_FOR_PAPER` 只表示可以继续用模拟账户观察。它不是实盘许可，也不表示未来能够盈利。

## 测试

```powershell
uv run pytest
```

外部网络失败的集成测试会跳过，不把单元测试整组打成失败。合成数据只出现在测试里，并标记为 `synthetic`。正式回测报告拒绝把合成数据写成真实行情。

## 模式

| 模式 | v1 行为 |
|---|---|
| RESEARCH | 默认。下载行情、回测 |
| PAPER | 本地模拟账户，不访问下单接口 |
| TESTNET | 配置里可以显示，但 v1 不实现测试网下单 |
| LIVE | 代码直接拒绝启动 |

杠杆、做空、借币、提现、划转和真实下单都没有实现，也不能通过配置打开。
