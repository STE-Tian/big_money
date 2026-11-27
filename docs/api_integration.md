# Binance public market data

Checked on 2026-11-27 against the Spot REST document in `binance/binance-spot-api-docs` (`rest-api.md`, Kline/Candlestick data) and a live request.

- Market-data base URL: `https://data-api.binance.vision`
- Fallback REST base URL: `https://api.binance.com`
- `GET /api/v3/klines`: weight 2, `limit` default 500, maximum 1000. A live `limit=1001` request still returned 1000 rows.
- `GET /api/v3/exchangeInfo`, `/api/v3/time`, `/api/v3/ticker/price`, `/api/v3/ticker/24hr` are the only other calls.
- `exchangeInfo` reported `REQUEST_WEIGHT` 6000 per minute.
- Response headers observed: `x-mbx-used-weight` and `x-mbx-used-weight-1m`.
- HTTP 418 stops the client. HTTP 429 waits for `Retry-After`. HTTP 5xx uses a limited exponential backoff and may try the fallback host.
- The first daily candles returned for BTCUSDT and ETHUSDT opened at 2017-08-17T00:00:00Z.
- Order, account, withdraw, and margin paths are rejected before a request is sent.
- No API key is sent. WebSocket streams and Spot Testnet order lifecycle are not part of this build.
