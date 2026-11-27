"""Local candlestick view. This page does not call Binance."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pquant.config.settings import Settings
from pquant.research.factors.momentum import momentum
from pquant.research.factors.moving_average import simple_moving_average
from pquant.services.market import load_candles


def render(settings: Settings) -> None:
    symbol = st.selectbox("交易对", settings.market_data.symbols)
    interval = st.selectbox("周期", ["1d", "4h", "1h"], index=0)
    candles = load_candles(settings, symbol, interval)
    if not candles:
        st.info("暂无结果")
        return
    frame = pd.DataFrame(
        {
            "open_time": [c.open_time for c in candles],
            "open": [float(c.open) for c in candles],
            "high": [float(c.high) for c in candles],
            "low": [float(c.low) for c in candles],
            "close": [float(c.close) for c in candles],
            "volume": [float(c.volume) for c in candles],
            "is_closed": [c.is_closed for c in candles],
        }
    )
    start, end = st.slider("显示区间（按行号）", 0, len(frame) - 1, (max(0, len(frame) - 365), len(frame) - 1))
    view = frame.iloc[start : end + 1].copy()
    view["sma20"] = simple_moving_average(view["close"], 20)
    view["sma50"] = simple_moving_average(view["close"], 50)
    view["momentum60"] = momentum(view["close"], 60)
    figure = go.Figure(
        data=[
            go.Candlestick(
                x=view["open_time"],
                open=view["open"],
                high=view["high"],
                low=view["low"],
                close=view["close"],
                name="OHLC",
            )
        ]
    )
    figure.add_trace(go.Scatter(x=view["open_time"], y=view["sma20"], name="SMA20"))
    figure.add_trace(go.Scatter(x=view["open_time"], y=view["sma50"], name="SMA50"))
    figure.update_layout(title=f"{symbol} {interval} UTC", xaxis_rangeslider_visible=False)
    st.plotly_chart(figure, width="stretch")
    volume = go.Figure(go.Bar(x=view["open_time"], y=view["volume"], name="volume"))
    volume.update_layout(title="成交量")
    st.plotly_chart(volume, width="stretch")
    mom = go.Figure(go.Scatter(x=view["open_time"], y=view["momentum60"], name="momentum 60"))
    mom.update_layout(title="60 日动量（窗口内可见数据；窗口不足时为空）")
    st.plotly_chart(mom, width="stretch")
    st.caption("时间使用交易所 UTC K 线，不会改写原始时间。未收盘 K 线如果存在，会显示但不会被历史回测使用。")
