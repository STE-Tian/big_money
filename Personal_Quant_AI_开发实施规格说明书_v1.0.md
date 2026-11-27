# Personal Quant AI 开发实施规格说明书

**版本：** v1.0  
**文档用途：** 交给 Codex、Claude Code、OpenCode 等编码 Agent，作为项目实现的唯一需求基线  
**目标形态：** 个人使用的量化研究、历史回测、模拟交易与 AI 研究平台  
**首期交易范围：** Binance Spot 公共行情；不接实盘下单  
**首期标的：** BTCUSDT、ETHUSDT  
**首期周期：** 1d；数据层兼容 4h、1h  
**核心原则：** 先证明数据和回测可信，再讨论策略是否有价值；任何阶段都不能把回测盈利等同于未来收益。

---

## 0. 给编码 Agent 的总指令

请严格按本规格说明书开发，不要擅自扩大需求或跳过验收。

### 0.1 开发目标

交付一个能在 Windows 11 本地运行的 Python 项目，完成以下闭环：

1. 从 Binance 公共接口下载 BTCUSDT、ETHUSDT 历史现货 K 线。
2. 对行情数据进行标准化、校验、去重、增量更新，并保存到本地数据库/Parquet。
3. 实现至少三种基准策略：Buy & Hold、SMA 20/50、60 日动量。
4. 实现一个可复现、考虑手续费和滑点、不会使用未来数据的回测引擎。
5. 对真实下载的 BTC/ETH 历史数据执行回测、样本外验证、参数稳健性和成本敏感性分析。
6. 输出完整回测报告、净值曲线、回撤曲线、交易明细和数据质量报告。
7. 实现模拟账户和 Paper Broker，不连接真实下单接口。
8. 预留 LangGraph AI Research 工作流，但 Agent 只能调用白名单研究工具，不能自行下真实订单。

### 0.2 明确禁止

- 不得在第一版实现真实资金下单。
- 不得实现或启用杠杆、保证金、合约、做空、借币、提现、资金划转。
- 不得要求用户提供 API Secret；公开行情采集不需要 API Key。
- 不得将 API Key/Secret 写入源代码、日志、Notebook、测试数据或 Git。
- 不得编造回测结果、行情覆盖范围、收益率或夏普比率。
- 不得只写回测代码却不运行回测；必须尝试下载真实数据并运行。如果网络、地区、接口权限或其他条件阻断，必须明确报告阻断原因、实际完成范围和复现命令，不能用随机数据冒充真实行情。
- 不得在回测中用当根 K 线收盘价产生信号并假设同一收盘价成交。
- 不得用未来数据填补历史缺失值，不得随机打乱时间序列作为训练/测试切分。
- 不得让 LLM 直接生成并执行任意 Python 代码或访问任意交易所下单 API。

### 0.3 交付要求

编码 Agent 最终必须交付：

- 完整源码及项目目录。
- 安装与运行说明（Windows PowerShell 和 CMD 至少有一种完整方式，优先 PowerShell）。
- 数据库初始化与行情采集命令。
- 单元测试和集成测试。
- 真实历史数据回测报告（若网络允许）。
- 回测结果 CSV/JSON、图表 PNG/HTML、实验配置快照。
- `IMPLEMENTATION_STATUS.md`：逐项列出完成、未完成、失败和原因。
- `BACKTEST_REPORT.md`：不能只给结论，必须包含数据、方法、假设、指标和局限。

---

## 1. 项目定位与范围

### 1.1 产品定位

Personal Quant AI 是一套个人量化研究工作台，而不是自动承诺盈利的交易机器人。它将市场数据、策略研究、历史回测、模拟交易和 AI 研究流程整合在一起。

用户有 Python、SQL 和数据科学基础，但加密货币交易经验较少。系统需要让用户能看懂每个信号的来源、策略何时买入/退出、交易成本如何计算、策略在哪些市场阶段失效。

### 1.2 三阶段运行模式

| 模式 | 作用 | 是否访问真实账户 | 是否会产生真实订单 |
|---|---|---:|---:|
| RESEARCH | 下载行情、计算因子、策略回测 | 否 | 否 |
| PAPER | 使用虚拟资金进行模拟交易 | 否 | 否 |
| TESTNET | 验证交易所测试环境的 API 生命周期 | 使用独立测试密钥 | 否，使用测试环境虚拟资产 |
| LIVE | 未来预留，首期禁止实现 | 未启用 | 首期绝对禁止 |

所有模式必须在配置和 Dashboard 中显式显示。默认 `TRADING_MODE=research`，`LIVE` 模式在代码层被禁用，而不只是隐藏按钮。

### 1.3 首期范围

**包含：**
- Binance Spot 公共行情。
- BTCUSDT、ETHUSDT。
- 1d K 线优先，之后可扩展 4h、1h。
- 本地 DuckDB + SQLite + Parquet。
- 指标与三个基础策略。
- 历史回测、时间序列验证、成本压力测试。
- Paper Trading。
- Streamlit Dashboard。
- 受限的 AI 研究 Agent。

**首期不包含：**
- Futures、Margin、Options、杠杆和做空。
- 真实自动下单和实盘资金管理。
- 高频交易、盘口级回测、做市、跨交易所套利。
- 机器学习价格预测模型。
- 多用户 SaaS、云端部署、分布式消息队列。

---

## 2. 设计原则

1. **数据可信优先：** 数据完整性和时间对齐未通过时，不得运行正式回测。
2. **可复现：** 每个实验保存数据版本、数据哈希、策略版本、参数、成本配置、代码 Commit、时间范围和指标。
3. **时间因果正确：** 信号只使用当时已知的数据；成交只能发生在信号形成之后。
4. **成本真实透明：** 所有策略都必须计入手续费和滑点；成本参数清晰展示，不能默认为零。
5. **策略与交易所解耦：** 策略只输出目标仓位/信号，Broker 负责模拟执行。
6. **AI 不拥有资金控制权：** Agent 只能读取数据、选择已登记策略、发起回测和分析结果。
7. **默认安全：** 不需要账户权限时不申请密钥；首期不实现实盘下单。
8. **复杂度受控：** 采用模块化单体，不使用微服务、Kafka、Kubernetes 等首期不必要组件。

---

## 3. 技术选型

| 层次 | 推荐技术 | 说明 |
|---|---|---|
| 语言 | Python 3.11 或 3.12 | 选择依赖兼容且稳定的版本，并由 `uv.lock` 锁定 |
| 环境/依赖 | uv | 创建虚拟环境、锁定依赖 |
| HTTP | httpx | REST 请求、超时、连接池、重试 |
| WebSocket | websockets 或官方兼容客户端 | 首期可延后，优先完成 REST 历史数据 |
| 数据分析 | pandas、numpy | OHLCV 与因子计算 |
| 本地分析库 | DuckDB | K 线和回测分析数据 |
| 业务元数据 | SQLite | 实验、策略、Paper 订单、账户快照 |
| 文件数据 | Parquet | 原始/清洗后行情快照和实验输入 |
| 图表 | Plotly | 交互式行情和回测结果 |
| Dashboard | Streamlit | 个人量化工作台 |
| API（可选） | FastAPI | 若 Dashboard 与后台需要分离再引入；首期不是硬性要求 |
| Agent | LangGraph | P4 阶段再接入；不能阻塞 P0-P3 |
| 校验 | Pydantic | 配置、信号、实验参数和 API 数据结构 |
| 测试 | pytest | 单元、集成、回归测试 |
| 日志 | Python logging 或 structlog | 结构化日志，不记录密钥 |

**架构建议：** 第一版采用模块化单体。不要为了“看起来高级”一开始搭建一组微服务。

**回测引擎建议：** 第一版实现一个小而透明的自定义长仓/现金事件驱动引擎。可用 pandas/numpy 计算信号，但仓位、现金、成交、费用和净值必须按事件顺序记账。后续可再增加 vectorbt 等作为研究加速器，不能让第三方库掩盖成交时点和费用逻辑。

---

## 4. 总体架构

```text
                  ┌──────────────────────────────┐
                  │          Streamlit           │
                  │ Overview / Market / Research │
                  │ Backtest / Paper / AI / Risk │
                  └──────────────┬───────────────┘
                                 │
                  ┌──────────────▼───────────────┐
                  │       Application Layer      │
                  │ Use Cases / Services / Config│
                  └──────┬───────────────┬───────┘
                         │               │
           ┌─────────────▼─────┐   ┌─────▼─────────────────┐
           │ Data Engineering  │   │ Quant Research Engine │
           │ REST / WS / Clean │   │ Factors / Strategies  │
           │ Validation / Sync │   │ Backtest / Validation │
           └─────────────┬─────┘   └─────┬─────────────────┘
                         │               │
                  ┌──────▼───────────────▼─────┐
                  │      Storage Layer        │
                  │ DuckDB / SQLite / Parquet │
                  └──────────────┬────────────┘
                                 │
                  ┌──────────────▼─────────────┐
                  │    Paper Trading Engine   │
                  │ Portfolio / Orders / Fills │
                  │ Risk Checks / Audit Logs   │
                  └────────────────────────────┘

                  LangGraph Agent (P4)
                  只调用白名单 Research Tools
                  不具备真实下单权限
```

### 4.1 模块边界

- `exchange adapter`：只负责交易所协议、请求、响应映射。
- `ingestion`：负责分页、数据校验、去重、增量同步。
- `storage`：负责保存和读取，不包含策略逻辑。
- `research`：负责因子、策略、组合和回测。
- `execution`：负责 Paper Broker、订单状态和风控。
- `agents`：负责研究流程编排，不直接接触密钥和交易接口。
- `dashboard`：展示和触发应用服务，不直接访问 Binance。

---

## 5. Binance API 接入规格

> 实施时必须再次对照 Binance 官方文档确认端点、参数、权限、请求权重和测试环境地址。不要仅依赖本文件中的静态说明，因为交易所 API 文档可能变化。

### 5.1 公共行情接口

历史行情不需要 API Key。建议优先使用 Binance 官方 Market Data Only 地址：

- Public market-data base URL：`https://data-api.binance.vision`
- 可替代 REST base URL：`https://api.binance.com`
- 历史 K 线：`GET /api/v3/klines`
- 交易对规则：`GET /api/v3/exchangeInfo`
- 服务器时间：`GET /api/v3/time`
- 最新价格：`GET /api/v3/ticker/price`
- 24 小时行情：`GET /api/v3/ticker/24hr`

历史 K 线请求需要支持：`symbol`、`interval`、`startTime`、`endTime`、`limit`。必须分页下载，不得只下载最近一页。具体最大 limit 和请求权重必须从当期官方端点文档或 `exchangeInfo`/响应头确认，不要硬编码未经核实的限制。

建议第一期采集：

| symbol | market | interval | 用途 |
|---|---|---|---|
| BTCUSDT | Spot | 1d | 主研究标的 |
| ETHUSDT | Spot | 1d | 第二研究标的 |
| BTCUSDT | Spot | 4h | P2 后扩展 |
| ETHUSDT | Spot | 4h | P2 后扩展 |

### 5.2 分页与增量同步

采集器要求：

1. 从配置起始日期开始，按官方接口允许的最大页数分页。
2. 每次请求保存 `startTime`、`endTime`、HTTP 状态、请求时间、返回条数和数据区间。
3. 以时间戳作为幂等键，重复运行不产生重复 K 线。
4. 中断后可从数据库中最后一根已确认完整的 K 线继续。
5. 首次完整下载后，日常任务仅补充最近缺失区间。
6. 最近一根尚未收盘的 K 线标记为未完成，不参与回测。
7. 请求遇到 429 时读取 `Retry-After` 并退避；遇到 418 必须停止请求直到禁令时间结束，不能无限重试。
8. 5XX 或网络超时后有限次数指数退避；写订单类请求未来必须先查状态，不能盲目重发。首期只采行情，无订单写操作。
9. 记录 Binance 响应头中的使用权重信息，避免超限。

### 5.3 WebSocket（非 P0）

完成 REST 后再开发实时行情。要求：

- 支持 `kline` 和 `trade` stream；首期只需要 kline。
- 明确区分 K 线更新事件与 K 线最终收盘事件。
- 自动重连、心跳、Ping/Pong、连接轮换。
- 网络中断期间不能假设没有行情；重连后用 REST 回补缺失区间。
- 消息入库要幂等。
- WebSocket 只负责实时传输，不作为历史数据唯一来源。

官方 Spot Testnet WebSocket 文档写明测试环境连接有 24 小时有效期并有 Ping/Pong 要求；生产环境实施时以当期 Spot WebSocket Streams 文档为准。

### 5.4 API Key 与权限

当前 P0-P2 公共行情下载、因子、历史回测完全不需要 API Key。

若未来需要读取账户余额/私有订单历史，才考虑独立创建只读 API Key，并启用最小必要的账户数据读取权限。用户截图中曾经勾选 `Enable Spot & Margin & Stock Trading`；当前研究阶段应关闭交易权限。保持以下权限关闭：

- Spot/Margin/Stock Trading
- Futures
- Withdrawals
- Margin Loan/Repay/Transfer
- Universal Transfer
- Alpha Withdrawals
- Prediction Trading

不允许使用同一个密钥同时承担研究读取和未来交易。任何 Secret 不得进入 Agent 上下文、聊天记录、日志、数据库导出或 Git 仓库。

IP 白名单要求：只填写运行需要签名 API 请求的机器的公网出口 IP；`192.168.x.x` 等私有局域网 IP 不能作为 Binance 服务器看到的公网出口地址。若公网 IP 会变化，应采用安全、稳定的网络出口方案并人工更新白名单，不应直接关闭 IP 限制。

### 5.5 Spot Testnet

测试环境单独配置，不混用生产密钥：

- Spot Testnet REST base URL：实施时以 Binance Testnet REST API 文档为准，常见地址为 `https://testnet.binance.vision`。
- Spot Testnet WebSocket：官方文档示例为 `wss://stream.testnet.binance.vision/ws`。
- 仅 P5 用于验证订单创建、查询、撤销、状态更新和异常恢复。
- Testnet 的虚拟成交不等于真实市场流动性或滑点验证。
- Paper Trading 和 Testnet 是两件事：Paper 用本地模拟撮合验证策略；Testnet 用交易所测试环境验证 API 生命周期。

### 5.6 地区与账户适用性

程序的技术可连接性不等于账户、平台服务或交易行为必然适用于用户所在地。编码 Agent 不得通过代理、伪造地区、虚假身份或绕过交易所限制来实现账户访问。任何未来实盘功能之前，用户需自行确认账户所在地、身份、平台服务条款及当地监管要求。香港证监会持牌平台名单会更新，应以官方最新名单为准；本项目首期不依赖 Binance 实盘权限。

---

## 6. 数据标准与数据质量

### 6.1 统一 K 线结构

字段建议：

| 字段 | 类型 | 说明 |
|---|---|---|
| exchange | VARCHAR | binance |
| market_type | VARCHAR | spot |
| symbol | VARCHAR | BTCUSDT |
| interval | VARCHAR | 1d / 4h / 1h |
| open_time | TIMESTAMP UTC | K 线开始时间 |
| close_time | TIMESTAMP UTC | K 线结束时间 |
| open | DOUBLE | 开盘价 |
| high | DOUBLE | 最高价 |
| low | DOUBLE | 最低价 |
| close | DOUBLE | 收盘价 |
| volume | DOUBLE | 基础资产成交量 |
| quote_volume | DOUBLE | 计价资产成交额 |
| trade_count | BIGINT | 成交笔数 |
| taker_buy_base_volume | DOUBLE | 主动买入基础资产量 |
| taker_buy_quote_volume | DOUBLE | 主动买入计价资产量 |
| is_closed | BOOLEAN | 是否已收盘 |
| source | VARCHAR | 数据来源 |
| ingested_at | TIMESTAMP UTC | 入库时间 |

联合唯一键：`exchange + market_type + symbol + interval + open_time`。

金融金额、价格和数量在模拟撮合记账时优先使用 Decimal；用于统计计算时可转换为 float64。必须防止浮点累计误差造成余额或数量错误。

### 6.2 数据质量检查

每次同步必须输出 Data Quality Report，至少包含：

- 起止时间、总行数、收盘 K 线数量。
- 重复记录数量。
- 时间排序错误数量。
- 缺失 K 线时间点数量和比例。
- OHLC 合法性：`high >= max(open, close, low)`，`low <= min(open, close, high)`。
- volume、quote_volume 不得为负。
- 不得存在未来时间戳。
- 最新完整 K 线的延迟时间。
- 与上一版本数据相比是否发生修订。

加密市场通常连续交易，但如果历史数据存在缺失，不允许静默前向填充 OHLCV。回测应跳过不完整区间或将该实验标记为数据质量不合格，并在报告中披露。

### 6.3 时间处理

- 数据库统一保存 UTC。
- Binance 毫秒时间戳统一转换成 UTC。
- Dashboard 可转换为用户本地时间，但不修改原始时间。
- 日线边界以交易所 K 线定义为准，不能自行假定等于电脑本地零点。
- 所有回测结果必须注明时区和 K 线时间规则。

---

## 7. 数据库设计

建议三个逻辑域：Market（DuckDB）、Research（SQLite）、Trading（SQLite）。首期可将 Research 和 Trading 放在同一个 SQLite 文件中，但表和 Repository 必须分域。

### 7.1 Market 数据库（DuckDB）

核心表：`market_candles`

建议索引/排序方式：按 `symbol, interval, open_time` 排序；对常用查询字段建立适当索引（DuckDB 是否需要索引由查询性能测试决定）。

辅助表：

- `instruments`：交易对、状态、baseAsset、quoteAsset、价格精度、数量精度、最小数量和最小名义金额等规则快照。
- `ingestion_runs`：每次采集的开始结束时间、请求数、返回条数、失败数、数据区间。
- `data_quality_reports`：每次数据质量检查结论。
- `market_trades`：逐笔成交，首期可不实现。

### 7.2 Research 数据库（SQLite）

建议表：

**strategies**
- strategy_id（主键）
- name
- description
- asset_scope
- timeframe
- created_at
- status

**strategy_versions**
- version_id（主键）
- strategy_id（外键）
- version
- parameters_schema_json
- code_commit
- created_at

**research_experiments**
- experiment_id（主键）
- title
- hypothesis
- objective
- status
- created_at

**backtest_runs**
- run_id（主键）
- experiment_id
- strategy_version_id
- symbol
- interval
- start_time
- end_time
- dataset_hash
- config_json
- code_commit
- started_at
- completed_at
- status
- error_message

**backtest_metrics**
- run_id（外键）
- metric_name
- metric_value
- metric_unit
- segment（full/train/validation/test/walk_forward）

**optimization_runs**（P4 后可实现）
- optimization_id
- strategy_version_id
- search_space_json
- objective_metric
- result_json

**agent_runs**（P4 后可实现）
- agent_run_id
- task_type
- input_json
- output_json
- status
- started_at
- ended_at
- model_name
- prompt_version

### 7.3 Trading 数据库（SQLite）

**paper_accounts**
- account_id
- name
- quote_asset
- initial_cash
- current_cash
- created_at

**paper_orders**
- order_id（内部 UUID）
- client_order_id（幂等键）
- account_id
- strategy_id
- symbol
- side
- order_type
- quantity
- limit_price
- status
- created_at
- updated_at
- source_signal_id

**paper_fills**
- fill_id
- order_id
- fill_time
- fill_price
- fill_quantity
- fee_amount
- fee_asset
- slippage_amount

**paper_positions**
- account_id
- symbol
- quantity
- average_entry_price
- updated_at

**paper_equity_snapshots**
- account_id
- timestamp
- cash
- market_value
- total_equity
- unrealized_pnl
- realized_pnl

**risk_events**
- event_id
- account_id
- event_time
- severity
- event_type
- details_json
- action_taken

### 7.4 数据库约束

- 所有写入使用事务。
- 使用外键和唯一约束。
- `client_order_id` 必须唯一，防止重复下单/重复模拟成交。
- 每笔 Fill 必须能追溯到 Order 和策略信号。
- 回测 Run 必须记录配置快照、数据哈希和代码版本。
- 不保存 API Secret。

---

## 8. 策略规格（v1）

首期只研究现货多头/现金策略，不做做空，不使用杠杆。

### 8.1 策略 0：Buy & Hold 基准

- 第一个可交易时点买入并持有。
- 在测试区间末尾按最后可用价格估值；若生成完整成交日志，则末尾可按设置选择不强制平仓。
- 必须计入买入手续费和滑点；若回测强制平仓，则同样计入卖出成本。
- 作用：衡量复杂策略是否真正改善了持有体验。

### 8.2 策略 1：SMA 20/50 趋势策略

定义：
- `sma_short = SMA(close, 20)`
- `sma_long = SMA(close, 50)`
- 若 `sma_short > sma_long`，目标仓位为 100% 现货；否则目标仓位为 0%（现金）。
- 在 t 日收盘后计算信号。
- 最早于 t+1 根 K 线开盘执行仓位调整。
- 均线 warm-up 未完成前不交易。

### 8.3 策略 2：60 日动量

定义：
- `momentum_60 = close / close.shift(60) - 1`
- 若 `momentum_60 > 0`，目标仓位 100%；否则 0%。
- t 日收盘后产生信号，t+1 开盘执行。
- warm-up 期不交易。

### 8.4 后续可选策略（不阻塞 v1）

- 20/60/120 日动量对比。
- 价格高于 200 日均线的趋势过滤。
- 波动率目标仓位（必须先验证仓位计算和风险约束）。
- 多资产 ETF/加密资产轮动（需处理不同资产上市历史、时间戳和缺失数据）。

### 8.5 所有策略统一输出

策略接口应输出标准化信号，而不是直接下单：

- strategy_id
- strategy_version
- symbol
- signal_time
- target_weight（0 到 1）
- signal_value
- reason
- input_data_timestamp

所有策略都要通过同一个回测引擎和成本模型。

---

## 9. 回测引擎：必须正确的事项

这是本项目最重要的模块。回测验证结果的可信度优先于 Agent 和页面美观。

### 9.1 成交时点原则

**禁止未来函数/前视偏差：** t 日收盘数据形成的信号不能以 t 日收盘价成交。

v1 统一规则：

1. 使用 t 日已收盘 K 线计算信号。
2. 信号在 t 日收盘之后才有效。
3. 在 t+1 日开盘价附近模拟成交。
4. 买入成交价 = `next_open * (1 + slippage_rate)`。
5. 卖出成交价 = `next_open * (1 - slippage_rate)`。
6. 手续费 = `成交名义金额 * fee_rate`。
7. 仓位变化只有在目标仓位变化时才发生，不要每天无意义地重复交易。

### 9.2 记账模型

必须按资金和持仓记账：

- initial_cash
- cash_balance
- asset_quantity
- average_entry_price
- realized_pnl
- unrealized_pnl
- fee_paid
- trade_count
- equity

在每根 K 线开盘完成预定交易后，用该 K 线收盘价计算持仓市值和账户净值。

禁止出现：
- 现金余额小于允许误差的负数。
- 现货资产数量为负。
- 未计手续费就更新持仓。
- 同一信号重复生成多个订单。
- 回测结尾未解释的资金或资产余额差异。

### 9.3 成本配置

必须支持可配置：
- `fee_rate_per_side`
- `slippage_rate_per_side`

初始回测示例假设（仅为研究参数，不代表 Binance 对该用户的实际费率）：

| 情景 | 单边手续费假设 | 单边滑点假设 |
|---|---:|---:|
| Base | 0.10% | 0.05% |
| Stress 1 | 0.15% | 0.10% |
| Stress 2 | 0.20% | 0.20% |

这些值都必须可在配置文件中修改。正式报告必须把实际使用的值写出来。未来如有账户实际费率，应由用户自行确认后录入，不得从网络文章猜测。

### 9.4 初始资金

默认回测账户初始资金可以设为 10,000 USDT，纯粹作为统一实验基准，不代表建议用户投入该金额。所有策略和标的必须使用相同起始资金、时间区间、成本假设。

### 9.5 指标定义

至少计算：

| 指标 | 定义/要求 |
|---|---|
| Total Return | 期末净值 / 期初净值 - 1 |
| CAGR | 按实际日历天数年化；加密资产按 365 天计年 |
| Annualized Volatility | 日收益标准差 × sqrt(365) |
| Sharpe Ratio | 日超额收益均值 / 日超额收益标准差 × sqrt(365)；默认无风险利率为 0，但报告必须声明 |
| Sortino Ratio | 使用下行偏差计算，并明确目标收益基准 |
| Max Drawdown | `equity / equity.cummax() - 1` 的最小值 |
| Calmar Ratio | CAGR / abs(Max Drawdown)，回撤为 0 时需特殊处理 |
| Win Rate | 按已平仓交易计算盈利交易比例，并定义交易配对规则 |
| Profit Factor | 盈利交易总额 / 亏损交易绝对值总额；没有亏损交易时需标记不可比 |
| Exposure | 处于持仓状态的时间占比 |
| Turnover | 总成交名义金额 / 平均净值 |
| Trade Count | 成交/完整交易次数分别列示，不能混淆 |
| Fee Paid | 累计手续费 |
| Slippage Cost | 累计滑点成本 |

日线 Sharpe 年化因子使用 `sqrt(365)`；4h 数据使用 `sqrt(365*6)`。报告必须按周期选择，不得统一用 sqrt(252)。

### 9.6 Benchmark

每个策略至少与两个基准比较：
- Buy & Hold（同标的、同区间、同成本口径）。
- Cash（全程持有 USDT，收益按 0 处理，不包含理财收益）。

必须比较收益、最大回撤、波动率、Sharpe、市场暴露和交易成本。

---

## 10. 回测验证方案与验收门槛

### 10.1 数据范围

- 尽可能下载 BTCUSDT、ETHUSDT 在 Binance Spot 上可获得的完整日线历史。
- 以实际 API 返回的第一根、最后一根完整 K 线为准。
- 报告必须写出真实起止日期、K 线数量、缺失比例、数据下载日期。
- 若完整覆盖不足以支持合理样本外测试，必须标记 `INSUFFICIENT_HISTORY`，不能缩短区间后假装验证充分。

### 10.2 时间切分

使用时间顺序切分，禁止随机打乱：
- Train：最早 60%。
- Validation：接下来 20%。
- Test：最后 20%，锁定为最终样本外区间。

策略参数选择只能使用 Train/Validation。最终 Test 只运行一次并报告。若看过 Test 结果后继续修改参数，该 Test 不再是独立测试，必须重新定义新的未使用样本外区间。

所有特征使用过去数据。由于 SMA/动量需要 warm-up，切分边界处要确保特征计算只使用边界之前已知的历史数据；不要将未来数据回灌到过去。

### 10.3 Walk-forward

在数据足够时补充滚动验证：
- 训练窗口：至少 365 天；若历史数据更长，优先 2 年。
- 测试窗口：90 天。
- 每次向前滚动 90 天。
- 每个测试窗口仅使用此前历史训练/选择参数。
- 报告各窗口结果以及汇总，不只展示最好的窗口。

如果数据不足，明确报告未执行和原因。

### 10.4 参数稳健性

最少测试：
- SMA short window：10、20、30。
- SMA long window：50、100、150。
- 只测试 short < long 的组合。
- 动量 window：20、60、120。

参数网格只能在 Train/Validation 中比较；最终 Test 使用事先锁定的参数。

### 10.5 成本敏感性

每个策略至少跑 Base、Stress 1、Stress 2 三种成本情景。输出不同成本下的净收益、CAGR、Sharpe、最大回撤和交易次数。若扣除成本后优势消失，应明确说明策略不具备当前成本假设下的可行性。

### 10.6 市场阶段分析

按时间段展示结果，避免只看全样本总收益。至少：
- 按自然年份拆分。
- 按季度拆分（如数据足够）。
- 标出策略收益为负的区间。
- 展示最大回撤起止时间及恢复时间。

可做牛/熊/震荡阶段描述，但阶段分类规则必须预先定义，不能事后为了美化结果挑区间。

### 10.7 结果判断

不允许简单按最高历史收益选策略。报告应把策略归类为：

- `REJECT`：数据/实现测试失败，或样本外净收益、风险/成本表现明显不可接受。
- `INCONCLUSIVE`：样本不足、结果不稳定、不同区间差异大，或成本变化后结果翻转。
- `CANDIDATE_FOR_PAPER`：样本外结果在已设定成本下具有正向表现，且在参数和成本压力测试中没有明显崩塌，可进入模拟交易观察。

`CANDIDATE_FOR_PAPER` 不等于可以实盘，更不代表未来盈利。不得在文档或 Dashboard 使用“稳定盈利”“稳赚”等表述。

### 10.8 回测报告必须包含

1. 数据源、交易对、周期、时间范围、下载时间和数据哈希。
2. 数据质量报告、缺失值处理规则。
3. 策略逻辑和参数。
4. 成交时点、手续费、滑点和初始资金。
5. Train/Validation/Test 的所有关键指标。
6. Buy & Hold 和 Cash 对照。
7. 净值曲线、回撤曲线、年度/月度收益图。
8. 交易明细 CSV。
9. 参数稳健性矩阵。
10. 成本敏感性结果。
11. 主要失败案例和策略局限。
12. 明确结论：REJECT / INCONCLUSIVE / CANDIDATE_FOR_PAPER。

---

## 11. Paper Trading 模拟交易

### 11.1 目的

Paper Trading 验证策略信号、订单状态、资产记账和程序稳定性；它不是历史回测的替代品，也不能证明真实成交滑点。

### 11.2 订单状态

至少实现：
- CREATED
- SUBMITTED
- PARTIALLY_FILLED（若 v1 模拟支持部分成交）
- FILLED
- CANCELED
- REJECTED
- EXPIRED

每次状态变化记录事件日志。

### 11.3 撮合方式

- 对基于日线/4h 的策略，信号形成后在下一根 K 线开盘按配置滑点模拟成交。
- 如果后续做实时 Paper，使用可获得的 bid/ask 或 trade 数据制定更接近实时的模拟规则，并在报告中标明它不是交易所真实撮合。
- 所有订单都检查模拟账户余额和交易对精度规则。

### 11.4 幂等与恢复

- 同一个策略信号重复处理不能产生重复订单。
- 使用唯一 `client_order_id`。
- 程序重启后从数据库恢复账户、订单和持仓状态。
- 订单状态和 Fill 记账必须在事务内保持一致。

### 11.5 Paper 阶段观察时间

建议至少连续观察 60–90 天，且需要有足够多的有效交易样本；低频策略可能需要更久。观察期间记录数据中断、信号延迟、模拟滑点和策略回撤。未达到预设样本量时不能宣称验证完成。

---

## 12. AI Agent 设计（P4）

Agent 的作用是自动组织研究，而不是替代确定性回测和风控程序。

### 12.1 角色

**Supervisor Agent**
- 接收用户研究问题。
- 调用 Planner、Backtest Evaluator、Risk Analyst、Report Agent。
- 管理研究状态和终止条件。

**Research Planner Agent**
- 将研究问题转换为结构化 Experiment Spec。
- 只能选择策略注册表中已存在的策略。
- 参数范围必须通过 Pydantic Schema 校验。

**Backtest Evaluator Agent**
- 只能调用回测工具和读取实验指标。
- 不得修改成交规则或数据。
- 说明实验结果的局限。

**Risk Analyst Agent**
- 分析回撤、波动、成本、集中度和异常。
- 风控结果来自确定性指标，不允许 LLM 虚构数值。

**Report Agent**
- 基于数据库结果生成报告。
- 每个数字必须能追溯到 `run_id`。

### 12.2 Agent 白名单工具

允许：
- `list_available_symbols()`
- `get_market_data()`
- `get_data_quality_report()`
- `list_strategies()`
- `create_experiment()`
- `run_backtest()`
- `compare_backtests()`
- `get_risk_metrics()`
- `generate_report()`

禁止：
- `place_live_order()`
- `cancel_live_order()`
- `withdraw_asset()`
- `transfer_asset()`
- `modify_api_permissions()`
- 读取任意环境变量或密钥。
- 未经白名单审核执行 Agent 生成的 Python/SQL/Shell 代码。

### 12.3 Agent 的实验输入

结构化字段至少有：
- objective
- symbols
- timeframe
- date_range
- strategy_ids
- parameter_space
- fee_scenario
- slippage_scenario
- validation_scheme
- experiment_budget（最大实验次数/运行时间）

Agent 不能自行扩大标的范围、交易品种或权限。

---

## 13. Dashboard 页面要求

首期页面：

### Overview
- 系统模式：RESEARCH / PAPER / TESTNET。
- 数据最后更新时间。
- 数据质量状态。
- 最近一次回测状态。
- 当前 Paper 账户资产和累计收益。

### Market
- BTC、ETH K 线。
- 成交量。
- SMA20/SMA50。
- 60 日动量。
- 时间范围和周期筛选。

### Strategy Lab
- 策略列表、策略说明、参数。
- 生成实验。
- 实验运行状态。
- 策略版本历史。

### Backtesting
- 净值曲线。
- 回撤曲线。
- 指标卡片。
- Benchmark 对照。
- Train/Validation/Test 切分结果。
- 参数稳健性和成本敏感性。
- 交易明细下载。

### Paper Trading
- 模拟账户余额、持仓、净值。
- 订单列表和状态。
- 成交明细。
- 风险事件。

### AI Research（P4）
- 研究任务输入。
- Agent 节点状态。
- 实验摘要。
- 引用的 run_id 和报告。

### Settings
- 数据源配置。
- 数据采集区间。
- 成本假设。
- 系统模式。
- 不展示 Secret 明文。

所有收益、风险指标必须显示统计时间范围和数据来源；没有数据时显示“暂无结果”，不能显示示例数字伪装成真实结果。

---

## 14. 项目目录建议

```text
personal-quant-ai/
├── pyproject.toml
├── uv.lock
├── README.md
├── .env.example
├── .gitignore
├── configs/
│   ├── app.yaml
│   ├── data.yaml
│   ├── universe.yaml
│   ├── strategies.yaml
│   ├── costs.yaml
│   └── risk.yaml
├── data/
│   ├── raw/
│   ├── processed/
│   ├── parquet/
│   ├── duckdb/
│   └── reports/
├── src/
│   └── pquant/
│       ├── __init__.py
│       ├── config/
│       ├── domain/
│       ├── adapters/
│       │   └── exchanges/
│       │       └── binance/
│       ├── ingestion/
│       ├── storage/
│       ├── research/
│       │   ├── factors/
│       │   ├── strategies/
│       │   ├── backtest/
│       │   └── validation/
│       ├── execution/
│       │   ├── brokers/
│       │   ├── portfolio/
│       │   └── risk/
│       ├── agents/
│       ├── dashboard/
│       │   └── pages/
│       ├── jobs/
│       └── utils/
├── scripts/
│   ├── init_db.py
│   ├── collect_history.py
│   ├── validate_data.py
│   └── run_backtests.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── backtest_regression/
│   └── fixtures/
└── docs/
    ├── architecture.md
    ├── database.md
    ├── api_integration.md
    ├── backtest_methodology.md
    └── operations.md
```

数据目录、数据库文件、API Secret、日志中的私人信息默认必须加入 `.gitignore`。

---

## 15. 配置规格

建议配置项（具体 YAML 结构由实现者落地）：

```yaml
app:
  name: Personal Quant AI
  environment: development
  trading_mode: research
  live_trading_enabled: false

market_data:
  exchange: binance
  market_type: spot
  base_url: https://data-api.binance.vision
  symbols: [BTCUSDT, ETHUSDT]
  intervals: [1d]
  history_start: null  # 首次运行由用户设置；null 时提示用户，不擅自下载无限范围
  exclude_open_candle: true

backtest:
  initial_cash: 10000
  quote_asset: USDT
  annualization_days: 365
  signal_execution: next_bar_open
  default_fee_rate_per_side: 0.001
  default_slippage_rate_per_side: 0.0005
  risk_free_rate: 0.0

validation:
  train_ratio: 0.60
  validation_ratio: 0.20
  test_ratio: 0.20
  walk_forward_train_days: 365
  walk_forward_test_days: 90

safety:
  allow_leverage: false
  allow_short: false
  allow_withdrawals: false
  allow_live_orders: false
  require_paper_mode: true
```

上述手续费和滑点只是回测起始假设，不是 Binance 对该用户的实际费率。需要在报告和 UI 中清楚显示。

---

## 16. 测试要求

### 16.1 单元测试

**数据层：**
- 时间戳毫秒转 UTC 正确。
- OHLC 合法性校验正确。
- 重复 K 线按唯一键去重。
- 未收盘 K 线不会进入回测输入。
- 分页边界不会漏行或重复行。

**因子层：**
- SMA 计算与手工小样本结果一致。
- Momentum 计算与手工小样本结果一致。
- warm-up 区间输出为空/NaN，不产生交易信号。

**回测层：**
- t 收盘形成的信号只能在 t+1 开盘执行。
- 手续费增加时，在同样成交条件下净收益不应提高。
- 滑点增加时，在同样成交条件下净收益不应提高。
- 目标仓位未改变时不会重复交易。
- 不会出现负现金或负现货数量。
- 所有 Fill 的金额、手续费、现金变化可以手工复算。
- 最大回撤公式与手工构造净值序列一致。
- Benchmark 使用相同开始时间、初始资金和成本口径。

**Paper Broker：**
- 订单状态流转合法。
- 重复 signal 不重复创建 order。
- 重启后可恢复账户状态。
- 成交、手续费、持仓、现金和净值一致。

### 16.2 防止未来数据泄漏的回归测试

构造一组固定历史数据：
1. 运行策略并保存 t 之前的信号。
2. 任意修改 t 之后的价格数据。
3. 重新计算。
4. t 之前的信号必须完全不变。

若未来数据变化会影响过去信号，测试失败。

### 16.3 集成测试

- 连接 Binance 公共行情接口。
- 下载一个小时间范围的 BTCUSDT K 线。
- 保存后重新读取，行数、时间范围和关键字段一致。
- 对下载数据运行一个策略和回测。
- 生成 JSON/CSV/PNG 报告。

网络集成测试应区分“本地代码失败”和“外部 API 不可达”。外部网络失败不得导致单元测试整体不可运行。

---

## 17. 开发阶段与里程碑

### P0：工程底座

交付：项目可安装、可启动、配置加载、日志、DuckDB/SQLite 初始化、基础测试。  
验收：一条命令初始化数据库；pytest 基础测试通过。

### P1：真实行情数据

交付：Binance REST 历史 K 线分页、增量更新、数据质量报告、Parquet 导出。  
验收：BTCUSDT、ETHUSDT 至少能下载用户指定时间范围；重复运行幂等；数据质量报告真实生成。

### P2：回测与验证（最高优先级）

交付：三种策略、事件驱动回测、成本模型、指标、时间切分、Walk-forward（数据足够时）、稳健性与成本压力测试、报告。  
验收：所有回测单元测试通过；真实数据回测至少成功运行一次；否则给出明确网络阻断报告。

### P3：Paper Trading

交付：模拟账户、订单、成交、持仓、净值、风险事件、恢复机制。  
验收：能够连续模拟，且账务对得上；无重复订单。

### P4：AI Research Agent

交付：LangGraph Supervisor 和研究 Agent，工具白名单，实验自动编排和报告。  
验收：Agent 能按用户任务调用既有策略和回测，不能绕过校验；不能访问密钥或真实交易接口。

### P5：Spot Testnet API 生命周期（可选）

交付：单独 Testnet Adapter、订单/撤单/查询/状态同步测试。  
验收：使用独立 Testnet Key 完成测试环境订单全流程；生产账户无交易权限。

### P6：实盘可行性评估（不属于首期开发）

只有当用户明确提出且所在地、账户与平台服务适用性已确认后，才单独设计。不能由 Agent 自动开启。必须另行做安全评审、风险限额、人工确认、Kill Switch 和最小资金测试。

---

## 18. 首次运行流程（面向用户）

编码 Agent 应在 README 写清楚以下流程：

1. 安装 Python 和 uv。
2. 克隆/进入项目目录。
3. 安装依赖。
4. 复制 `.env.example` 为 `.env`（首期不需要填 Binance Secret）。
5. 修改 `configs/app.yaml` 中历史起始日期和标的。
6. 初始化数据库。
7. 下载 BTC/ETH 历史 K 线。
8. 运行数据质量检查。
9. 运行基准回测。
10. 查看 `data/reports/` 下的报告和图表。
11. 启动 Streamlit Dashboard。

README 至少提供以下命令的实际实现版本：

- `uv sync`
- 数据库初始化命令
- 历史数据采集命令
- 数据质量检查命令
- 回测命令
- 测试命令
- Dashboard 启动命令

不要只写伪命令；必须与实际项目入口一致。

---

## 19. 最终验收清单

### 必须完成

- [ ] Windows 本地环境能安装和启动。
- [ ] DuckDB、SQLite 正常初始化。
- [ ] Binance 公共接口能获取 BTCUSDT、ETHUSDT 历史 K 线。
- [ ] 历史采集支持分页、断点续传、去重和增量更新。
- [ ] 未收盘 K 线不会用于历史回测。
- [ ] 数据质量报告包含完整性和缺失情况。
- [ ] Buy & Hold、SMA 20/50、60 日动量可运行。
- [ ] 成交时点采用下一根 K 线开盘。
- [ ] 手续费和滑点已纳入每次交易。
- [ ] 指标计算有单元测试。
- [ ] 有未来数据泄漏回归测试。
- [ ] 有 Train/Validation/Test 时间切分。
- [ ] 有成本敏感性和参数稳健性报告。
- [ ] 有 BTC、ETH 的真实历史数据回测结果，或明确记录真实数据下载失败的原因。
- [ ] 回测报告给出策略结论及局限，不宣称保证盈利。
- [ ] Paper Trading 不访问真实下单 API。
- [ ] Agent 只能调用白名单研究工具。
- [ ] Git 仓库中没有密钥和本地数据文件。

### 通过标准

代码测试通过只是工程正确性的证据，不是策略有效性的证据。策略研究结论必须来自真实行情、正确成交时点、成本扣除、样本外测试和稳健性分析。

---

## 20. 交给编码 Agent 的执行提示词

下面这段可以直接复制给 Codex / Claude Code / OpenCode：

> 请阅读并严格遵循 `Personal_Quant_AI_开发实施规格说明书_v1.0.md`。你负责把规格落地为一个可运行的 Python 项目。先检查工作目录和现有文件，不要覆盖已有用户文件。按 P0 → P1 → P2 → P3 → P4 的顺序开发，不要先做漂亮 UI 或 Agent 而跳过数据和回测。
>
> 第一优先级是 P1 数据工程与 P2 回测验证。必须接入 Binance 公共行情 API 下载真实 BTCUSDT、ETHUSDT K 线，生成数据质量报告；然后实现 Buy & Hold、SMA 20/50、60 日动量三个基准策略。回测必须使用 t 日收盘产生信号、t+1 日开盘成交，计入单边手续费与滑点，提供 Buy & Hold 和 Cash benchmark，严格按时间切分 Train/Validation/Test，执行参数稳健性和成本敏感性分析。
>
> 不允许编造回测结果。如果外部 API 无法访问，记录真实 HTTP/网络错误和复现步骤；不要用随机数或人工构造数据冒充真实 BTC/ETH 回测。可以用合成数据进行单元测试，但报告必须标记为 synthetic test，不得与真实历史回测混淆。
>
> 开发完成后运行 pytest、数据采集、数据校验、真实回测和 Dashboard 启动检查。输出 `IMPLEMENTATION_STATUS.md`、`BACKTEST_REPORT.md`、指标 CSV、交易明细 CSV、净值/回撤图和实验配置快照。最终回复列出实际运行过的命令、通过/失败的测试、真实数据的时间范围和行数、回测结果及尚未完成的部分。
>
> 第一版禁止实盘下单；禁止合约、杠杆、提现、转账。不要向用户索取 API Secret。公开行情不需要 API Key。Agent 不能调用任何真实交易写接口。

---

## 21. 官方参考文档

实现时应以官方文档的最新内容为准：

1. Binance Developer Documentation：<https://developers.binance.com/en/docs/introduction>
2. Binance Spot REST API General Information：<https://developers.binance.com/en/docs/products/spot/rest-api>
3. Binance Spot Market Data Only：<https://developers.binance.com/en/docs/products/spot/market-data-only>
4. Binance Spot API Reference / Market Data Endpoints：从官方 Spot REST API 页面进入，确认 Kline、Exchange Information、Server Time 等具体端点及限制。
5. Binance Spot WebSocket Streams：<https://developers.binance.com/en/docs/products/spot/web-socket-streams>
6. Binance Spot Testnet：<https://testnet.binance.vision/>
7. Binance Spot Testnet WebSocket Streams：<https://developers.binance.com/zh-CN/docs/products/spot/testnet/web-socket-streams>
8. Hong Kong SFC Virtual Asset Trading Platforms Lists：<https://www.sfc.hk/en/Welcome-to-the-Fintech-Contact-Point/Virtual-assets/Virtual-asset-trading-platforms-operators/Lists-of-virtual-asset-trading-platforms>

**文档核对日期：** 2026-11-27。API 端点、请求权重、测试环境、权限和地区规则可能变化；编码 Agent 必须在实现时复核官方文档。

---

## 22. 本项目的成功定义

v1.0 的成功不是“AI 帮我赚到钱”，而是：

- 能可靠获取和管理真实行情数据；
- 每一个回测都能复现；
- 每一笔模拟交易都能解释；
- 能识别未来数据泄漏、过拟合和成本侵蚀；
- 能用样本外结果判断某个策略是否值得继续观察；
- AI 能减少研究和实验整理工作，但无法绕开确定性风控。

只有在这些条件都满足后，才有资格讨论实盘。即使历史回测和模拟交易表现良好，也不构成未来盈利保证。
