import asyncio
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from paperhub.config import Settings
from paperhub.history import History
from paperhub.runtime import Runtime
from paperhub.server import create_app


@pytest.fixture(autouse=True)
def instant_retry(monkeypatch):
    monkeypatch.setattr("paperhub.runtime.RETRY_DELAY", 0)


@pytest.fixture
def settings(tmp_path):
    return Settings(font_path="test-font", font_path_bold="test-font", data_dir=str(tmp_path),
                    strata_url="http://127.0.0.1/metrics", usage_url="http://127.0.0.1/api/usage")


def cheap_renderer(view, settings, mode="dashboard"):
    return str(view["updated_at"]).encode()


def test_history_roundtrip_prune_and_restart(tmp_path, strata, usage):
    path = tmp_path / "history.db"
    store = History(path)
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    now = 7 * 86400 + 4000
    store.record_strata(strata, now - 7 * 86400 - 1)
    store.record_usage(usage, now - 7 * 86400 - 1)
    store.record_usage(usage, now - 7 * 86400)
    for i in range(125):
        store.record_strata(strata, now - 30 * i)
    rows = store.recent(now)
    assert len(rows) == 120
    assert rows[0]["at"] == now - 119 * 30
    assert rows[-1]["gpu_power"] == strata["hardware"]["gpu_power"]
    assert rows[-1]["tok_s"] is None
    store.prune(now)
    assert store.db.execute("SELECT COUNT(*) FROM strata").fetchone()[0] == 125
    assert store.db.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 9
    store.save_telemetry({"bat": 42})
    store.close()
    store = History(path)
    assert store.telemetry() == {"bat": 42}
    assert len(store.recent(now)) == 120
    store.close()


def test_independent_failure_recovery_and_offline(settings, tmp_path, strata, usage):
    async def scenario():
        current = [1793411985.0]
        broken = set()
        def handler(request):
            if request.url.path in broken:
                return httpx.Response(503)
            return httpx.Response(200, json=strata if request.url.path == "/metrics" else usage)
        history = History(tmp_path / "poll.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(settings, history, client, clock=lambda: current[0], renderer=cheap_renderer)
            assert runtime.health()["strata_age_s"] is None
            assert await runtime.poll_once("strata")
            assert await runtime.poll_once("xangi")
            last = runtime.data["strata"]
            broken.add("/metrics")
            current[0] += 600
            assert not await runtime.poll_once("strata")
            assert await runtime.poll_once("xangi")
            assert runtime.data["strata"] is last
            assert "オフライン" in runtime.view["status"]["strata"]
            assert "strata" in runtime.view["failures"]
            assert runtime.health() == {"ok": True, "strata_age_s": 600, "xangi_age_s": 0}
            broken.clear()
            assert await runtime.poll_once("strata")
            assert "strata" not in runtime.failures
            assert runtime.view["status"]["strata"] == ""
        history.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("api_key", ["", "fixture-strata-key"])
def test_strata_authentication(settings, tmp_path, strata, usage, caplog, api_key):
    async def scenario():
        requests = []

        def handler(request):
            requests.append((request.url.path, request.headers.get("Authorization")))
            if request.url.path == "/metrics":
                if api_key and len(requests) == 1:
                    return httpx.Response(401, text=f"Rejected: {api_key}")
                return httpx.Response(200, json=strata)
            return httpx.Response(200, json=usage)

        store = History(tmp_path / "auth.db")
        config = replace(settings, strata_api_key=api_key)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(config, store, client, renderer=cheap_renderer)
            assert await runtime.poll_once("strata")
            assert await runtime.poll_once("xangi")
        store.close()

        if api_key:
            assert requests == [("/metrics", f"Bearer {api_key}")] * 2 + [("/api/usage", None)]
            assert "status=401" in caplog.text
            assert "Rejected: [redacted]" in caplog.text
            assert api_key not in caplog.text
        else:
            assert requests == [("/metrics", None), ("/api/usage", None)]

    asyncio.run(scenario())


def test_bad_json_retains_previous(settings, tmp_path, strata):
    async def scenario():
        responses = iter([httpx.Response(200, json=strata), httpx.Response(200, json=[]), httpx.Response(200, json=[]),
                          httpx.Response(200, text="bad"), httpx.Response(200, text="bad"),
                          httpx.Response(200, json={"requests": "bad"}), httpx.Response(200, json={"requests": "bad"})])
        store = History(tmp_path / "bad.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: next(responses))) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            assert await runtime.poll_once("strata")
            for _ in range(3):
                assert not await runtime.poll_once("strata")
                assert runtime.data["strata"] == strata
        store.close()
    asyncio.run(scenario())


def test_api_cache_telemetry_and_read_only(settings):
    calls = []
    def renderer(view, config, mode="dashboard"):
        calls.append(view)
        return b"cached-png"
    app = create_app(settings, renderer=renderer, polling=False, clock=lambda: 1000)
    with TestClient(app) as client:
        initial = client.get("/paper.json").json()
        for _ in range(3):
            assert client.get("/paper.png").content == b"cached-png"
        assert len(calls) == 3
        assert client.get("/paper.png?bat=70&rssi=-60").headers["content-type"] == "image/png"
        assert client.get("/paper.json").json() == initial
        client.portal.call(app.state.runtime.refresh)
        assert client.get("/paper.json").json()["telemetry"] == {"bat": 70, "rssi": -60}
        assert client.get("/paper.png?bat=101").status_code == 422
        assert client.get("/paper.png?rssi=1").status_code == 422
        assert client.post("/paper.json").status_code == 405
        assert client.get("/healthz").json() == {"ok": True, "strata_age_s": None, "xangi_age_s": None}
    with TestClient(create_app(settings, renderer=renderer, polling=False)) as client:
        assert client.get("/paper.json").json()["telemetry"]["bat"] == 70


def test_startup_history_is_in_first_render(settings, tmp_path, strata):
    history = History(tmp_path / "history.db")
    history.record_strata(strata, 999)
    history.close()
    with TestClient(create_app(settings, renderer=cheap_renderer, clock=lambda: 1000, polling=False)) as client:
        assert len(client.get("/paper.json").json()["history"]) == 1


def test_polling_cadence_and_cleanup(settings, tmp_path, monkeypatch):
    async def scenario():
        store = History(tmp_path / "cadence.db")
        async with httpx.AsyncClient() as client:
            runtime = Runtime(settings, store, client, clock=lambda: 700000, renderer=cheap_renderer)
            polls, delays = [], []
            async def once(name):
                polls.append(name)
            async def stop(delay):
                delays.append(delay)
                raise asyncio.CancelledError
            monkeypatch.setattr(runtime, "poll_once", once)
            monkeypatch.setattr("paperhub.runtime.asyncio.sleep", stop)
            for name, interval in (("strata", 30), ("xangi", 60)):
                with pytest.raises(asyncio.CancelledError):
                    await runtime.poll(name)
                assert interval - 1 < delays[-1] <= interval
            assert polls == ["strata", "xangi"]
            with pytest.raises(asyncio.CancelledError):
                await runtime.cleanup()
            assert delays[-1] == 3600
        store.close()
    asyncio.run(scenario())


def test_failure_before_first_success_and_timeout(settings, tmp_path):
    async def scenario():
        def handler(request):
            assert request.extensions["timeout"]["read"] == 3
            raise httpx.ReadTimeout("timeout")
        store = History(tmp_path / "timeout.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            assert not await runtime.poll_once("strata")
            assert runtime.view["status"]["strata"] == "データなし"
            assert runtime.health()["strata_age_s"] is None
            assert "strata" in runtime.failures
        store.close()
    asyncio.run(scenario())


def test_prefill_cache_survives_restart_and_pruning(tmp_path, strata):
    import copy
    path = tmp_path / "prefill.db"
    payload = copy.deepcopy(strata)
    payload["requests"] = [{"prompt_tokens": 12600, "reused": 12000, "prompt_ms": 4580,
                             "time": 1793142000}]
    store = History(path)
    store.record_strata(payload, 1793411985)
    sample = store.last_prefill()
    assert sample["value"] == pytest.approx(600 / 4.58)
    assert sample["at"] == 1793142000
    # Polls with only small or malformed prefill requests cannot erase the cache.
    store.record_strata(strata, 1793412015)
    assert store.last_prefill() == sample
    payload["requests"][0].update(time=1793141999, prompt_ms=3000)
    store.record_strata(payload, 1793412045)
    assert store.last_prefill() == sample  # Ignore an older replay.
    store.prune(1793411985 + 8 * 86400)
    assert store.recent(1793411985 + 8 * 86400) == []
    assert store.last_prefill() == sample
    store.close()
    store = History(path)
    assert store.last_prefill() == sample
    store.close()
    assert History.read_prefill(path) == sample


def test_runtime_prefill_fallback_after_restart(settings, tmp_path, strata):
    async def scenario():
        path = tmp_path / "fallback.db"
        store = History(path)
        store.record_strata({"requests": [{"prompt_tokens": 1310, "reused": 0,
                              "prompt_ms": 10000, "time": 1793142000}]}, 1793411900)
        store.close()
        store = History(path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=strata))) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            assert await runtime.poll_once("strata")
            result = runtime.view["strata"]
            assert result["prefill_tok_s"] == "131"
            assert result["prefill_cached"]
            assert result["prefill_date"] == "10/28"
        store.close()
    asyncio.run(scenario())


def test_prefill_read_legacy_db_and_schema_migration(tmp_path):
    import sqlite3
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE device (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        db.execute("INSERT INTO device VALUES (1, ?)", ('{"bat": 87}',))
    assert History.read_prefill(path) is None
    store = History(path)
    assert store.last_prefill() is None
    assert store.telemetry() == {"bat": 87}
    store.close()


def test_retry_after_two_seconds_recovers(settings, tmp_path, strata, monkeypatch, caplog):
    async def scenario():
        calls, delays = [], []
        def handler(request):
            calls.append(request)
            return httpx.Response(503, text="temporary") if len(calls) == 1 else httpx.Response(200, json=strata)
        async def wait(delay):
            delays.append(delay)
        monkeypatch.setattr("paperhub.runtime.RETRY_DELAY", 2)
        monkeypatch.setattr("paperhub.runtime.asyncio.sleep", wait)
        store = History(tmp_path / "retry.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            assert await runtime.poll_once("strata")
            assert len(calls) == 2 and delays == [2]
            assert runtime.failures == {}
            assert store.db.execute("SELECT COUNT(*) FROM strata").fetchone()[0] == 1
        store.close()
    asyncio.run(scenario())
    records = [record for record in caplog.records if record.name == "paperhub.runtime"]
    assert len(records) == 1
    assert records[0].args[2] == "HTTPStatusError"
    assert records[0].args[4:] == (503, "temporary")


def test_retry_limit_and_diagnostic_prefix(settings, tmp_path, monkeypatch, caplog):
    async def scenario():
        calls, delays = [], []
        body = "診断" * 100 + "DO_NOT_LOG_TAIL"
        def handler(request):
            calls.append(request)
            return httpx.Response(502, text=body)
        async def wait(delay):
            delays.append(delay)
        monkeypatch.setattr("paperhub.runtime.RETRY_DELAY", 2)
        monkeypatch.setattr("paperhub.runtime.asyncio.sleep", wait)
        store = History(tmp_path / "limit.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            assert not await runtime.poll_once("xangi")
            assert len(calls) == 2 and delays == [2]
            assert runtime.data["xangi"] is None
            assert runtime.view["failures"] == {}  # No successful fetch yet: show no-data.
        store.close()
    asyncio.run(scenario())
    records = [record for record in caplog.records if record.name == "paperhub.runtime"]
    assert len(records) == 2
    assert [record.args[1] for record in records] == [1, 2]
    for record in records:
        assert record.args[2] == "HTTPStatusError"
        assert "502" in record.args[3]
        assert record.args[4] == 502
        assert record.args[5] == "診断" * 100
        assert "DO_NOT_LOG_TAIL" not in record.getMessage()


def test_transport_diagnostics_without_response(settings, tmp_path, caplog):
    async def scenario():
        def handler(request):
            raise httpx.ConnectError("connection unavailable")
        store = History(tmp_path / "connect.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            assert not await runtime.poll_once("strata")
        store.close()
    asyncio.run(scenario())
    records = [record for record in caplog.records if record.name == "paperhub.runtime"]
    assert len(records) == 2
    assert records[0].args[2:] == ("ConnectError", "connection unavailable", None, "")


def test_other_source_progresses_during_retry(settings, tmp_path, strata, usage, monkeypatch):
    async def scenario():
        sleeping, release = asyncio.Event(), asyncio.Event()
        attempts = []
        def handler(request):
            if request.url.path == "/metrics":
                attempts.append(request)
                return httpx.Response(503) if len(attempts) == 1 else httpx.Response(200, json=strata)
            return httpx.Response(200, json=usage)
        async def wait(delay):
            sleeping.set()
            await release.wait()
        monkeypatch.setattr("paperhub.runtime.asyncio.sleep", wait)
        store = History(tmp_path / "independent.db")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runtime = Runtime(settings, store, client, renderer=cheap_renderer)
            pending = asyncio.create_task(runtime.poll_once("strata"))
            await sleeping.wait()
            assert await runtime.poll_once("xangi")
            assert runtime.data["xangi"] == usage
            assert runtime.data["strata"] is None
            release.set()
            assert await pending
        store.close()
    asyncio.run(scenario())
