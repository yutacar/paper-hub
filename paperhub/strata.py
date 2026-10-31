"""Section 2: derive display values from the Strata monitor JSON."""

from datetime import datetime
from zoneinfo import ZoneInfo

from .common import MISSING, compact_tokens, mapping, number, rounded, shown


def prefill_tokens_per_second(request):
    request = mapping(request)
    prompt, reused, ms = (number(request.get(k)) for k in
                          ("prompt_tokens", "reused", "prompt_ms"))
    if None in (prompt, reused, ms) or ms <= 0:
        return None
    return (prompt - reused) / (ms / 1000)


def context_usage(request, max_context):
    request = mapping(request)
    prompt, output, maximum = (number(request.get("prompt_tokens")),
                               number(request.get("output_tokens")), number(max_context))
    if None in (prompt, output, maximum) or maximum <= 0:
        return None
    return {"tokens": prompt + output, "capacity": maximum,
            "percent": (prompt + output) / maximum * 100}


def relative_time(now, timestamp):
    now, timestamp = number(now), number(timestamp)
    if now is None or timestamp is None:
        return MISSING
    seconds = max(0, int(now - timestamp))
    days, hours = seconds // 86400, seconds % 86400 // 3600
    if days:
        return f"{days}日{hours}時間前"
    if hours:
        return f"{hours}時間前"
    if seconds >= 60:
        return f"{seconds // 60}分前"
    return f"{seconds}秒前"


def is_strata_offline(last_success, now):
    """No success means 'no data'; offline starts at exactly ten minutes."""
    last_success, now = number(last_success), number(now)
    return last_success is not None and now is not None and now - last_success >= 600


def _memory(hardware, used_key, total_key):
    used, total = number(hardware.get(used_key)), number(hardware.get(total_key))
    return (f"{used / 2**30:.1f}" if used is not None else MISSING) + " / " + (
        str(rounded(total / 2**30)) if total is not None else MISSING) + " GB"


def _request_time(timestamp, now, timezone):
    timestamp = number(timestamp)
    if timestamp is None:
        return MISSING
    dt = datetime.fromtimestamp(timestamp, timezone)
    today = datetime.fromtimestamp(now, timezone).date()
    return dt.strftime("%H:%M" if dt.date() == today else "%m/%d %H:%M")


def prefill_sample(payload):
    """Latest qualifying request, with its source timestamp (not poll time)."""
    for item in mapping(payload).get("requests") or []:
        item = mapping(item)
        prompt, reused = number(item.get("prompt_tokens")), number(item.get("reused"))
        if prompt is not None and reused is not None and prompt - reused >= 512:
            value = prefill_tokens_per_second(item)
            return {"value": value, "at": number(item.get("time"))} if value is not None else None
    return None


def format_strata(payload, *, tz):
    """Use payload.time as the snapshot clock; timezone must be explicit."""
    payload = mapping(payload)
    engine, hardware, live, totals = (mapping(payload.get(k)) for k in
                                     ("engine", "hardware", "live", "totals"))
    requests = payload.get("requests") or []
    latest = mapping(requests[0]) if requests else {}
    now = number(payload.get("time"))
    context = context_usage(latest, engine.get("max_context"))
    sample = prefill_sample(payload)
    prefill = sample["value"] if sample else None
    reused, prompt = number(totals.get("reused")), number(totals.get("prompt_tokens"))
    ratio = reused / prompt if reused is not None and prompt is not None and prompt > 0 else None
    state = live.get("state")
    power = number(hardware.get("gpu_power"))
    power_limit = number(hardware.get("gpu_power_limit"))
    context_tokens = (str(rounded(context["tokens"])) if context["tokens"] < 1000
                      else f'{rounded(context["tokens"] / 1000)}k') if context else MISSING
    result = {
        "state": shown(state).capitalize(), "phase": MISSING, "live_tok_s": MISSING,
        "model": shown(engine.get("model")),
        "decode_tok_s": shown(number(latest.get("decode_tok_s"))),
        "prefill_tok_s": str(rounded(prefill)) if prefill is not None else MISSING,
        "prefill_value": prefill, "context_value": context,
        "context": (f"{rounded(context['percent'])}% "
                    f"({context_tokens} / "
                    f"{compact_tokens(context['capacity'], capacity=True)})") if context else MISSING,
        "vram": _memory(hardware, "gpu_mem_used", "gpu_mem_total"),
        "ram": _memory(hardware, "ram_used", "ram_total"),
        "experts_cached": shown(engine.get("expert_slots")),
        "gpu_power": power,
        "decode_history": [number(mapping(item).get("decode_tok_s")) for item in reversed(requests[:10])],
        "gpu_util": shown(number(hardware.get("gpu_util"))),
        "gpu_temp": shown(number(hardware.get("gpu_temp"))),
        "power": f"{rounded(power) if power is not None else MISSING} / "
                 f"{rounded(power_limit) if power_limit is not None else MISSING} W",
        "pcie": f"Gen{shown(hardware.get('gpu_pcie_gen'))} x{shown(hardware.get('gpu_pcie_width'))}",
        "cpu": shown(number(hardware.get("cpu"))),
        "last_request": relative_time(now, latest.get("time")),
        "cache_reuse_ratio": ratio,
        "cache_reuse": f"{rounded(ratio * 100)}%" if ratio is not None else MISSING,
        "recent_requests": [],
    }
    if state is not None and state != "idle":
        result.update(phase=shown(live.get("phase")), live_tok_s=shown(number(live.get("tok_s"))))
    timezone = ZoneInfo(tz)
    for item in requests[:3]:
        item = mapping(item)
        finish = item.get("finish")
        result["recent_requests"].append({
            "time": _request_time(item.get("time"), now, timezone) if now is not None else MISSING,
            "status": {"length": "MaxTok", "stop": "Done"}.get(finish, shown(finish)),
            "tokens": f"{compact_tokens(item.get('prompt_tokens'))} → {shown(item.get('output_tokens'))}",
            "decode_tok_s": shown(number(item.get("decode_tok_s"))),
            "duration_s": shown(number(item.get("duration_s"))),
        })
    return result
