#!/usr/bin/env python
"""Validate stored V2 spot klines and funding-rate features."""

from __future__ import annotations

import sys

from pquant.jobs.v2_data import validate_main

if __name__ == "__main__":
    sys.exit(validate_main())
