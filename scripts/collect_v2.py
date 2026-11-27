#!/usr/bin/env python
"""Download V2 spot klines and funding-rate features."""

from __future__ import annotations

import sys

from pquant.jobs.v2_data import collect_main

if __name__ == "__main__":
    sys.exit(collect_main())
