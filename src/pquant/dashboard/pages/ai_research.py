"""Run the deterministic LangGraph research workflow on local data."""

from __future__ import annotations

import streamlit as st

from pquant.agents.tools import ResearchToolbox
from pquant.agents.workflow import run_research
from pquant.config.settings import Settings
from pquant.services.market import load_candles, load_quality


def render(settings: Settings) -> None:
    st.caption("Agent 只能调用研究工具白名单。它不能下单、读取密钥，也不能执行生成的代码。")
    objective = st.text_input("研究问题", value="比较 BTC 与 ETH 上的基准策略")
    symbols = st.multiselect("标的", settings.universe_symbols, default=list(settings.universe_symbols))
    strategies = st.multiselect(
        "策略",
        ["buy_and_hold", "sma_cross", "momentum", "cash"],
        default=["buy_and_hold", "sma_cross"],
    )
    if st.button("运行研究流程"):
        candles = {}
        quality = {}
        for symbol in symbols:
            rows = load_candles(settings, symbol, settings.market_data.intervals[0])
            if rows:
                candles[symbol] = rows
                report = load_quality(settings, symbol, settings.market_data.intervals[0])
                if report:
                    quality[symbol] = report
        if not candles:
            st.info("暂无结果")
            return
        tools = ResearchToolbox(settings, candles, quality)
        result = run_research(
            tools,
            {
                "objective": objective,
                "symbols": symbols,
                "timeframe": settings.market_data.intervals[0],
                "strategy_ids": strategies,
                "fee_scenario": "base",
                "slippage_scenario": "base",
                "validation_scheme": "time_split",
                "experiment_budget": min(10, max(1, len(symbols) * len(strategies))),
            },
        )
        st.write("节点状态")
        st.json(result.get("node_status") or {})
        st.write("run_id")
        st.write(result.get("run_ids") or "暂无结果")
        st.text(result.get("report") or "暂无结果")
