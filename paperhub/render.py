"""960x540, nondithered sixteen-level dashboard and offline CLI."""

import argparse
import io
import json
import math
import time
from functools import lru_cache
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


def metric(c, box, label, value, *, kind=None, size=24):
    panel(c, box, fill=255)
    x, y, right, bottom = box
    if kind:
        icon(c, kind, x + 10, y + 9)
    c.text(x + (42 if kind else 10), y + 23, label, bold=True, width=right - x - (52 if kind else 20))
    c.text(x + 10, bottom - 12, value, size=size, bold=True, width=right - x - 20)


def memory_metric(c, box, label, value, *, kind="chip"):
    """Keep capacity and unit intact by moving GB into the heading if needed."""
    compact = value.replace(" / ", "/").replace(" GB", "GB")
    width = box[2] - box[0] - 20
    if c.draw.textlength(compact, font=c.font(22, True)) <= width:
        metric(c, box, label, compact, kind=kind, size=22)
        return
    capacity = compact.removesuffix("GB")
    if c.draw.textlength(capacity, font=c.font(22, True)) <= width:
        metric(c, box, label + " GB", capacity, size=22)
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
        c.right(535, 65, state, size=24, width=250)
    metadata(c, view, "strata", 535, 38, 330)
    if view["status"]["strata"]:
        c.text(24, 121, view["status"]["strata"], width=500)
        return
    if not s:
        return
    model = s["model"]
    if s["state"].lower() not in ("idle", "—"):
        model = s["phase"] + " " + s["live_tok_s"] + " t/s · " + model
    c.text(20, 65, model, width=535 - state_width - 32)
    panel(c, (12, 78, 540, 178))
    icon(c, "chip", 24, 98)
    c.text(58, 119, "Decode", size=24, bold=True)
    end = c.text(166, 124, s["decode_tok_s"], size=54, bold=True, width=160)
    c.text(end, 124, " t/s", size=24, width=58)
    samples = s["decode_history"]
    maximum = spark_summary(samples)
    maximum = "max " + maximum + (" t/s" if maximum not in ("idle", "—") else "")
    c.right(528, 103, maximum, width=190)
    c.bars(230, 130, samples, width=300, height=40)

    # Prefill gets a wider card so a persisted date remains readable at 22px.
    prefill = s["prefill_tok_s"]
    if prefill != "—":
        prefill += " t/s"
        if s.get("prefill_cached"):
            prefill += "（" + s["prefill_date"] + "）"
    metric(c, (12, 182, 232, 267), "Prefill", prefill, kind="layers",
           size=22 if s.get("prefill_cached") else 24)
    memories = (((240, 182, 390, 267), "VRAM", s["vram"]),
                ((398, 182, 540, 267), "RAM", s["ram"]))
    # Keep both headings consistent when either card needs GB in its heading.
    show_chips = all(c.draw.textlength(value.replace(" / ", "/").replace(" GB", "GB"),
                                     font=c.font(22, True)) <= box[2] - box[0] - 20
                     for box, _, value in memories)
    for box, label, value in memories:
        memory_metric(c, box, label, value, kind="chip" if show_chips else None)
    metric(c, (12, 269, 128, 331), "GPU", s["gpu_util"] + "%", size=26)
    metric(c, (136, 269, 252, 331), "温度", s["gpu_temp"] + "°C", size=26)
    panel(c, (260, 269, 540, 331))
    c.text(270, 291, "電力", bold=True)
    c.right(528, 291, s["power"], width=198)
    c.spark(270, 297, [row.get("gpu_power") for row in view["history"]], width=140, height=32)
    c.draw.line((420, 300, 420, 327), fill=102, width=2)
    c.text(430, 328, "Ctx " + s["context"].split(" ", 1)[0], width=100)

    panel(c, (12, 339, 540, 465))
    c.draw.rounded_rectangle((13, 340, 217, 369), radius=8, fill=85)
    c.text(24, 364, "直近リクエスト", bold=True, fill=255)
    c.right(528, 364, "experts " + s["experts_cached"], width=285)
    for i, request in enumerate(s["recent_requests"][:3]):
        y = 394 + i * 33
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
    metadata(c, view, "xangi", 940, 38, 200, failure_y=65)
    panel(c, (564, 78, 946, 453))
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
    panel(c, (5, 473, 954, 534), fill=255)
    s = view["strata"]
    icon(c, "layers", 20, 490)
    c.text(54, 511, "キャッシュ再利用率", bold=True, width=215)
    c.draw.line((269, 482, 269, 526), fill=102, width=2)
    value = s["cache_reuse"] if s else "—"
    c.text(282, 519, value, size=40, bold=True, width=120)
    ratio = number(s.get("cache_reuse_ratio")) if s else None
    percent = ratio * 100 if ratio is not None else None
    c.draw.ellipse((411, 483, 453, 525), outline=102, width=2)
    if percent is not None:
        c.draw.arc((411, 483, 453, 525), start=-90, end=-90 + max(0, min(100, percent)) * 3.6, fill=85, width=8)
    c.draw.line((468, 482, 468, 526), fill=102, width=2)
    c.text(482, 497, "最終リクエスト", width=210)
    c.text(482, 525, s["last_request"] if s else "—", size=24, width=210)
    telemetry = view["telemetry"]
    parts = []
    if number(telemetry.get("bat")) is not None:
        parts.append(f"電池 {telemetry['bat']}%")
    if number(telemetry.get("rssi")) is not None:
        parts.append(f"{telemetry['rssi']}dBm")
    if parts:
        c.draw.line((700, 482, 700, 526), fill=102, width=2)
        c.text(714, 513, "  ".join(parts), width=230)


def render_png(view, settings):
    c = Canvas(settings)
    panel(c, (5, 5, 547, 467), fill=238)
    panel(c, (557, 5, 954, 461), fill=238)
    draw_strata(c, view)
    draw_usage(c, view)
    draw_footer(c, view)
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
        args.out.write_bytes(render_png(view, settings))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"描画に失敗しました: {exc}\n")


if __name__ == "__main__":
    main()
