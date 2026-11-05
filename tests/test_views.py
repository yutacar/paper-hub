"""Offline API, lazy cache and display regression coverage for all views."""
import io

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont

from paperhub.config import Settings
from paperhub.dashboard import snapshot
from paperhub.render import Canvas, render_png, fixture_history, detail_reset, main
from paperhub.server import create_app

NOW = 1793411985
MODES = ("dashboard", "strata", "usage")


@pytest.fixture
def settings(tmp_path):
    return Settings(font_path="test-font", font_path_bold="test-font", data_dir=str(tmp_path),
                    strata_url="http://127.0.0.1/metrics", usage_url="http://127.0.0.1/api/usage")


@pytest.fixture(autouse=True)
def fonts(monkeypatch):
    monkeypatch.setattr(Canvas, "font", lambda self, size, bold=False: ImageFont.load_default(size=size))


def make_view(settings, strata, usage, **kwargs):
    values = dict(now=NOW, strata=strata, usage=usage, successes={"strata": NOW, "xangi": NOW},
                  history=fixture_history(strata, NOW))
    values.update(kwargs)
    return snapshot(settings, **values)


def test_api_modes_headers_validation_and_telemetry(settings):
    with TestClient(create_app(settings, polling=False, clock=lambda: NOW)) as client:
        default = client.get("/paper.png")
        assert default.headers["x-paper-view"] == "dashboard"
        assert default.content == client.get("/paper.png?view=dashboard").content
        images = []
        for i, mode in enumerate(MODES):
            response = client.get(f"/paper.png?view={mode}&bat={80+i}&rssi={-50-i}")
            assert response.status_code == 200
            assert response.headers["x-paper-view"] == mode
            assert response.headers["cache-control"] == "no-store"
            assert client.app.state.runtime.telemetry == {"bat": 80+i, "rssi": -50-i}
            images.append(response.content)
        assert len(set(images)) == 3
        for query in ("view=other", "view=", "view=Strata", "view=usage&bat=101"):
            assert client.get("/paper.png?" + query).status_code == 422
        assert "details" not in client.get("/paper.json").json()
        assert client.get("/healthz").json() == {"ok": True, "strata_age_s": None, "xangi_age_s": None}


def test_lazy_cache_invalidation_and_shared_snapshot(settings, strata):
    calls = []
    def renderer(view, config, mode="dashboard"):
        calls.append((mode, view))
        return (mode + str(len(calls))).encode()
    app = create_app(settings, renderer=renderer, polling=False, clock=lambda: NOW,
                     transport=httpx.MockTransport(lambda request: httpx.Response(200, json=strata)))
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert not calls and runtime.pngs == {}
        for mode in MODES:
            first = client.get("/paper.png?view=" + mode).content
            assert first == client.get("/paper.png?view=" + mode).content
        assert len(calls) == 3
        assert all(view is calls[0][1] for _, view in calls)
        old_json = client.get("/paper.json").json()
        client.get("/paper.png?view=usage&bat=87")
        assert client.get("/paper.json").json() == old_json
        assert len(calls) == 3
        assert client.portal.call(runtime.poll_once, "strata")
        assert not runtime.pngs and len(calls) == 3  # Fetch never renders.
        assert len(runtime.view["history"]) == 1
        for mode in MODES:
            client.get("/paper.png?view=" + mode)
        assert len(calls) == 6
        assert calls[3][1] is not calls[0][1]
        assert all(view is calls[3][1] for _, view in calls[3:])


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("scenario", ("normal", "empty", "offline", "failed", "long"))
def test_views_format_and_text_bounds(settings, strata, usage, mode, scenario, monkeypatch):
    boxes, sizes = [], []
    original = ImageDraw.ImageDraw.text
    def draw(self, xy, text, **kwargs):
        boxes.append(self.textbbox(xy, text, font=kwargs["font"], anchor=kwargs["anchor"]))
        sizes.append(kwargs["font"].size)
        return original(self, xy, text, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", draw)
    extra = {}
    if scenario == "empty":
        strata, usage = None, None
        view = snapshot(settings, now=NOW)
    else:
        if scenario in ("offline", "failed"):
            extra.update(successes={"strata": NOW-700 if scenario == "offline" else NOW-300, "xangi": NOW-300},
                         failures={"strata": NOW, "xangi": NOW})
        if scenario == "long":
            strata["engine"]["model"] = "long model " * 100
            strata["live"]["state"] = "generating"
            for provider in usage["providers"]:
                provider["label"] = "long provider " * 100
        view = make_view(settings, strata, usage, telemetry={"bat": 100, "rssi": -200}, **extra)
    image = Image.open(io.BytesIO(render_png(view, settings, mode=mode)))
    assert image.size == (960, 540) and image.mode == "L"
    assert set(image.tobytes()) <= set(range(17, 256, 17))
    assert boxes and min(sizes) >= 22
    assert all(0 <= left <= right <= 960 and 0 <= top <= bottom <= 540 for left, top, right, bottom in boxes)


def test_usage_detail_groups_resets_navigation_and_telemetry(settings, strata, usage, monkeypatch):
    texts = []
    original = Canvas.text
    def text(self, x, y, value, **kwargs):
        texts.append(str(value))
        return original(self, x, y, value, **kwargs)
    monkeypatch.setattr(Canvas, "text", text)
    view = make_view(settings, strata, usage, telemetry={"bat": 87, "rssi": -58})
    render_png(view, settings, mode="usage")
    for provider in view["usage"]["providers"]:
        for group in provider["groups"]:
            assert group["display_heading"] in texts
    assert not any(value.startswith("ほか") for value in texts)
    assert any("○ ○ ●" in value and "B: ダッシュボード" in value for value in texts)
    assert "電池 87%  -58dBm" in texts
    assert "あと4h20m" in texts and any(value.startswith("10/31 ") for value in texts)
    texts.clear()
    render_png(view, settings, mode="strata")
    assert any("○ ● ○" in value and "B: AI利用量" in value for value in texts)
    assert "10/31 10:57" in texts
    assert len(view["strata"]["recent_requests"]) == 3
    assert render_png(view, settings) == render_png(view, settings, mode="dashboard")


@pytest.mark.parametrize("delta,expected", [(0, "リセット済"), (-1, "リセット済"), (59, "まもなく"), (60, "あと0h01m")])
def test_detail_reset_boundaries(settings, strata, usage, delta, expected):
    view = make_view(settings, strata, usage)
    view["details"]["usage_resets"] = {"p": {"g": [NOW+delta]}}
    relative, absolute = detail_reset(view, "p", "g", 0, settings.tz)
    assert relative == expected and absolute.startswith("10/31 ")


@pytest.mark.parametrize("mode", MODES)
def test_cli_modes(settings, monkeypatch, tmp_path, mode):
    from pathlib import Path
    monkeypatch.setattr(Settings, "load", lambda **kwargs: settings)
    fixtures = Path(__file__).parent / "fixtures"
    output = tmp_path / (mode + ".png")
    main(["--strata", str(fixtures / "strata_monitor.json"), "--usage", str(fixtures / "xangi_usage.json"),
          "--now", str(NOW), "--out", str(output), "--view", mode])
    assert Image.open(output).size == (960, 540)


@pytest.mark.parametrize("mode", ("strata", "usage"))
def test_detail_inner_cards_clear_outer_bottom(settings, strata, usage, mode, monkeypatch):
    import paperhub.render as renderer
    boxes = []
    original = renderer.panel
    def panel(canvas, box, **kwargs):
        boxes.append(box)
        return original(canvas, box, **kwargs)
    monkeypatch.setattr(renderer, "panel", panel)
    render_png(make_view(settings, strata, usage), settings, mode=mode)
    outer, footer = boxes[0], next(box for box in boxes if box[1] == 487)
    inner = [box for box in boxes[1:] if box != footer]
    assert inner and outer[3] - max(box[3] for box in inner) >= 8
    assert footer[1] - outer[3] >= 8
