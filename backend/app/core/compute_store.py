"""Append-only versioned AI observations, isolated from macro and CRCL storage."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.app.core.db import connect
from backend.app.schemas.compute import ObservationBatch, today

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "ai_compute.db"


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(DB_PATH, row_factory=sqlite3.Row)
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
          id TEXT PRIMARY KEY, entity TEXT NOT NULL, metric TEXT NOT NULL,
          period_start TEXT NOT NULL, period_end TEXT NOT NULL,
          frequency TEXT NOT NULL, kind TEXT NOT NULL, basis TEXT NOT NULL,
          definition_version TEXT NOT NULL, published_at TEXT NOT NULL,
          observed_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_compute_series ON observations(entity,metric,period_end);
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY, ts TEXT NOT NULL, source TEXT NOT NULL,
          status TEXT NOT NULL, inserted INTEGER NOT NULL, message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reports (
          id INTEGER PRIMARY KEY, ts TEXT NOT NULL, data_hash TEXT NOT NULL,
          model TEXT NOT NULL, payload TEXT NOT NULL
        );
        """)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def import_batch(batch: ObservationBatch, *, dry_run=False):
    # Validation happens before opening the write transaction. The batch is atomic.
    points = []
    for o in batch.observations:
        d = o.model_dump(mode="json")
        identity = {k: v for k, v in d.items() if k != "observed_at"}
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        points.append((key, d))
    if dry_run:
        return {"status": "valid", "observations": len(points), "entities": sorted({d["entity"] for _, d in points}),
                "notes": batch.notes, "inserted": 0, "dry_run": True}
    inserted = 0
    with connection() as c:
        for key, d in points:
            cursor = c.execute("""INSERT OR IGNORE INTO observations
                (id,entity,metric,period_start,period_end,frequency,kind,basis,definition_version,
                 published_at,observed_at,payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (key, d["entity"], d["metric"], d["period_start"] or "", d["period_end"],
                 d["frequency"], d["kind"], d["basis"], d["definition_version"],
                 d["published_at"], d["observed_at"], json.dumps(d, ensure_ascii=False, allow_nan=False)))
            inserted += cursor.rowcount
    return {"status": "ok", "observations": len(points), "inserted": inserted, "notes": batch.notes, "dry_run": False}


def observations(as_of: str | None = None):
    cutoff = as_of or today().isoformat()
    # Do not overwrite earlier vintages; choose the last public version as of cutoff.
    with connection() as c:
        rows = c.execute("""SELECT payload FROM (
            SELECT payload, ROW_NUMBER() OVER (
              PARTITION BY entity,metric,period_start,period_end,frequency,kind,basis,definition_version,
                json_extract(payload,'$.scope'),json_extract(payload,'$.currency'),json_extract(payload,'$.unit'),
                CASE WHEN kind='consensus' THEN json_extract(payload,'$.source_id') ELSE '' END
              ORDER BY published_at DESC, observed_at DESC, id DESC) AS rn
            FROM observations WHERE substr(published_at,1,10)<=?
        ) WHERE rn=1 ORDER BY json_extract(payload,'$.period_end')""", (cutoff,)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def expectation_vintages(as_of=None):
    with connection() as c:
        rows = c.execute("SELECT payload FROM observations WHERE kind IN ('guidance','consensus') AND substr(published_at,1,10)<=? ORDER BY published_at,observed_at", (as_of or today().isoformat(),)).fetchall()
    return [json.loads(r["payload"]) for r in rows]


def log(source, status, inserted=0, message=""):
    with connection() as c:
        c.execute("INSERT INTO runs(ts,source,status,inserted,message) VALUES(?,?,?,?,?)",
                  (now(), source, status, inserted, message[:1600]))


def logs(limit=60):
    with connection() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))]


def save_report(data_hash, model, payload):
    with connection() as c:
        c.execute("INSERT INTO reports(ts,data_hash,model,payload) VALUES(?,?,?,?)",
                  (now(), data_hash, model, json.dumps(payload, ensure_ascii=False, allow_nan=False)))


def report():
    with connection() as c:
        row = c.execute("SELECT * FROM reports WHERE json_extract(payload,'$.schema_version')='ai-compute-report-v2' ORDER BY id DESC LIMIT 1").fetchone()
    return {**dict(row), "payload": json.loads(row["payload"])} if row else None
