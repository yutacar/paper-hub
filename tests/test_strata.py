from datetime import datetime, timezone

import pytest

from paperhub.strata import (
    context_usage, format_strata, is_strata_offline,
    prefill_tokens_per_second, relative_time,
)


def test_fixture_calculations(strata):
    result = format_strata(strata, tz="Asia/Tokyo")
    assert result["prefill_value"] is None
    assert result["prefill_tok_s"] == "—"
    assert result["context_value"]["tokens"] == 247
    assert result["context_value"]["percent"] == pytest.approx(0.3768920898)
    assert result["context"] == "0% (247 / 64K)"
    assert result["vram"] == "14.6 / 16 GB"
    assert result["ram"] == "61.7 / 79 GB"
    assert result["cache_reuse_ratio"] == pytest.approx(240 / 325)
    assert result["cache_reuse"] == "74%"
    assert result["last_request"] == "2分前"
    assert result["model"] == strata["engine"]["model"]
    assert result["experts_cached"] == "3189"
    assert result["pcie"] == "Gen4 x4"
    assert result["power"] == "8 / 180 W"
    assert result["cpu"] == "0.1"
    assert len(result["recent_requests"]) == 3
    assert result["recent_requests"][0] == {
        "time": "10:57", "status": "Done", "tokens": "65 → 182",
        "decode_tok_s": "37.0", "duration_s": "6.5",
    }
    assert result["recent_requests"][1]["status"] == "MaxTok"
    assert result["phase"] == "—"
    assert result["live_tok_s"] == "—"


def test_spec_illustrative_values():
    # SPEC examples belong to an older snapshot; keep the actual fixture intact.
    payload = {
        "time": 300000,
        "requests": [{"prompt_tokens": 12600, "reused": 12000,
                      "prompt_ms": 4580, "output_tokens": 64, "time": 62400}],
        "engine": {"max_context": 65536},
        "hardware": {"gpu_mem_used": 13.3 * 2**30, "gpu_mem_total": 16 * 2**30,
                     "ram_used": 44.2 * 2**30, "ram_total": 79 * 2**30},
        "totals": {"reused": 44, "prompt_tokens": 100},
    }
    result = format_strata(payload, tz="Asia/Tokyo")
    assert result["prefill_tok_s"] == "131"
    assert result["context"] == "19% (13k / 64K)"
    assert result["vram"] == "13.3 / 16 GB"
    assert result["ram"] == "44.2 / 79 GB"
    assert result["cache_reuse"] == "44%"
    assert result["last_request"] == "2日18時間前"
    assert result["recent_requests"][0]["tokens"] == "12.6k → 64"


@pytest.mark.parametrize("sample", [None, {}, {"prompt_tokens": None},
    {"prompt_tokens": 5, "reused": 1, "prompt_ms": 0},
    {"prompt_tokens": 5, "reused": 1, "prompt_ms": -1}])
def test_prefill_missing_or_zero(sample):
    assert prefill_tokens_per_second(sample) is None


@pytest.mark.parametrize("maximum", [None, 0, -1])
def test_context_invalid_capacity(maximum):
    assert context_usage({"prompt_tokens": 1, "output_tokens": 2}, maximum) is None


@pytest.mark.parametrize("payload", [{}, {"engine": None, "hardware": None,
    "live": None, "totals": None, "requests": None}, {"requests": [None]}])
def test_null_and_missing(payload):
    result = format_strata(payload, tz="Asia/Tokyo")
    for key in ("state", "phase", "model", "decode_tok_s", "prefill_tok_s",
                "context", "last_request", "cache_reuse", "cpu", "experts_cached"):
        assert result[key] == "—"
    assert result["vram"] == "— / — GB"
    assert result["ram"] == "— / — GB"


def test_empty_requests_and_active_state(strata):
    strata["requests"] = []
    strata["totals"]["prompt_tokens"] = 0
    strata["live"].update(state="running", phase="decode", tok_s=12.5)
    result = format_strata(strata, tz="Asia/Tokyo")
    assert result["recent_requests"] == []
    for key in ("decode_tok_s", "prefill_tok_s", "context", "last_request", "cache_reuse"):
        assert result[key] == "—"
    assert result["phase"] == "decode"
    assert result["live_tok_s"] == "12.5"


@pytest.mark.parametrize("age,expected", [(237600, "2日18時間前"), (2100, "35分前"),
    (3600, "1時間前"), (59, "59秒前"), (-5, "0秒前")])
def test_relative_time(age, expected):
    assert relative_time(300000, 300000 - age) == expected


def test_offline_boundary():
    assert not is_strata_offline(None, 1000)
    assert not is_strata_offline(400.1, 1000)
    assert is_strata_offline(400, 1000)
    assert is_strata_offline(399, 1000)
    assert not is_strata_offline(1100, 1000)


def test_recent_dates(strata):
    strata["time"] = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc).timestamp()
    strata["requests"][0]["time"] = strata["time"] - 10 * 3600
    strata["requests"][0]["finish"] = "cancelled"
    row = format_strata(strata, tz="Asia/Tokyo")["recent_requests"][0]
    assert row["time"] == "10/31 23:00"
    assert row["status"] == "cancelled"


def test_prefill_selects_latest_qualifying_request(strata):
    strata["requests"] = [
        {"prompt_tokens": 511, "reused": 0, "prompt_ms": 100},
        {"prompt_tokens": 1512, "reused": 1000, "prompt_ms": 256},
        {"prompt_tokens": 2000, "reused": 0, "prompt_ms": 100},
    ]
    result = format_strata(strata, tz="Asia/Tokyo")
    assert result["prefill_value"] == 2000
    assert result["prefill_tok_s"] == "2000"


@pytest.mark.parametrize("requests", [[], [None, {}],
    [{"prompt_tokens": 1000, "reused": 489, "prompt_ms": 100}],
    [{"prompt_tokens": 1000, "reused": None, "prompt_ms": 100}],
    [{"prompt_tokens": 1000, "reused": 0, "prompt_ms": 0},
     {"prompt_tokens": 2000, "reused": 0, "prompt_ms": 100}]])
def test_prefill_no_qualifier_or_invalid_latest(requests):
    result = format_strata({"requests": requests}, tz="Asia/Tokyo")
    assert result["prefill_value"] is None
    assert result["prefill_tok_s"] == "—"


def test_display_state_and_power(strata):
    result = format_strata(strata, tz="Asia/Tokyo")
    assert result["state"] == "Idle"
    assert result["power"] == "8 / 180 W"


def test_decode_history_is_last_ten_oldest_to_newest():
    payload = {"requests": [{"decode_tok_s": value} for value in range(12)]}
    assert format_strata(payload, tz="UTC")["decode_history"] == list(range(9, -1, -1))
