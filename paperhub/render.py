"""960x540, nondithered sixteen-level dashboard and offline CLI."""

import argparse
import io
import json
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

    def text(self, x, y, value, size=22, width=None, fill=0, bold=False):
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
        self.draw.line((x, y + height - 2, x + width - 1, y + height - 2), fill=0, width=2)
        if maximum <= 0:
            return
        for index, value in enumerate(values):
            if value is None or value <= 0:
                continue
            bar_height = max(1, round(value / maximum * (height - 2)))
            left = x + index * width // 10 + 2
            right = x + (index + 1) * width // 10 - 3
            self.draw.rectangle((left, y + height - 2 - bar_height, right, y + height - 3), fill=0)

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
                    self.draw.line(points, fill=0, width=2)
                points = []
                continue
            py = y + height - 2 if high == low else y + height - 2 - (value - low) / (high - low) * (height - 4)
            points.append((x + i * (width - 1) / max(1, len(values) - 1), py))
        if len(points) > 1:
            self.draw.line(points, fill=0, width=2)
        elif points:
            px, py = points[0]
            self.draw.line((px, py, px + width - 1, py), fill=0, width=2)


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


def header(c, left, right, title, updated, failure=None, size=24):
    metadata = updated + (" " + failure if failure else " 更新")
    maximum = right - left - 140
    metadata_width = min(maximum, c.draw.textlength(metadata, font=c.font(22)))
    c.text(left, 30, title, size=size, bold=True, width=right - left - metadata_width - 12)
    c.right(right, 30, metadata, width=maximum)


def render_png(view, settings):
    c = Canvas(settings)
    d = c.draw
    d.line((500, 0, 500, 490), fill=0, width=2)
    d.line((0, 490, 959, 490), fill=0, width=2)
    s = view["strata"]
    state = " ● " + s["state"] if s else ""
    if s and s["state"].lower() not in ("idle", "—"):
        state += " " + s["phase"] + " " + s["live_tok_s"] + "t/s"
    header(c, 14, 486, "Strata" + state, view["success_labels"].get("strata", "—"),
           view["failures"].get("strata"))
    header(c, 514, 948, "AI利用量", view["success_labels"].get("xangi", "—"),
           view["failures"].get("xangi"), size=26)
    if view["status"]["strata"]:
        c.text(14, 110, view["status"]["strata"], width=472)
    elif s:
        c.text(14, 64, s["model"], width=472)
        for label, value, baseline in (("Decode", s["decode_tok_s"], 118),
                                       ("Prefill", s["prefill_tok_s"], 172)):
            c.text(14, baseline, label, size=24)
            end = c.text(130, baseline, value, size=24 if value == "—" else 48,
                         bold=value != "—", width=240)
            if value != "—":
                suffix = "t/s"
                if label == "Prefill" and s.get("prefill_cached"):
                    suffix += "（" + s["prefill_date"] + "）"
                c.text(end + 4, baseline, suffix, size=24, width=486 - end - 4)
        c.text(14, 209, "VRAM " + s["vram"].replace(" GB", "GB"), size=24, width=282)
        c.text(304, 209, "experts " + s["experts_cached"], width=182)
        ctx = s["context"].split(" ", 1)[0]
        c.text(14, 241, "RAM " + s["ram"].replace(" GB", "GB") + "   Ctx " + ctx,
               size=24, width=472)
        c.text(14, 273, f"GPU {s['gpu_util']}%  {s['gpu_temp']}°C  {s['power']}",
               size=24, width=472)
        samples = s["decode_history"]
        c.text(14, 307, "Decode", size=22)
        maximum = spark_summary(samples)
        maximum = "max " + maximum + ("t/s" if maximum not in ("idle", "—") else "")
        c.right(314, 307, maximum, width=210)
        c.bars(14, 318, samples, width=300, height=40)
        c.text(334, 307, "電力")
        current = number(s["gpu_power"])
        c.right(474, 307, f"{current:.3g}W" if current is not None else "—", width=90)
        c.spark(334, 318, [row.get("gpu_power") for row in view["history"]])
        c.text(14, 380, "直近リクエスト", size=24, bold=True)
        for i, request in enumerate(s["recent_requests"][:3]):
            y = 409 + i * 33
            c.text(14, y, request["time"][-5:], width=64)
            c.text(88, y, request["status"], width=88)
            c.right(318, y, request["tokens"].replace(" → ", "→"), width=134)
            c.right(414, y, request["decode_tok_s"] + "t/s", width=84)
            c.right(486, y, request["duration_s"] + "s", width=60)
    u = view["usage"]
    if u is None:
        c.text(514, 110, "データなし", size=26)
    else:
        y, omitted = 90, 0
        for provider in u["providers"]:
            for group in provider["groups"]:
                needed = 34 + len(group["windows"]) * 38
                if y + needed > 430:
                    omitted += 1
                    continue
                c.text(514, y, group["heading"], size=26, bold=True, width=432)
                y += 34
                for window in group["windows"]:
                    inverted = window["inverted"]
                    fg = 255 if inverted else 0
                    if inverted:
                        d.rectangle((512, y - 29, 949, y + 8), fill=0)
                    c.text(514, y, window["label"], size=24, fill=fg, width=38)
                    x, by, width = 558, y - 18, 146
                    d.rectangle((x, by, x + width, by + 14), outline=fg, width=2)
                    if window["used_percent"] is not None:
                        filled = int((width - 4) * min(100, max(0, window["used_percent"])) / 100)
                        if filled:
                            d.rectangle((x + 2, by + 2, x + 1 + filled, by + 12), fill=fg)
                    if window["pace_percent"] is not None:
                        px = x + int(width * window["pace_percent"] / 100)
                        d.line((px, by - 3, px, by + 17), fill=fg, width=3)
                    c.text(716, y, window["used_label"], size=24, fill=fg, width=68)
                    c.text(790, y, window["reset"], fill=fg, width=158)
                    y += 38
        text = zero_summary(u["zero_providers"], lambda value: d.textlength(value, font=c.font(22)))
        if text:
            c.text(514, 450, text, width=432)
        if omitted:
            c.text(514, 480, f"ほか{omitted}グループ", width=432)
    if s:
        c.text(14, 514, f"最終 {s['last_request']} ・ キャッシュ再利用 {s['cache_reuse']}", width=675)
    c.text(14, 538, "更新 " + view["updated_label"])
    telemetry = view["telemetry"]
    parts = []
    if number(telemetry.get("bat")) is not None:
        parts.append(f"電池 {telemetry['bat']}%")
    if number(telemetry.get("rssi")) is not None:
        parts.append(f"{telemetry['rssi']}dBm")
    if parts:
        c.text(700, 538, "  ".join(parts), width=248)
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
