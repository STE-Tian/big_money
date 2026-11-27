"""Human-readable backtest report. Every number is copied from the computed summary."""

from __future__ import annotations

from typing import Any


def render_backtest_report(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Personal Quant AI v1 回测报告")
    lines.append("")
    lines.append("本报告描述一次可复现的历史研究，不构成投资建议，也不表示策略在未来能够盈利。")
    lines.append("`CANDIDATE_FOR_PAPER` 只表示按事先写死的规则，该结果可以进入模拟账户观察。它不是实盘许可。")
    lines.append("v1 没有真实下单功能。手续费和滑点是研究假设，不是该账户在 Binance 的实际费率。")
    lines.append("")
    lines.append("## 1. 数据")
    lines.append("")
    lines.append(f"- 生成时间（UTC）：{summary.get('generated_at')}")
    lines.append(f"- 代码版本：`{summary.get('code_commit')}`")
    lines.append(f"- 数据来源：Binance Spot 公共 REST，优先 `{summary.get('base_url')}`。")
    lines.append("- 下载不使用 API Key，也不使用 API Secret。")
    lines.append("- 时区：K 线时间以交易所返回的 UTC 开盘时间为准，不按本机零点重切日线。")
    lines.append("- 未收盘 K 线不进入历史回测。缺失 K 线不做前向填充。")
    lines.append("")
    symbols = summary.get("symbols") or {}
    if not symbols:
        lines.append("没有可分析的本地行情。下面不会填写收益率或夏普比率。")
        if summary.get("blockers"):
            lines.append("")
            lines.append("### 阻断记录")
            lines.append("")
            for blocker in summary["blockers"]:
                lines.append(f"- {blocker}")
        return "\n".join(lines) + "\n"
    for symbol, payload in symbols.items():
        lines.append(f"### {symbol} {payload.get('interval')}")
        lines.append("")
        if payload.get("error"):
            lines.append(f"未计算业绩：{payload['error']}")
            lines.append("")
            continue
        quality = payload.get("quality") or {}
        lines.append(f"- 收盘 K 线数：{payload.get('row_count_closed')}")
        lines.append(f"- 含未收盘的存储行数：{payload.get('row_count_including_open')}")
        lines.append(f"- 起始：{payload.get('start')}")
        lines.append(f"- 结束：{payload.get('end')}")
        lines.append(f"- 数据哈希（收盘 K 线）：`{payload.get('dataset_hash')}`")
        lines.append(f"- 质量状态：{quality.get('status')}")
        lines.append(
            f"- 重复行 {quality.get('duplicate_rows')}，乱序 {quality.get('out_of_order_rows')}，"
            f"缺失 {quality.get('missing_count')}（比例 {quality.get('missing_ratio')}），"
            f"OHLC 非法 {quality.get('ohlc_invalid_count')}，负成交量 {quality.get('negative_volume_count')}，"
            f"未来时间戳 {quality.get('future_timestamp_count')}，修订 {quality.get('revised_candle_count')}。"
        )
        lines.append(f"- 最新收盘 K 线滞后（秒）：{quality.get('latest_closed_lag_seconds')}")
        reasons = quality.get("reasons") or []
        if reasons:
            lines.append("- 质量说明：" + "；".join(str(item) for item in reasons))
        lines.append("- 缺失处理：不前向填充。回测只使用实际存在的已收盘 K 线，并在质量状态中披露缺口。")
        lines.append("")
    lines.append("## 2. 方法与假设")
    lines.append("")
    assumptions = summary.get("assumptions") or {}
    lines.append("- 策略只输出目标权重，权重限制在 0 到 1。不做空，不加杠杆。")
    lines.append("- 信号使用 t 日已收盘数据。成交发生在 t+1 日开盘，不使用 t 日收盘价作为 t 日成交价。")
    lines.append("- Buy & Hold 的决定在样本第一根 K 线开盘前已经确定，因此在第一根 K 线开盘买入并持有。")
    lines.append("- SMA 与动量在均线或动量窗口完成之前不交易。窗口未完成的目标权重是空值，不是 0。")
    lines.append("- 买入价 = 下一根开盘价 × (1 + 滑点)。卖出价 = 下一根开盘价 × (1 - 滑点)。")
    lines.append("- 手续费 = 成交名义金额 × 单边费率，从现金扣除。滑点已经包含在成交价里，报告中的滑点成本是相对原始开盘价的备忘金额，不会再扣一次。")
    lines.append("- 目标权重没有变化时不重复交易。样本最后一根 K 线可以形成信号，但没有下一根开盘，因此不会在样本内成交。")
    lines.append("- 回测结束不平仓，持仓按最后一根收盘价估值。")
    lines.append(f"- 初始资金：{assumptions.get('initial_cash')} {assumptions.get('quote_asset')}。这是统一实验基准，不是建议投入金额。")
    lines.append(f"- 基准成本：单边手续费 {assumptions.get('base_fee')}，单边滑点 {assumptions.get('base_slippage')}。")
    lines.append(f"- Stress 1：手续费 {assumptions.get('stress_1_fee')}，滑点 {assumptions.get('stress_1_slippage')}。")
    lines.append(f"- Stress 2：手续费 {assumptions.get('stress_2_fee')}，滑点 {assumptions.get('stress_2_slippage')}。")
    lines.append("- 无风险利率按年 0 处理，并在报告中显式声明。夏普使用日超额收益的样本标准差，日线年化因子是 sqrt(365)，不是 sqrt(252)。4h 使用 sqrt(365×6)，1h 使用 sqrt(365×24)。")
    lines.append("- Sortino 的下行偏差是负超额收益平方的平均值再开方（除以 N），目标收益是每根 K 线的无风险收益。")
    lines.append("- 最大回撤 = equity / equity.cummax() - 1 的最小值。回撤为 0 时 Calmar 记为 undefined。")
    lines.append("- 胜率只统计已平仓的往返交易：从空仓到持仓再回到空仓，结束现金高于开始现金记为盈利。未平仓的 Buy & Hold 没有已平仓交易，胜率是 undefined，不能写成 100%。")
    lines.append("- 没有亏损往返时，Profit Factor 记为 incomparable，不使用无穷大。")
    lines.append("- 时间切分按 K 线顺序：最早 60% 为 Train，其后 20% 为 Validation，最后 20% 为 Test。不随机打乱。")
    lines.append("- 参数网格只在 Train 和 Validation 上比较。Test 使用规格里事先锁定的 SMA 20/50 和 60 日动量，不使用网格最优参数重跑 Test。")
    lines.append("- 分段回测在段首按分段之前已经形成的目标权重重建仓位，因此各段净值不能相加还原全样本。")
    lines.append("- Walk-forward：历史足够时训练窗口优先 730 天，否则 365 天；测试窗口 90 天，步长 90 天。窗口不足时明确跳过。")
    lines.append("- 市场阶段规则事先固定：过去 90 个日历日收益率高于 +20% 为 bull，低于 -20% 为 bear，其余为 range。该标签只作描述，不参与选参。")
    lines.append("")
    lines.append("## 3. 策略定义")
    lines.append("")
    lines.append("- Buy & Hold：第一根可交易开盘买入并持有。")
    lines.append("- SMA 20/50：短均线高于长均线则目标权重为 1，否则为 0。")
    lines.append("- 60 日动量：close / close.shift(60) - 1 > 0 则目标权重为 1，否则为 0。")
    lines.append("- Cash：全程持有 USDT，收益按 0 建模，不包含理财收益。")
    lines.append("")
    lines.append("## 4. 全样本与 Train / Validation / Test")
    lines.append("")
    lines.append("指标的精确值在各 run 目录的 `metrics.csv` 与 `metrics.json`。下表给阅读用的四位小数；空值写 undefined，不补 0。")
    lines.append("")
    for symbol, payload in symbols.items():
        if payload.get("error"):
            continue
        lines.append(f"### {symbol}")
        lines.append("")
        lines.append("| 策略 | 区间 | Total Return | CAGR | Volatility | Sharpe | Max DD | Exposure | Fills | Closed trades | Fee |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for strategy_id, item in (payload.get("strategies") or {}).items():
            for segment in ("full", "train", "validation", "test"):
                metrics = ((item.get("segments") or {}).get(segment) or {}).get("metrics") or {}
                if not metrics:
                    continue
                lines.append(
                    "| {strategy} | {segment} | {total} | {cagr} | {vol} | {sharpe} | {dd} | {exp} | {fills} | {closed} | {fee} |".format(
                        strategy=strategy_id,
                        segment=segment,
                        total=_fmt(metrics, "total_return"),
                        cagr=_fmt(metrics, "cagr"),
                        vol=_fmt(metrics, "annualized_volatility"),
                        sharpe=_fmt(metrics, "sharpe_ratio"),
                        dd=_fmt(metrics, "max_drawdown"),
                        exp=_fmt(metrics, "exposure"),
                        fills=_fmt(metrics, "fill_count", percent=False),
                        closed=_fmt(metrics, "closed_trade_count", percent=False),
                        fee=_fmt(metrics, "fee_paid", percent=False),
                    )
                )
        lines.append("")
        lines.append("与 Buy & Hold、Cash 使用同一起始资金、同一时间和同一成本口径。Cash 的波动为 0 时，夏普按定义是 undefined，总收益应为 0。")
        lines.append("")
    lines.append("## 5. 成本敏感性")
    lines.append("")
    lines.append("三档成本都使用事先锁定的策略参数。Test 上的压力结果用于披露成本是否翻转结论，不用于重新挑选参数。")
    lines.append("")
    for symbol, payload in symbols.items():
        for strategy_id, item in (payload.get("strategies") or {}).items():
            lines.append(f"### {symbol} {strategy_id}")
            lines.append("")
            lines.append("| 情景 | 手续费 | 滑点 | 全样本收益 | 全样本 Sharpe | 全样本最大回撤 | 全样本成交次数 | Test 收益 | Test Sharpe |")
            lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
            for name, row in (item.get("costs") or {}).items():
                full = row.get("full") or {}
                test = row.get("test") or {}
                lines.append(
                    f"| {name} | {row.get('fee_rate_per_side')} | {row.get('slippage_rate_per_side')} | "
                    f"{_fmt(full, 'total_return')} | {_fmt(full, 'sharpe_ratio')} | {_fmt(full, 'max_drawdown')} | "
                    f"{_fmt(full, 'fill_count', percent=False)} | {_fmt(test, 'total_return')} | {_fmt(test, 'sharpe_ratio')} |"
                )
            lines.append("")
    lines.append("## 6. 参数稳健性")
    lines.append("")
    lines.append("网格：SMA 短窗口 10/20/30，长窗口 50/100/150，且短窗口小于长窗口。动量窗口 20/60/120。表内只有 Train 和 Validation。")
    lines.append("")
    for symbol, payload in symbols.items():
        lines.append(f"### {symbol} SMA")
        lines.append("")
        lines.append("| short | long | Train return | Train Sharpe | Validation return | Validation Sharpe | Validation max DD |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|")
        for row in (payload.get("robustness") or {}).get("sma_cross") or []:
            lines.append(
                f"| {row.get('short_window')} | {row.get('long_window')} | {_num(row.get('train_total_return'))} | "
                f"{_num(row.get('train_sharpe'))} | {_num(row.get('validation_total_return'))} | {_num(row.get('validation_sharpe'))} | "
                f"{_num(row.get('validation_max_drawdown'))} |"
            )
        lines.append("")
        lines.append(f"### {symbol} Momentum")
        lines.append("")
        lines.append("| window | Train return | Train Sharpe | Validation return | Validation Sharpe | Validation max DD |")
        lines.append("|---:|---:|---:|---:|---:|---:|")
        for row in (payload.get("robustness") or {}).get("momentum") or []:
            lines.append(
                f"| {row.get('window')} | {_num(row.get('train_total_return'))} | {_num(row.get('train_sharpe'))} | "
                f"{_num(row.get('validation_total_return'))} | {_num(row.get('validation_sharpe'))} | {_num(row.get('validation_max_drawdown'))} |"
            )
        lines.append("")
    lines.append("## 7. Walk-forward")
    lines.append("")
    for symbol, payload in symbols.items():
        lines.append(f"### {symbol}")
        lines.append("")
        lines.append(f"- 使用的训练窗口天数：{payload.get('walk_forward_train_days')}")
        lines.append(f"- 完整测试窗口数：{payload.get('walk_forward_window_count')}")
        if payload.get("walk_forward_skip_reason"):
            lines.append(f"- 未执行或窗口为空的原因：{payload.get('walk_forward_skip_reason')}")
        for strategy_id, item in (payload.get("strategies") or {}).items():
            rows = item.get("walk_forward") or []
            if not rows:
                continue
            returns = [row.get("total_return") for row in rows if isinstance(row.get("total_return"), float)]
            negatives = sum(1 for value in returns if value < 0)
            lines.append(
                f"- {strategy_id} 固定参数窗口 {len(rows)} 个，其中收益为负的窗口 {negatives} 个。"
                "各窗口明细见 CSV，不只展示最好的窗口。"
            )
        lines.append("")
    lines.append("## 8. 年度、季度、回撤与失效区间")
    lines.append("")
    lines.append("第一个年度或季度可能不是完整自然周期，CSV 中 `partial_start=true` 标出这种情况。负收益区间全部列出，不做事后挑选。")
    lines.append("")
    for symbol, payload in symbols.items():
        for strategy_id, item in (payload.get("strategies") or {}).items():
            episode = item.get("drawdown_episode") or {}
            lines.append(f"### {symbol} {strategy_id}")
            lines.append("")
            lines.append(f"- 最大回撤：{_num(episode.get('max_drawdown'))}")
            lines.append(f"- 峰值时间：{episode.get('peak_time')}")
            lines.append(f"- 谷值时间：{episode.get('trough_time')}")
            lines.append(f"- 恢复时间：{episode.get('recovery_time') or '样本结束时尚未恢复'}")
            lines.append(f"- 收益为负的年份：{', '.join(item.get('negative_years') or []) or '无'}")
            lines.append(f"- 收益为负的季度：{', '.join(item.get('negative_quarters') or []) or '无'}")
            lines.append(f"- 预先定义的市场阶段内，策略平均单期收益：{item.get('regime_mean_bar_return')}")
            lines.append("")
    lines.append("## 9. 图表与交易明细")
    lines.append("")
    lines.append("每个全样本 run 目录包含净值曲线、回撤曲线、年度收益图（PNG 与 Plotly HTML）、交易明细 CSV、净值 CSV、指标 CSV/JSON 和配置快照。")
    lines.append("HTML 图表通过 Plotly CDN 显示；离线查看使用同目录 PNG。")
    lines.append("")
    for symbol, payload in symbols.items():
        for strategy_id, item in (payload.get("strategies") or {}).items():
            paths = item.get("artifact_paths") or {}
            lines.append(f"- {symbol} {strategy_id} run `{item.get('run_id')}`")
            for key in ("equity_png", "drawdown_png", "yearly_png", "trades_csv", "metrics_csv", "config_snapshot"):
                if paths.get(key):
                    lines.append(f"  - {key}: `{paths[key]}`")
        lines.append("")
    lines.append("## 10. 结论")
    lines.append("")
    lines.append("结论使用 `src/pquant/research/validation/judgment.py` 中事先写明的规则。看完结果后没有改阈值。")
    lines.append("规则概要：数据不合格直接 REJECT；测试区间短于 180 天则 INCONCLUSIVE 并标记 INSUFFICIENT_HISTORY；测试收益不高于 -20% 且夏普为负则 REJECT；其余只有在测试收益、测试夏普、回撤、两档压力成本、训练/测试同向为正，以及参数网格和 walk-forward 的过半条件都满足时，才是 CANDIDATE_FOR_PAPER。")
    lines.append("")
    for symbol, payload in symbols.items():
        bh = ((payload.get("strategies") or {}).get("buy_and_hold") or {}).get("segments", {}).get("test", {}).get("metrics", {})
        for strategy_id, item in (payload.get("strategies") or {}).items():
            judgment = item.get("judgment") or {}
            lines.append(f"### {symbol} {strategy_id}")
            lines.append("")
            lines.append(f"- 标签：`{judgment.get('label')}`")
            lines.append(f"- 历史状态：`{judgment.get('history_status')}`")
            for reason in judgment.get("reasons") or []:
                lines.append(f"- {reason}")
            if strategy_id not in {"buy_and_hold", "cash"} and bh:
                own = ((item.get("segments") or {}).get("test") or {}).get("metrics") or {}
                own_value = _raw_metric(own, "total_return")
                bh_value = _raw_metric(bh, "total_return")
                if own_value is None or bh_value is None:
                    relation = "与买入持有的数值比较不完整。"
                elif own_value > bh_value:
                    relation = "测试区间收益高于同一区间的买入持有。"
                elif own_value < bh_value:
                    relation = "测试区间收益低于同一区间的买入持有。"
                else:
                    relation = "测试区间收益与买入持有相同。"
                lines.append(
                    f"- Test 收益 {_fmt(own, 'total_return')}，同一 Test 上的 Buy & Hold 为 {_fmt(bh, 'total_return')}。"
                    f"{relation}这个比较不改变上面的规则标签。"
                )
            lines.append("- 该标签不表示未来收益，也不表示可以实盘。")
            lines.append("")
    lines.append("## 11. 局限")
    lines.append("")
    lines.append("- 日线下一根开盘的滑点模型不是订单簿成交，也不能代表实时冲击成本。")
    lines.append("- 没有建模资金费率以外的现货借币，因为 v1 不允许借币和做空。")
    lines.append("- 未使用的最后一根信号、分段重置现金、以及费用导致的现金残留，都会使全样本和分段结果不可直接相加。")
    lines.append("- 加密资产在样本内的大幅上涨会让买入持有看起来很强。这是这段历史的结果，不是未来的承诺。")
    lines.append("- 参数网格和 walk-forward 仍然是有限的稳健性检查，不能排除过拟合。")
    lines.append("- 公共行情接口的修订、缺口和停机都会改变可复现实验的输入。报告保留了数据哈希和下载时间，便于对照。")
    lines.append("")
    lines.append("## 12. 复现")
    lines.append("")
    lines.append("```powershell")
    lines.append("uv sync")
    lines.append("uv run python scripts/init_db.py")
    lines.append("uv run python scripts/collect_history.py")
    lines.append("uv run python scripts/validate_data.py")
    lines.append("uv run python scripts/run_backtests.py")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _fmt(metrics: dict[str, Any], name: str, *, percent: bool = True) -> str:
    return _num(_raw_metric(metrics, name), percent=percent)


def _raw_metric(metrics: dict[str, Any], name: str) -> Any:
    raw = metrics.get(name)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _num(value: Any, *, percent: bool = True) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, float):
        if percent and abs(value) <= 10:
            return f"{value:.4f}"
        return f"{value:.4f}"
    return str(value)
