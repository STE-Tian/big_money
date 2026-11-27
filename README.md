# Personal Quant AI

个人量化研究、历史回测、模拟交易和受限 AI 研究工作台。它不是自动盈利的交易机器人，也不会在 v1 发送真实订单。

Personal Quant AI is a local research workbench for Binance Spot public market data, historical backtests, paper trading, and a restricted research workflow. Live trading is disabled in code.

默认 `TRADING_MODE=research`。公开行情不需要 API Key，也不要填写 API Secret。

V1 仍是传统策略基线：日线、收盘发信号、下一根开盘成交。Big Money V2 的正式规格在 [`docs/V2_SPEC.md`](docs/V2_SPEC.md)。P1 是 1h/4h 现货与资金费率特征。P2 在这些特征上训练 Logistic 基线、LightGBM 和未标注 HMM，并用一条预先登记的只做多或持有现金规则，走 V1 的下一根开盘成交来和买入持有、SMA、动量、现金比较。P3–P6 的动态仓位、PBO/DSR、Agent 扩展和模拟盘没有实现。资金费率只作为研究特征，不交易永续合约。P2 的结论在 [`docs/V2_P2_REPORT.md`](docs/V2_P2_REPORT.md)。2025-01-18 至 2026-11-26 已经在 V1 中看过，不是 V2 的未触碰测试集。

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

## Big Money V2 P1

V1 的 `configs/app.yaml` 仍然只采集日线。V2 使用 `configs/v2_data.yaml`：默认研究模式，`LIVE` 关闭，不需要 API Secret。窗口长度写在该文件的 `features.windows` 里。

规格、特征公式和锁定约束见 [`docs/V2_SPEC.md`](docs/V2_SPEC.md)。P3–P6 仍未实现。

下面三组命令在 Windows PowerShell、CMD 和 Linux 上相同，只是换行符不同。先进入仓库根目录。`--start` 含该开盘时间，`--end` 不含该开盘时间，并且必须落在 1h 与 4h 的 UTC 边界上。

```powershell
uv run python scripts/collect_v2.py --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z
uv run python scripts/validate_v2.py
uv run python scripts/build_v2_features.py
```

- `collect_v2.py` 从 `data.binance.vision` 读取现货 K 线月度/日度档案和 USD-M 资金费率月度档案，并校验 SHA-256。现货缺口或较短窗口会再用 `data-api.binance.vision` 的公开 K 线交叉核对。资金费率查询只调用 `GET /fapi/v1/fundingRate`。
- `validate_v2.py` 根据本地 Parquet 重算质量报告。
- `build_v2_features.py` 生成点时刻特征。1d 相对强弱来自六根已收盘 4h K 线，不覆盖 V1 日线文件。

未收盘 K 线不进入特征。缺失 bar 不填补。特征在该 bar 收盘时才可用。HTTP 451 会记入报告，程序不会用代理绕过。若资金费率 REST 不可用，但已发布档案覆盖窗口，则使用档案。

原始 Parquet 在 `data/parquet/`，不提交。行数、校验和、数据集哈希与质量结论在 `data/reports/v2/p1_manifest.json`。2026-10 这一个窗口是流水线验收样本，不是全历史，也不是 V2 的未触碰测试集。

V1 日线回测命令保持原样，不需要先跑 V2。

## Big Money V2 P2

P2 读取上面的特征库，不重新实现成交。规则、网格、种子和证据门槛都在 `configs/v2_p2.yaml`，要在看到结果之前就写好。决策周期是 4h：信号在收盘形成，V1 引擎在下一根开盘成交。基准成本仍是单边手续费 0.001、滑点 0.0005，压力披露使用 `configs/costs.yaml` 的 `stress_2`。初始资金 10,000 USDT。仓位只有 0 或 1。

一个月的 P1 样本不够训练。下面这组窗口长于单月，并且落在 1h/4h 边界上。`--end` 不含该时刻。

```powershell
uv run python scripts/collect_v2.py --start 2020-01-01T00:00:00Z --end 2026-11-27T00:00:00Z
uv run python scripts/validate_v2.py
uv run python scripts/build_v2_features.py
uv run python scripts/train_v2_p2.py
```

`fapi.binance.com` 若返回 HTTP 451，采集会记下来并不绕过，资金费率改用 `data.binance.vision` 的月度档案。原始 Parquet 仍不提交。训练写出 `docs/V2_P2_REPORT.md`、`data/reports/v2/p2_summary.json`，以及被 git 忽略的 `data/reports/v2/p2_predictions.parquet`。

2020-01-01 至 2026-11-26 这一次运行里，预先登记的 LightGBM 规则没有通过证据门槛。它在 4h 样本上成交很多次，扣除基准成本后的净值远低于买入持有，也低于现金。研究结论是保持现金。这不是实盘建议。不要把与 2025-01-18 至 2026-11-26 重叠的一段写成新的测试集。数字、哈希和复现命令在 [`docs/V2_P2_REPORT.md`](docs/V2_P2_REPORT.md)。

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
