"""Binance archive timestamps are milliseconds, except spot files from 2025-01-01.

The public-data README states that spot archive timestamps from 2025-01-01
onward are microseconds. A millisecond timestamp through year 5138 stays below
1e14, so values at or above that threshold are microseconds. REST klines remain
milliseconds. Integer division by 1000 matches the REST close time of
open + interval - 1 millisecond.
"""

from __future__ import annotations

_MICROSECOND_THRESHOLD = 100_000_000_000_000


def epoch_to_ms(value: int) -> int:
    raw = int(value)
    if raw >= _MICROSECOND_THRESHOLD:
        return raw // 1000
    return raw
