import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskQueue:
    """SQLite-backed queue for manually submitted and scheduled research tasks."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'queued', 'running', 'awaiting_approval',
                            'completed', 'rejected', 'failed'
                        )
                    ),
                    origin TEXT NOT NULL,
                    schedule_id TEXT,
                    scheduled_for REAL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    research_path TEXT,
                    consolidated_path TEXT,
                    approved_path TEXT,
                    approval_stage TEXT NOT NULL DEFAULT 'idea_selection',
                    scripts_path TEXT,
                    approved_scripts_path TEXT,
                    workflow_thread_id TEXT,
                    active_stage TEXT,
                    active_agent TEXT,
                    active_since TEXT,
                    error TEXT,
                    UNIQUE (schedule_id, scheduled_for)
                )
                """
            )
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tasks)")
            }
            migrations = {
                "approval_stage": (
                    "ALTER TABLE tasks ADD COLUMN approval_stage "
                    "TEXT NOT NULL DEFAULT 'idea_selection'"
                ),
                "scripts_path": "ALTER TABLE tasks ADD COLUMN scripts_path TEXT",
                "approved_scripts_path": (
                    "ALTER TABLE tasks ADD COLUMN approved_scripts_path TEXT"
                ),
                "workflow_thread_id": (
                    "ALTER TABLE tasks ADD COLUMN workflow_thread_id TEXT"
                ),
                "active_stage": "ALTER TABLE tasks ADD COLUMN active_stage TEXT",
                "active_agent": "ALTER TABLE tasks ADD COLUMN active_agent TEXT",
                "active_since": "ALTER TABLE tasks ADD COLUMN active_since TEXT",
            }
            for column, statement in migrations.items():
                if column not in existing_columns:
                    connection.execute(statement)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS tasks_status_created "
                "ON tasks(status, created_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL CHECK (interval_seconds > 0),
                    next_run_at REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_commands (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    command_type TEXT NOT NULL CHECK (
                        command_type IN ('approve', 'write_scripts')
                    ),
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'completed', 'failed')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_control (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO worker_control (
                    singleton_id, stop_requested, updated_at
                )
                VALUES (1, 0, ?)
                """,
                (utc_now(),),
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS worker_commands_status_created "
                "ON worker_commands(status, created_at)"
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_command_per_task
                ON worker_commands(task_id)
                WHERE status IN ('queued', 'running')
                """
            )

    def enqueue(self, topic: str) -> str:
        cleaned_topic = topic.strip()
        if not cleaned_topic:
            raise ValueError("Topic cannot be empty.")

        task_id = uuid4().hex
        timestamp = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO tasks (id, topic, status, origin, created_at, updated_at)
                VALUES (?, ?, 'queued', 'manual', ?, ?)
                """,
                (task_id, cleaned_topic, timestamp, timestamp),
            )
        return task_id

    def add_schedule(
        self,
        topic: str,
        interval_seconds: int,
        run_immediately: bool = False,
    ) -> str:
        cleaned_topic = topic.strip()
        if not cleaned_topic:
            raise ValueError("Topic cannot be empty.")
        if interval_seconds <= 0:
            raise ValueError("Schedule interval must be greater than zero.")

        schedule_id = uuid4().hex
        now = time.time()
        next_run_at = now if run_immediately else now + interval_seconds
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO schedules (
                    id, topic, interval_seconds, next_run_at, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (schedule_id, cleaned_topic, interval_seconds, next_run_at, utc_now()),
            )
        return schedule_id

    def enqueue_due_schedules(self, now: float | None = None) -> list[str]:
        current_time = time.time() if now is None else now
        created_at = utc_now()
        task_ids: list[str] = []

        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            schedules = connection.execute(
                """
                SELECT id, topic, interval_seconds, next_run_at
                FROM schedules
                WHERE enabled = 1 AND next_run_at <= ?
                ORDER BY next_run_at
                """,
                (current_time,),
            ).fetchall()

            for schedule in schedules:
                task_id = uuid4().hex
                scheduled_for = float(schedule["next_run_at"])
                connection.execute(
                    """
                    INSERT OR IGNORE INTO tasks (
                        id, topic, status, origin, schedule_id, scheduled_for,
                        created_at, updated_at
                    )
                    VALUES (?, ?, 'queued', 'schedule', ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        schedule["topic"],
                        schedule["id"],
                        scheduled_for,
                        created_at,
                        created_at,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0]:
                    task_ids.append(task_id)

                # Skip missed intervals rather than creating a burst of stale work.
                connection.execute(
                    "UPDATE schedules SET next_run_at = ? WHERE id = ?",
                    (
                        current_time + int(schedule["interval_seconds"]),
                        schedule["id"],
                    ),
                )

        return task_ids

    def claim_next(self) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'queued'
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if task is None:
                return None

            updated_at = utc_now()
            connection.execute(
                "UPDATE tasks SET status = 'running', updated_at = ? "
                "WHERE id = ? AND status = 'queued'",
                (updated_at, task["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task["id"],),
            ).fetchone()
            return dict(claimed)

    def mark_awaiting_approval(
        self,
        task_id: str,
        research_path: str | Path,
        consolidated_path: str | Path,
        approval_stage: str = "idea_selection",
    ) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'awaiting_approval',
                    updated_at = ?,
                    research_path = ?,
                    consolidated_path = ?,
                    approval_stage = ?,
                    workflow_thread_id = ?,
                    active_stage = NULL,
                    active_agent = NULL,
                    active_since = NULL,
                    error = NULL
                WHERE id = ? AND status = 'running'
                """,
                (
                    utc_now(),
                    str(research_path),
                    str(consolidated_path),
                    approval_stage,
                    task_id,
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Task {task_id} is not in the running state.")

    def set_script_approval(
        self,
        task_id: str,
        scripts_path: str | Path,
        workflow_thread_id: str,
        approved_path: str | Path | None = None,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'awaiting_approval',
                    approval_stage = 'script_review',
                    scripts_path = ?,
                    approved_path = COALESCE(?, approved_path),
                    workflow_thread_id = ?,
                    updated_at = ?,
                    active_stage = NULL,
                    active_agent = NULL,
                    active_since = NULL,
                    error = NULL
                WHERE id = ? AND status IN (
                    'awaiting_approval', 'completed', 'running'
                )
                """,
                (
                    str(scripts_path),
                    str(approved_path) if approved_path else None,
                    workflow_thread_id,
                    utc_now(),
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Task {task_id} cannot be moved to script approval."
                )

    def finish_approval(
        self,
        task_id: str,
        approved_path: str | Path | None = None,
        rejected: bool = False,
        approved_scripts_path: str | Path | None = None,
    ) -> None:
        status = "rejected" if rejected else "completed"
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = ?,
                    updated_at = ?,
                    approved_path = COALESCE(?, approved_path),
                    approved_scripts_path = COALESCE(?, approved_scripts_path),
                    active_stage = NULL,
                    active_agent = NULL,
                    active_since = NULL
                WHERE id = ? AND status IN ('awaiting_approval', 'running')
                """,
                (
                    status,
                    utc_now(),
                    str(approved_path) if approved_path else None,
                    str(approved_scripts_path) if approved_scripts_path else None,
                    task_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"Task {task_id} is not waiting for idea approval."
                )

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'failed', updated_at = ?, error = ?,
                    active_stage = NULL, active_agent = NULL, active_since = NULL
                WHERE id = ? AND status = 'running'
                """,
                (utc_now(), error, task_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Task {task_id} is not in the running state.")

    def fail_orphaned_running_tasks(self) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'failed',
                    updated_at = ?,
                    error = 'Worker stopped unexpectedly; inspect output before re-enqueueing.',
                    active_stage = NULL,
                    active_agent = NULL,
                    active_since = NULL
                WHERE status = 'running'
                """,
                (utc_now(),),
            )
            return cursor.rowcount

    def get_task(self, task_id: str) -> dict:
        with self._connection() as connection:
            task = connection.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        return dict(task)

    def list_tasks(self, limit: int = 50) -> list[dict]:
        with self._connection() as connection:
            tasks = connection.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(task) for task in tasks]

    def list_awaiting_approval(self) -> list[dict]:
        with self._connection() as connection:
            tasks = connection.execute(
                """
                SELECT * FROM tasks
                WHERE status = 'awaiting_approval'
                ORDER BY updated_at, id
                """
            ).fetchall()
        return [dict(task) for task in tasks]

    def set_task_activity(
        self,
        task_id: str,
        stage: str | None,
        agent: str | None,
    ) -> None:
        timestamp = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'running',
                    approval_stage = COALESCE(?, approval_stage),
                    active_stage = ?,
                    active_agent = ?,
                    active_since = ?,
                    updated_at = ?,
                    error = NULL
                WHERE id = ? AND status IN (
                    'queued', 'running', 'awaiting_approval', 'completed'
                )
                """,
                (stage, stage, agent, timestamp, timestamp, task_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Cannot update activity for task {task_id}.")

    def record_task_error(self, task_id: str, error: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE tasks SET error = ?, updated_at = ? WHERE id = ?",
                (error, utc_now(), task_id),
            )

    def create_worker_command(
        self,
        task_id: str,
        command_type: str,
        payload: dict,
    ) -> str:
        if command_type not in {"approve", "write_scripts"}:
            raise ValueError(f"Unsupported worker command: {command_type}")
        command_id = uuid4().hex
        timestamp = utc_now()
        with self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO worker_commands (
                        id, task_id, command_type, payload, status,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        command_id,
                        task_id,
                        command_type,
                        json.dumps(payload, ensure_ascii=False),
                        timestamp,
                        timestamp,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ValueError(
                    f"Task {task_id} already has a queued or running dashboard action."
                ) from error
        return command_id

    def claim_worker_command(self) -> dict | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            command = connection.execute(
                """
                SELECT * FROM worker_commands
                WHERE status = 'queued'
                ORDER BY created_at, id
                LIMIT 1
                """
            ).fetchone()
            if command is None:
                return None

            connection.execute(
                """
                UPDATE worker_commands
                SET status = 'running', updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (utc_now(), command["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM worker_commands WHERE id = ?",
                (command["id"],),
            ).fetchone()
            result = dict(claimed)
            result["payload"] = json.loads(result["payload"])
            return result

    def finish_worker_command(
        self,
        command_id: str,
        error: str | None = None,
    ) -> None:
        status = "failed" if error else "completed"
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE worker_commands
                SET status = ?, updated_at = ?, error = ?
                WHERE id = ? AND status = 'running'
                """,
                (status, utc_now(), error, command_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Worker command {command_id} is not running.")

    def list_worker_commands(self, limit: int = 20) -> list[dict]:
        with self._connection() as connection:
            commands = connection.execute(
                """
                SELECT * FROM worker_commands
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for command in commands:
            item = dict(command)
            item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    def set_stop_requested(self, requested: bool) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE worker_control
                SET stop_requested = ?, updated_at = ?
                WHERE singleton_id = 1
                """,
                (int(requested), utc_now()),
            )

    def is_stop_requested(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT stop_requested FROM worker_control WHERE singleton_id = 1"
            ).fetchone()
        return bool(row["stop_requested"]) if row else False

    def restore_approval_after_command_error(
        self,
        task_id: str,
        stage: str,
        error: str,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'awaiting_approval',
                    approval_stage = ?,
                    active_stage = NULL,
                    active_agent = NULL,
                    active_since = NULL,
                    error = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (stage, error, utc_now(), task_id),
            )
            if cursor.rowcount == 0:
                task_exists = connection.execute(
                    "SELECT 1 FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if task_exists is None:
                    raise KeyError(f"Task not found: {task_id}")

    def restore_completed_task_after_script_error(
        self,
        task_id: str,
        error: str,
    ) -> None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE tasks
                SET status = 'completed',
                    active_stage = NULL,
                    active_agent = NULL,
                    active_since = NULL,
                    error = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (error, utc_now(), task_id),
            )
            if cursor.rowcount == 0:
                task_exists = connection.execute(
                    "SELECT 1 FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                if task_exists is None:
                    raise KeyError(f"Task not found: {task_id}")
