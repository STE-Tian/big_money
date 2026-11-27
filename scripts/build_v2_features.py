#!/usr/bin/env python
"""Build the V2 point-in-time feature store."""

from __future__ import annotations

import sys

from pquant.jobs.v2_data import build_main

if __name__ == "__main__":
    sys.exit(build_main())
