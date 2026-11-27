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

## V2 P1 additions

Checked on 2026-11-27. These calls are not used by the V1 daily backtest.

- Public archives: `https://data.binance.vision`. Spot klines use `/data/spot/monthly/klines/{SYMBOL}/{INTERVAL}/` and the matching `daily` path. Each zip is verified with the sibling `.CHECKSUM` SHA-256 file. The public-data README states that spot archive timestamps from 2025-01-01 onward are microseconds. `BTCUSDT-1h-2026-11-01.zip` was observed in that form and without a header.
- USD-M funding-rate archives, monthly only: `/data/futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{YYYY-MM}.zip`. The 2026-10 BTCUSDT file has a header and the columns `calc_time,funding_interval_hours,last_funding_rate`. `calc_time` is Unix milliseconds and can include a millisecond offset. The first October 2026 BTCUSDT row is `2026-10-01T00:00:00.001Z`.
- USD-M kline archives are not downloaded. Perpetual prices are not a V2 input.
- Funding REST, research feature only: `GET https://fapi.binance.com/fapi/v1/fundingRate`. No other fapi path is allowed, and no API key is sent. The official USD-M connector notes: `startTime` and `endTime` are inclusive, rows are ascending, and a range longer than `limit` returns the first `limit` rows from `startTime`. Weight is shared 500/5min/IP with `fundingInfo` when `limit` is at most 200, and 100/5min/IP above that. The default limit is 200. `markPrice` may be an empty string on older rows; empty is stored as missing, not zero.
- The V1 spot client still rejects `/fapi/v1/fundingRate`. The funding client is separate and also rejects order, leverage, position, account, withdraw, margin, and transfer paths.
- HTTP 451 is recorded. The collector does not bypass a location restriction. If a checksummed archive already covers the requested window, that archive is the source.
