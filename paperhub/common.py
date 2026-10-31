"""Shared formatting; no environment or network access."""

import math
from collections.abc import Mapping

MISSING = "—"


def number(value):
    """Accept finite JSON numbers, excluding booleans."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value):
            return value
    return None


def mapping(value):
    return value if isinstance(value, Mapping) else {}


def rounded(value):
    """Round nonnegative display values half up, rather than ties to even."""
    return math.floor(value + 0.5)


def shown(value):
    return MISSING if value is None else str(value)


def compact_tokens(value, *, capacity=False):
    value = number(value)
    if value is None:
        return MISSING
    if value < 1000:
        return str(rounded(value))
    if capacity:
        return f"{value / 1024:.0f}K"
    return f"{value / 1000:.1f}".rstrip("0").rstrip(".") + "k"
