"""Small SQLite repository used by the offline MVP."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..models import Task


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
            conn.execute("CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS feedback (feedback_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)")

    def save_task(self, task: Task) -> None:
        payload = json.dumps(task.to_dict(), ensure_ascii=False)
        with self._connection() as conn:
            conn.execute("INSERT OR REPLACE INTO tasks(task_id,payload,updated_at) VALUES (?,?,?)", (task.task_id, payload, task.updated_at))

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_feedback(self, event: dict[str, Any]) -> None:
        with self._connection() as conn:
            conn.execute("INSERT OR REPLACE INTO feedback(feedback_id,payload,created_at) VALUES (?,?,?)", (event["feedback_id"], json.dumps(event, ensure_ascii=False), event["created_at"]))
