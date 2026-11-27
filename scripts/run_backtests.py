#!/usr/bin/env python
"""Run baseline backtests on local market data."""

from __future__ import annotations

import sys

from pquant.jobs.run_backtests import main

if __name__ == "__main__":
    sys.exit(main())
