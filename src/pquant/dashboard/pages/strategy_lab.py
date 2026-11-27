"""Strategy descriptions and stored versions."""

from __future__ import annotations

import streamlit as st

from pquant.config.settings import Settings
from pquant.research.strategies.registry import build_strategies, robustness_grid
from pquant.storage.research_store import ResearchStore


def render(settings: Settings) -> None:
    st.subheader("已登记策略")
    for strategy in build_strategies(settings):
        description = {
            "buy_and_hold": settings.strategies.buy_and_hold.description,
            "sma_cross": settings.strategies.sma_cross.description,
            "momentum": settings.strategies.momentum.description,
            "cash": settings.strategies.cash.description,
        }.get(strategy.strategy_id, "")
        st.markdown(f"**{strategy.strategy_id}** `{strategy.version}`")
        st.write(description)
    pairs, windows = robustness_grid(settings)
    st.subheader("稳健性网格（只用于 Train / Validation）")
    st.write({"sma_pairs": pairs, "momentum_windows": windows})
    st.subheader("策略版本")
    try:
        rows = ResearchStore(settings.storage.sqlite_path).list_strategies()
    except Exception:
        rows = []
    if not rows:
        st.info("暂无结果")
    else:
        st.dataframe(rows, width="stretch")
    st.caption("实验由命令 `uv run python scripts/run_backtests.py` 写入。页面不会连接交易所。")
