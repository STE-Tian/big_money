"""Show stored backtest artifacts. It does not invent metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from pquant.config.settings import Settings


def render(settings: Settings) -> None:
    summary_path = settings.storage.reports_dir / "summary.json"
    if not summary_path.exists():
        st.info("暂无结果")
        return
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    symbols = summary.get("symbols") or {}
    if not symbols:
        st.info("暂无结果")
        if summary.get("blockers"):
            st.write(summary["blockers"])
        return
    symbol = st.selectbox("交易对", list(symbols))
    payload = symbols[symbol]
    strategies = payload.get("strategies") or {}
    if not strategies:
        st.info("暂无结果")
        return
    strategy_id = st.selectbox("策略", list(strategies))
    item = strategies[strategy_id]
    judgment = item.get("judgment") or {}
    st.write(f"标签：`{judgment.get('label')}`。这不是收益承诺。")
    st.write(f"统计区间：{payload.get('start')} → {payload.get('end')} UTC。数据哈希 `{payload.get('dataset_hash')}`。")
    segments = item.get("segments") or {}
    rows = []
    for name, segment in segments.items():
        metrics = segment.get("metrics") or {}
        rows.append(
            {
                "segment": name,
                "total_return": _value(metrics, "total_return"),
                "cagr": _value(metrics, "cagr"),
                "sharpe": _value(metrics, "sharpe_ratio"),
                "max_drawdown": _value(metrics, "max_drawdown"),
                "volatility": _value(metrics, "annualized_volatility"),
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch")
    paths = item.get("artifact_paths") or {}
    for label, key in (("净值", "equity_png"), ("回撤", "drawdown_png"), ("年度", "yearly_png")):
        raw = paths.get(key)
        if raw and Path(raw).exists():
            st.image(raw, caption=label)
    trades = paths.get("trades_csv")
    if trades and Path(trades).exists():
        data = Path(trades).read_bytes()
        st.download_button("下载交易明细 CSV", data=data, file_name=Path(trades).name)
    st.subheader("成本敏感性")
    st.json(item.get("costs") or {})
    st.subheader("判断理由")
    st.write(judgment.get("reasons") or "暂无结果")


def _value(metrics: dict, name: str):
    raw = metrics.get(name) or {}
    return raw.get("value")
