#!/usr/bin/env python
"""Initialize DuckDB and SQLite."""

from __future__ import annotations

import sys

from pquant.jobs.init_db import main

if __name__ == "__main__":
    sys.exit(main())
