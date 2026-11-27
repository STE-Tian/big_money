#!/usr/bin/env python
"""Train the V2 P2 models and write the research report. Does not place orders."""

from __future__ import annotations

import sys

from pquant.jobs.v2_p2 import train_main

if __name__ == "__main__":
    sys.exit(train_main())
