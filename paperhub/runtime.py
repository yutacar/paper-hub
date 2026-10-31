"""Independent source polling and cached rendering."""

import asyncio
import logging
import time
from collections.abc import Mapping

import httpx

from .dashboard import snapshot
from .render import render_png

log = logging.getLogger(__name__)
INTERVALS = {"strata": 30, "xangi": 60}
RETRY_DELAY = 2


class Runtime:
    def __init__(self, settings, history, client, *, clock=time.time, renderer=render_png):
        self.settings, self.history, self.client = settings, history, client
        self.clock, self.renderer = clock, renderer
        self.data = {"strata": None, "xangi": None}
        self.successes, self.failures = {}, {}
        self.telemetry = history.telemetry()
        self.view, self.png = None, None
        # Startup history is loaded before the very first render.
        self.refresh()

    def refresh(self):
        now = self.clock()
        view = snapshot(self.settings, now=now, strata=self.data["strata"], usage=self.data["xangi"],
                        successes=self.successes, failures=self.failures,
                        history=self.history.recent(now), telemetry=self.telemetry,
                        last_prefill=self.history.last_prefill())
        png = self.renderer(view, self.settings)
        self.view, self.png = view, png

    async def poll_once(self, name):
        url = self.settings.strata_url if name == "strata" else self.settings.usage_url
        for attempt in range(2):
            response = None
            try:
                # Each attempt has its own total three-second deadline.
                async with asyncio.timeout(3):
                    response = await self.client.get(url, timeout=3)
                    response.raise_for_status()
                    payload = response.json()
                if not isinstance(payload, Mapping):
                    raise ValueError("JSON root must be an object")
                validate_payload(name, payload)
                now = self.clock()
                next_data = {**self.data, name: payload}
                next_successes = {**self.successes, name: now}
                next_failures = {key: value for key, value in self.failures.items() if key != name}
                candidate = snapshot(self.settings, now=now, strata=next_data["strata"], usage=next_data["xangi"],
                                     successes=next_successes, failures=next_failures,
                                     history=self.history.recent(now), telemetry=self.telemetry,
                                     last_prefill=self.history.last_prefill())
                self.renderer(candidate, self.settings)
            except (httpx.HTTPError, TimeoutError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                status = response.status_code if response is not None else None
                prefix = response.text[:200] if response is not None else ""
                log.warning("%s fetch failed attempt=%d/2 exception=%s message=%r status=%s response_prefix=%r",
                            name, attempt + 1, type(exc).__name__, str(exc), status, prefix)
                if attempt == 0:
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                self.failures[name] = self.clock()
                self.refresh()
                return False
            if name == "strata":
                self.history.record_strata(payload, now)
            else:
                self.history.record_usage(payload, now)
            self.data[name] = payload
            self.successes[name] = now
            self.failures.pop(name, None)
            self.refresh()
            return True

    async def poll(self, name):
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            await self.poll_once(name)
            await asyncio.sleep(max(0, INTERVALS[name] - (loop.time() - started)))

    async def cleanup(self):
        while True:
            self.history.prune(self.clock())
            await asyncio.sleep(3600)

    def save_telemetry(self, *, bat=None, rssi=None):
        values = {**self.telemetry}
        if bat is not None:
            values["bat"] = bat
        if rssi is not None:
            values["rssi"] = rssi
        if values != self.telemetry:
            self.history.save_telemetry(values)
            self.telemetry = values
        # The current cached PNG remains unchanged until the next source update.

    def health(self):
        now = self.clock()
        return {"ok": True, **{f"{name}_age_s": max(0, now - self.successes[name])
                                if name in self.successes else None for name in INTERVALS}}


def validate_payload(name, payload):
    """Reject wrong container types while preserving missing/null fields."""
    def items(value):
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(v, Mapping) for v in value):
            raise ValueError("Expected an array of objects")
        return value
    if name == "strata":
        items(payload.get("requests"))
        for key in ("engine", "hardware", "live", "totals"):
            if payload.get(key) is not None and not isinstance(payload[key], Mapping):
                raise ValueError("Expected an object")
    else:
        for provider in items(payload.get("providers")):
            for group in items(provider.get("groups")):
                items(group.get("windows"))
