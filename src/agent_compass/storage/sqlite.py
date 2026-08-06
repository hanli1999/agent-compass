"""SQLite repository for tasks, memories, checkpoints, feedback, idempotency."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..models import Task

SCHEMA_VERSION = "1"

_SCHEMA = [
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS feedback (feedback_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS memories ("
    "memory_id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, "
    "memory_type TEXT NOT NULL, privacy TEXT NOT NULL, score REAL, updated_at TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status)",
    "CREATE INDEX IF NOT EXISTS idx_memories_privacy ON memories(privacy)",
    "CREATE TABLE IF NOT EXISTS checkpoints ("
    "task_id TEXT NOT NULL, phase TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, "
    "PRIMARY KEY (task_id, phase, created_at))",
    "CREATE TABLE IF NOT EXISTS idempotency_keys ("
    "key TEXT PRIMARY KEY, scope TEXT NOT NULL, task_id TEXT, recorded_at TEXT NOT NULL)",
]


class SQLiteStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self):
        with self._connection() as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO meta(key,value) VALUES (?,?)",
                    ("schema_version", SCHEMA_VERSION),
                )

    # ---------------- tasks ----------------

    def save_task(self, task: Task) -> None:
        payload = json.dumps(task.to_dict(), ensure_ascii=False)
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tasks(task_id,payload,updated_at) VALUES (?,?,?)",
                (task.task_id, payload, task.updated_at),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_tasks(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ---------------- feedback ----------------

    def save_feedback(self, event: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feedback(feedback_id,payload,created_at) VALUES (?,?,?)",
                (
                    event["feedback_id"],
                    json.dumps(event, ensure_ascii=False),
                    event["created_at"],
                ),
            )

    def list_feedback(self, task_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self._connection() as conn:
            if task_id:
                rows = conn.execute(
                    "SELECT payload FROM feedback WHERE json_extract(payload,'$.task_id')=? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT payload FROM feedback ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ---------------- memories ----------------

    def save_memory(self, memory: dict[str, Any]) -> None:
        payload = json.dumps(memory, ensure_ascii=False)
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO memories(memory_id,payload,status,memory_type,privacy,score,updated_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    memory["memory_id"],
                    payload,
                    memory["status"],
                    memory["memory_type"],
                    memory["privacy"],
                    memory.get("score"),
                    memory["updated_at"],
                    memory["created_at"],
                ),
            )

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_memories(
        self,
        *,
        status: str | None = None,
        privacy: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if privacy:
            clauses.append("privacy=?")
            params.append(privacy)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT payload FROM memories{where} ORDER BY updated_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    def delete_memory(self, memory_id: str) -> bool:
        with self._connection() as conn:
            cur = conn.execute("DELETE FROM memories WHERE memory_id=?", (memory_id,))
        return cur.rowcount > 0

    # ---------------- checkpoints ----------------

    def save_checkpoint(self, task_id: str, phase: str, payload: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints(task_id,phase,payload,created_at) VALUES (?,?,?,?)",
                (task_id, phase, json.dumps(payload, ensure_ascii=False), payload["created_at"]),
            )

    def latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT payload FROM checkpoints WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_checkpoints(self, task_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT payload FROM checkpoints WHERE task_id=? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [json.loads(r["payload"]) for r in rows]

    # ---------------- idempotency ----------------

    def has_idempotency_key(self, key: str) -> bool:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM idempotency_keys WHERE key=?", (key,)
            ).fetchone()
        return row is not None

    def record_idempotency_key(self, key: str, scope: str, task_id: str | None = None) -> None:
        from ..models import utc_now

        with self._connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO idempotency_keys(key,scope,task_id,recorded_at) VALUES (?,?,?,?)",
                (key, scope, task_id, utc_now()),
            )

    def schema_version(self) -> str:
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        return row["value"] if row else SCHEMA_VERSION
