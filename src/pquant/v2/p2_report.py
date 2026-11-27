"""Markdown report for one P2 study. Numbers come from the summary dict."""

from __future__ import annotations

from typing import Any


def render_report(summary: dict[str, Any]) -> str:
    judgment = summary["judgment"]
    lines: list[str] = []
    lines.append("# Big Money V2 P2 研究报告")
    lines.append("")
    lines.append(f"生成时间（UTC）：{summary['generated_at']}")
    lines.append(f"模型版本：`{summary['model_version']}`。随机种子：`{summary['seed']}`。")
    lines.append(f"配置 SHA-256：`{summary['config_sha256']}`。")
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append(judgment["conclusion"])
    lines.append("")
    if judgment["reasons"]:
        lines.append("未通过的预先登记条件：")
        lines.append("")
        for reason in judgment["reasons"]:
            lines.append(f"- {reason}")
        lines.append("")
    lines.append(
        f"证据规则 `{judgment['rule']}` 的主策略是 `{judgment['primary_strategy']}`。"
        f"足够证据：{'是' if judgment['enough_evidence'] else '否'}。"
        f"研究结论是否保持现金：{'是' if judgment['stay_in_cash'] else '否'}。"
    )
    lines.append("")
    lines.append(
        "这条结论不是未来收益保证，也不是实盘许可。"
        f"{summary['v1_seen_window']['note']}"
    )
    lines.append("")
    lines.append("## 本轮做了什么")
    lines.append("")
    lines.append(
        "P2 在 P1 点时刻特征上训练了 Logistic 回归基线（方向概率）和与之配对的 Ridge 回归（预期收益），"
        "LightGBM 主模型（预期收益、方向概率、0.1/0.9 分位数半宽作为不确定性），"
        "以及对角协方差高斯 HMM。HMM 只输出按训练集发射均值排序后的未标注状态概率，"
        "状态个数在每个训练前缀上用 BIC 选择。重叠标签从训练开始就做了 Purging，"
        "评估区间之后按配置做了 Embargo。"
    )
    lines.append("")
    lines.append(
        f"交易规则预先写在 `configs/v2_p2.yaml`：在 {summary['decision_interval']} 收盘，"
        f"若 {summary['rule_horizon']} 预期简单收益严格大于一轮往返的基准成本 "
        f"{summary['round_trip_base_cost']}（单边手续费 {summary['fee_rate_per_side']}，"
        f"单边滑点 {summary['slippage_rate_per_side']}，两边相加），目标权重为 1，否则为 0。"
        "信号交给 V1 回测引擎，在下一根 4h 开盘成交。初始资金 "
        f"{summary['initial_cash']} USDT。压力成本使用 `{summary['stress_scenario']}`："
        f"单边手续费 {summary['stress_fee_rate_per_side']}，单边滑点 {summary['stress_slippage_rate_per_side']}。"
        "压力测试沿用同一套目标权重，不按压力成本重新开仓。"
    )
    lines.append("")
    lines.append("## 本轮没有做")
    lines.append("")
    for item in summary["not_implemented"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 数据")
    lines.append("")
    lines.append(
        "行情来自币安公开档案 `data.binance.vision` 的现货 1h/4h K 线和 USD-M 资金费率月度档案。"
        "资金费率只作为特征。原始 Parquet 不进 git。下表的哈希对应当前用来训练的已收盘样本。"
    )
    lines.append("")
    lines.append("| 序列 | 行数 | 已收盘 | 缺失 | 状态 | 数据集 SHA-256 | 起点 | 终点 |")
    lines.append("| --- | ---: | ---: | ---: | --- | --- | --- | --- |")
    for name, row in sorted(summary["data"]["spot"].items()):
        lines.append(
            f"| {name} | {row['rows']} | {row['closed_rows']} | {row['missing_count']} | {row['status']} | "
            f"`{row['dataset_hash']}` | {row['start']} | {row['end']} |"
        )
    lines.append("")
    lines.append("| 资金费率 | 行数 | 缺口 | 状态 | 数据集 SHA-256 | 第一条 | 最后一条 |")
    lines.append("| --- | ---: | ---: | --- | --- | --- | --- |")
    for symbol, row in sorted(summary["data"]["funding"].items()):
        lines.append(
            f"| {symbol} | {row['rows']} | {row['gap_count']} | {row['status']} | `{row['dataset_hash']}` | "
            f"{row['first_funding_time']} | {row['last_funding_time']} |"
        )
    lines.append("")
    if summary["data"]["funding_rest_errors"]:
        lines.append("资金费率 REST 错误（没有绕过）：")
        lines.append("")
        for item in summary["data"]["funding_rest_errors"]:
            lines.append(f"- {item['symbol']}: {item['rest_error']}")
        lines.append("")
        if any("451" in str(item.get("rest_error", "")) for item in summary["data"]["funding_rest_errors"]):
            lines.append(
                "fapi.binance.com 返回 HTTP 451。采集没有使用代理或改写身份。"
                "资金费率用已发布的月度档案。档案最后一条早于现货样本末端时，之后的 bar 只看见当时已经公布的最后一条费率，不把更晚的费率填回去。"
            )
            lines.append("")
    else:
        lines.append("本次摘要里没有记录到资金费率 REST 错误字符串。若采集日志里出现 HTTP 451，以采集清单为准，程序不会用代理绕过。")
        lines.append("")
    lines.append(f"特征表 SHA-256：`{summary['data']['feature_hash']}`。")
    lines.append(f"P1 特征配置哈希：{', '.join(f'`{item}`' for item in summary['data']['feature_config_hashes'])}。")
    lines.append("决策样本（已收盘 4h）：")
    lines.append("")
    for symbol, rows in summary["data"]["closed_decision_rows"].items():
        lines.append(
            f"- {symbol}: {rows} 根规范收盘 4h，{summary['data']['decision_start'][symbol]} 至 "
            f"{summary['data']['decision_end_close'][symbol]}。"
            f"收盘时间不是 open+4h-1ms 的 bar 已排除 {summary['data']['dropped_noncanonical_4h'][symbol]} 根，"
            f"1h 特征同样排除 {summary['data']['dropped_noncanonical_1h_features'][symbol]} 根。"
            "这些行来自档案或 REST 修订，没有被改写成标准收盘时间。"
        )
    lines.append("")
    lines.append("预先登记的整段测试折之后，尾部不足一个测试折的 bar 没有拿来补测：")
    lines.append("")
    for symbol, tail in summary["unused_tail"].items():
        lines.append(
            f"- {symbol}: {tail['bars_after_last_full_fold']} 根未使用，"
            f"第一根未使用开盘时间 {tail['first_unused_open']}"
        )
    lines.append("")
    lines.append("## 方法")
    lines.append("")
    lines.append(
        "标签是收盘到收盘的简单收益。4h、12h、24h 分别是 1、3、6 根 4h bar。"
        "若中间缺 bar，标签为空，不把两边的价格接上。特征只用该 bar 收盘时已经存在的 P1 列，"
        "再加上 `available_time` 不晚于该 4h 收盘、并且不超过 1 小时的最新 1h 特征。"
    )
    lines.append("")
    lines.append(
        "滚动方案是扩展窗口。第一折的验证段选择 Logistic 的 C、Ridge 的 alpha，以及 LightGBM 的 "
        "`num_leaves`、`min_child_samples`、`lambda_l2`。选择指标是验证集 log loss，LightGBM 在 log loss 相同的候选里再看 MAE。"
        "选出的参数冻结，后面的折只重拟合，不再调参。每一折的训练行都去掉标签与测试区间重叠的样本，"
        "并去掉测试区间之后 `embargo_bars` 根决策。验证段相对拟合段同样处理。"
        "HMM 在该折测试开始之前的观测上拟合，测试段只用前向滤波概率，不用平滑概率。"
    )
    lines.append("")
    lines.append(
        "线性基线的不确定性是该折 Ridge 的样本内残差标准差。LightGBM 的不确定性是 0.1 与 0.9 分位数预测差距的一半。"
        "方向概率的伯努利方差可以由概率本身算出。这些都不是保形区间。"
        "主模型的特征贡献用 LightGBM `pred_contrib`（树 SHAP）。`shap.TreeExplainer` 在一小段样本上做了对照，对照结果不参与选模。"
    )
    lines.append("")
    lines.append("冻结后的超参数：")
    lines.append("")
    for symbol, grid in summary["hyperparameters"].items():
        lines.append(f"### {symbol}")
        lines.append("")
        lines.append("| 模型 | 特征 | 期限 | 参数 |")
        lines.append("| --- | --- | --- | --- |")
        for key, value in sorted(grid.items()):
            model, feature_set, horizon = key.split("|")
            rendered = ", ".join(f"{name}={item}" for name, item in value.items())
            lines.append(f"| {model} | {feature_set} | {horizon} | {rendered} |")
        lines.append("")
    chosen_states = {int(fold["hmm_states"]) for fold in summary["folds"]}
    grid_cap = max(int(count) for fold in summary["folds"] for count in fold["hmm_scores"])
    if chosen_states == {grid_cap}:
        lines.append(
            f"每一折的 BIC 都选中了网格上界 {grid_cap}。状态数不是事先写成牛市、熊市、震荡或高波动，"
            "但这次数据把预先登记的上界选满了。序号仍然只是训练集发射均值的排序。"
        )
        lines.append("")
    lines.append("HMM 在各测试折训练集上选中的状态数：")
    lines.append("")
    lines.append("| 符号 | 折 | 测试开盘 | 状态数 | BIC/AIC 分数 | 训练观测数 |")
    lines.append("| --- | ---: | --- | ---: | --- | ---: |")
    for fold in summary["folds"]:
        scores = ", ".join(f"{count}:{score:.1f}" for count, score in sorted(fold["hmm_scores"].items()))
        lines.append(
            f"| {fold['symbol']} | {fold['fold_id']} | {fold['test_start']} | {fold['hmm_states']} | {scores} | {fold['hmm_n_train']} |"
        )
    lines.append("")
    lines.append("## 预测质量")
    lines.append("")
    lines.append(
        "方向准确率和 Brier 分数描述预测，不是交易目标。`fraction_above_round_trip` 只在规则使用的期限上有定义，"
        "表示预期收益超过基准往返成本的比例。样本外包含已经在 V1 中看过的区间，单独的重叠切片不能当作未触碰测试集。"
    )
    lines.append("")
    lines.append(
        "| 符号 | 模型 | 特征 | 期限 | 切片 | 计分样本 | MAE | RMSE | 方向准确率 | Brier | Spearman | 平均预测 | 平均实现 | 超过成本的比例 |"
    )
    lines.append("| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for row in summary["prediction_metrics"]:
        if row["slice"] not in {"oos", "before_v1_seen", "overlaps_v1_seen_not_a_fresh_holdout"}:
            continue
        if row["model"] == "logistic" and row["feature_set"] == "no_hmm" and row["horizon"] != summary["rule_horizon"] and row["slice"] != "oos":
            continue
        lines.append(
            "| {symbol} | {model} | {feature_set} | {horizon} | {slice} | {scored_rows} | {mae} | {rmse} | {direction_accuracy} | {brier} | {spearman} | {mean_expected_return} | {mean_realized_return} | {fraction_above_round_trip} |".format(
                **{key: _num(row[key]) for key in row}
            )
        )
    lines.append("")
    lines.append("## 策略对照")
    lines.append("")
    lines.append(
        "同一 4h 样本外区间、同一初始资金、同一 V1 成交规则。买入持有在区间第一根开盘买入。"
        "SMA 与动量使用 `configs/strategies.yaml` 里的 bar 数（20/50 与 60），没有按 4h 重新换算成日数，也没有在测试段上重选。"
        "因此它们在区间开头有一段预热。Logistic 与 LightGBM 的第一笔成交最早发生在第一根信号之后的下一根开盘。"
    )
    lines.append("")
    primary = summary["judgment"]["primary_strategy"]
    primary_rows = [
        row
        for row in summary["strategy_comparison"]
        if row["strategy_id"] == primary and row["slice"] == "oos" and row["scenario"] == "base"
    ]
    if primary_rows:
        fills = ", ".join(f"{row['symbol']} {int(row['fill_count'])} 笔" for row in primary_rows)
        returns = ", ".join(f"{row['symbol']} {_num(row['total_return'])}" for row in primary_rows)
        lines.append(
            f"主策略 `{primary}` 在全部样本外的基准成本下，总收益为 {returns}，成交 {fills}。"
            "预测有时越过 0.003 的往返成本，于是规则反复开平仓。扣费之后它没有胜过现金，也没有胜过买入持有。"
            "门槛没有在看到这张表之后改过。"
        )
        lines.append("")
    for slice_name, title in (
        ("oos", "全部滚动样本外"),
        ("before_v1_seen", "2025-01-18 之前的样本外"),
        ("overlaps_v1_seen_not_a_fresh_holdout", "与 V1 已查看窗口重叠的样本外（不是未触碰测试集）"),
    ):
        lines.append(f"### {title}")
        lines.append("")
        lines.append("| 策略 | 符号 | 成本 | 总收益 | Sharpe | 最大回撤 | 暴露 | 换手 | 期末权益 | 手续费 | 成交笔数 |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in summary["strategy_comparison"]:
            if row["slice"] != slice_name:
                continue
            lines.append(
                f"| {row['strategy_id']} | {row['symbol']} | {row['scenario']} | {_num(row['total_return'])} | "
                f"{_num(row['sharpe_ratio'])} | {_num(row['max_drawdown'])} | {_num(row['exposure'])} | "
                f"{_num(row['turnover'])} | {_num(row['final_equity'])} | {_num(row['fee_paid'])} | {_num(row['fill_count'])} |"
            )
        lines.append("")
    lines.append("## 特征贡献")
    lines.append("")
    lines.append(
        "下面是规则期限上、带 HMM 概率的 LightGBM 回归在各折测试行上的平均绝对树 SHAP。"
        "它说明模型用了哪些列，不是交易许可。"
    )
    lines.append("")
    cross = summary.get("shap_crosscheck")
    if cross:
        lines.append(
            f"SHAP 对照：{cross.get('symbol')} 第 {cross.get('fold_id')} 折，"
            f"{cross.get('rows')} 行，最大绝对差 {_num(cross.get('max_abs_diff'))}。{cross.get('note')}"
        )
        lines.append("")
    for block in summary["attribution"]:
        if block["horizon"] != summary["rule_horizon"] or block["feature_set"] != "with_hmm":
            continue
        lines.append(f"### {block['symbol']} {block['model']}（{block['rows']} 行）")
        lines.append("")
        lines.append("| 特征 | 平均绝对贡献 |")
        lines.append("| --- | ---: |")
        for item in block["mean_abs_shap"][:12]:
            lines.append(f"| {item['feature']} | {_num(item['mean_abs'])} |")
        lines.append("")
    lines.append("## 局限")
    lines.append("")
    lines.append("- 2025-01-18 至 2026-11-26 不是干净的 V2 测试集。报告里与这段重叠的结果只作披露。")
    lines.append("- 第一折验证段选出的超参数被冻结。这避免了用后面的折反复挑选最好看的参数，但也把选择绑在最早的一段验证期上。")
    lines.append("- 交易规则用 24h 预测去覆盖一次往返成本，而引擎每个 4h bar 都可以改变仓位。期限并不一致。P3 才要求收益和波动率使用同一期限。")
    lines.append("- 状态按训练集发射均值排序，换一折之后同一个序号不一定是同一种市场。报告不把它们叫成牛市或熊市。")
    lines.append("- 线性不确定性是样本内残差尺度。LightGBM 分位数宽度没有做校准。")
    lines.append("- 没有计算 PBO、DSR、嵌套样本外门槛或 bootstrap。因此即使某条曲线赚钱，也不把多试几次之后的选择偏差算进去。")
    lines.append("- V1 的 SMA 和动量窗口是 bar 数。用在 4h 上时，日历长度比日线回测短，这里没有为了更好看而加长。")
    lines.append("")
    lines.append("## 复现")
    lines.append("")
    lines.append("在仓库根目录，Windows PowerShell、CMD 和 Linux 都是同一组 `uv` 命令。")
    lines.append("")
    lines.append("```text")
    lines.append("uv sync")
    lines.append("uv run python scripts/collect_v2.py --start 2020-01-01T00:00:00Z --end 2026-11-27T00:00:00Z")
    lines.append("uv run python scripts/validate_v2.py")
    lines.append("uv run python scripts/build_v2_features.py")
    lines.append("uv run python scripts/train_v2_p2.py")
    lines.append("uv run pytest")
    lines.append("```")
    lines.append("")
    lines.append(
        "训练脚本写出本文件、`data/reports/v2/p2_summary.json` 和 git 忽略的 "
        "`data/reports/v2/p2_predictions.parquet`。预测表 SHA-256："
        f"`{summary['prediction_sha256']}`（{summary['prediction_rows']} 行）。"
    )
    lines.append("")
    return "\n".join(lines)


def _num(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)
