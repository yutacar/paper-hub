"""Shared immutable display snapshot for PNG and JSON."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .common import number, rounded
from .strata import format_strata, is_strata_offline
from .usage import format_usage


def snapshot(settings, *, now, strata=None, usage=None, successes=None,
             failures=None, history=None, telemetry=None, last_prefill=None):
    successes, failures = successes or {}, failures or {}
    def clock(stamp):
        return datetime.fromtimestamp(stamp, ZoneInfo(settings.tz)).strftime("%H:%M")
    def status(name, data):
        if data is None:
            return "データなし"
        if name == "strata" and is_strata_offline(successes.get(name), now):
            return f"Strata オフライン（最終 {clock(successes[name])}）"
        return ""
    strata_view = format_strata(strata, tz=settings.tz) if strata is not None else None
    if strata_view is not None:
        strata_view["prefill_cached"] = False
        strata_view["prefill_date"] = None
        if strata_view["prefill_value"] is None and last_prefill:
            value = number(last_prefill.get("value"))
            if value is not None and value >= 0:
                stamp = number(last_prefill.get("at"))
                date = datetime.fromtimestamp(stamp, ZoneInfo(settings.tz)).strftime("%m/%d") if stamp is not None else "—"
                strata_view.update(prefill_value=value, prefill_tok_s=str(rounded(value)),
                                   prefill_cached=True, prefill_date=date)
    return {
        "updated_at": now, "updated_label": clock(now),
        "strata": strata_view,
        "usage": format_usage(usage, now=now, tz=settings.tz, order=settings.order,
                              hide=settings.hide) if usage is not None else None,
        "status": {name: status(name, data) for name, data in (("strata", strata), ("xangi", usage))},
        "failures": {name: f"取得失敗 {clock(stamp)}" for name, stamp in failures.items()
                     if name in successes and now - successes[name] >= 300},
        "success_labels": {name: clock(stamp) for name, stamp in successes.items()},
        "history": history or [], "telemetry": dict(telemetry or {}),
    }
