"""Paper portfolio reads its state from SQLite. This module only converts stored decimals."""

from __future__ import annotations

from decimal import Decimal


def dec(value: object) -> Decimal:
    return Decimal(str(value))
