import io

import pytest
from PIL import Image, ImageFont

from paperhub.config import Settings
from paperhub.dashboard import snapshot
from paperhub.render import Canvas, render_png


@pytest.fixture
def settings():
    return Settings(font_path="test-font", font_path_bold="test-font")


@pytest.fixture
def drawing_font(monkeypatch):
    monkeypatch.setattr(Canvas, "font", lambda self, size, bold=False: ImageFont.load_default(size=size))


def test_png_format_and_levels(settings, drawing_font, strata, usage):
    view = snapshot(settings, now=1793411985, strata=strata, usage=usage,
                    successes={"strata": 1793411985, "xangi": 1793411985})
    image = Image.open(io.BytesIO(render_png(view, settings)))
    assert image.size == (960, 540)
    assert image.mode == "L"
    assert set(image.tobytes()) <= set(range(0, 256, 17))
    assert 0 not in image.tobytes()
    assert image.getpixel((547, 300)) == 102
    assert image.getpixel((100, 471)) == 102


def test_no_data_and_offline(settings, drawing_font, strata):
    empty = snapshot(settings, now=1000)
    assert empty["status"] == {"strata": "データなし", "xangi": "データなし"}
    assert render_png(empty, settings).startswith(b"\x89PNG")
    stale = snapshot(settings, now=1000, strata=strata, successes={"strata": 400},
                     failures={"strata": 999}, telemetry={"bat": 70, "rssi": -60})
    assert "オフライン" in stale["status"]["strata"]
    assert stale["failures"]["strata"].startswith("取得失敗 ")
    assert render_png(stale, settings)


def test_settings_validation(monkeypatch):
    with pytest.raises(ValueError, match="FONT_PATH.*STRATA_MONITOR_URL.*XANGI_USAGE_URL"):
        Settings.load(values={})
    monkeypatch.setattr(ImageFont, "truetype", lambda *a, **kw: object())
    values = {"FONT_PATH": "test-font", "STRATA_MONITOR_URL": "http://127.0.0.1/metrics",
              "XANGI_USAGE_URL": "http://127.0.0.1/api/usage"}
    result = Settings.load(values=values)
    assert result.font_path_bold == result.font_path
    for key, value, message in (("TZ", "invalid", "TZ"), ("PORT", "0", "PORT"),
                                ("STRATA_MONITOR_URL", "bad", "STRATA_MONITOR_URL")):
        with pytest.raises(ValueError, match=message):
            Settings.load(values={**values, key: value})


def test_cli_from_fixtures(tmp_path, settings, drawing_font, monkeypatch):
    from pathlib import Path
    from paperhub.render import main
    monkeypatch.setattr(Settings, "load", lambda **kwargs: settings)
    root = Path(__file__).parent / "fixtures"
    output = tmp_path / "nested" / "paper.png"
    main(["--strata", str(root / "strata_monitor.json"), "--usage", str(root / "xangi_usage.json"),
          "--out", str(output), "--now", "1793411985"])
    assert Image.open(output).size == (960, 540)


def test_spark_zero_line_and_usage_inversion(settings, drawing_font, strata, usage):
    from paperhub.render import fixture_history
    rows = fixture_history(strata, 1793411985)
    assert len(rows) == 60
    assert rows[-1]["gpu_util"] == 0
    c = Canvas(settings)
    c.spark(0, 0, [0] * 120)
    assert c.image.getpixel((70, 30)) == 85
    # A just-opened window has zero pace; positive use must be inverted.
    window = usage["providers"][0]["groups"][0]["windows"][0]
    window.update(usedPercent=50, resetsAt=1793411985 + 300 * 60)
    view = snapshot(settings, now=1793411985, strata=strata, usage=usage, history=rows)
    image = Image.open(io.BytesIO(render_png(view, settings)))
    # Claude is first; Codex follows in the second card section.
    assert image.getpixel((938, 210)) == 85  # Heading remains lighter.
    assert image.getpixel((938, 244)) == 51


def test_readable_rows_and_metric_baseline(settings, drawing_font, strata, usage, monkeypatch):
    calls = []
    original = Canvas.text
    def record(self, x, y, value, size=22, **kwargs):
        calls.append((x, y, str(value), size))
        return original(self, x, y, value, size=size, **kwargs)
    monkeypatch.setattr(Canvas, "text", record)
    view = snapshot(settings, now=1793411985, strata=strata, usage=usage)
    render_png(view, settings)
    assert all(size >= 22 for _, _, _, size in calls)
    recent = [call for call in calls if "→" in call[2]]
    assert len(recent) == 3
    for _, y, _, _ in recent:
        row = [text for _, baseline, text, _ in calls if baseline == y]
        assert len(row) == 5
        assert row[-2].endswith("t/s")
        assert row[-1].endswith("s")
    assert all(b[1] - a[1] >= max(a[3], b[3]) * 1.3 for a, b in zip(recent, recent[1:]))
    decode = next(call for call in calls if call[2] == "37.0")
    unit = next(call for call in calls if call[2] == " t/s")
    assert unit[1] == decode[1]
    assert unit[0] > decode[0]
    assert any(text == "48°C" for _, _, text, _ in calls)
    assert any(text == "8 / 180 W" for _, _, text, _ in calls)
    assert any(text == "experts 3189" for _, _, text, _ in calls)
    assert not any("Gen4" in text or "CPU" in text for _, _, text, _ in calls)


@pytest.mark.parametrize("samples,expected", [([0, 0, None], "idle"),
    ([None], "—"), ([1, 3.25, 2], "3.25"), ([0, -1], "0")])
def test_spark_max(samples, expected):
    from paperhub.render import spark_summary
    assert spark_summary(samples) == expected


def test_expired_window_has_no_pace_line(settings, drawing_font, usage, monkeypatch):
    from PIL import ImageDraw
    from paperhub.usage import format_usage
    provider = usage["providers"][0]
    provider["groups"] = provider["groups"][:1]
    provider["groups"][0]["windows"] = provider["groups"][0]["windows"][:1]
    window = provider["groups"][0]["windows"][0]
    usage["providers"] = [provider]
    lines = []
    original = ImageDraw.ImageDraw.line
    def record(self, xy, fill=None, width=0, **kwargs):
        if width == 3:
            lines.append(xy)
        return original(self, xy, fill=fill, width=width, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "line", record)
    window["resetsAt"] = 1793411985
    view = snapshot(settings, now=1793411985, usage=usage)
    render_png(view, settings)
    assert lines == []
    window["resetsAt"] += 1
    view = snapshot(settings, now=1793411985, usage=usage)
    render_png(view, settings)
    assert len(lines) == 1


def test_zero_summary_shortening_and_ellipsis():
    from paperhub.render import zero_summary
    providers = [{"label": "P", "groups": ["A", "B"]}]
    assert zero_summary(providers, len, 30) == "0%: P（A, B）"
    result = zero_summary(providers, len, 10)
    assert len(result) == 10 and result.endswith("…")


def test_fixed_request_columns_and_single_summary(settings, drawing_font, strata, usage, monkeypatch):
    cells, summaries = [], []
    original, text_original = Canvas.right, Canvas.text
    def right(self, x, y, value, **kwargs):
        if y in (384, 417, 450):
            cells.append((x, y, value))
        return original(self, x, y, value, **kwargs)
    def text(self, x, y, value, **kwargs):
        if str(value).startswith("0%:"):
            summaries.append((x, y, value))
        return text_original(self, x, y, value, **kwargs)
    monkeypatch.setattr(Canvas, "right", right)
    monkeypatch.setattr(Canvas, "text", text)
    render_png(snapshot(settings, now=1793411985, strata=strata, usage=usage), settings)
    assert len(summaries) == 1
    assert "\n" not in summaries[0][2]
    for y in (384, 417, 450):
        assert [x for x, baseline, _ in cells if baseline == y] == [340, 449, 528]
    c = Canvas(settings)
    end = c.right(318, 50, "65→182", width=134)
    assert end == pytest.approx(318)


@pytest.mark.parametrize("telemetry,expected", [({}, None), ({"bat": 87, "rssi": -58}, "電池 87%  -58dBm"),
    ({"bat": 0}, "電池 0%"), ({"rssi": -58}, "-58dBm")])
def test_footer_only_received_values(settings, drawing_font, telemetry, expected, monkeypatch):
    values = []
    original = Canvas.text
    def record(self, x, y, value, **kwargs):
        if x == 714 and y == 511:
            values.append(value)
        return original(self, x, y, value, **kwargs)
    monkeypatch.setattr(Canvas, "text", record)
    render_png(snapshot(settings, now=1793411985, telemetry=telemetry), settings)
    assert values == ([] if expected is None else [expected])


def test_prefill_cached_date_and_small_missing(settings, drawing_font, strata, monkeypatch):
    calls = []
    original = Canvas.text
    def record(self, x, y, value, size=22, **kwargs):
        calls.append((value, y, size))
        return original(self, x, y, value, size=size, **kwargs)
    monkeypatch.setattr(Canvas, "text", record)
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    assert ("—", 232, 24) in calls
    calls.clear()
    render_png(snapshot(settings, now=1793411985, strata=strata,
                        last_prefill={"value": 131, "at": 1793142000}), settings)
    assert ("131 t/s（10/28）", 232, 22) in calls
    # A current qualifying request overrides the stored fallback.
    strata["requests"] = [{"prompt_tokens": 1000, "reused": 0, "prompt_ms": 1000}]
    view = snapshot(settings, now=1793411985, strata=strata, last_prefill={"value": 131, "at": 1793142000})
    assert view["strata"]["prefill_value"] == 1000
    assert not view["strata"]["prefill_cached"]


def test_decode_bars_bounds_and_missing_slots(settings, drawing_font):
    canvas = Canvas(settings)
    canvas.bars(0, 0, [None, 1, 2], width=300, height=40)
    pixels = canvas.image.load()
    assert pixels[285, 0] == 85  # Latest, maximum request reaches the top.
    assert pixels[255, 0] == 255  # Lower request has a shorter bar.
    assert pixels[225, 20] == 255  # Missing request has no bar.
    assert pixels[300, 20] == 255
    assert pixels[285, 40] == 255


def test_cli_reads_prefill_without_modifying_db(tmp_path, settings, drawing_font, monkeypatch):
    from pathlib import Path
    from paperhub.history import History
    from paperhub.render import main
    monkeypatch.setattr(Settings, "load", lambda **kwargs: settings)
    path = tmp_path / "saved.db"
    store = History(path)
    store.record_strata({"requests": [{"prompt_tokens": 1310, "reused": 0, "prompt_ms": 10000,
                                      "time": 1793142000}]}, 1793411985)
    store.close()
    before = path.read_bytes()
    captions = []
    original = Canvas.text
    def record(self, x, y, value, **kwargs):
        captions.append(value)
        return original(self, x, y, value, **kwargs)
    monkeypatch.setattr(Canvas, "text", record)
    fixtures = Path(__file__).parent / "fixtures"
    main(["--strata", str(fixtures / "strata_monitor.json"), "--usage", str(fixtures / "xangi_usage.json"),
          "--out", str(tmp_path / "image.png"), "--now", "1793411985", "--history-db", str(path)])
    assert "131 t/s（10/28）" in captions
    assert path.read_bytes() == before


@pytest.mark.parametrize("source", ["strata", "xangi"])
@pytest.mark.parametrize("age,visible", [(299.999, False), (300, True), (301, True)])
def test_failure_display_five_minute_boundary(settings, strata, usage, source, age, visible):
    view = snapshot(settings, now=1793411985, strata=strata, usage=usage,
                    successes={source: 1793411985 - age}, failures={source: 1793411985})
    assert (source in view["failures"]) is visible


def test_failure_header_preserves_body_layout(settings, drawing_font, strata, usage, monkeypatch):
    calls = []
    original = Canvas.text
    def record(self, x, y, value, size=22, **kwargs):
        calls.append((x, y, value, size))
        return original(self, x, y, value, size=size, **kwargs)
    monkeypatch.setattr(Canvas, "text", record)
    args = dict(now=1793411985, strata=strata, usage=usage,
                successes={"strata": 1793411685, "xangi": 1793411685})
    render_png(snapshot(settings, **args), settings)
    def is_metadata(call):
        return str(call[2]).endswith("更新") or "取得失敗" in str(call[2])
    body = [call for call in calls if not is_metadata(call)]
    calls.clear()
    render_png(snapshot(settings, **args, failures={"strata": 1793411985, "xangi": 1793411985}), settings)
    assert [call for call in calls if not is_metadata(call)] == body
    failures = [call for call in calls if "取得失敗" in str(call[2])]
    assert len(failures) == 2
    assert [y for _, y, _, _ in failures] == [38, 65]
    assert all("10:54" in text and "10:59" in text for _, _, text, _ in failures)
    assert failures[0][0] > 150
    assert failures[1][0] > 650


def test_cards_keep_three_usage_groups_and_decode_chart(settings, drawing_font, strata, usage, monkeypatch):
    texts, charts = [], []
    original_text, original_bars = Canvas.text, Canvas.bars
    def text(self, x, y, value, **kwargs):
        texts.append(str(value))
        return original_text(self, x, y, value, **kwargs)
    def bars(self, x, y, samples, width=300, height=40):
        charts.append((x, y, width, height))
        return original_bars(self, x, y, samples, width=width, height=height)
    monkeypatch.setattr(Canvas, "text", text)
    monkeypatch.setattr(Canvas, "bars", bars)
    render_png(snapshot(settings, now=1793411985, strata=strata, usage=usage), settings)
    assert any("Antigravity Gemini" in value for value in texts)
    assert not any(value.startswith("ほか") for value in texts)
    assert charts == [(230, 110, 300, 40)]


def test_long_and_missing_content_stays_inside_canvas(settings, drawing_font, strata, usage, monkeypatch):
    from PIL import ImageDraw
    boxes = []
    original = ImageDraw.ImageDraw.text
    def text(self, xy, value, **kwargs):
        boxes.append(self.textbbox(xy, value, font=kwargs["font"], anchor=kwargs["anchor"]))
        return original(self, xy, value, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    strata["engine"]["model"] = "Very long model name " * 20
    strata["requests"] = []
    for provider in usage["providers"]:
        provider["label"] = "Long provider name " * 10
    render_png(snapshot(settings, now=1793411985, strata=strata, usage=usage,
                        last_prefill={"value": 131, "at": 1793142000},
                        telemetry={"bat": 100, "rssi": -200}), settings)
    assert boxes
    assert all(0 <= left <= right <= 960 and 0 <= top <= bottom <= 540
               for left, top, right, bottom in boxes)


@pytest.mark.parametrize("ratio,expected", [(0, -90), (0.74, 176.4), (None, None)])
def test_reuse_ring_uses_numeric_ratio(settings, drawing_font, strata, ratio, expected, monkeypatch):
    from PIL import ImageDraw
    arcs = []
    original = ImageDraw.ImageDraw.arc
    def arc(self, box, start, end, **kwargs):
        arcs.append((start, end))
        return original(self, box, start=start, end=end, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "arc", arc)
    view = snapshot(settings, now=1793411985, strata=strata)
    view["strata"]["cache_reuse_ratio"] = ratio
    render_png(view, settings)
    if expected is None:
        assert arcs == []
    else:
        assert len(arcs) == 1
        assert arcs[0] == pytest.approx((-90, expected))


def test_provider_icons_plan_free_headings_and_top_update(settings, drawing_font, strata, usage, monkeypatch):
    import paperhub.render as renderer
    texts, icons, updates = [], [], []
    original_text, original_right = Canvas.text, Canvas.right
    original_icon = renderer.provider_icon
    def text(self, x, y, value, **kwargs):
        texts.append(str(value))
        return original_text(self, x, y, value, **kwargs)
    def right(self, x, y, value, **kwargs):
        if str(value).endswith("更新"):
            updates.append((x, y))
        return original_right(self, x, y, value, **kwargs)
    def icon(canvas, provider_id, x, y):
        icons.append(provider_id)
        return original_icon(canvas, provider_id, x, y)
    monkeypatch.setattr(Canvas, "text", text)
    monkeypatch.setattr(Canvas, "right", right)
    monkeypatch.setattr(renderer, "provider_icon", icon)
    view = snapshot(settings, now=1793411985, strata=strata, usage=usage)
    image = Image.open(io.BytesIO(render_png(view, settings)))
    assert icons == ["claude-code", "codex", "antigravity"]
    assert not any("(pro)" in value or "(plus)" in value for value in texts)
    assert updates == [(535, 38), (940, 38)]
    assert image.getpixel((800, 110)) == 85  # Same dark background as request heading.
    assert image.getpixel((635, 165)) == 119  # Gray usage fill, distinct from text.


def test_generating_state_is_not_truncated(settings, drawing_font, strata, monkeypatch):
    from PIL import ImageDraw
    texts = []
    original = ImageDraw.ImageDraw.text
    def text(self, xy, value, **kwargs):
        texts.append(str(value))
        return original(self, xy, value, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    strata["live"]["state"] = "generating"
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    assert "● Generating" in texts


@pytest.mark.parametrize("used,total", [(100.2, 128), (900.2, 1024)])
def test_large_memory_capacity_and_unit_remain_complete(settings, drawing_font, strata, monkeypatch, used, total):
    from PIL import ImageDraw
    texts = []
    original = ImageDraw.ImageDraw.text
    def text(self, xy, value, **kwargs):
        texts.append((xy, str(value)))
        return original(self, xy, value, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", text)
    for prefix in ("gpu_mem", "ram"):
        strata["hardware"][prefix + "_used"] = used * 2**30
        strata["hardware"][prefix + "_total"] = total * 2**30
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    for label, left in (("VRAM", 240), ("RAM", 398)):
        cells = [value for (x, y), value in texts if x == left + 10 and 166 < y < 251]
        assert (f"{used}/{total}GB" in cells or
                label + " GB" in cells and (f"{used}/{total}" in cells or
                                           str(used) in cells and "/" + str(total) in cells))


def test_power_chart_clears_card_border(settings, drawing_font, strata, monkeypatch):
    charts = []
    original = Canvas.spark
    def spark(self, x, y, samples, width=140, height=32):
        charts.append((y, height))
        return original(self, x, y, samples, width=width, height=height)
    monkeypatch.setattr(Canvas, "spark", spark)
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    assert charts and all(y + height <= 319 for y, height in charts)


def test_two_line_memory_keeps_readable_baseline_spacing(settings, drawing_font, monkeypatch):
    from paperhub.render import memory_metric
    rows = []
    original = Canvas.text
    def text(self, x, y, value, size=22, **kwargs):
        rows.append((y, size, str(value)))
        return original(self, x, y, value, size=size, **kwargs)
    monkeypatch.setattr(Canvas, "text", text)
    memory_metric(Canvas(settings), (398, 166, 540, 251), "RAM", "12345.6 / 65536 GB")
    assert [value for _, _, value in rows] == ["RAM GB", "12345.6", "/65536"]
    assert all(b[0] - a[0] >= max(a[1], b[1]) * 1.3 for a, b in zip(rows, rows[1:]))
    assert rows[-1][0] <= 244


def test_memory_icons_are_consistent_for_mixed_capacities(settings, drawing_font, strata, monkeypatch):
    import paperhub.render as renderer
    kinds = []
    original = renderer.memory_metric
    def memory(canvas, box, label, value, **kwargs):
        kinds.append(kwargs["kind"])
        return original(canvas, box, label, value, **kwargs)
    monkeypatch.setattr(renderer, "memory_metric", memory)
    strata["hardware"]["ram_used"] = 900.2 * 2**30
    strata["hardware"]["ram_total"] = 1024 * 2**30
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    assert kinds == [None, None]


def test_card_rows_keep_eight_pixel_gaps(settings, drawing_font, strata, monkeypatch):
    import paperhub.render as renderer
    boxes = []
    original = renderer.panel
    def panel(canvas, box, **kwargs):
        boxes.append(box)
        return original(canvas, box, **kwargs)
    monkeypatch.setattr(renderer, "panel", panel)
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    rows = [(12, 72, 540, 158), (12, 166, 232, 251),
            (12, 259, 128, 321), (12, 329, 540, 455)]
    assert all(box in boxes for box in rows)
    assert all(b[1] - a[3] == 8 for a, b in zip(rows, rows[1:]))


def test_outer_frames_and_footer_clearance(settings, drawing_font, strata, monkeypatch):
    import paperhub.render as renderer
    boxes, footer_rows = [], []
    original_panel, original_text = renderer.panel, Canvas.text
    def panel(canvas, box, **kwargs):
        boxes.append(box)
        return original_panel(canvas, box, **kwargs)
    def text(self, x, y, value, size=22, **kwargs):
        if x == 482:
            footer_rows.append((y, size))
        return original_text(self, x, y, value, size=size, **kwargs)
    monkeypatch.setattr(renderer, "panel", panel)
    monkeypatch.setattr(Canvas, "text", text)
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    left, right = boxes[:2]
    assert left[3] == right[3] == 463
    assert (12, 329, 540, 455) in boxes
    assert left[3] - 455 == 8  # Card-to-outer-frame inset.
    footer = (5, 471, 954, 534)
    assert footer in boxes
    assert footer[1] - left[3] == 8
    assert 539 - footer[3] == 5  # Same margin as the sides.
    assert footer_rows[1][0] - footer_rows[0][0] >= max(size for _, size in footer_rows) * 1.3
    for baseline, size in footer_rows:
        _, top, _, bottom = Canvas(settings).font(size).getbbox("3時間前", anchor="ls")
        assert baseline + top >= footer[1] + 4
        assert baseline + bottom <= footer[3] - 3


def test_panel_tops_and_header_metric_spacing(settings, drawing_font, strata, monkeypatch):
    import paperhub.render as renderer
    boxes, texts = [], []
    original_panel, original_text = renderer.panel, Canvas.text
    def panel(canvas, box, **kwargs):
        boxes.append(box)
        return original_panel(canvas, box, **kwargs)
    def text(self, x, y, value, size=22, **kwargs):
        texts.append((x, y, str(value), size))
        return original_text(self, x, y, value, size=size, **kwargs)
    monkeypatch.setattr(renderer, "panel", panel)
    monkeypatch.setattr(Canvas, "text", text)
    strata["live"]["state"] = "generating"
    render_png(snapshot(settings, now=1793411985, strata=strata), settings)
    assert (12, 72, 540, 158) in boxes
    assert (564, 72, 946, 453) in boxes
    assert next(y for _, y, value, _ in texts if value == "● Generating") == 64
    assert next(y for x, y, value, _ in texts if x == 20 and strata["engine"]["model"] in value) == 64
    label = next(y for _, y, value, _ in texts if value == "Prefill")
    assert label == 196
    assert any(y == 232 and value == "—" for _, y, value, _ in texts)
    assert 24 * 1.3 <= 232 - label <= 38
