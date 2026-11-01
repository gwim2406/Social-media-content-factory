import logging
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from typing import Callable

import fcntl
from langgraph.checkpoint.sqlite import SqliteSaver

from orchestration.task_queue import TaskQueue
from orchestration.workflow import (
    build_workflow,
    resume_task_approval,
    task_config,
)


BASE_DIR = Path(__file__).resolve().parent.parent
WORKSPACE_DIR = BASE_DIR / "workspace"
TASKS_DIR = WORKSPACE_DIR / "tasks"
QUEUE_DATABASE = WORKSPACE_DIR / "agent_queue.sqlite"
WORKFLOW_DATABASE = WORKSPACE_DIR / "agent_workflow.sqlite"
WORKER_LOCK = WORKSPACE_DIR / "agent_worker.lock"
LOGGER = logging.getLogger(__name__)


@contextmanager
def exclusive_worker_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+", encoding="utf-8")
    locked = False
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except BlockingIOError as error:
            raise RuntimeError(
                "Another worker is already running. Use only one worker process."
            ) from error

        lock_handle.seek(0)
        lock_handle.truncate()
        lock_handle.write(f"{os.getpid()}\n")
        lock_handle.flush()
        yield
    finally:
        try:
            if locked:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def process_next_task(queue: TaskQueue) -> dict | None:
    queue.enqueue_due_schedules()
    task = queue.claim_next()
    if task is None:
        return None

    task_dir = TASKS_DIR / task["id"]
    task_dir.mkdir(parents=True, exist_ok=True)
    research_path = task_dir / "latest_content_research.json"
    consolidated_path = task_dir / "latest_consolidated_ideas.json"

    try:
        with SqliteSaver.from_conn_string(str(WORKFLOW_DATABASE)) as checkpointer:
            workflow = build_workflow(
                checkpointer,
                on_stage=queue.set_task_activity,
            )
            result = workflow.invoke(
                {
                    "task_id": task["id"],
                    "topic": task["topic"],
                    "task_dir": str(task_dir),
                },
                config=task_config(task["id"]),
            )

        if "__interrupt__" not in result:
            raise RuntimeError("The workflow ended without reaching idea approval.")

        approval_stage = result["__interrupt__"][0].value.get("stage")
        if approval_stage != "idea_selection":
            raise RuntimeError(
                f"Expected idea-selection approval, received {approval_stage!r}."
            )
        queue.mark_awaiting_approval(
            task["id"], research_path=research_path,
            consolidated_path=consolidated_path,
            approval_stage=approval_stage,
        )
    except KeyboardInterrupt:
        queue.mark_failed(task["id"], "Worker interrupted by the user.")
        raise
    except Exception as error:
        queue.mark_failed(task["id"], f"{type(error).__name__}: {error}")
        raise

    return queue.get_task(task["id"])


def approve_task(
    queue: TaskQueue,
    task_id: str,
    ranks: list[int] | None = None,
    rejected: bool = False,
) -> dict:
    task = queue.get_task(task_id)
    if task["status"] not in {"awaiting_approval", "running"}:
        raise ValueError(
            f"Task {task_id} is {task['status']}, not awaiting idea approval."
        )

    decision = {"decision": "reject"} if rejected else {
        "decision": "approve",
        "ranks": ranks or [],
    }
    workflow_thread_id = task.get("workflow_thread_id") or task_id
    with SqliteSaver.from_conn_string(str(WORKFLOW_DATABASE)) as checkpointer:
        workflow = build_workflow(
            checkpointer,
            on_stage=queue.set_task_activity,
        )
        result = resume_task_approval(
            workflow,
            task_id,
            decision,
            workflow_thread_id=workflow_thread_id,
        )

    if "__interrupt__" in result:
        interrupt_value = result["__interrupt__"][0].value
        approval_stage = interrupt_value.get("stage")
        if approval_stage != "script_review":
            raise RuntimeError(
                f"Unexpected workflow approval stage: {approval_stage!r}."
            )
        task_dir = Path(task["research_path"]).parent
        scripts_path = task_dir / "scripts.json"
        queue.set_script_approval(
            task_id,
            scripts_path=scripts_path,
            workflow_thread_id=workflow_thread_id,
            approved_path=task_dir / "approved_ideas.json",
        )
        return {
            "awaiting_approval": True,
            "approval_stage": approval_stage,
            "scripts_path": str(scripts_path),
        }

    approved_path = result.get("approved_path")
    script_decision = result.get("script_approval_decision")
    idea_decision = result.get("idea_approval_decision")
    if script_decision in {"approved", "rejected"}:
        approved_scripts_path = result.get("approved_scripts_path")
        if not approved_scripts_path:
            raise RuntimeError(f"Script approval did not save its result for {task_id}.")
        queue.finish_approval(
            task_id,
            approved_path=approved_path,
            approved_scripts_path=approved_scripts_path,
            rejected=script_decision == "rejected",
        )
    elif idea_decision == "rejected":
        queue.finish_approval(task_id, approved_path=approved_path, rejected=True)
    else:
        raise RuntimeError(f"Workflow did not complete approval for task {task_id}.")

    return result


def start_scriptwriting_for_completed_task(queue: TaskQueue, task_id: str) -> dict:
    task = queue.get_task(task_id)
    if task["status"] not in {"completed", "running"} or not task.get("approved_path"):
        raise ValueError(
            f"Task {task_id} must be completed with approved ideas before scriptwriting."
        )

    approved_path = Path(task["approved_path"])
    with approved_path.open("r", encoding="utf-8") as handle:
        approval_data = json.load(handle)
    approved_ideas = approval_data.get("approved_ideas", [])
    if not approved_ideas:
        raise ValueError(f"No approved ideas are present in {approved_path}.")

    if not task.get("research_path"):
        raise ValueError("The task has no saved research file for scriptwriting.")

    workflow_thread_id = f"{task_id}:scriptwriting"
    task_dir = approved_path.parent
    with SqliteSaver.from_conn_string(str(WORKFLOW_DATABASE)) as checkpointer:
        workflow = build_workflow(
            checkpointer,
            on_stage=queue.set_task_activity,
        )
        result = workflow.invoke(
            {
                "task_id": task_id,
                "topic": task["topic"],
                "task_dir": str(task_dir),
                "research_path": task["research_path"],
                "approved_ideas": approved_ideas,
                "script_only": True,
            },
            config=task_config(workflow_thread_id),
        )

    if "__interrupt__" not in result:
        raise RuntimeError("Scriptwriting ended without reaching script approval.")
    interrupt_value = result["__interrupt__"][0].value
    if interrupt_value.get("stage") != "script_review":
        raise RuntimeError(
            f"Expected script-review approval, received {interrupt_value.get('stage')!r}."
        )

    scripts_path = task_dir / "scripts.json"
    queue.set_script_approval(
        task_id,
        scripts_path=scripts_path,
        workflow_thread_id=workflow_thread_id,
        approved_path=approved_path,
    )
    return queue.get_task(task_id)


def process_worker_command(queue: TaskQueue, command: dict) -> None:
    task_id = command["task_id"]
    payload = command["payload"]
    command_type = command["command_type"]
    stage = payload.get(
        "stage",
        "script_review" if command_type == "write_scripts" else "idea_selection",
    )

    try:
        if command_type == "approve":
            task = queue.get_task(task_id)
            if task["approval_stage"] != stage:
                raise ValueError(
                    f"Task is at {task['approval_stage']}, not requested stage {stage}."
                )
            agent = (
                "Scriptwriter (Gemini Browser)"
                if stage == "idea_selection" and payload.get("decision") == "approve"
                else "Workflow Orchestrator"
            )
            active_stage = (
                "scriptwriting"
                if agent == "Scriptwriter (Gemini Browser)"
                else stage
            )
            queue.set_task_activity(task_id, active_stage, agent)
            approve_task(
                queue,
                task_id,
                ranks=payload.get("ranks"),
                rejected=payload.get("decision") == "reject",
            )
        elif command_type == "write_scripts":
            queue.set_task_activity(
                task_id,
                "scriptwriting",
                "Scriptwriter (Gemini Browser)",
            )
            start_scriptwriting_for_completed_task(queue, task_id)
        else:
            raise ValueError(f"Unsupported worker command: {command_type}")
    except Exception as error:
        error_message = f"{type(error).__name__}: {error}"
        queue.finish_worker_command(command["id"], error=error_message)
        if command_type == "approve":
            queue.restore_approval_after_command_error(
                task_id,
                stage=stage,
                error=error_message,
            )
        else:
            queue.restore_completed_task_after_script_error(task_id, error_message)
        raise
    else:
        queue.finish_worker_command(command["id"])


def run_worker(
    queue: TaskQueue,
    poll_seconds: float = 30,
    once: bool = False,
    approval_handler: Callable[[TaskQueue, dict], None] | None = None,
    stop_event: Event | None = None,
) -> int:
    if poll_seconds <= 0:
        raise ValueError("Poll interval must be greater than zero.")

    with exclusive_worker_lock(WORKER_LOCK):
        queue.set_stop_requested(False)
        orphaned_count = queue.fail_orphaned_running_tasks()
        if orphaned_count:
            LOGGER.warning(
                "Marked %s interrupted task(s) failed; inspect before re-enqueueing.",
                orphaned_count,
            )

        LOGGER.info("Worker started; polling every %s seconds.", poll_seconds)
        reported_pending: set[str] = set()
        while True:
            if (stop_event and stop_event.is_set()) or queue.is_stop_requested():
                LOGGER.info("Worker stopped at a safe task boundary.")
                return 0

            try:
                queue.enqueue_due_schedules()
                command = queue.claim_worker_command()
                if command:
                    process_worker_command(queue, command)
                    if once:
                        return 0
                    continue

                pending_approvals = queue.list_awaiting_approval()
                if pending_approvals:
                    if approval_handler is None:
                        for pending_task in pending_approvals:
                            if pending_task["id"] not in reported_pending:
                                print(
                                    f"Task {pending_task['id']} is awaiting "
                                    f"{pending_task['approval_stage']} approval."
                                )
                                reported_pending.add(pending_task["id"])
                    else:
                        for pending_task in pending_approvals:
                            approval_handler(queue, pending_task)
                        if once:
                            return 0
                        continue

                task = process_next_task(queue)
                if task:
                    if approval_handler is None:
                        print(
                            f"Task {task['id']} is awaiting "
                            f"{task['approval_stage']} approval."
                        )
                    else:
                        approval_handler(queue, task)
                    if once:
                        return 0
                    continue

                if once:
                    print("No queued tasks or due schedules.")
                    return 0

                if stop_event:
                    stop_event.wait(poll_seconds)
                else:
                    time.sleep(poll_seconds)
            except KeyboardInterrupt:
                LOGGER.info("Worker stopped. Any pending approval remains saved.")
                return 130
            except Exception:
                LOGGER.exception("Task processing or approval failed.")
                if once:
                    return 1
                try:
                    if stop_event:
                        stop_event.wait(poll_seconds)
                    else:
                        time.sleep(poll_seconds)
                except KeyboardInterrupt:
                    LOGGER.info("Worker stopped.")
                    return 130
