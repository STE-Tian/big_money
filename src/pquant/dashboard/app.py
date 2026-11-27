"""Streamlit workbench. It reads local services and does not call Binance."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from pquant.config.loader import load_settings  # noqa: E402
from pquant.dashboard.pages import (  # noqa: E402
    ai_research,
    backtesting,
    market,
    overview,
    paper,
    settings as settings_page,
    strategy_lab,
)
from pquant.domain.errors import ConfigError, LiveTradingDisabledError  # noqa: E402


def main() -> None:
    st.set_page_config(page_title="Personal Quant AI", layout="wide")
    st.title("Personal Quant AI")
    st.warning("历史回测和模拟交易不代表未来收益。本系统不会下真实订单。")
    try:
        settings = load_settings(ROOT)
    except (LiveTradingDisabledError, ConfigError) as exc:
        st.error(str(exc))
        st.stop()
    page = st.sidebar.radio(
        "页面",
        ["Overview", "Market", "Strategy Lab", "Backtesting", "Paper Trading", "AI Research", "Settings"],
    )
    st.sidebar.write(f"模式 {settings.app.trading_mode.value.upper()}")
    st.sidebar.write("LIVE 已在代码中禁用")
    pages = {
        "Overview": overview.render,
        "Market": market.render,
        "Strategy Lab": strategy_lab.render,
        "Backtesting": backtesting.render,
        "Paper Trading": paper.render,
        "AI Research": ai_research.render,
        "Settings": settings_page.render,
    }
    pages[page](settings)


if __name__ == "__main__":
    main()
