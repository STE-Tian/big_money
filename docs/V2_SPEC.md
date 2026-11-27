# Big Money V2 规格说明书

版本：V2 规格全文。代码实现范围为 P1 与 P2。  
日期：2026-11-27。  
状态：P1 数据基础设施与 P2 预测研究已实现。P2 包含 Logistic 基线、LightGBM、未标注 HMM、训练期的 Purging 与 Embargo，以及一条预先登记的只做多或持有现金规则。P3–P6 仍只写在规格中：没有均值-方差动态仓位、没有 PBO/DSR 计算、没有扩展 Agent，也没有打开模拟盘或实盘。P2 的运行结果见 `docs/V2_P2_REPORT.md`。

本文是 Big Money V2 的正式规格。V1 规格仍以仓库中的 V1 文档和 `README.md` 为准。两者冲突时：V1 回测成交与记账以 V1 代码和本文第 3 节为准；V2 研究模块以本文为准。本文中的锁定约束不得被后续实现削弱。

---

## 1. 目标

V2 把项目定义为 **Regime-Aware AI Quant System**：市场状态识别、机器学习预测和动态风险管理。它不再只回答“今天买还是卖”，而是回答四个问题：

1. 现在是什么市场环境？
2. 未来几个小时或几天，收益分布可能是什么？
3. 预测收益是否足以覆盖交易成本？
4. 当前风险水平允许投入多少资金？

系统必须被允许得出这个结论：**当前没有统计证据证明存在足够的交易优势，因此保持现金，不交易。** 预测准确率不是最终目标。扣除真实交易成本之后的策略收益，以及该收益是否经得起多重检验，才是目标。

五个核心系统都在本文定义。本轮只实现第 1 个系统的数据基础设施：

| 系统 | 职责 | 本轮 |
| --- | --- | --- |
| 数据科学层 | 点时刻市场特征库 | 实现 P1 |
| AI 模型层 | 未标注状态概率、收益预测、波动率预测 | 只写规格 |
| 交易决策层 | 由净收益、置信度和波动率得到 0%–100% 目标仓位 | 只写规格 |
| 量化风险层 | 独立于交易信号的风险状态机 | 只写规格 |
| Agent 层 | 组织研究，不直接下单、不挑选“最好看”的回测 | 只写规格 |

---

## 2. 与 V1 的关系

V1 继续是传统策略基线，默认研究 BTCUSDT、ETHUSDT 现货日线。V1 回测规则不变：

- 信号在 bar \(t\) 收盘形成；
- 成交在 bar \(t+1\) 开盘；
- 买入价 = 开盘价 × (1 + 滑点)，卖出价 = 开盘价 × (1 − 滑点)；
- 手续费按成交名义金额扣除一次，滑点已经含在成交价里，不第二次扣除；
- 目标权重只允许落在 \([0, 1]\)；
- 未收盘 K 线不能进入历史回测；
- 样本末端的信号没有下一根 K 线，因此不会在样本内成交。

这些语义由 `src/pquant/research/backtest/engine.py` 和 `accounting.py` 实现。V2 不得修改成交价、费用或滑点公式，也不得把 V2 特征写入 V1 日线 Parquet。V2 从 4h 收盘 K 线汇总出来的 1d 序列只存在于特征文件中，路径与 `binance_spot_{SYMBOL}_1d.parquet` 分开。

后续任何一个 AI 模型都必须和 V1 的买入持有、SMA、动量、现金基准做**同口径比较**：同一时间切分、同一成本假设、同一“收盘发信号、下一根开盘成交”的执行规则。不允许只展示 AI 自己最好看的收益曲线。Logistic 回归是 LightGBM 之前必须存在的线性基线。

`configs/app.yaml` 的默认周期仍然是 `1d`。1h 和 4h 由 `configs/v2_data.yaml` 单独采集。

---

## 3. 禁止事项与推荐主路径

不作为本项目主路径，也不在本轮实现：

- 用 LSTM 预测 BTC 明天涨跌，并据此作为主要交易系统；
- 用 Transformer 预测价格，并据此作为主要交易系统；
- 让大模型读新闻后直接决定买卖；
- 让 Agent 自己改参数，再把回测收益最高的那一次称为盈利策略。

推荐主路径是：LightGBM + HMM + 风险预测 + 动态仓位 + 成本敏感型交易 + 严格统计验证。XGBoost 可以作为树模型的对照，但不能跳过 Logistic 回归基线，也不能替代验证。

交易范围同时禁止：

- 交易永续合约，包括用资金费率或主动买卖量作为下单依据去交易合约；
- 做空、杠杆、借贷、兑出、划转；
- 打开 `LIVE`，或在代码里发送实盘订单；
- 把 API Secret 写入源码、配置、日志、notebook、测试或 git。

公开行情不使用 API Key。程序只读取 `TRADING_MODE`、`LOG_LEVEL`、`PQUANT_CONFIG_DIR`。Secret 名称会被忽略，不会进入配置。

---

## 4. 已锁定的设计约束

以下九条已经确认。实现可以增加检查，不能放宽。

1. **交易范围。** 资金费率和主动买卖量只作为现货研究特征。不交易永续合约，不做空，不加杠杆，不借贷，不兑出，不划转。`LIVE` 保持关闭。不要求、不写入 API Secret。
2. **HMM。** 先输出未标注的隐状态概率。牛市、熊市、震荡、高波动只能是事后解释，不能在训练前把状态硬编码成这四个名字。状态个数在训练集上用预先登记的准则选择。
3. **仓位（P3，仅规格）。** 预期净收益 \(\hat\mu_{net,t}\) 和预测波动率 \(\hat\sigma_t\) 必须是同一预测期限。风险厌恶系数 \(\lambda\)，以及 0% / 25% / 50% / 75% / 100% 这些离散档，都是待回测参数，不是现在就能使用的默认仓位。
4. **样本与泄漏。** 1h / 4h 增加 K 线根数，但重叠标签使有效样本远小于根数。Purging 和 Embargo 从 P2 训练开始就做，不留到 P4。
5. **比较基准。** V2 与 V1 的买入持有、SMA、动量、现金基准使用同一套协议。LightGBM 之前必须先有 Logistic 回归基线。
6. **测试集污染。** 2025-01-18 至 2026-11-26 的 V1 测试结果已经被看过。这段数据可以用于 V2 模型开发，不能再被称作 V2 的全新、未触碰测试集。真正的前向样本来自之后的模拟盘。
7. **DSR 的含义。** “DSR 不低于 95%”表示：在扣除策略筛选偏差和非正态影响之后，夏普比率为正的把握不低于 95%。它不是盈利保证，也不是行业通用承诺。
8. **Agent 原则。** LLM 负责提出假设、组织实验、分析结果。特征、训练、仓位、成交和风控必须由可复现的 Python 程序执行。模型不能随意改策略参数，再把自己挑出的最好结果称为盈利策略。
9. **风控与执行分离。** 模型认为行情不好，不等于一定要卖出。风控系统认为账户面临不可接受的损失时，可以独立暂停新交易。V1 的回撤标签只是模拟观察，不是已经实现的自动平仓。V2 必须把风险状态机和实际执行动作分开实现、分别测试。

---

## 5. 五个系统

### 5.1 数据科学层（P1，本轮实现）

采集并对齐 BTCUSDT、ETHUSDT 的现货 1h / 4h K 线，以及对应 USD-M 永续合约的资金费率历史。资金费率只作为特征。特征在 bar 收盘时点可得，写入 Parquet 特征库。细节见第 6–8 节。

### 5.2 AI 模型层（P2，已实现；GARCH 未拟合）

顺序固定：

1. **Logistic 回归基线**必须先存在。
2. **模型 B：LightGBM / XGBoost。** 输入是点时刻市场特征，输出未来 4h、12h、24h 的预期收益、收益为正的概率，以及预测不确定性。可以用 SHAP 解释，解释结果不是交易许可。
3. **模型 A：HMM。** 输出各隐状态的概率，不输出“牛/熊/震荡/高波动”这四个硬编码名字。名称只能在事后解释里出现。
4. **模型 C：EWMA / GARCH。** 预测波动率，服务于风险预算。预测期限必须和仓位公式里的收益期限一致。

不把 Transformer 当作第一模型。标签如果使用未来 \(H\) 根 K 线的收益，训练样本中与验证/测试标签窗口重叠的部分必须 Purging，并在切分边界加 Embargo。这是 P2 的训练要求，不是 P4 才补上的报告步骤。

P2 代码按上面的顺序实现了 Logistic 基线、LightGBM 和未标注 HMM。预期收益、收益为正的概率和预测不确定性都有输出。LightGBM 使用树 SHAP（`pred_contrib`），并用 `shap.TreeExplainer` 做对照。GARCH/EWMA 仍未拟合，也不进入仓位。P2 的交易规则只是预先登记的 0/1 成本过滤，不是第 5.3 节的动态仓位。运行记录见 `docs/V2_P2_REPORT.md`。

### 5.3 交易决策层（P3，不实现）

从“预测上涨就 100% 买入，预测下跌就 100% 卖出”改为：

预测净收益 + 预测置信度 + 同一期限的预测波动率 → 风险预算 → 目标持仓比例。

比例可以是 \([0, 1]\) 上的连续值，也可以是研究用离散档。离散档是否采用、档位如何划分，由回测决定，不预置为默认策略。

弱于交易成本的预测，目标仓位为 0。强看跌时目标仓位也是 0。V2 第一阶段仍然只做多或持有现金，不做空。

示例公式，其中 \(\lambda\) 不是默认值：

\[
w_t=\operatorname{clip}\left(\frac{\hat\mu_{net,t}}{\lambda\hat\sigma_t^2},0,1\right)
\]

\(\hat\mu_{net,t}\) 是扣除成本后的预期收益，\(\hat\sigma_t\) 是同一期限的预测波动率。两者期限不同则公式无效。\(w_t<0\) 在第一阶段不解释为做空，而是被截断到 0。

### 5.4 量化风险层（P3/P4，不实现）

独立模块。风险控制不塞进交易信号：

- Volatility Targeting
- Maximum Drawdown Control
- CVaR
- Position Limit
- Cost-aware Filter：预测收益不足以覆盖成本时不交易
- Circuit Breaker：异常行情停止新开仓

风险状态机输出的是风险状态。执行层另行决定是否允许新交易、是否减仓。两个模块分开测试。模型看空不等于风控已经平仓；风控暂停也不需要等待模型改口。

### 5.5 Agent 层（P5，不实现）

建议角色：Supervisor、Data、Feature、Alpha、Risk、Validation、Execution。最终产品是研究报告，不是订单。

原则见第 4 节第 8 条。Agent 可以提议特征、窗口和实验设计；计算、训练、仓位、成交和风控仍由版本化的 Python 程序执行，并留下配置哈希、数据哈希和代码版本。

---

## 6. 数据字典

研究交易标的只有现货 BTCUSDT 与 ETHUSDT。USD-M 永续合约不是交易标的。

| 数据 | 周期 | 市场 | 用途 | 本轮 |
| --- | --- | --- | --- | --- |
| K 线 OHLCV | 1h、4h | 现货 | 价格、成交量 | 采集 |
| 成交笔数 `trade_count` | 1h、4h | 现货 | 活跃度 | 采集 |
| 主动买入成交量 `taker_buy_base_volume`、`taker_buy_quote_volume` | 1h、4h | 现货 | 买卖力量 | 采集 |
| 主动卖出成交量 | 1h、4h | 现货 | 由总量减主动买入，不另请求 | 计算 |
| 资金费率事件 | 事件时间，不假定永远是 8 小时 | USD-M 永续，仅特征 | 多空情绪 | 采集 |
| ETH/BTC 相对强弱 | 4h、1d | 现货 | 相对表现 | 由收盘 K 线计算 |
| 滚动历史波动率 | 1h、4h，以及由 4h 汇总的 1d | 现货 | 风险研究 | 计算 |

1d 相对强弱和 1d 波动率使用完整的 UTC 日。一个 UTC 日只有在 00:00、04:00、08:00、12:00、16:00、20:00 六根 4h K 线都已收盘时才生成。缺任意一根则该日不生成，不用别的价格填上。这个 1d 序列不替换 V1 日线。

### 6.1 现货 K 线字段

与 V1 `Candle` 相同，来源是 Binance 现货 K 线：

| 字段 | 含义 |
| --- | --- |
| open_time / close_time | K 线开盘、收盘时间，UTC |
| open, high, low, close | 价格，按十进制字符串保存 |
| volume / quote_volume | 成交量、成交额 |
| trade_count | 成交笔数 |
| taker_buy_base_volume / taker_buy_quote_volume | 主动买入量、主动买入额 |
| is_closed | 交易所时间已经晚于 close_time |
| source / ingested_at | 来源与入库时间 |

主动卖出基础资产量 = `volume - taker_buy_base_volume`。主动卖出计价资产量同理。若主动买入为负，或大于对应总量，该行不合格，不平衡特征为空，不用 0 代替。

### 6.2 资金费率字段

资金费率行的类型是 `FundingEvent`。`research_feature_only` 恒为真，`tradable` 恒为假。它不是 K 线，不能进入 V1 回测引擎。

| 字段 | 含义 |
| --- | --- |
| symbol | BTCUSDT 或 ETHUSDT，表示对应永续合约的费率，不表示要交易该合约 |
| funding_time | 资金费事件时间，UTC，保留毫秒偏移 |
| funding_rate | 该次资金费率，可为负 |
| funding_interval_hours | 档案里公布的间隔；REST 历史行没有该字段时为空，不默认写成 8 |
| mark_price | REST 若给出非空标记价格则保存；空字符串保持为空，不写成 0 |
| rate_type | 若 REST 提供则保存，例如 Regular；不参与下单 |
| source | `binance_vision:` 档案或 `binance_fapi:` 查询 |

负费率是合法数据。重复的 `funding_time` 不合格。间隔大于该行自己公布的 `funding_interval_hours` 加 1 分钟容差时，记为缺口并披露，不插值。

### 6.3 数据源

2026-11-27 对照官方材料后使用下列来源，不臆造端点：

- 现货 REST：优先 `https://data-api.binance.vision`，连接失败或 HTTP 5xx 时才回退 `https://api.binance.com`。K 线为 `GET /api/v3/klines`，`limit` 最大 1000，weight 为 2。这与 V1 相同。
- 公开档案：`https://data.binance.vision`。现货 K 线为 `/data/spot/{monthly|daily}/klines/{SYMBOL}/{INTERVAL}/{SYMBOL}-{INTERVAL}-{DATE}.zip`。USD-M 资金费率只有月度档案：`/data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY-MM}.zip`。每个 zip 必须通过同目录 `.CHECKSUM` 的 SHA-256。HTTP 404 表示尚未发布，不是一条伪造的行情。
- 现货档案时间戳：Binance Public Data 说明，2025-01-01 起现货档案时间为微秒。实现用“不小于 \(10^{14}\) 则视为微秒”的规则还原成毫秒，与 REST 的 `open + interval - 1ms` 收盘时间对齐。2026-11-01 的 BTCUSDT 1h 日档案已核对为微秒且无表头。
- 资金费率档案列：`calc_time,funding_interval_hours,last_funding_rate`。`calc_time` 是 Unix 毫秒，可以带毫秒偏移。2026-10 的 BTCUSDT 档案首行是 `2026-10-01T00:00:00.001Z`，不能四舍五入到整点后再使用。
- 资金费率 REST：仅 `GET https://fapi.binance.com/fapi/v1/fundingRate`。官方 USD-M connector 说明：`startTime` 与 `endTime` 含端点，结果升序；区间长于 `limit` 时从 `startTime` 起返回 `limit` 行；未给时间则返回最近 200 行。`limit ≤ 200` 与 `GET /fapi/v1/fundingInfo` 共享 500/5min/IP；`limit > 200` 为 100/5min/IP。本项目默认 `limit=200`。响应里的 `markPrice` 在 2023-11-01 之前的历史行可以是空字符串。
- 不下载 USD-M K 线。永续合约价格不进入特征库，也不进入回测。档案路径允许列表明确拒绝 `/data/futures/um/monthly/klines/`。
- V1 现货客户端的允许列表不包含 `/fapi/`。资金费率客户端只允许上述一个 GET，拒绝 order、leverage、position、account、withdraw、margin、transfer。不发送 API Key，不跟随重定向。

HTTP 418 停止请求。HTTP 451 表示该位置不能使用该主机。采集程序记录 451，不使用代理或虚假身份绕过。若已发布的档案覆盖请求窗口，现货或资金费率仍以校验过的档案为准；覆盖不足时，该序列不合格，并用空值表示缺失，不编造费率。

### 6.4 质量规则

与 V1 相同，并增加主动买卖量检查：

- 缺失、重复、乱序、OHLC 非法、负成交量、未来时间戳都要计数；
- 缺失比例默认超过 1% 则不合格；
- 不前向填充缺失 K 线，也不用未来值填补；
- 未收盘 K 线可以按 V1 的方式标记后保存，但不得进入特征或回测样本；
- 收盘样本计算数据集 SHA-256；
- 同一 `open_time` 上，REST 与档案的 OHLCV 身份不同则计为修订。2026-10 的验收窗口中，该修订数为 0。

存储：

- 现货 K 线沿用 V1 Parquet：`data/parquet/binance_spot_{SYMBOL}_{INTERVAL}.parquet`，价格仍是十进制字符串；
- 资金费率：`data/parquet/binance_um_funding_{SYMBOL}.parquet`；
- 特征：`data/parquet/v2_features/{SYMBOL}_{INTERVAL}.parquet`；
- 这些 Parquet 不提交。质量摘要在 `data/reports/v2/`。

---

## 7. 对齐、点时刻与防泄漏

时间全部使用时区明确的 UTC。采集窗口是 K 线 `open_time` 上的半开区间 \([\text{start}, \text{end})\)。用户给出的 `start` 和 `end` 必须落在所采集周期的边界上。`history_end` 为空时，结束时间取交易所时间向下取整到最小采集周期，因此正在形成的 bar 不会被当成已收盘。

### 7.1 特征的可用时间

每一行特征的 `available_time` 等于该 bar 的 `close_time`。在时刻 \(T\) 只能使用 `available_time ≤ T` 的行。这与 V1 “收盘才能形成信号”一致。特征不绑定到同一根 K 线的开盘价上。

### 7.2 资金费率对齐

对收盘时间 \(C\) 的 bar，资金费率特征是满足 `funding_time ≤ C` 的最新一条事件。

- `funding_time = C` 可见；
- 比 \(C\) 晚 1 毫秒的事件不可见；
- 此前没有任何事件时，特征为空，不用更晚的费率倒填；
- 这是已经公布数值的 as-of 连接，不是用未来值填补缺失 bar。

### 7.3 滚动窗口

窗口长度写在 `configs/v2_data.yaml` 的 `features.windows`，按 1h、4h、1d 分开，不在代码里写死魔法数。这些长度是可复现的研究参数，不是已经优化好的仓位参数，也不能在看到结果后悄悄改掉再宣称发现了优势。

滚动窗口是向后的，包含当前 bar 上已经收盘可知的量，除非定义明确写成“截至上一根”。窗口内出现空值，或历史根数不足，则结果为空，不用部分窗口冒充完整窗口。缺失 bar 不插入。窗口按已观测到的收盘 bar 计数，因此缺口会让窗口跨过更长的钟表时间；这种情况必须保持可见，而不是填平。

同一 `open_time` 上若另一交易对没有收盘 bar，相对强弱为空。不允许把前一根或后一根的价格复制过来。

### 7.4 标签泄漏（P2 起）

P1 特征本身不得使用 \(t\) 之后的信息。P2 预测未来 \(H\) 根收益时，标签的信息会延伸到决策时点之后。训练时必须：

- **Purging：** 去掉标签区间与测试区间重叠的训练样本；
- **Embargo：** 在测试区间之后再留出一段训练禁区，减轻序列相关把信息带过边界。

单次 60/20/20 不是 V2 的模型选择协议。见第 10 节。

### 7.5 合成数据

单元测试里的合成 K 线标记为 `synthetic` 或只在内存中使用。写入研究特征库的命令拒绝 `synthetic` 来源。正式报告不得把合成数据写成 BTC 或 ETH 的真实历史。

---

## 8. 特征定义

设 bar \(t\) 收盘时已知 \(C_t\)、\(V_t\)、\(Q_t\)、主动买入基础资产量 \(B_t\)、主动买入计价资产量 \(B^q_t\)。主动卖出基础资产量 \(S_t=V_t-B_t\)。下面的窗口 \(W\) 取该周期配置值。\(\ln\) 只在两个价格或两个成交量都为正时有定义。

### 8.1 主动买卖不平衡

\[
I_t=\frac{B_t-S_t}{V_t}=\frac{2B_t-V_t}{V_t}
\]

\(V_t\le 0\)、\(B_t<0\) 或 \(B_t>V_t\) 时 \(I_t\) 为空。计价资产不平衡 \(I^q_t\) 用 \(Q_t\) 与 \(B^q_t\) 同样计算。

滚动不平衡是截至 \(t\) 的 \(W\) 个已观测 \(I\) 的算术平均。滚动值为正时，`taker_buy_exceeds_sell_roll = 1`，否则为 0。这表示“主动买入持续大于主动卖出”这一研究问题，不是交易信号。

### 8.2 价格与成交量是否同步

\[
r_t=\ln\frac{C_t}{C_{t-1}}
\]

成交量基准是 **上一根之前** 的 \(W\) 根成交量均值，不含当前 bar：

\[
\bar V_t=\frac{1}{W}\sum_{i=1}^{W} V_{t-i},\qquad
\rho_t=\frac{V_t}{\bar V_t}-1
\]

\[
D_t=r_t-\rho_t
\]

`price_up_volume_not = 1` 当且仅当 \(r_t>0\) 且 \(\rho_t\le 0\)。这对应“价格上涨，但成交量没有相对此前水平同步增长”。任一输入为空则标志为空，而不是 0。

`price_volume_corr` 是 \(r\) 与 \(\rho\) 在截至 \(t\) 的 \(W\) 根上的 Pearson 相关。任一方差为 0 或窗口内有空值时为空。

### 8.3 资金费率与价格

\(f_t\) 是第 7.2 节的 as-of 费率。

\[
f_t r_t
\]

`price_down_funding_high = 1` 当且仅当 \(r_t<0\) 且 \(f_t>\tau\)。\(\tau\) 是配置项 `funding_high_threshold`，当前文件中的值为 0.0001，只用于把“价格下跌而资金费率仍然高”写成可复现列。它不是仓位阈值，也不是交易规则。\(f_t\) 为空时该标志为空。

### 8.4 ETH/BTC 相对强弱

只在配置的 `relative_strength_intervals` 上计算，默认 4h 和 1d。1h 列保留为空，避免把未要求的频率当成已经研究过。

在同一 `open_time` 上：

\[
g_t=r^{\mathrm{ETH}}_t-r^{\mathrm{BTC}}_t
\]

相对强弱是 \(g\) 的截至 \(t\) 的 \(W\) 根滚动和。任一交易对该时点没有收益时，\(g_t\) 为空。

`btc_up_ethbtc_weak = 1` 当且仅当 \(r^{\mathrm{BTC}}_t>0\) 且 \(g_t<0\)。滚动版本把这两个量换成各自的 \(W\) 根滚动和。这对应“BTC 上涨，而 ETH/BTC 走弱”。两个交易对的同一时点写入相同的相对强弱值。

### 8.5 滚动历史波动率

\(v_t\) 是 \(r\) 在截至 \(t\) 的 \(W\) 根上的样本标准差，分母为 \(W-1\)。年化时：

\[
v^{\mathrm{ann}}_t=v_t\sqrt{N}
\]

\(N\) 使用 V1 已有的周期年化根数：1d 为 365，4h 为 \(365\times 6\)，1h 为 \(365\times 24\)。这是历史波动率，不是 GARCH 预测。GARCH/EWMA 仍未拟合。P2 的不确定性来自方向概率的伯努利方差、Ridge 的样本内残差标准差，以及 LightGBM 的 0.1/0.9 分位数半宽。这些数不进入仓位公式。

### 8.6 可复现性

特征行携带 `feature_set_version`（`v2.p1`）、`config_hash`、现货数据集哈希和资金费率数据集哈希。相同的收盘 K 线、相同的资金费率事件和相同的窗口配置必须得到相同的特征哈希。改变任一窗口长度会改变 `config_hash`。

---

## 9. 验证方法（P4，不实现）

验证比模型本身更重要。三个正式方法：

### 9.1 嵌套前向滚动

不再只做一次 60/20/20。每一轮：用当时可见的历史训练，在随后一段上选择参数，再在更后的一段上测试，然后时间向前滚动。预测未来 \(H\) 根收益时，重叠标签从 P2 起就做 Purging 和 Embargo。

与 V1 基准比较时，每一折使用同一区间、同一成本和同一成交规则。V1 原有的 60/20/20 与 walk-forward 仍然是 V1 报告协议，不因 V2 规格而改写。P2 做了带 Purging 和 Embargo 的滚动验证，但没有实现本节的嵌套验收门槛，也没有计算 PBO 或 DSR。

### 9.2 PBO

使用 Bailey、Borwein、López de Prado 与 Zhu 提出的 CSCV（combinatorially symmetric cross-validation）估计回测过拟合概率：样本内选出的最优策略，在样本外排名低于中位数的概率。PBO 评估的是选择过程，不是单个净值曲线的自我说明。

### 9.3 DSR

Deflated Sharpe Ratio 同时考虑多重尝试造成的选择偏差，以及收益的非正态。报告 DSR 时必须同时说明试验次数、试验夏普的离散程度、样本长度、偏度和峰度。含义以第 4 节第 7 条为准。

P4 还包含 bootstrap 与压力成本测试。本轮不计算这些统计量。

### 9.4 预登记的初始验收门槛

这些是 V2 研究的预登记门槛，不是行业通用的盈利保证，也不是本轮 P1 的通过条件。未通过则保持现金。

| 指标 | 初始验收目标 |
| --- | --- |
| 测试集净收益 | 大于 0 |
| 测试集 Sharpe | 大于 0.8 |
| 滚动窗口盈利比例 | 不低于 70% |
| DSR | 不低于 95%（含义见第 4 节第 7 条） |
| 压力成本测试 | 净收益仍然为正 |
| 最大回撤 | 不超过预先设定的 30% 风险预算 |
| 模拟盘 | 至少连续观察 3–6 个月 |

2025-01-18 至 2026-11-26 不能充当上表里的“未触碰测试集”。通过上表之后，才单独评估是否做小额实盘；评估本身也不打开 `LIVE`。

---

## 10. 关于 2026 年 BTC 机器学习预印本

实现时核对到的文献是：Andrei Bysik 与 Robert Ślepaczuk，*Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting*，arXiv:2606.00060，https://arxiv.org/abs/2606.00060 。SSRN 记录为 2026-05-29，https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6795938 。它仍是预印本，不是盈利保证。

已核对的方向性结论：

- 样本约 7 万条小时级 BTC/USDT 观测，论文使用的是 USD-M 期货，不是本项目的现货交易标的；
- 比较了 XGBoost、LSTM 与 iTransformer；
- 只按预测涨跌方向交易时，计入 10 个基点交易成本后策略失效；
- 加入按成本过滤弱预测的规则后，部分配置重新表现出正的净结果；
- XGBoost 在描述上较强，但论文自己的配对区块 bootstrap 在多重检验校正后，并不支持“统计上稳健地优于神经网络”的结论；
- 过滤后的结果也没有被证明在 bootstrap 调整后显著优于买入持有。

本项目采纳的只有这条方法论：预测准确率不是目标；扣除真实交易成本后的策略结果才是目标；而且即便如此，仍要允许“没有足够优势，所以不交易”。不把该文的期货样本、具体收益率或夏普数字当作本项目的预期收益。本项目不做空、不交易永续合约。

---

## 11. 里程碑

| 阶段 | 内容 | 本仓库状态 |
| --- | --- | --- |
| P1 | 数据基础设施：1h/4h 现货、资金费率特征、点时刻特征库 | 本轮实现 |
| P2 | Logistic 基线、LightGBM、HMM；训练时即做 Purging 与 Embargo | 已实现。GARCH 未拟合。见 `docs/V2_P2_REPORT.md` |
| P3 | 组合与风险管理；仓位参数由回测决定；风控与执行分离 | 未实现 |
| P4 | 嵌套前向滚动、Purged Validation、PBO、DSR、bootstrap、压力测试 | 未实现 |
| P5 | Agent 研究平台：实验管理、SHAP、研究报告 | 未实现 |
| P6 | 模拟盘：实时行情、模拟成交、真实账户费率参数、交易记录与影子监控 | 未实现 |

P6 本身不打开 `LIVE`。只有在预登记门槛和模拟盘观察之后，才**另外**评估小额实盘。那个评估不属于 P6 的默认开关。

---

## 12. 本轮 P1 验收

P1 完成是指：

- 本文已入库，锁定约束和 P1–P6 路线没有被削弱；
- 能采集并对齐 BTCUSDT、ETHUSDT 的 1h、4h 现货，以及二者的资金费率特征；
- 特征可复现，且测试能抓住未来函数、未收盘 bar、资金费率被当成交易、以及对齐错误；
- V1 日线回测的成交与记账语义不变，原有 V1 测试仍通过；
- `uv run pytest` 通过；
- 没有密钥入库，`LIVE` 仍被代码拒绝。

验收用的公开样本是已发布档案能够完整覆盖、并且可以和现货 REST 交叉核对的窗口：`2026-10-01T00:00:00Z`（含）至 `2026-11-01T00:00:00Z`（不含）。该窗口的行数、校验和、数据集哈希与质量结论写在 `data/reports/v2/p1_manifest.json`。它不是全历史，也不是 V2 的未触碰测试集。原始 Parquet 不提交。

在产生该报告的环境里，`data-api.binance.vision` 与 `data.binance.vision` 可用；`api.binance.com` 与 `fapi.binance.com` 返回 HTTP 451。采集记录了 451，没有绕过。2026-10 的资金费率月度档案覆盖了该窗口，因此资金费率质量仍然合格。现货 REST 交叉核对与档案的修订数为 0。

运行方式见 `README.md` 的 V2 一节。

---

## 13. 参考文献

下列出处在 2026-11-27 核对过。没有核对到 URL 的方法只保留书名或论文名，不编造链接。

- Bailey, David H., Jonathan M. Borwein, Marcos López de Prado, and Qiji Jim Zhu. “The Probability of Backtest Overfitting.” 工作论文日期 2015-02-27。SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253 。作者 PDF: https://davidhbailey.com/dhbpapers/backtest-prob.pdf 。CSCV 与 PBO 的定义以该文为准。SSRN 页面将其期刊版本标为 *Journal of Computational Finance*。
- Bailey, David H., and Marcos López de Prado. “The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality.” *Journal of Portfolio Management* 40(5): 94–107, 2014. SSRN: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 。作者 PDF: https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf 。
- López de Prado, Marcos. *Advances in Financial Machine Learning*. Wiley, 2018. Purging 与 Embargo 采用该书中对带重叠标签的交叉验证的处理，不另造 URL。
- Bysik, Andrei, and Robert Ślepaczuk. “Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting.” arXiv:2606.00060, 2026. https://arxiv.org/abs/2606.00060 。
- Binance Spot REST 与 `GET /api/v3/klines` 的 weight、`limit`，沿用仓库 `docs/api_integration.md` 在 2026-11-27 的核对。
- Binance USD-M `GET /fapi/v1/fundingRate` 的参数、升序、含端点时间，以及 `limit` 两档 weight，对照 `binance-connector-python` 中 USD-M OpenAPI 注释，文档页为 `https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api/Get-Funding-Rate-History`。
- Binance Public Data 档案布局、现货 2025-01-01 起的微秒时间戳、K 线列和校验和文件，对照 `https://github.com/binance/binance-public-data` 的 README。资金费率档案列由 2026-10 的 BTCUSDT 月度文件核对为 `calc_time,funding_interval_hours,last_funding_rate`。
