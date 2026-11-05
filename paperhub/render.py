"""960x540, nondithered sixteen-level dashboard and offline CLI."""

import argparse
import io
import json
import math
import time
from functools import lru_cache
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .common import number
from .config import Settings
from .dashboard import snapshot
from .usage import zero_provider_label


class Canvas:
    def __init__(self, settings):
        self.settings = settings
        self.image = Image.new("L", (960, 540), 255)
        self.draw = ImageDraw.Draw(self.image)

    @lru_cache(maxsize=16)
    def font(self, size, bold=False):
        return ImageFont.truetype(self.settings.font_path_bold if bold else self.settings.font_path, size)

    def text(self, x, y, value, size=22, width=None, fill=34, bold=False):
        value = str(value)
        font = self.font(size, bold)
        if width is not None and self.draw.textlength(value, font=font) > width:
            while value and self.draw.textlength(value + "…", font=font) > width:
                value = value[:-1]
            value += "…"
        if "°C" in value:
            # Some CJK fonts give the degree symbol a full em of advance.
            # Keep its original baseline, closing only its trailing blank space.
            before, after = value.split("°C", 1)
            self.draw.text((x, y), before + "°", font=font, fill=fill, anchor="ls")
            degree_ink = font.getmask("°").getbbox()
            advance = min(font.getlength("°"), degree_ink[2] + 2) if degree_ink else font.getlength("°")
            cursor = x + self.draw.textlength(before, font=font) + advance
            self.draw.text((cursor, y), "C" + after, font=font, fill=fill, anchor="ls")
            return cursor + self.draw.textlength("C" + after, font=font)
        self.draw.text((x, y), value, font=font, fill=fill, anchor="ls")
        return x + self.draw.textlength(value, font=font)

    def right(self, right, y, value, size=22, width=None, **kwargs):
        value = str(value)
        font = self.font(size, kwargs.get("bold", False))
        if width is not None and self.draw.textlength(value, font=font) > width:
            while value and self.draw.textlength(value + "…", font=font) > width:
                value = value[:-1]
            value += "…"
        x = right - self.draw.textlength(value, font=font)
        return self.text(x, y, value, size=size, **kwargs)

    def bars(self, x, y, samples, width=300, height=40):
        values = [number(value) for value in samples[-10:]]
        values = [None] * (10 - len(values)) + values
        maximum = max((value for value in values if value is not None and value > 0), default=0)
        self.draw.line((x, y + height - 2, x + width - 1, y + height - 2), fill=85, width=2)
        if maximum <= 0:
            return
        for index, value in enumerate(values):
            if value is None or value <= 0:
                continue
            bar_height = max(1, round(value / maximum * (height - 2)))
            left = x + index * width // 10 + 2
            right = x + (index + 1) * width // 10 - 3
            self.draw.rectangle((left, y + height - 2 - bar_height, right, y + height - 3), fill=85)

    def spark(self, x, y, samples, width=140, height=32):
        values = [number(v) for v in samples]
        finite = [v for v in values if v is not None]
        if not finite:
            return
        low, high = min(finite), max(finite)
        points = []
        for i, value in enumerate(values):
            if value is None:
                if len(points) > 1:
                    self.draw.line(points, fill=85, width=2)
                points = []
                continue
            py = y + height - 2 if high == low else y + height - 2 - (value - low) / (high - low) * (height - 4)
            points.append((x + i * (width - 1) / max(1, len(values) - 1), py))
        if len(points) > 1:
            self.draw.line(points, fill=85, width=2)
        elif points:
            px, py = points[0]
            self.draw.line((px, py, px + width - 1, py), fill=85, width=2)


def spark_summary(samples):
    values = [number(value) for value in samples]
    finite = [value for value in values if value is not None]
    if not finite:
        return "—"
    if all(value == 0 for value in finite):
        return "idle"
    maximum = max(finite)
    return f"{maximum:.3g}"


def zero_summary(providers, measure, width=432):
    """Provider-grouped summary, kept to one line at the normal font size."""
    if not providers:
        return ""
    text = "0%: " + ", ".join(zero_provider_label(provider) for provider in providers)
    if measure(text) <= width:
        return text
    while text and measure(text + "…") > width:
        text = text[:-1]
    return text + "…"


def panel(c, box, radius=10, fill=255):
    c.draw.rounded_rectangle(box, radius=radius, outline=102, fill=fill, width=2)


def icon(c, kind, x, y, size=24):
    """Original monochrome line icons; no downloaded brand assets."""
    d = c.draw
    if kind == "layers":
        for offset in (12, 6, 0):
            d.line([(x, y + 6 + offset), (x + size / 2, y + offset),
                    (x + size, y + 6 + offset), (x + size / 2, y + 12 + offset),
                    (x, y + 6 + offset)], fill=51, width=2)
    elif kind == "bars":
        for i in range(3):
            d.rectangle((x + i * 8, y + 18 - i * 7, x + i * 8 + 5, y + 24), fill=51)
    elif kind == "chip":
        d.rectangle((x + 4, y + 4, x + 20, y + 20), outline=51, width=2)
        for offset in (7, 12, 17):
            for line in ((x, y + offset, x + 4, y + offset),
                         (x + 20, y + offset, x + 24, y + offset),
                         (x + offset, y, x + offset, y + 4),
                         (x + offset, y + 20, x + offset, y + 24)):
                d.line(line, fill=51, width=2)

def provider_icon(c, provider_id, x, y):
    """Small geometric provider marks, drawn directly at display resolution."""
    d = c.draw
    if provider_id == "claude-code":
        for i in range(8):
            angle = i * math.pi / 8
            dx, dy = 11 * math.cos(angle), 11 * math.sin(angle)
            d.line((x + 12 - dx, y + 12 - dy, x + 12 + dx, y + 12 + dy), fill=255, width=2)
    elif provider_id == "codex":
        for i in range(6):
            angle = i * math.pi / 3
            cx, cy = x + 12 + 6 * math.cos(angle), y + 12 + 6 * math.sin(angle)
            d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), outline=255, width=2)
    elif provider_id == "antigravity":
        d.polygon([(x + 12, y), (x + 16, y + 8), (x + 24, y + 12),
                   (x + 16, y + 16), (x + 12, y + 24), (x + 8, y + 16),
                   (x, y + 12), (x + 8, y + 8)], fill=85, outline=255, width=2)
    else:
        d.ellipse((x + 2, y + 2, x + 22, y + 22), outline=255, width=2)


def metric(c, box, label, value, *, kind=None, size=24, centered=False):
    panel(c, box, fill=255)
    x, y, right, bottom = box
    if kind:
        icon(c, kind, x + 10, y + (12 if centered else 9))
    c.text(x + (42 if kind else 10), y + (30 if centered else 23), label, bold=True, width=right - x - (52 if kind else 20))
    c.text(x + 10, y + 66 if centered else bottom - 12, value, size=size, bold=True, width=right - x - 20)


def compact_memory(value):
    return value.replace(" / ", "/").replace(" GB", "GB")


def fits_memory(c, box, text):
    return c.draw.textlength(text, font=c.font(22, True)) <= box[2] - box[0] - 20


def memory_metric(c, box, label, value, *, kind="chip"):
    """Keep capacity and unit intact by moving GB into the heading if needed."""
    compact = compact_memory(value)
    if fits_memory(c, box, compact):
        metric(c, box, label, compact, kind=kind, size=22, centered=True)
        return
    capacity = compact.removesuffix("GB")
    if fits_memory(c, box, capacity):
        metric(c, box, label + " GB", capacity, size=22, centered=True)
        return
    # Very large capacities use two rows at the same minimum font size.
    panel(c, box)
    x, y, right, bottom = box
    c.text(x + 10, y + 20, label + " GB", bold=True)
    used, total = capacity.split("/", 1)
    c.text(x + 10, y + 49, used, bold=True)
    c.text(x + 10, y + 78, "/" + total, bold=True)


def metadata(c, view, source, right, y, width, failure_y=None):
    updated = view["success_labels"].get(source, "—")
    failure = view["failures"].get(source)
    text = updated + (" " + failure if failure and failure_y is None else " 更新")
    c.right(right, y, text, width=width, fill=85)
    if failure and failure_y is not None:
        c.right(right, failure_y, updated + " " + failure, width=366, fill=85)


def draw_strata(c, view):
    s = view["strata"]
    icon(c, "layers", 20, 17)
    c.text(55, 39, "Strata", size=30, bold=True)
    state_width = 0
    if s:
        state = "● " + s["state"]
        state_width = min(250, c.draw.textlength(state, font=c.font(24)))
        c.right(535, 64, state, size=24, width=250)
    metadata(c, view, "strata", 535, 38, 330)
    if view["status"]["strata"]:
        c.text(24, 121, view["status"]["strata"], width=500)
        return
    if not s:
        return
    model = s["model"]
    if s["state"].lower() not in ("idle", "—"):
        model = s["phase"] + " " + s["live_tok_s"] + " t/s · " + model
    c.text(20, 64, model, width=535 - state_width - 32)
    panel(c, (12, 72, 540, 158))
    icon(c, "chip", 24, 78)
    c.text(58, 99, "Decode", size=24, bold=True)
    end = c.text(24, 150, s["decode_tok_s"], size=54, bold=True, width=160)
    c.text(end, 150, " t/s", size=24, width=58)
    samples = s["decode_history"]
    maximum = spark_summary(samples)
    maximum = "max " + maximum + (" t/s" if maximum not in ("idle", "—") else "")
    c.right(528, 99, maximum, width=190)
    c.bars(230, 110, samples, width=300, height=40)

    # Prefill gets a wider card so a persisted date remains readable at 22px.
    prefill = s["prefill_tok_s"]
    if prefill != "—":
        prefill += " t/s"
        if s.get("prefill_cached"):
            prefill += "（" + s["prefill_date"] + "）"
    metric(c, (12, 166, 232, 251), "Prefill", prefill, kind="layers",
           size=22 if s.get("prefill_cached") else 24, centered=True)
    memories = (((240, 166, 390, 251), "VRAM", s["vram"]),
                ((398, 166, 540, 251), "RAM", s["ram"]))
    # Keep both headings consistent when either card needs GB in its heading.
    show_chips = all(fits_memory(c, box, compact_memory(value)) for box, _, value in memories)
    for box, label, value in memories:
        memory_metric(c, box, label, value, kind="chip" if show_chips else None)
    metric(c, (12, 259, 128, 321), "GPU", s["gpu_util"] + "%", size=26)
    metric(c, (136, 259, 252, 321), "温度", s["gpu_temp"] + "°C", size=26)
    panel(c, (260, 259, 540, 321))
    c.text(270, 281, "電力", bold=True)
    c.right(528, 281, s["power"], width=198)
    c.spark(270, 287, [row.get("gpu_power") for row in view["history"]], width=140, height=32)
    c.draw.line((420, 290, 420, 317), fill=102, width=2)
    c.text(430, 318, "Ctx " + s["context"].split(" ", 1)[0], width=100)

    panel(c, (12, 329, 540, 455))
    c.draw.rounded_rectangle((13, 330, 217, 359), radius=8, fill=85)
    c.text(24, 354, "直近リクエスト", bold=True, fill=255)
    c.right(528, 354, "experts " + s["experts_cached"], width=285)
    for i, request in enumerate(s["recent_requests"][:3]):
        y = 384 + i * 33
        c.text(24, y, request["time"][-5:], width=64)
        c.text(98, y, request["status"], width=88)
        c.right(340, y, request["tokens"].replace(" → ", "→"), width=144)
        c.right(449, y, request["decode_tok_s"] + " t/s", width=99)
        c.right(528, y, request["duration_s"] + "s", width=69)
        if i < 2:
            for x in range(24, 528, 7):
                c.draw.line((x, y + 6, x + 3, y + 6), fill=102, width=1)


def draw_usage(c, view):
    icon(c, "bars", 568, 17)
    c.text(604, 39, "AI利用量", size=30, bold=True)
    metadata(c, view, "xangi", 940, 38, 200, failure_y=64)
    panel(c, (564, 72, 946, 455))
    u = view["usage"]
    if u is None:
        c.text(578, 118, "データなし", size=26)
        return
    y, omitted = 107, 0
    for provider in u["providers"]:
        for group in provider["groups"]:
            needed = 34 + len(group["windows"]) * 33
            if y + needed - 33 > 402:
                omitted += 1
                continue
            c.draw.rounded_rectangle((571, y - 25, 939, y + 6), radius=5, fill=85)
            provider_icon(c, provider["id"], 578, y - 22)
            c.text(612, y, group["display_heading"], bold=True, width=320, fill=255)
            y += 34
            for window in group["windows"]:
                inverted = window["inverted"]
                fg = 255 if inverted else 34
                if inverted:
                    c.draw.rectangle((570, y - 26, 940, y + 5), fill=51)
                c.text(578, y, window["label"], fill=fg, width=34)
                x, by, width = 615, y - 17, 110
                c.draw.rectangle((x, by, x + width, by + 14), outline=fg, width=2)
                if window["used_percent"] is not None:
                    filled = int((width - 4) * min(100, max(0, window["used_percent"])) / 100)
                    if filled:
                        c.draw.rectangle((x + 2, by + 2, x + 1 + filled, by + 12), fill=187 if inverted else 119)
                if window["pace_percent"] is not None:
                    px = x + int(width * window["pace_percent"] / 100)
                    c.draw.line((px, by - 3, px, by + 17), fill=fg, width=3)
                c.right(783, y, window["used_label"], fill=fg, width=54)
                c.text(793, y, window["reset"], fill=fg, width=145)
                y += 33
            for x in range(576, 936, 7):
                c.draw.line((x, y - 16, x + 3, y - 16), fill=102, width=1)
            y += 12
    text = zero_summary(u["zero_providers"], lambda value: c.draw.textlength(value, font=c.font(22)), width=354)
    if omitted:
        text = f"ほか{omitted}グループ" + (" · " + text if text else "")
    if text:
        c.text(578, 443, text, width=354)


def draw_footer(c, view):
    panel(c, (5, 471, 954, 534), fill=255)
    s = view["strata"]
    icon(c, "layers", 20, 488)
    c.text(54, 509, "キャッシュ再利用率", bold=True, width=215)
    c.draw.line((269, 480, 269, 524), fill=102, width=2)
    value = s["cache_reuse"] if s else "—"
    c.text(282, 517, value, size=40, bold=True, width=120)
    ratio = number(s.get("cache_reuse_ratio")) if s else None
    percent = ratio * 100 if ratio is not None else None
    c.draw.ellipse((411, 481, 453, 523), outline=102, width=2)
    if percent is not None:
        c.draw.arc((411, 481, 453, 523), start=-90, end=-90 + max(0, min(100, percent)) * 3.6, fill=85, width=8)
    c.draw.line((468, 480, 468, 524), fill=102, width=2)
    c.text(482, 496, "最終リクエスト", width=210)
    c.text(482, 528, s["last_request"] if s else "—", size=24, width=210)
    telemetry = view["telemetry"]
    parts = []
    if number(telemetry.get("bat")) is not None:
        parts.append(f"電池 {telemetry['bat']}%")
    if number(telemetry.get("rssi")) is not None:
        parts.append(f"{telemetry['rssi']}dBm")
    if parts:
        c.draw.line((700, 480, 700, 524), fill=102, width=2)
        c.text(714, 511, "  ".join(parts), width=230)


def detail_header(c, view, title, source):
    panel(c, (5, 5, 954, 479), fill=238)
    c.text(20, 39, title, size=32, bold=True, width=460)
    metadata(c, view, source, 940, 39, 430)


def detail_footer(c, view, mode):
    panel(c, (5, 487, 954, 534))
    last = view["strata"]["last_request"] if view["strata"] else "—"
    c.text(20, 517, "最終 " + last, width=350)
    parts = []
    for key, suffix in (("bat", "%"), ("rssi", "dBm")):
        value = number(view["telemetry"].get(key))
        if value is not None:
            parts.append(("電池 " if key == "bat" else "") + str(value) + suffix)
    if parts:
        c.text(382, 517, "  ".join(parts), width=242)
    position = "○ ● ○  B: AI利用量" if mode == "strata" else "○ ○ ●  B: ダッシュボード"
    c.right(940, 517, position, width=300)


def detail_bar(c, x, y, width, percent, *, height=8, ink=119):
    c.draw.rectangle((x, y, x + width, y + height), outline=102, width=2)
    value = number(percent)
    if value is not None:
        filled = round((width - 4) * max(0, min(100, value)) / 100)
        if filled:
            c.draw.rectangle((x + 2, y + 2, x + 1 + filled, y + height - 2), fill=ink)


def draw_strata_detail(c, view):
    detail_header(c, view, "Strata 詳細", "strata")
    detail_footer(c, view, "strata")
    s = view["strata"]
    if view["status"]["strata"]:
        c.text(24, 125, view["status"]["strata"], size=28, width=904)
        return
    if not s:
        return
    c.text(20, 68, s["model"], width=680)
    c.right(940, 68, "● " + s["state"], size=24, width=220)
    panel(c, (12, 80, 460, 199))
    c.text(28, 108, "Decode", size=26, bold=True)
    maximum = spark_summary(s["decode_history"])
    c.right(446, 108, "max " + maximum + (" t/s" if maximum not in ("idle", "—") else ""), width=260)
    end = c.text(28, 164, s["decode_tok_s"], size=64, bold=True, width=310)
    c.text(end, 164, " t/s", size=26, width=80)
    c.bars(28, 173, s["decode_history"], width=418, height=20)
    panel(c, (468, 80, 704, 199))
    c.text(484, 108, "Prefill", size=26, bold=True)
    value = s["prefill_tok_s"]
    c.text(484, 160, value + (" t/s" if value != "—" else ""),
           size=24 if value == "—" else 32, bold=True, width=204)
    if s.get("prefill_cached"):
        c.text(484, 191, "履歴 " + s["prefill_date"], width=204)
    panel(c, (712, 80, 948, 199))
    c.text(728, 108, "Context", size=26, bold=True)
    ctx = s.get("context_value")
    percent = ctx["percent"] if ctx else None
    c.text(728, 151, f"{percent:.1f}%" if percent is not None else "—", size=36, bold=True, width=204)
    c.text(728, 179, s["context"].split(" ", 1)[-1] if ctx else "—", width=204)
    detail_bar(c, 728, 188, 204, percent)
    hardware = view.get("details", {}).get("hardware", {})
    for x, label, key, prefix in ((12, "VRAM", "vram", "gpu_mem"), (328, "RAM", "ram", "ram")):
        panel(c, (x, 207, x + 308, 277))
        c.text(x + 16, 230, label, size=22, bold=True)
        c.right(x + 292, 262, compact_memory(s[key]), size=24, width=276)
        used, total = number(hardware.get(prefix + "_used")), number(hardware.get(prefix + "_total"))
        ratio = used / total * 100 if used is not None and total is not None and total > 0 else None
        detail_bar(c, x + 16, 267, 276, ratio, height=6)
    panel(c, (644, 207, 948, 277))
    c.text(660, 235, "キャッシュ再利用", size=24, bold=True, width=224)
    c.text(660, 268, s["cache_reuse"], size=34, bold=True, width=160)
    ratio = number(s["cache_reuse_ratio"])
    c.draw.ellipse((882, 217, 932, 267), outline=102, width=2)
    if ratio is not None:
        c.draw.arc((882, 217, 932, 267), -90, -90 + max(0, min(1, ratio)) * 360, fill=85, width=9)
    panel(c, (12, 285, 636, 345))
    c.text(28, 312, f"GPU {s['gpu_util']}%   {s['gpu_temp']}°C   {s['power']}", size=24, width=592)
    samples = [row.get("gpu_power") for row in view["history"]]
    c.spark(28, 319, samples, width=350, height=18)
    c.right(620, 338, "電力 max " + spark_summary(samples) + " W", width=230)
    panel(c, (644, 285, 948, 345))
    c.text(660, 308, "CPU " + s["cpu"] + "%", width=272)
    c.text(660, 337, s["pcie"], width=136)
    c.right(932, 337, "experts " + s["experts_cached"], width=162)
    panel(c, (12, 353, 948, 471))
    c.text(28, 378, "直近リクエスト", size=24, bold=True)
    stamps = view.get("details", {}).get("request_times", [])
    for i, request in enumerate(s["recent_requests"][:3]):
        y = 407 + i * 29
        stamp = stamps[i] if i < len(stamps) else None
        label = datetime.fromtimestamp(stamp, ZoneInfo(c.settings.tz)).strftime("%m/%d %H:%M") if stamp is not None else request["time"]
        c.text(28, y, label, width=164)
        c.text(208, y, request["status"], width=116)
        c.right(568, y, request["tokens"].replace(" → ", "→"), width=232)
        c.right(764, y, request["decode_tok_s"] + " t/s", width=180)
        c.right(932, y, request["duration_s"] + " s", width=152)


def detail_reset(view, provider, group, index, tz):
    resets = view.get("details", {}).get("usage_resets", {}).get(provider, {}).get(group, [])
    stamp = number(resets[index]) if index < len(resets) else None
    if stamp is None:
        return "—", "—"
    remaining = stamp - view["updated_at"]
    if remaining <= 0:
        relative = "リセット済"
    elif remaining < 60:
        relative = "まもなく"
    else:
        minutes = int(remaining // 60)
        relative = f"あと{minutes // 60}h{minutes % 60:02d}m"
    return relative, datetime.fromtimestamp(stamp, ZoneInfo(tz)).strftime("%m/%d %H:%M")


def draw_usage_detail(c, view):
    detail_header(c, view, "AI利用量 詳細", "xangi")
    panel(c, (12, 68, 948, 471))
    detail_footer(c, view, "usage")
    u = view["usage"]
    if u is None:
        c.text(24, 125, "データなし", size=28)
        return
    y, omitted = 100, 0
    groups = [(provider, group) for provider in u["providers"] for group in provider["groups"]]
    for index, (provider, group) in enumerate(groups):
        if y + 32 + max(0, len(group["windows"]) - 1) * 36 > 425:
            omitted = len(groups) - index
            break
        c.draw.rounded_rectangle((18, y - 26, 942, y + 7), radius=7, fill=85)
        provider_icon(c, provider["id"], 30, y - 23)
        c.text(68, y, group["display_heading"], size=26, bold=True, fill=255, width=858)
        y += 32
        for i, window in enumerate(group["windows"]):
            fg = 255 if window["inverted"] else 34
            if window["inverted"]:
                c.draw.rectangle((18, y - 26, 942, y + 7), fill=51)
            c.text(30, y, window["label"], size=24, fill=fg, width=50)
            x, by, width = 94, y - 18, 300
            c.draw.rectangle((x, by, x + width, by + 16), outline=fg, width=2)
            used = window["used_percent"]
            if used is not None:
                fill = round((width - 4) * max(0, min(100, used)) / 100)
                if fill:
                    c.draw.rectangle((x + 2, by + 2, x + 1 + fill, by + 14), fill=187 if window["inverted"] else 119)
            if window["pace_percent"] is not None:
                px = x + round(width * window["pace_percent"] / 100)
                c.draw.line((px, by - 3, px, by + 19), fill=fg, width=3)
            c.right(484, y, window["used_label"], size=26, fill=fg, width=78)
            relative, absolute = detail_reset(view, provider["id"], group["id"], i, c.settings.tz)
            c.text(508, y, relative, fill=fg, width=196)
            c.text(724, y, absolute, fill=fg, width=202)
            y += 36
        y += 12
    summary = zero_summary(u["zero_providers"], lambda text: c.draw.textlength(text, font=c.font(22)), width=912)
    if omitted:
        summary = f"ほか{omitted}グループ" + (" · " + summary if summary else "")
    if summary:
        c.text(24, 463, summary, width=912)


def render_png(view, settings, mode="dashboard"):
    c = Canvas(settings)
    if mode == "dashboard":
        panel(c, (5, 5, 547, 463), fill=238)
        panel(c, (557, 5, 954, 463), fill=238)
        draw_strata(c, view)
        draw_usage(c, view)
        draw_footer(c, view)
    elif mode == "strata":
        draw_strata_detail(c, view)
    elif mode == "usage":
        draw_usage_detail(c, view)
    else:
        raise ValueError("Unknown paper view")
    image = c.image.point([int((i + 8) // 17) * 17 for i in range(256)])
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def fixture_history(strata, now):
    """Use only the five fixture history fields also persisted by the server."""
    source = strata.get("history") or {}
    keys = ("gpu_util", "gpu_power", "cpu", "disk_read_mb", "tok_s")
    count = min(120, max((len(source.get(key) or []) for key in keys), default=0))
    rows = []
    for index in range(count):
        row = {"at": now - (count - 1 - index) * 30}
        for key in keys:
            values = source.get(key) or []
            offset = len(values) - count + index
            row[key] = values[offset] if 0 <= offset < len(values) else None
        rows.append(row)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strata", required=True, type=Path)
    parser.add_argument("--usage", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--view", choices=("dashboard", "strata", "usage"), default="dashboard")
    parser.add_argument("--now", type=float, default=None)
    parser.add_argument("--history-db", type=Path, help="既存SQLiteの最後の有効Prefillを参照（読取専用）")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(server=False)
        strata = json.loads(args.strata.read_text(encoding="utf-8"))
        usage = json.loads(args.usage.read_text(encoding="utf-8"))
        now = args.now if args.now is not None else time.time()
        last_prefill = None
        if args.history_db is not None:
            import sqlite3
            from .history import History
            try:
                last_prefill = History.read_prefill(args.history_db)
            except sqlite3.Error:
                raise ValueError("履歴DBを読み込めません") from None
        view = snapshot(settings, now=now, strata=strata, usage=usage, last_prefill=last_prefill,
                        successes={"strata": now, "xangi": now},
                        history=fixture_history(strata, now))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_bytes(render_png(view, settings, mode=args.view))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"描画に失敗しました: {exc}\n")


if __name__ == "__main__":
    main()
