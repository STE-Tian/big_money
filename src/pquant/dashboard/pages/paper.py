"""Paper account view and a local simulated fill. No exchange client is imported."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import streamlit as st

from pquant.config.settings import Settings
from pquant.execution.brokers.paper import PaperBroker
from pquant.services.market import load_candles
from pquant.storage.market_store import MarketStore
from pquant.storage.trading_store import TradingStore


def render(settings: Settings) -> None:
    st.caption("模拟成交只写入本地 SQLite，不会发送到 Binance。")
    store = TradingStore(settings.storage.sqlite_path)
    try:
        store.init_schema()
        accounts = store.list_accounts()
    except Exception as exc:
        st.info(f"暂无结果（{exc}）")
        return
    base = settings.scenario("base")
    broker = PaperBroker(
        store,
        allowed_symbols=set(settings.universe_symbols),
        quote_asset=settings.backtest.quote_asset,
        fee_rate=base.fee_rate_per_side,
        slippage_rate=base.slippage_rate_per_side,
        max_drawdown_event=settings.risk.max_drawdown_event,
    )
    with st.form("create-account"):
        name = st.text_input("账户名称", value="paper")
        cash = st.text_input("初始现金", value=format(settings.backtest.initial_cash, "f"))
        submitted = st.form_submit_button("创建模拟账户")
    if submitted:
        account_id = broker.create_account(name, Decimal(cash))
        st.success(f"已创建 {account_id}")
        accounts = store.list_accounts()
    if not accounts:
        st.info("暂无结果")
        return
    labels = {f"{row['name']} {row['account_id']}": row["account_id"] for row in accounts}
    selected = st.selectbox("账户", list(labels))
    account_id = labels[selected]
    view = broker.account_view(account_id)
    st.write("现金", view["account"]["current_cash"])
    st.subheader("持仓")
    st.dataframe(view["positions"] or [{"status": "暂无结果"}], width="stretch")
    st.subheader("订单")
    st.dataframe(view["orders"] or [{"status": "暂无结果"}], width="stretch")
    st.subheader("成交")
    st.dataframe(view["fills"] or [{"status": "暂无结果"}], width="stretch")
    st.subheader("风险事件")
    st.dataframe(view["risk_events"] or [{"status": "暂无结果"}], width="stretch")
    st.subheader("用最近一根已收盘 K 线的下一根开盘做模拟成交")
    symbol = st.selectbox("标的", settings.market_data.symbols, key="paper-symbol")
    weight = st.selectbox("目标权重", ["0", "1"], index=1)
    if st.button("提交模拟目标"):
        candles = load_candles(settings, symbol, settings.market_data.intervals[0])
        closed = [c for c in candles if c.is_closed]
        if len(candles) < 2 or not closed:
            st.info("暂无结果")
            return
        signal_bar = closed[-1]
        following = next((c for c in candles if c.open_time > signal_bar.open_time), None)
        if following is None:
            st.info("没有信号之后的开盘价，暂不成交。")
            return
        instrument = None
        try:
            instrument = MarketStore(settings.storage.duckdb_path).get_instrument(symbol)
        except Exception:
            instrument = None
        if instrument is not None:
            broker.qty_step = instrument.qty_step
            broker.min_notional = instrument.min_notional
        result = broker.process_target(
            account_id=account_id,
            strategy_id="manual_paper",
            symbol=symbol,
            target_weight=Decimal(weight),
            signal_time=signal_bar.close_time,
            execution_time=following.open_time,
            execution_open=following.open,
            mark_price=following.open,
        )
        st.write(result["order"]["status"] if result.get("order") else result.get("reason"))
        st.caption(f"信号时间 {signal_bar.close_time.isoformat()}，模拟成交时间 {following.open_time.isoformat()} UTC。")
        _ = datetime.now(timezone.utc)
