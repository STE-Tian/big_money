"""Configuration view. Secret values are not displayed because they are not loaded."""

from __future__ import annotations

import streamlit as st

from pquant.config.settings import Settings


def render(settings: Settings) -> None:
    st.subheader("模式")
    st.write(settings.app.trading_mode.value.upper())
    st.write("LIVE：代码禁用")
    st.write(f"live_trading_enabled = {settings.app.live_trading_enabled}")
    st.subheader("数据源")
    st.write(settings.market_data.base_url)
    st.write(settings.market_data.fallback_base_url)
    st.write("标的", settings.market_data.symbols)
    st.write("周期", settings.market_data.intervals)
    st.write("历史起点", settings.market_data.history_start)
    st.subheader("成本假设（不是账户实际费率）")
    for name, scenario in settings.costs.scenarios.items():
        st.write(
            name,
            "fee",
            format(scenario.fee_rate_per_side, "f"),
            "slippage",
            format(scenario.slippage_rate_per_side, "f"),
        )
    st.subheader("安全开关")
    st.json(
        {
            "allow_live_orders": settings.safety.allow_live_orders,
            "allow_leverage": settings.safety.allow_leverage,
            "allow_short": settings.safety.allow_short,
            "allow_withdrawals": settings.safety.allow_withdrawals,
        }
    )
    st.caption("API Secret：不使用，也不显示。公开行情不需要 API Key。")
    if settings.ignored_env_names:
        st.write("检测到但未读取的环境变量名：", ", ".join(settings.ignored_env_names))
