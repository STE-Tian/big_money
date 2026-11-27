# Backtest methodology

A target chosen from the close of bar t is filled at the open of bar t+1.

- Buy price = next open × (1 + slippage).
- Sell price = next open × (1 - slippage).
- Fee = fill notional × fee rate, deducted from cash.
- The reported slippage cost is the notional difference versus the raw open. It is already inside the fill price and is not deducted again.
- The book starts in cash. A new order is created only when the target weight changes.
- The last bar can form a signal, but that signal is not filled inside the sample.
- Buy & Hold is known before the first open, so its single buy is the first open.
- SMA and momentum emit no target during warm-up.
- The sample is not force-closed. Open quantity is marked at the last close.
- Cash is a zero-return benchmark with the same initial cash and cost settings.
- Daily Sharpe uses the sample standard deviation of per-bar excess returns and `sqrt(365)`. Four-hour bars use `sqrt(365 * 6)`. The risk-free rate defaults to 0 and is stated in the report.
- Sortino divides by the square root of the mean squared negative excess return.
- Calmar is undefined when max drawdown is 0. Profit factor is undefined when there is no losing closed round trip. Win rate uses flat-to-flat round trips only.
- Train / validation / test are the earliest 60% / next 20% / last 20% of closed bars. The parameter grid does not see the test segment.
- Walk-forward uses a 730-day train window when history allows, otherwise 365 days, then a 90-day test stepped by 90 days.
- Missing bars are not forward-filled.
