"""Section 3: derive xangi usage rows, without fetching data."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .common import MISSING, mapping, number, rounded


def pace_percent(window, now):
    window = mapping(window)
    duration, reset, now = (number(window.get("windowDurationMins")),
                            number(window.get("resetsAt")), number(now))
    if None in (duration, reset, now) or duration <= 0 or reset <= now:
        return None
    return min(100.0, max(0.0, (duration * 60 - (reset - now)) / (duration * 60) * 100))


def reset_label(window, now, *, tz):
    window = mapping(window)
    duration, reset, now = (number(window.get("windowDurationMins")),
                            number(window.get("resetsAt")), number(now))
    if reset is not None and now is not None and reset <= now:
        return "リセット済"
    if None in (duration, reset, now) or duration <= 0:
        return MISSING
    if reset - now < 60:
        return "まもなく"
    if duration <= 300:
        minutes = max(0, int((reset - now) // 60))
        return f"あと{minutes // 60}h{minutes % 60:02d}m"
    dt = datetime.fromtimestamp(reset, ZoneInfo(tz))
    return f"{dt.month}/{dt.day} {dt:%H:%M}"


def provider_is_stale(provider, now):
    provider = mapping(provider)
    if provider.get("sourceType") != "live":
        return True
    try:
        updated = datetime.fromisoformat(provider["sourceUpdatedAt"].replace("Z", "+00:00"))
        if updated.tzinfo is None:
            return True
        return now - updated.timestamp() >= 600
    except (KeyError, TypeError, ValueError, AttributeError):
        return True


def _csv(value):
    return [s.strip() for s in value.split(",") if s.strip()]


def format_usage(payload, *, now, tz, order="", hide=""):
    """Return ordered providers and a trailing zero-usage summary.

    order/hide accept the corresponding environment variable strings, supplied
    by the caller. This calculation layer never reads configuration files.
    """
    ranks = {name: i for i, name in reversed(list(enumerate(_csv(order))))}
    hidden = set(_csv(hide))
    providers = mapping(payload).get("providers") or []
    providers = sorted(providers, key=lambda p: ranks.get(p.get("id"), len(ranks)))
    result, zero_labels, zero_providers = [], [], []
    for provider in providers:
        stale = provider_is_stale(provider, now)
        label = provider.get("label") or MISSING
        groups, provider_zero = [], []
        for group in provider.get("groups") or []:
            if f"{provider.get('id')}:{group.get('id')}" in hidden:
                continue
            windows = group.get("windows") or []
            # Test raw values: a positive fraction must not disappear after rounding.
            if windows and all(number(w.get("usedPercent")) == 0 for w in windows):
                raw_label = group.get("label") or MISSING
                zero_labels.append(raw_label)
                aliases = {"gemini": "Gemini", "gemini-argon": "Argon", "third-party": "Claude・GPT"}
                short = aliases.get(group.get("id")) if provider.get("id") == "antigravity" else None
                provider_zero.append(short or raw_label.replace("共通枠", "").replace("専用枠", "").strip() or raw_label)
                continue
            group_label = group.get("label") or MISSING
            plan = group.get("planType")
            heading = label + (" (古)" if stale else "")
            if group_label != label:
                heading += " " + group_label
            display_heading = heading
            heading += f" ({plan})" if plan else ""
            rows = []
            for window in windows:
                used = number(window.get("usedPercent"))
                pace = pace_percent(window, now)
                label_value = window.get("label") or MISSING
                display_label = {"5時間": "5h", "週次": "週"}.get(label_value, label_value)
                rows.append({"label": display_label,
                             "used_percent": rounded(used) if used is not None else None,
                             "used_label": f"{rounded(used)}%" if used is not None else MISSING,
                             "pace_percent": pace,
                             "reset": reset_label(window, now, tz=tz),
                             "inverted": used is not None and pace is not None and used > pace})
            groups.append({"id": group.get("id"), "heading": heading, "display_heading": display_heading, "windows": rows})
        if provider_zero:
            zero_providers.append({"id": provider.get("id"), "label": label, "groups": provider_zero})
        if groups:
            result.append({"id": provider.get("id"), "label": label + (" (古)" if stale else ""),
                           "stale": stale, "groups": groups})
    return {"providers": result, "zero_groups": zero_labels, "zero_providers": zero_providers,
            "zero_summary": "0%: " + ", ".join(zero_provider_label(item) for item in zero_providers) if zero_providers else ""}


def zero_provider_label(provider):
    return provider["label"] + "（" + ", ".join(provider["groups"]) + "）"
