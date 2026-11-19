import hashlib
import json
import os
import sqlite3
import threading
from typing import Any, Optional

DB_PATH = os.getenv("SQLITE_DB_PATH", "./incubrix_jobs.db")
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _LOCK, _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                results_json TEXT,
                error_json TEXT,
                timeline_json TEXT,
                cache_dir TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS idempotency_keys (
                idem_key TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                job_id TEXT NOT NULL
            )
            """
        )


def compute_payload_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def lookup_idempotency(idem_key: str) -> Optional[tuple[str, str]]:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT payload_hash, job_id FROM idempotency_keys WHERE idem_key = ?", (idem_key,)).fetchone()
        return (row["payload_hash"], row["job_id"]) if row else None


def save_idempotency(idem_key: str, payload_hash: str, job_id: str) -> None:
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO idempotency_keys (idem_key, payload_hash, job_id) VALUES (?, ?, ?)",
            (idem_key, payload_hash, job_id),
        )


def create_job_record(job_id: str, request_data: dict[str, Any]) -> None:
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO jobs (job_id, status, progress, request_json) VALUES (?, 'queued', 0, ?)",
            (job_id, json.dumps(request_data)),
        )


def update_job_record(
    job_id: str,
    *,
    status: Optional[str] = None,
    progress: Optional[int] = None,
    results: Optional[dict[str, Any]] = None,
    error: Optional[dict[str, Any]] = None,
    timeline: Optional[list[dict[str, Any]]] = None,
    cache_dir: Optional[str] = None,
) -> None:
    fields, values = [], []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if results is not None:
        fields.append("results_json = ?")
        values.append(json.dumps(results))
    if error is not None:
        fields.append("error_json = ?")
        values.append(json.dumps(error))
    if timeline is not None:
        fields.append("timeline_json = ?")
        values.append(json.dumps(timeline))
    if cache_dir is not None:
        fields.append("cache_dir = ?")
        values.append(cache_dir)
    if not fields:
        return
    values.append(job_id)
    with _LOCK, _conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", values)


def get_job_record(job_id: str) -> Optional[dict[str, Any]]:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        return {
            "job_id": row["job_id"],
            "status": row["status"],
            "progress": row["progress"],
            "request": json.loads(row["request_json"]),
            "results": json.loads(row["results_json"]) if row["results_json"] else None,
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "timeline": json.loads(row["timeline_json"]) if row["timeline_json"] else None,
            "cache_dir": row["cache_dir"],
        }