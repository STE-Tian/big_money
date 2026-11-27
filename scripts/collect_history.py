#!/usr/bin/env python
"""Download public Binance spot klines."""

from __future__ import annotations

import sys

from pquant.jobs.collect_history import main

if __name__ == "__main__":
    sys.exit(main())
