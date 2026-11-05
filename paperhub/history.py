"""SQLite history, accessed on the application's event-loop thread."""

import json
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path

from .common import mapping, number
from .strata import prefill_sample


class History:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS strata (
                at REAL NOT NULL, gpu_util REAL, gpu_power REAL,
                cpu REAL, disk_read_mb REAL, tok_s REAL);
            CREATE INDEX IF NOT EXISTS strata_at ON strata(at);
            CREATE TABLE IF NOT EXISTS usage (
                at REAL NOT NULL, provider TEXT, group_id TEXT, label TEXT,
                used_percent REAL, duration_mins REAL, resets_at REAL);
            CREATE INDEX IF NOT EXISTS usage_at ON usage(at);
            CREATE TABLE IF NOT EXISTS prefill (
                id INTEGER PRIMARY KEY CHECK (id = 1), value REAL NOT NULL, request_at REAL);
            CREATE TABLE IF NOT EXISTS device (
                id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL);
        """)

    def record_strata(self, payload, now, *, commit=True):
        """Use commit=False only within a caller-owned database transaction."""
        hardware, live = mapping(payload.get("hardware")), mapping(payload.get("live"))
        row = (now, *(number(hardware.get(k)) for k in
                      ("gpu_util", "gpu_power", "cpu", "disk_read_mb")), number(live.get("tok_s")))
        with self.db if commit else nullcontext():
            self.db.execute("INSERT INTO strata VALUES (?, ?, ?, ?, ?, ?)", row)
            sample = prefill_sample(payload)
            if sample is not None:
                self.db.execute("""
                    INSERT INTO prefill VALUES (1, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET value=excluded.value, request_at=excluded.request_at
                    WHERE prefill.request_at IS NULL OR
                        (excluded.request_at IS NOT NULL AND excluded.request_at >= prefill.request_at)
                """, (sample["value"], sample["at"]))

    def record_usage(self, payload, now, *, commit=True):
        """Use commit=False only within a caller-owned database transaction."""
        rows = []
        for provider in payload.get("providers") or []:
            for group in provider.get("groups") or []:
                for window in group.get("windows") or []:
                    rows.append((now, provider.get("id"), group.get("id"), window.get("label"),
                                 number(window.get("usedPercent")),
                                 number(window.get("windowDurationMins")), number(window.get("resetsAt"))))
        with self.db if commit else nullcontext():
            self.db.executemany("INSERT INTO usage VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    def recent(self, now):
        rows = self.db.execute("SELECT * FROM strata WHERE at >= ? AND at <= ? ORDER BY at DESC, rowid DESC LIMIT 120",
                               (now - 3600, now)).fetchall()
        return [dict(row) for row in reversed(rows)]

    def prune(self, now):
        with self.db:
            for table in ("strata", "usage"):
                self.db.execute(f"DELETE FROM {table} WHERE at < ?", (now - 7 * 86400,))

    def telemetry(self):
        row = self.db.execute("SELECT payload FROM device WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else {}

    def save_telemetry(self, values):
        with self.db:
            self.db.execute("INSERT INTO device VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                            (json.dumps(values, allow_nan=False),))

    def last_prefill(self):
        row = self.db.execute("SELECT value, request_at FROM prefill WHERE id=1").fetchone()
        return {"value": row[0], "at": row[1]} if row else None

    @staticmethod
    def read_prefill(path):
        """CLI reads existing history without creating or migrating it."""
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
            try:
                row = db.execute("SELECT value, request_at FROM prefill WHERE id=1").fetchone()
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc):
                    return None
                raise
            return {"value": row[0], "at": row[1]} if row else None

    def close(self):
        self.db.close()
