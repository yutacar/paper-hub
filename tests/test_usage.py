import pytest

from paperhub.usage import format_usage, pace_percent, provider_is_stale, reset_label


NOW = 1793410719


def test_spec_pace():
    window = {"resetsAt": 1793509200, "windowDurationMins": 10080}
    assert pace_percent(window, NOW) == pytest.approx(83.71676587301587)
    assert round(pace_percent(window, NOW)) == 84
    assert reset_label(window, NOW, tz="Asia/Tokyo") == "11/1 14:00"
    assert reset_label(window, NOW, tz="UTC") == "11/1 05:00"


def test_fixture_usage(usage):
    result = format_usage(usage, now=NOW, tz="Asia/Tokyo",
                          order="claude-code,codex,antigravity")
    assert [p["id"] for p in result["providers"]] == ["claude-code", "codex", "antigravity"]
    claude = result["providers"][0]
    assert not claude["stale"]
    assert claude["groups"][0]["heading"] == "Claude Code アカウント枠 (pro)"
    row = claude["groups"][0]["windows"][1]
    assert row["used_percent"] == 46
    assert row["pace_percent"] == pytest.approx(83.71663078705154)
    assert not row["inverted"]
    assert row["reset"] == "11/1 14:00"
    assert result["providers"][2]["groups"][0]["windows"][0]["used_label"] == "3%"
    assert result["zero_groups"] == ["Gemini 4 Argon", "Claude・GPT共通枠"]
    assert result["zero_summary"] == "0%: Antigravity（Argon, Claude・GPT）"


@pytest.mark.parametrize("reset,expected", [(NOW + 301 * 60, 0), (NOW, None),
    (NOW - 1, None), (NOW + 150 * 60, 50)])
def test_pace_clamped(reset, expected):
    assert pace_percent({"resetsAt": reset, "windowDurationMins": 300}, NOW) == expected


@pytest.mark.parametrize("window", [{}, None, {"resetsAt": NOW + 1, "windowDurationMins": 0},
    {"resetsAt": None, "windowDurationMins": 300}])
def test_missing_window(window):
    assert pace_percent(window, NOW) is None
    assert reset_label(window, NOW, tz="Asia/Tokyo") == "—"


@pytest.mark.parametrize("delta,expected", [(4 * 3600 + 41 * 60 + .9, "あと4h41m"),
    (0, "リセット済"), (-60, "リセット済"), (60, "あと0h01m")])
def test_short_reset(delta, expected):
    assert reset_label({"resetsAt": NOW + delta, "windowDurationMins": 300},
                       NOW, tz="Asia/Tokyo") == expected


def test_stale_boundary():
    provider = {"sourceType": "live", "sourceUpdatedAt": "2026-01-01T00:00:00Z"}
    now = 1767225600
    assert not provider_is_stale(provider, now + 599.9)
    assert provider_is_stale(provider, now + 600)
    provider["sourceType"] = "cached"
    assert provider_is_stale(provider, now)
    for stamp in (None, "invalid", "2026-01-01T00:00:00"):
        provider.update(sourceType="live", sourceUpdatedAt=stamp)
        assert provider_is_stale(provider, now)
    assert provider_is_stale({}, now)


def test_order_hide_and_stale(usage):
    result = format_usage(usage, now=NOW + 10000, tz="UTC", order="codex",
                          hide="antigravity:gemini, antigravity:gemini-argon")
    assert [p["id"] for p in result["providers"]] == ["codex", "claude-code"]
    assert all(p["stale"] and p["label"].endswith(" (古)") for p in result["providers"])
    assert result["zero_groups"] == ["Claude・GPT共通枠"]


def test_fraction_rounding_null_and_inversion(usage):
    provider = usage["providers"][0]
    group = provider["groups"][0]
    group["windows"] = [
        {"usedPercent": 2.5, "resetsAt": NOW + 300 * 60, "windowDurationMins": 300},
        {"usedPercent": None}, {"usedPercent": 0.1},
        {"usedPercent": 50, "resetsAt": NOW + 150 * 60, "windowDurationMins": 300},
    ]
    result = format_usage({"providers": [provider]}, now=NOW, tz="UTC")
    rows = result["providers"][0]["groups"][0]["windows"]
    assert rows[0]["used_percent"] == 3
    assert rows[0]["inverted"]
    assert rows[1]["used_label"] == "—"
    assert not rows[1]["inverted"]
    assert rows[2]["used_label"] == "0%"
    assert not rows[3]["inverted"]
    assert result["zero_groups"] == []


def test_empty_usage():
    assert format_usage({}, now=NOW, tz="UTC") == {
        "providers": [], "zero_groups": [], "zero_providers": [], "zero_summary": ""}


@pytest.mark.parametrize("delta", [-1, 0, -0.001])
@pytest.mark.parametrize("duration", [300, 10080])
def test_expired_reset_suppresses_pace(usage, delta, duration):
    window = usage["providers"][0]["groups"][0]["windows"][0]
    window.update(resetsAt=NOW + delta, windowDurationMins=duration)
    assert reset_label(window, NOW, tz="UTC") == "リセット済"
    assert pace_percent(window, NOW) is None
    result = format_usage(usage, now=NOW, tz="UTC", order="codex")
    row = result["providers"][0]["groups"][0]["windows"][0]
    assert row["reset"] == "リセット済"
    assert row["pace_percent"] is None
    assert not row["inverted"]


def test_duplicate_group_heading(usage):
    group = format_usage(usage, now=NOW, tz="UTC")["providers"][0]["groups"][0]
    assert group["heading"] == "Codex (plus)"
    assert [w["label"] for w in group["windows"]] == ["5h", "週"]


@pytest.mark.parametrize("remaining,label", [(0, "リセット済"), (0.1, "まもなく"),
    (59.999, "まもなく"), (60, "あと0h01m")])
def test_reset_last_minute(remaining, label):
    assert reset_label({"resetsAt": NOW + remaining, "windowDurationMins": 300},
                       NOW, tz="UTC") == label


def test_weekly_last_minute():
    assert reset_label({"resetsAt": NOW + 59, "windowDurationMins": 10080},
                       NOW, tz="UTC") == "まもなく"


def test_zero_groups_aggregate_per_provider(usage):
    gemini = usage["providers"][1]["groups"][0]
    for window in gemini["windows"]:
        window["usedPercent"] = 0
    result = format_usage(usage, now=NOW, tz="UTC")
    assert result["zero_summary"] == "0%: Antigravity（Gemini, Argon, Claude・GPT）"
    assert result["zero_providers"] == [{"id": "antigravity", "label": "Antigravity",
                                          "groups": ["Gemini", "Argon", "Claude・GPT"]}]
    result = format_usage(usage, now=NOW, tz="UTC", hide="antigravity:gemini-argon")
    assert result["zero_summary"] == "0%: Antigravity（Gemini, Claude・GPT）"


def test_zero_multiple_provider_order(usage):
    for provider in usage["providers"]:
        for group in provider["groups"]:
            for window in group["windows"]:
                window["usedPercent"] = 0
    result = format_usage(usage, now=NOW, tz="UTC", order="claude-code,codex,antigravity")
    assert [provider["id"] for provider in result["zero_providers"]] == ["claude-code", "codex", "antigravity"]
