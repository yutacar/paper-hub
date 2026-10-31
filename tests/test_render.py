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
    assert image.getpixel((500, 300)) == 0
    assert image.getpixel((100, 490)) == 0


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
    assert c.image.getpixel((70, 30)) == 0
    # A just-opened window has zero pace; positive use must be inverted.
    window = usage["providers"][0]["groups"][0]["windows"][0]
    window.update(usedPercent=50, resetsAt=1793411985 + 300 * 60)
    view = snapshot(settings, now=1793411985, strata=strata, usage=usage, history=rows)
    image = Image.open(io.BytesIO(render_png(view, settings)))
    # First provider is Claude by default; Codex follows after 118 pixels.
    assert image.getpixel((948, 225)) == 0


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
    unit = next(call for call in calls if call[2] == "t/s")
    assert unit[1] == decode[1]
    assert unit[0] > decode[0]
    assert any("48°C" in text and "8 / 180 W" in text for _, _, text, _ in calls)
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
        if y in (409, 442, 475):
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
    for y in (409, 442, 475):
        assert [x for x, baseline, _ in cells if baseline == y] == [318, 414, 486]
    c = Canvas(settings)
    end = c.right(318, 50, "65→182", width=134)
    assert end == pytest.approx(318)


@pytest.mark.parametrize("telemetry,expected", [({}, None), ({"bat": 87, "rssi": -58}, "電池 87%  -58dBm"),
    ({"bat": 0}, "電池 0%"), ({"rssi": -58}, "-58dBm")])
def test_footer_only_received_values(settings, drawing_font, telemetry, expected, monkeypatch):
    values = []
    original = Canvas.text
    def record(self, x, y, value, **kwargs):
        if x == 700 and y == 538:
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
    assert ("—", 172, 24) in calls
    calls.clear()
    render_png(snapshot(settings, now=1793411985, strata=strata,
                        last_prefill={"value": 131, "at": 1793142000}), settings)
    assert ("131", 172, 48) in calls
    assert ("t/s（10/28）", 172, 24) in calls
    # A current qualifying request overrides the stored fallback.
    strata["requests"] = [{"prompt_tokens": 1000, "reused": 0, "prompt_ms": 1000}]
    view = snapshot(settings, now=1793411985, strata=strata, last_prefill={"value": 131, "at": 1793142000})
    assert view["strata"]["prefill_value"] == 1000
    assert not view["strata"]["prefill_cached"]


def test_decode_bars_bounds_and_missing_slots(settings, drawing_font):
    canvas = Canvas(settings)
    canvas.bars(0, 0, [None, 1, 2], width=300, height=40)
    pixels = canvas.image.load()
    assert pixels[285, 0] == 0  # Latest, maximum request reaches the top.
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
    assert "t/s（10/28）" in captions
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
    body = [call for call in calls if call[1] > 30]
    calls.clear()
    render_png(snapshot(settings, **args, failures={"strata": 1793411985, "xangi": 1793411985}), settings)
    assert [call for call in calls if call[1] > 30] == body
    failures = [call for call in calls if "取得失敗" in str(call[2])]
    assert len(failures) == 2
    assert all(y == 30 and "10:54" in text and "10:59" in text for _, y, text, _ in failures)
    assert failures[0][0] > 150
    assert failures[1][0] > 650
