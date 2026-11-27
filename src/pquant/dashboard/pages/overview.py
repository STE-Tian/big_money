"""Overview page. Numbers come from local files. Empty states say 暂无结果."""

from __future__ import annotations

import json

import streamlit as st

from pquant.config.settings import Settings
from pquant.services.market import load_quality
from pquant.storage.market_store import MarketStore
from pquant.storage.research_store import ResearchStore
from pquant.storage.trading_store import TradingStore


def render(settings: Settings) -> None:
    st.subheader("系统模式")
    mode = settings.app.trading_mode.value.upper()
    st.write(f"当前模式：**{mode}**")
    st.write("RESEARCH / PAPER / TESTNET 可以在配置中声明。LIVE 在代码中禁用，不能通过页面打开。")
    st.write("实盘下单：禁用。杠杆、做空、提现、划转：禁用。")
    st.subheader("数据")
    found = False
    try:
        store = MarketStore(settings.storage.duckdb_path)
        for symbol in settings.market_data.symbols:
            for interval in settings.market_data.intervals:
                quality = load_quality(settings, symbol, interval)
                ingestion = None
                try:
                    ingestion = store.latest_ingestion(symbol, interval)
                except Exception:
                    ingestion = None
                if quality is None and ingestion is None:
                    continue
                found = True
                st.write(
                    f"{symbol} {interval}：质量 {None if quality is None else quality.get('status')}，"
                    f"最近采集 {None if ingestion is None else ingestion.get('finished_at')}"
                )
    except Exception:
        found = False
    if not found:
        st.info("暂无结果")
    st.subheader("最近回测")
    try:
        runs = ResearchStore(settings.storage.sqlite_path).list_runs(limit=5)
    except Exception:
        runs = []
    if not runs:
        st.info("暂无结果")
    else:
        st.dataframe(runs, width="stretch")
    st.subheader("模拟账户")
    try:
        accounts = TradingStore(settings.storage.sqlite_path).list_accounts()
    except Exception:
        accounts = []
    if not accounts:
        st.info("暂无结果")
    else:
        st.dataframe(accounts, width="stretch")
    summary = settings.storage.reports_dir / "summary.json"
    if summary.exists():
        payload = json.loads(summary.read_text(encoding="utf-8"))
        st.caption(f"最近一次报告生成时间：{payload.get('generated_at')}。历史结果不是未来收益。")
