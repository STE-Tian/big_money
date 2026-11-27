# Personal Quant AI v1 回测报告

本报告描述一次可复现的历史研究，不构成投资建议，也不表示策略在未来能够盈利。
`CANDIDATE_FOR_PAPER` 只表示按事先写死的规则，该结果可以进入模拟账户观察。它不是实盘许可。
v1 没有真实下单功能。手续费和滑点是研究假设，不是该账户在 Binance 的实际费率。

## 1. 数据

- 生成时间（UTC）：2026-11-27T03:50:27.222270+00:00
- 代码版本：`7e528ffe394e1072a8d4373bc29156602b461007`
- 数据来源：Binance Spot 公共 REST，优先 `https://data-api.binance.vision`。
- 下载不使用 API Key，也不使用 API Secret。
- 时区：K 线时间以交易所返回的 UTC 开盘时间为准，不按本机零点重切日线。
- 未收盘 K 线不进入历史回测。缺失 K 线不做前向填充。

### BTCUSDT 1d

- 收盘 K 线数：3389
- 含未收盘的存储行数：3390
- 起始：2017-08-17T00:00:00+00:00
- 结束：2026-11-26T23:59:59.999000+00:00
- 数据哈希（收盘 K 线）：`082c50ae8d39acba97b1240a981026e6400835878b1202aa1adcd7bd72c586eb`
- 质量状态：qualified
- 重复行 0，乱序 0，缺失 0（比例 0.0），OHLC 非法 0，负成交量 0，未来时间戳 0，修订 0。
- 最新收盘 K 线滞后（秒）：13827.22327
- 缺失处理：不前向填充。回测只使用实际存在的已收盘 K 线，并在质量状态中披露缺口。

### ETHUSDT 1d

- 收盘 K 线数：3389
- 含未收盘的存储行数：3390
- 起始：2017-08-17T00:00:00+00:00
- 结束：2026-11-26T23:59:59.999000+00:00
- 数据哈希（收盘 K 线）：`fe05ccf267a13b604b5e3914338ede64a4b1dc8b5a53217e33fb53dc1887117c`
- 质量状态：qualified
- 重复行 0，乱序 0，缺失 0（比例 0.0），OHLC 非法 0，负成交量 0，未来时间戳 0，修订 0。
- 最新收盘 K 线滞后（秒）：13827.22327
- 缺失处理：不前向填充。回测只使用实际存在的已收盘 K 线，并在质量状态中披露缺口。

## 2. 方法与假设

- 策略只输出目标权重，权重限制在 0 到 1。不做空，不加杠杆。
- 信号使用 t 日已收盘数据。成交发生在 t+1 日开盘，不使用 t 日收盘价作为 t 日成交价。
- Buy & Hold 的决定在样本第一根 K 线开盘前已经确定，因此在第一根 K 线开盘买入并持有。
- SMA 与动量在均线或动量窗口完成之前不交易。窗口未完成的目标权重是空值，不是 0。
- 买入价 = 下一根开盘价 × (1 + 滑点)。卖出价 = 下一根开盘价 × (1 - 滑点)。
- 手续费 = 成交名义金额 × 单边费率，从现金扣除。滑点已经包含在成交价里，报告中的滑点成本是相对原始开盘价的备忘金额，不会再扣一次。
- 目标权重没有变化时不重复交易。样本最后一根 K 线可以形成信号，但没有下一根开盘，因此不会在样本内成交。
- 回测结束不平仓，持仓按最后一根收盘价估值。
- 初始资金：10000 USDT。这是统一实验基准，不是建议投入金额。
- 基准成本：单边手续费 0.001，单边滑点 0.0005。
- Stress 1：手续费 0.0015，滑点 0.001。
- Stress 2：手续费 0.002，滑点 0.002。
- 无风险利率按年 0 处理，并在报告中显式声明。夏普使用日超额收益的样本标准差，日线年化因子是 sqrt(365)，不是 sqrt(252)。4h 使用 sqrt(365×6)，1h 使用 sqrt(365×24)。
- Sortino 的下行偏差是负超额收益平方的平均值再开方（除以 N），目标收益是每根 K 线的无风险收益。
- 最大回撤 = equity / equity.cummax() - 1 的最小值。回撤为 0 时 Calmar 记为 undefined。
- 胜率只统计已平仓的往返交易：从空仓到持仓再回到空仓，结束现金高于开始现金记为盈利。未平仓的 Buy & Hold 没有已平仓交易，胜率是 undefined，不能写成 100%。
- 没有亏损往返时，Profit Factor 记为 incomparable，不使用无穷大。
- 时间切分按 K 线顺序：最早 60% 为 Train，其后 20% 为 Validation，最后 20% 为 Test。不随机打乱。
- 参数网格只在 Train 和 Validation 上比较。Test 使用规格里事先锁定的 SMA 20/50 和 60 日动量，不使用网格最优参数重跑 Test。
- 分段回测在段首按分段之前已经形成的目标权重重建仓位，因此各段净值不能相加还原全样本。
- Walk-forward：历史足够时训练窗口优先 730 天，否则 365 天；测试窗口 90 天，步长 90 天。窗口不足时明确跳过。
- 市场阶段规则事先固定：过去 90 个日历日收益率高于 +20% 为 bull，低于 -20% 为 bear，其余为 range。该标签只作描述，不参与选参。

## 3. 策略定义

- Buy & Hold：第一根可交易开盘买入并持有。
- SMA 20/50：短均线高于长均线则目标权重为 1，否则为 0。
- 60 日动量：close / close.shift(60) - 1 > 0 则目标权重为 1，否则为 0。
- Cash：全程持有 USDT，收益按 0 建模，不包含理财收益。

## 4. 全样本与 Train / Validation / Test

指标的精确值在各 run 目录的 `metrics.csv` 与 `metrics.json`。下表给阅读用的四位小数；空值写 undefined，不补 0。

### BTCUSDT

| 策略 | 区间 | Total Return | CAGR | Volatility | Sharpe | Max DD | Exposure | Fills | Closed trades | Fee |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| buy_and_hold | full | 13.1938 | 0.3307 | 0.6647 | 0.7662 | -0.8319 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| buy_and_hold | train | 3.7930 | 0.3249 | 0.7715 | 0.7566 | -0.8319 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| buy_and_hold | validation | 4.0803 | 1.3989 | 0.4863 | 2.0417 | -0.2615 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| buy_and_hold | test | -0.4188 | -0.2534 | 0.4312 | -0.4617 | -0.5297 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| sma_cross | full | 14.6300 | 0.3446 | 0.4700 | 0.8633 | -0.7503 | 0.5270 | 74.0000 | 37.0000 | 5785.2727 |
| sma_cross | train | 5.7665 | 0.4096 | 0.5420 | 0.9024 | -0.7503 | 0.4953 | 44.0000 | 22.0000 | 2295.9384 |
| sma_cross | validation | 1.0980 | 0.4902 | 0.3925 | 1.2107 | -0.3871 | 0.6224 | 14.0000 | 7.0000 | 190.4470 |
| sma_cross | test | 0.1010 | 0.0532 | 0.2630 | 0.3276 | -0.2898 | 0.5265 | 16.0000 | 8.0000 | 155.0193 |
| momentum | full | 32.5200 | 0.4597 | 0.4677 | 1.0412 | -0.6059 | 0.5270 | 166.0000 | 83.0000 | 25455.7705 |
| momentum | train | 10.8958 | 0.5598 | 0.5360 | 1.0961 | -0.6059 | 0.4943 | 99.0000 | 49.0000 | 6290.8722 |
| momentum | validation | 2.1203 | 0.8452 | 0.4092 | 1.7004 | -0.3797 | 0.6770 | 39.0000 | 19.0000 | 771.8485 |
| momentum | test | -0.0997 | -0.0549 | 0.2530 | -0.0976 | -0.2852 | 0.4749 | 30.0000 | 15.0000 | 280.9635 |
| cash | full | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cash | train | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cash | validation | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cash | test | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

与 Buy & Hold、Cash 使用同一起始资金、同一时间和同一成本口径。Cash 的波动为 0 时，夏普按定义是 undefined，总收益应为 0。

### ETHUSDT

| 策略 | 区间 | Total Return | CAGR | Volatility | Sharpe | Max DD | Exposure | Fills | Closed trades | Fee |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| buy_and_hold | full | 4.6033 | 0.2039 | 0.8583 | 0.6510 | -0.9397 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| buy_and_hold | train | 3.8808 | 0.3293 | 0.9805 | 0.7902 | -0.9397 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| buy_and_hold | validation | 1.3564 | 0.5863 | 0.5728 | 1.0885 | -0.4526 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| buy_and_hold | test | -0.5143 | -0.3221 | 0.6863 | -0.2263 | -0.6755 | 1.0000 | 1.0000 | 0.0000 | 9.9900 |
| sma_cross | full | 32.4129 | 0.4592 | 0.6332 | 0.9202 | -0.6798 | 0.4975 | 62.0000 | 31.0000 | 9299.4497 |
| sma_cross | train | 15.2408 | 0.6495 | 0.7445 | 1.0552 | -0.6798 | 0.4998 | 36.0000 | 18.0000 | 3443.4164 |
| sma_cross | validation | 0.5567 | 0.2690 | 0.4176 | 0.7770 | -0.3854 | 0.5383 | 16.0000 | 8.0000 | 182.4497 |
| sma_cross | test | 0.3216 | 0.1620 | 0.4091 | 0.5673 | -0.4720 | 0.4499 | 10.0000 | 5.0000 | 114.4222 |
| momentum | full | 41.0802 | 0.4959 | 0.6300 | 0.9616 | -0.7322 | 0.5335 | 128.0000 | 64.0000 | 18913.1397 |
| momentum | train | 11.1354 | 0.5654 | 0.7299 | 0.9905 | -0.7322 | 0.5209 | 69.0000 | 34.0000 | 5277.6833 |
| momentum | validation | 1.1233 | 0.4998 | 0.4654 | 1.1016 | -0.3921 | 0.6696 | 43.0000 | 21.0000 | 622.8351 |
| momentum | test | 0.6283 | 0.3001 | 0.4127 | 0.8385 | -0.3756 | 0.4351 | 18.0000 | 9.0000 | 249.4081 |
| cash | full | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cash | train | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cash | validation | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| cash | test | 0.0000 | 0.0000 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |

与 Buy & Hold、Cash 使用同一起始资金、同一时间和同一成本口径。Cash 的波动为 0 时，夏普按定义是 undefined，总收益应为 0。

## 5. 成本敏感性

三档成本都使用事先锁定的策略参数。Test 上的压力结果用于披露成本是否翻转结论，不用于重新挑选参数。

### BTCUSDT buy_and_hold

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 13.1938 | 0.7662 | -0.8319 | 1.0000 | -0.4188 | -0.4617 |
| stress_1 | 0.0015 | 0.001 | 13.1796 | 0.7660 | -0.8319 | 1.0000 | -0.4194 | -0.4630 |
| stress_2 | 0.002 | 0.002 | 13.1584 | 0.7658 | -0.8319 | 1.0000 | -0.4203 | -0.4649 |

### BTCUSDT sma_cross

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 14.6300 | 0.8633 | -0.7503 | 74.0000 | 0.1010 | 0.3276 |
| stress_1 | 0.0015 | 0.001 | 13.5152 | 0.8463 | -0.7533 | 74.0000 | 0.0835 | 0.2949 |
| stress_2 | 0.002 | 0.002 | 11.9902 | 0.8207 | -0.7577 | 74.0000 | 0.0578 | 0.2458 |

### BTCUSDT momentum

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 32.5200 | 1.0412 | -0.6059 | 166.0000 | -0.0997 | -0.0976 |
| stress_1 | 0.0015 | 0.001 | 27.3930 | 1.0029 | -0.6149 | 166.0000 | -0.1263 | -0.1613 |
| stress_2 | 0.002 | 0.002 | 21.1346 | 0.9454 | -0.6372 | 166.0000 | -0.1647 | -0.2567 |

### BTCUSDT cash

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | undefined |
| stress_1 | 0.0015 | 0.001 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | undefined |
| stress_2 | 0.002 | 0.002 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | undefined |

### ETHUSDT buy_and_hold

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 4.6033 | 0.6510 | -0.9397 | 1.0000 | -0.5143 | -0.2263 |
| stress_1 | 0.0015 | 0.001 | 4.5977 | 0.6509 | -0.9397 | 1.0000 | -0.5147 | -0.2270 |
| stress_2 | 0.002 | 0.002 | 4.5893 | 0.6507 | -0.9397 | 1.0000 | -0.5155 | -0.2281 |

### ETHUSDT sma_cross

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 32.4129 | 0.9202 | -0.6798 | 62.0000 | 0.3216 | 0.5673 |
| stress_1 | 0.0015 | 0.001 | 30.4042 | 0.9096 | -0.6836 | 62.0000 | 0.3084 | 0.5542 |
| stress_2 | 0.002 | 0.002 | 27.6153 | 0.8938 | -0.6893 | 62.0000 | 0.2889 | 0.5344 |

### ETHUSDT momentum

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 41.0802 | 0.9616 | -0.7322 | 128.0000 | 0.6283 | 0.8385 |
| stress_1 | 0.0015 | 0.001 | 36.0244 | 0.9396 | -0.7354 | 128.0000 | 0.5992 | 0.8150 |
| stress_2 | 0.002 | 0.002 | 29.5565 | 0.9067 | -0.7401 | 128.0000 | 0.5566 | 0.7796 |

### ETHUSDT cash

| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| base | 0.001 | 0.0005 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | undefined |
| stress_1 | 0.0015 | 0.001 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | undefined |
| stress_2 | 0.002 | 0.002 | 0.0000 | undefined | 0.0000 | 0.0000 | 0.0000 | undefined |

## 6. 参数稳健性

网格：SMA 短窗口 10/20/30，长窗口 50/100/150，且短窗口小于长窗口。动量窗口 20/60/120。表内只有 Train 和 Validation。

### BTCUSDT SMA

| short | long | Train return | Train Sharpe | Validation return | Validation Sharpe | Validation max DD |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 50 | 11.9182 | 1.1182 | 1.1862 | 1.2981 | -0.4001 |
| 10 | 100 | 4.1811 | 0.8239 | 2.0624 | 1.6011 | -0.3404 |
| 10 | 150 | 0.3705 | 0.3764 | 2.3234 | 1.6972 | -0.3253 |
| 20 | 50 | 5.7665 | 0.9024 | 1.0980 | 1.2107 | -0.3871 |
| 20 | 100 | 3.2367 | 0.7559 | 1.8638 | 1.5291 | -0.4055 |
| 20 | 150 | 0.7975 | 0.4726 | 2.2094 | 1.6753 | -0.3000 |
| 30 | 50 | 10.6145 | 1.0827 | 1.5507 | 1.4488 | -0.3050 |
| 30 | 100 | 4.2016 | 0.8210 | 2.3783 | 1.7728 | -0.2792 |
| 30 | 150 | 1.6604 | 0.6092 | 2.1916 | 1.6742 | -0.2425 |

### BTCUSDT Momentum

| window | Train return | Train Sharpe | Validation return | Validation Sharpe | Validation max DD |
|---:|---:|---:|---:|---:|---:|
| 20 | 14.4343 | 1.2045 | 1.7437 | 1.6331 | -0.3534 |
| 60 | 10.8958 | 1.0961 | 2.1203 | 1.7004 | -0.3797 |
| 120 | 0.7406 | 0.4532 | 3.0591 | 1.9968 | -0.2062 |

### ETHUSDT SMA

| short | long | Train return | Train Sharpe | Validation return | Validation Sharpe | Validation max DD |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 50 | 19.0685 | 1.1145 | 0.3354 | 0.5812 | -0.4082 |
| 10 | 100 | 5.5906 | 0.8396 | 0.7911 | 0.9179 | -0.4669 |
| 10 | 150 | 3.8597 | 0.7695 | 0.9086 | 0.9616 | -0.3454 |
| 20 | 50 | 15.2408 | 1.0552 | 0.5567 | 0.7770 | -0.3854 |
| 20 | 100 | 5.5653 | 0.8379 | 0.7930 | 0.9204 | -0.4336 |
| 20 | 150 | 2.4717 | 0.6815 | 1.1621 | 1.1033 | -0.2910 |
| 30 | 50 | 8.6012 | 0.9282 | 1.2784 | 1.2397 | -0.3264 |
| 30 | 100 | 4.0187 | 0.7725 | 0.4997 | 0.7083 | -0.4329 |
| 30 | 150 | 2.1799 | 0.6581 | 1.0572 | 1.0601 | -0.2910 |

### ETHUSDT Momentum

| window | Train return | Train Sharpe | Validation return | Validation Sharpe | Validation max DD |
|---:|---:|---:|---:|---:|---:|
| 20 | 15.0200 | 1.0631 | -0.3439 | -0.3672 | -0.6315 |
| 60 | 11.1354 | 0.9905 | 1.1233 | 1.1016 | -0.3921 |
| 120 | 4.0872 | 0.7787 | 1.2216 | 1.1485 | -0.2910 |

## 7. Walk-forward

### BTCUSDT

- 使用的训练窗口天数：730
- 完整测试窗口数：29
- buy_and_hold 固定参数窗口 29 个，其中收益为负的窗口 13 个。各窗口明细见 CSV，不只展示最好的窗口。
- sma_cross 固定参数窗口 29 个，其中收益为负的窗口 14 个。各窗口明细见 CSV，不只展示最好的窗口。
- momentum 固定参数窗口 29 个，其中收益为负的窗口 14 个。各窗口明细见 CSV，不只展示最好的窗口。
- cash 固定参数窗口 29 个，其中收益为负的窗口 0 个。各窗口明细见 CSV，不只展示最好的窗口。

### ETHUSDT

- 使用的训练窗口天数：730
- 完整测试窗口数：29
- buy_and_hold 固定参数窗口 29 个，其中收益为负的窗口 14 个。各窗口明细见 CSV，不只展示最好的窗口。
- sma_cross 固定参数窗口 29 个，其中收益为负的窗口 14 个。各窗口明细见 CSV，不只展示最好的窗口。
- momentum 固定参数窗口 29 个，其中收益为负的窗口 14 个。各窗口明细见 CSV，不只展示最好的窗口。
- cash 固定参数窗口 29 个，其中收益为负的窗口 0 个。各窗口明细见 CSV，不只展示最好的窗口。

## 8. 年度、季度、回撤与失效区间

第一个年度或季度可能不是完整自然周期，CSV 中 `partial_start=true` 标出这种情况。负收益区间全部列出，不做事后挑选。

### BTCUSDT buy_and_hold

- 最大回撤：-0.8319
- 峰值时间：2017-12-16T23:59:59.999000+00:00
- 谷值时间：2018-12-15T23:59:59.999000+00:00
- 恢复时间：2020-11-24T23:59:59.999000+00:00
- 收益为负的年份：2018, 2022, 2025, 2026
- 收益为负的季度：2018Q1, 2018Q2, 2018Q4, 2019Q3, 2019Q4, 2020Q1, 2021Q2, 2022Q1, 2022Q2, 2022Q3, 2022Q4, 2023Q3, 2024Q2, 2025Q1, 2025Q4, 2026Q1, 2026Q2, 2026Q4
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': 0.006042420645744331, 'bull': 0.005248654242135869, 'range': 0.000878543571582167, 'bear': -0.004647243627251886}

### BTCUSDT sma_cross

- 最大回撤：-0.7503
- 峰值时间：2017-12-16T23:59:59.999000+00:00
- 谷值时间：2019-03-04T23:59:59.999000+00:00
- 恢复时间：2020-11-30T23:59:59.999000+00:00
- 收益为负的年份：2018, 2022
- 收益为负的季度：2018Q1, 2018Q2, 2018Q3, 2018Q4, 2019Q1, 2019Q3, 2019Q4, 2020Q3, 2021Q2, 2021Q4, 2022Q1, 2022Q2, 2022Q3, 2022Q4, 2023Q2, 2023Q3, 2024Q2, 2024Q3, 2025Q1, 2025Q4, 2026Q1, 2026Q4
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': 0.0038565650527848747, 'bull': 0.003757783584928946, 'range': 0.0002996216787316897, 'bear': -0.0020480402342603365}

### BTCUSDT momentum

- 最大回撤：-0.6059
- 峰值时间：2021-11-08T23:59:59.999000+00:00
- 谷值时间：2023-01-10T23:59:59.999000+00:00
- 恢复时间：2024-02-26T23:59:59.999000+00:00
- 收益为负的年份：2018, 2022, 2026
- 收益为负的季度：2018Q1, 2018Q2, 2018Q4, 2019Q1, 2019Q3, 2019Q4, 2020Q1, 2020Q2, 2021Q2, 2022Q2, 2022Q3, 2022Q4, 2023Q2, 2023Q3, 2024Q2, 2024Q3, 2025Q1, 2025Q4, 2026Q1, 2026Q2, 2026Q4
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': 0.0017794209801795901, 'bull': 0.004535640999256755, 'range': -0.00043013316565346737, 'bear': -0.0005216231931838567}

### BTCUSDT cash

- 最大回撤：0.0000
- 峰值时间：2017-08-17T00:00:00+00:00
- 谷值时间：2017-08-17T00:00:00+00:00
- 恢复时间：2017-08-17T23:59:59.999000+00:00
- 收益为负的年份：无
- 收益为负的季度：无
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': 0.0, 'bull': 0.0, 'range': 0.0, 'bear': 0.0}

### ETHUSDT buy_and_hold

- 最大回撤：-0.9397
- 峰值时间：2018-01-13T23:59:59.999000+00:00
- 谷值时间：2018-12-15T23:59:59.999000+00:00
- 恢复时间：2021-01-24T23:59:59.999000+00:00
- 收益为负的年份：2018, 2019, 2022, 2025, 2026
- 收益为负的季度：2018Q1, 2018Q3, 2018Q4, 2019Q3, 2019Q4, 2022Q1, 2022Q2, 2022Q4, 2023Q3, 2024Q2, 2024Q3, 2025Q1, 2025Q4, 2026Q1, 2026Q2, 2026Q4
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': 0.0028034759697637794, 'range': 0.00133204372352016, 'bull': 0.005332188889786881, 'bear': -0.0036637521742817042}

### ETHUSDT sma_cross

- 最大回撤：-0.6798
- 峰值时间：2018-01-13T23:59:59.999000+00:00
- 谷值时间：2020-03-12T23:59:59.999000+00:00
- 恢复时间：2020-08-13T23:59:59.999000+00:00
- 收益为负的年份：2019, 2022, 2026
- 收益为负的季度：2018Q2, 2019Q1, 2019Q3, 2019Q4, 2020Q1, 2022Q1, 2022Q2, 2022Q3, 2022Q4, 2023Q2, 2023Q3, 2024Q2, 2024Q3, 2026Q1, 2026Q4
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': -0.0013995108036050062, 'range': 0.0005277980086355658, 'bull': 0.004712982439454925, 'bear': -0.0012902712444800902}

### ETHUSDT momentum

- 最大回撤：-0.7322
- 峰值时间：2018-01-13T23:59:59.999000+00:00
- 谷值时间：2019-02-07T23:59:59.999000+00:00
- 恢复时间：2020-12-27T23:59:59.999000+00:00
- 收益为负的年份：2018, 2022, 2026
- 收益为负的季度：2018Q2, 2018Q4, 2019Q3, 2020Q1, 2022Q2, 2022Q3, 2022Q4, 2023Q2, 2023Q3, 2024Q2, 2024Q3, 2025Q1, 2025Q4, 2026Q1, 2026Q2, 2026Q4
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': -0.0008224091390086301, 'range': 0.00020440520574665217, 'bull': 0.0048685691048624205, 'bear': -0.0009812699610798794}

### ETHUSDT cash

- 最大回撤：0.0000
- 峰值时间：2017-08-17T00:00:00+00:00
- 谷值时间：2017-08-17T00:00:00+00:00
- 恢复时间：2017-08-17T23:59:59.999000+00:00
- 收益为负的年份：无
- 收益为负的季度：无
- 预先定义的市场阶段内，策略平均单期收益：{'unclassified': 0.0, 'range': 0.0, 'bull': 0.0, 'bear': 0.0}

## 9. 图表与交易明细

每个全样本 run 目录包含净值曲线、回撤曲线、年度收益图（PNG 与 Plotly HTML）、交易明细 CSV、净值 CSV、指标 CSV/JSON 和配置快照。
HTML 图表通过 Plotly CDN 显示；离线查看使用同目录 PNG。

- BTCUSDT buy_and_hold run `9fb378c2-fa89-4a30-90e1-dc0da7f57937`
  - equity_png: `/workspace/data/reports/runs/9fb378c2-fa89-4a30-90e1-dc0da7f57937/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/9fb378c2-fa89-4a30-90e1-dc0da7f57937/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/9fb378c2-fa89-4a30-90e1-dc0da7f57937/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/9fb378c2-fa89-4a30-90e1-dc0da7f57937/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/9fb378c2-fa89-4a30-90e1-dc0da7f57937/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/9fb378c2-fa89-4a30-90e1-dc0da7f57937/config_snapshot.json`
- BTCUSDT sma_cross run `88174347-5f11-463b-bde4-42656391164b`
  - equity_png: `/workspace/data/reports/runs/88174347-5f11-463b-bde4-42656391164b/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/88174347-5f11-463b-bde4-42656391164b/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/88174347-5f11-463b-bde4-42656391164b/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/88174347-5f11-463b-bde4-42656391164b/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/88174347-5f11-463b-bde4-42656391164b/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/88174347-5f11-463b-bde4-42656391164b/config_snapshot.json`
- BTCUSDT momentum run `6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2`
  - equity_png: `/workspace/data/reports/runs/6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/6cbcbfd8-4471-4ab9-b0c4-b52329cc4be2/config_snapshot.json`
- BTCUSDT cash run `90e4571e-e718-41e3-9abb-b5125414ca01`
  - equity_png: `/workspace/data/reports/runs/90e4571e-e718-41e3-9abb-b5125414ca01/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/90e4571e-e718-41e3-9abb-b5125414ca01/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/90e4571e-e718-41e3-9abb-b5125414ca01/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/90e4571e-e718-41e3-9abb-b5125414ca01/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/90e4571e-e718-41e3-9abb-b5125414ca01/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/90e4571e-e718-41e3-9abb-b5125414ca01/config_snapshot.json`

- ETHUSDT buy_and_hold run `64811705-439b-4d40-a561-9401ff362675`
  - equity_png: `/workspace/data/reports/runs/64811705-439b-4d40-a561-9401ff362675/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/64811705-439b-4d40-a561-9401ff362675/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/64811705-439b-4d40-a561-9401ff362675/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/64811705-439b-4d40-a561-9401ff362675/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/64811705-439b-4d40-a561-9401ff362675/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/64811705-439b-4d40-a561-9401ff362675/config_snapshot.json`
- ETHUSDT sma_cross run `a62c047b-b0e8-46fb-933a-7cf711943b3e`
  - equity_png: `/workspace/data/reports/runs/a62c047b-b0e8-46fb-933a-7cf711943b3e/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/a62c047b-b0e8-46fb-933a-7cf711943b3e/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/a62c047b-b0e8-46fb-933a-7cf711943b3e/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/a62c047b-b0e8-46fb-933a-7cf711943b3e/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/a62c047b-b0e8-46fb-933a-7cf711943b3e/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/a62c047b-b0e8-46fb-933a-7cf711943b3e/config_snapshot.json`
- ETHUSDT momentum run `e6eb8aef-84c4-44c8-8e52-239cd61fca01`
  - equity_png: `/workspace/data/reports/runs/e6eb8aef-84c4-44c8-8e52-239cd61fca01/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/e6eb8aef-84c4-44c8-8e52-239cd61fca01/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/e6eb8aef-84c4-44c8-8e52-239cd61fca01/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/e6eb8aef-84c4-44c8-8e52-239cd61fca01/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/e6eb8aef-84c4-44c8-8e52-239cd61fca01/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/e6eb8aef-84c4-44c8-8e52-239cd61fca01/config_snapshot.json`
- ETHUSDT cash run `0e136fd3-4a69-4123-9d94-9e6bd479ac7c`
  - equity_png: `/workspace/data/reports/runs/0e136fd3-4a69-4123-9d94-9e6bd479ac7c/equity.png`
  - drawdown_png: `/workspace/data/reports/runs/0e136fd3-4a69-4123-9d94-9e6bd479ac7c/drawdown.png`
  - yearly_png: `/workspace/data/reports/runs/0e136fd3-4a69-4123-9d94-9e6bd479ac7c/yearly_returns.png`
  - trades_csv: `/workspace/data/reports/runs/0e136fd3-4a69-4123-9d94-9e6bd479ac7c/trades.csv`
  - metrics_csv: `/workspace/data/reports/runs/0e136fd3-4a69-4123-9d94-9e6bd479ac7c/metrics.csv`
  - config_snapshot: `/workspace/data/reports/runs/0e136fd3-4a69-4123-9d94-9e6bd479ac7c/config_snapshot.json`

## 10. 结论

结论使用 `src/pquant/research/validation/judgment.py` 中事先写明的规则。看完结果后没有改阈值。
规则概要：数据不合格直接 REJECT；测试区间短于 180 天则 INCONCLUSIVE 并标记 INSUFFICIENT_HISTORY；测试收益不高于 -20% 且夏普为负则 REJECT；其余只有在测试收益、测试夏普、回撤、两档压力成本、训练/测试同向为正，以及参数网格和 walk-forward 的过半条件都满足时，才是 CANDIDATE_FOR_PAPER。

### BTCUSDT buy_and_hold

- 标签：`REJECT`
- 历史状态：`ok`
- test total return is <= -20% and test Sharpe is negative
- 该标签不表示未来收益，也不表示可以实盘。

### BTCUSDT sma_cross

- 标签：`CANDIDATE_FOR_PAPER`
- 历史状态：`ok`
- pre-registered paper-observation rule passed; this is not permission to trade real money
- Test 收益 0.1010，同一 Test 上的 Buy & Hold 为 -0.4188。测试区间收益高于同一区间的买入持有。这个比较不改变上面的规则标签。
- 该标签不表示未来收益，也不表示可以实盘。

### BTCUSDT momentum

- 标签：`INCONCLUSIVE`
- 历史状态：`ok`
- test total return is not positive
- test Sharpe is not defined and positive
- stress-1 test total return is not positive
- stress-2 test total return is not above -10%
- train and test total returns are not both positive
- Test 收益 -0.0997，同一 Test 上的 Buy & Hold 为 -0.4188。测试区间收益高于同一区间的买入持有。这个比较不改变上面的规则标签。
- 该标签不表示未来收益，也不表示可以实盘。

### BTCUSDT cash

- 标签：`BENCHMARK_ONLY`
- 历史状态：`not_applicable`
- cash is the zero-return benchmark
- 该标签不表示未来收益，也不表示可以实盘。

### ETHUSDT buy_and_hold

- 标签：`REJECT`
- 历史状态：`ok`
- test total return is <= -20% and test Sharpe is negative
- 该标签不表示未来收益，也不表示可以实盘。

### ETHUSDT sma_cross

- 标签：`CANDIDATE_FOR_PAPER`
- 历史状态：`ok`
- pre-registered paper-observation rule passed; this is not permission to trade real money
- Test 收益 0.3216，同一 Test 上的 Buy & Hold 为 -0.5143。测试区间收益高于同一区间的买入持有。这个比较不改变上面的规则标签。
- 该标签不表示未来收益，也不表示可以实盘。

### ETHUSDT momentum

- 标签：`CANDIDATE_FOR_PAPER`
- 历史状态：`ok`
- pre-registered paper-observation rule passed; this is not permission to trade real money
- Test 收益 0.6283，同一 Test 上的 Buy & Hold 为 -0.5143。测试区间收益高于同一区间的买入持有。这个比较不改变上面的规则标签。
- 该标签不表示未来收益，也不表示可以实盘。

### ETHUSDT cash

- 标签：`BENCHMARK_ONLY`
- 历史状态：`not_applicable`
- cash is the zero-return benchmark
- 该标签不表示未来收益，也不表示可以实盘。

## 11. 局限

- 日线下一根开盘的滑点模型不是订单簿成交，也不能代表实时冲击成本。
- 没有建模资金费率以外的现货借币，因为 v1 不允许借币和做空。
- 未使用的最后一根信号、分段重置现金、以及费用导致的现金残留，都会使全样本和分段结果不可直接相加。
- 加密资产在样本内的大幅上涨会让买入持有看起来很强。这是这段历史的结果，不是未来的承诺。
- 参数网格和 walk-forward 仍然是有限的稳健性检查，不能排除过拟合。
- 公共行情接口的修订、缺口和停机都会改变可复现实验的输入。报告保留了数据哈希和下载时间，便于对照。

## 12. 复现

```powershell
uv sync
uv run python scripts/init_db.py
uv run python scripts/collect_history.py
uv run python scripts/validate_data.py
uv run python scripts/run_backtests.py
```
