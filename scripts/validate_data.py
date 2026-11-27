#!/usr/bin/env python
"""Validate stored candles and write a data-quality report."""

from __future__ import annotations

import sys

from pquant.jobs.validate_data import main

if __name__ == "__main__":
    sys.exit(main())
