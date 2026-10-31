"""Offline calculations for the paper-hub dashboard."""

from .strata import format_strata, is_strata_offline
from .usage import format_usage, pace_percent

__all__ = ["format_strata", "format_usage", "is_strata_offline", "pace_percent"]
