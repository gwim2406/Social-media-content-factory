import json
import logging
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agents.scriptwriter import normalize_approved_idea
from orchestration.runner import (
    QUEUE_DATABASE,
    TASKS_DIR,
    run_worker,
)
from orchestration.task_queue import TaskQueue


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "dashboard"
LOGGER = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 64 * 1024
AGENT_NAMES = (
    "Gemini Web Researcher",
    "Idea Consolidator",
    "Scriptwriter (Gemini Browser)",
)
STAGES = (
    ("research", "Web research and AI analysis"),
    ("idea_consolidation", "Idea consolidation"),
    ("idea_selection", "Idea approval"),
    ("scriptwriting", "Scriptwriting"),
    ("script_review", "Script approval"),
    ("video_generation", "Video generation"),
    ("video_review", "Video approval"),
    ("publishing", "Publishing"),
)


class DashboardController:
    def __init__(self, queue: TaskQueue, poll_seconds: float = 2):
        self.queue = queue
        self.poll_seconds = poll_seconds
        self._lock = threading.RLock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._last_exit_code: int | None = None
        self._last_error: str | None = None

    def start_worker(self) -> dict:
        with self._lock:
            self.queue.set_stop_requested(False)
            if self._thread and self._thread.is_alive():
                if self._stop_event and self._stop_event.is_set():
                    self._stop_event.clear()
                return self.status()

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._run_worker,
                args=(stop_event,),
                name="social-agent-worker",
                daemon=True,
            )
            self._stop_event = stop_event
            self._thread = thread
            self._last_error = None
            thread.start()
            return self.status()

    def _run_worker(self, stop_event: threading.Event) -> None:
        try:
            exit_code = run_worker(
                self.queue,
                poll_seconds=self.poll_seconds,
                stop_event=stop_event,
            )
            with self._lock:
                self._last_exit_code = exit_code
        except Exception as error:
            LOGGER.exception("Dashboard worker stopped unexpectedly.")
            with self._lock:
                self._last_error = f"{type(error).__name__}: {error}"

    def request_stop(self) -> dict:
        self.queue.set_stop_requested(True)
        with self._lock:
            if self._thread and self._thread.is_alive() and self._stop_event:
                self._stop_event.set()
            return self.status()

    def status(self) -> dict:
        with self._lock:
            running = bool(self._thread and self._thread.is_alive())
            stop_requested = bool(
                running and self._stop_event and self._stop_event.is_set()
            )
            return {
                "running": running,
                "stop_requested": stop_requested,
                "last_exit_code": self._last_exit_code,
                "last_error": self._last_error,
            }


def _read_json_artifact(raw_path: str | None) -> dict | None:
    if not raw_path:
        return None

    task_root = TASKS_DIR.resolve()
    artifact_path = Path(raw_path).resolve()
    if not artifact_path.is_relative_to(task_root):
        raise ValueError("Task artifact path is outside the task workspace.")
    if not artifact_path.is_file():
        return None

    with artifact_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _task_with_artifacts(task: dict) -> dict:
    result = dict(task)
    result["ideas"] = []
    result["approved_ideas"] = []
    result["scripts"] = []
    result["approved_scripts"] = []
    artifact_errors = []

    try:
        report = _read_json_artifact(task.get("consolidated_path"))
        if report:
            result["ideas"] = [
                normalize_approved_idea(idea)
                for idea in report.get("ideas", [])
            ]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        artifact_errors.append(f"Could not read ideas: {error}")

    try:
        approval = _read_json_artifact(task.get("approved_path"))
        if approval:
            result["approved_ideas"] = [
                normalize_approved_idea(idea)
                for idea in approval.get("approved_ideas", [])
            ]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        artifact_errors.append(f"Could not read approved ideas: {error}")

    try:
        scripts_data = _read_json_artifact(task.get("scripts_path"))
        if scripts_data:
            result["scripts"] = scripts_data.get("scripts", [])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        artifact_errors.append(f"Could not read scripts: {error}")

    try:
        approved_scripts = _read_json_artifact(task.get("approved_scripts_path"))
        if approved_scripts:
            result["approved_scripts"] = approved_scripts.get("approved_scripts", [])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        artifact_errors.append(f"Could not read approved scripts: {error}")

    result["artifact_errors"] = artifact_errors
    result["stage_label"] = dict(STAGES).get(
        result.get("active_stage") or result.get("approval_stage"),
        result.get("active_stage") or result.get("approval_stage") or "Queued",
    )
    return result


def _dashboard_state(queue: TaskQueue, controller: DashboardController) -> dict:
    raw_tasks = queue.list_tasks(limit=50)
    running_task = next((task for task in raw_tasks if task["status"] == "running"), None)
    focus_task = running_task or next(
        (task for task in raw_tasks if task["status"] == "awaiting_approval"),
        None,
    ) or (raw_tasks[0] if raw_tasks else None)
    tasks = []
    for task in raw_tasks:
        summary = {
            key: value
            for key, value in task.items()
            if key not in {
                "error",
                "research_path",
                "consolidated_path",
                "approved_path",
                "scripts_path",
                "approved_scripts_path",
            }
        }
        summary["stage_label"] = dict(STAGES).get(
            summary.get("active_stage") or summary.get("approval_stage"),
            summary.get("active_stage") or summary.get("approval_stage") or "Queued",
        )
        tasks.append(summary)
    worker_status = controller.status()
    worker_status["task_active"] = running_task is not None
    worker_status["running"] = worker_status["running"] or running_task is not None
    worker_status["stop_requested"] = worker_status["running"] and (
        worker_status["stop_requested"] or queue.is_stop_requested()
    )

    active_agent = running_task.get("active_agent") if running_task else None
    waiting_stage = (
        focus_task.get("approval_stage")
        if focus_task and focus_task["status"] == "awaiting_approval"
        else None
    )
    agents = []
    for name in AGENT_NAMES:
        active = name == active_agent
        if active:
            state = "active"
        elif waiting_stage and name == "Human Reviewer":
            state = "waiting"
        else:
            state = "idle"
        agents.append({"name": name, "status": state})
    agents.append({
        "name": "Human Reviewer",
        "status": "waiting" if waiting_stage else "idle",
    })

    return {
        "worker": worker_status,
        "agents": agents,
        "current_task_id": focus_task["id"] if focus_task else None,
        "current_task": _task_with_artifacts(focus_task) if focus_task else None,
        "tasks": tasks,
        "stages": [
            {"id": stage_id, "label": label}
            for stage_id, label in STAGES
        ],
        "commands": queue.list_worker_commands(limit=20),
    }


def _validate_ranks(selection: object, artifact: dict, key: str) -> list[int]:
    if not isinstance(selection, list) or not selection:
        raise ValueError("Select one or more items.")
    if any(type(rank) is not int for rank in selection):
        raise ValueError("Selections must be integer ranks.")
    if len(set(selection)) != len(selection):
        raise ValueError("Selections must not contain duplicate ranks.")
    available = {item.get("rank") for item in artifact.get(key, [])}
    invalid = sorted(set(selection) - available)
    if invalid:
        raise ValueError(f"Unknown rank(s): {invalid}")
    return selection


class DashboardRequestHandler(BaseHTTPRequestHandler):
    server_version = "SocialAgentDashboard/1.0"
    controller: DashboardController
    queue: TaskQueue

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json(status, {"error": message})

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            raise ValueError("Request body must be between 1 byte and 64 KiB.")
        try:
            data = json.loads(self.rfile.read(content_length))
        except json.JSONDecodeError as error:
            raise ValueError("Request body must be valid JSON.") from error
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    def _validate_local_request(self) -> bool:
        if self.client_address[0] not in {"127.0.0.1", "::1"}:
            self._send_error_json(403, "Dashboard accepts local connections only.")
            return False
        request_host = urlsplit(
            f"//{self.headers.get('Host', '')}"
        ).hostname
        if request_host not in {"127.0.0.1", "localhost", "::1"}:
            self._send_error_json(403, "Dashboard accepts local hosts only.")
            return False
        origin = self.headers.get("Origin")
        if origin:
            origin_parts = urlsplit(origin)
            if (
                origin_parts.scheme != "http"
                or origin_parts.hostname not in {"127.0.0.1", "localhost", "::1"}
                or origin_parts.netloc.lower() != self.headers.get("Host", "").lower()
            ):
                self._send_error_json(403, "Cross-origin requests are not allowed.")
                return False
        return True

    def do_GET(self) -> None:
        route = urlsplit(self.path).path
        if route == "/":
            page_path = STATIC_DIR / "index.html"
            body = page_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/api/state":
            self._send_json(200, _dashboard_state(self.queue, self.controller))
            return

        task_match = re.fullmatch(r"/api/tasks/([0-9a-f]{32})", route)
        if task_match:
            try:
                task = _task_with_artifacts(self.queue.get_task(task_match.group(1)))
            except KeyError as error:
                self._send_error_json(404, str(error))
                return
            self._send_json(200, task)
            return

        self._send_error_json(404, "Route not found.")

    def do_POST(self) -> None:
        if not self._validate_local_request():
            return

        route = urlsplit(self.path).path
        try:
            data = self._read_json_body()
            if route == "/api/start":
                topic = data.get("topic")
                if not isinstance(topic, str) or not topic.strip():
                    raise ValueError("Enter a research topic before starting.")
                if len(topic.strip()) > 500:
                    raise ValueError("Topic must be 500 characters or fewer.")
                task_id = self.queue.enqueue(topic.strip())
                worker = self.controller.start_worker()
                self._send_json(
                    202,
                    {
                        "task_id": task_id,
                        "message": "Manual task queued.",
                        "worker": worker,
                    },
                )
                return

            if route == "/api/worker/start":
                self._send_json(200, {"worker": self.controller.start_worker()})
                return

            if route == "/api/worker/stop":
                self._send_json(200, {"worker": self.controller.request_stop()})
                return

            decision_match = re.fullmatch(
                r"/api/tasks/([0-9a-f]{32})/approve",
                route,
            )
            if decision_match:
                task_id = decision_match.group(1)
                task = self.queue.get_task(task_id)
                if task["status"] != "awaiting_approval":
                    raise ValueError("This task is not awaiting approval.")
                requested_stage = data.get("stage")
                if requested_stage != task["approval_stage"]:
                    raise ValueError(
                        f"Task is awaiting {task['approval_stage']} approval."
                    )

                if data.get("decision") == "reject":
                    payload = {"stage": requested_stage, "decision": "reject"}
                elif data.get("decision") == "approve":
                    artifact_key = (
                        "scripts_path"
                        if requested_stage == "script_review"
                        else "consolidated_path"
                    )
                    artifact_path = task.get(artifact_key)
                    artifact = _read_json_artifact(artifact_path)
                    if not artifact:
                        raise ValueError("Approval artifact is not available.")
                    item_key = "scripts" if requested_stage == "script_review" else "ideas"
                    ranks = _validate_ranks(data.get("ranks"), artifact, item_key)
                    payload = {
                        "stage": requested_stage,
                        "decision": "approve",
                        "ranks": ranks,
                    }
                else:
                    raise ValueError("Decision must be 'approve' or 'reject'.")

                command_id = self.queue.create_worker_command(
                    task_id,
                    "approve",
                    payload,
                )
                worker = self.controller.start_worker()
                self._send_json(
                    202,
                    {
                        "command_id": command_id,
                        "message": "Approval submitted to the worker.",
                        "worker": worker,
                    },
                )
                return

            script_match = re.fullmatch(
                r"/api/tasks/([0-9a-f]{32})/write-scripts",
                route,
            )
            if script_match:
                task_id = script_match.group(1)
                task = self.queue.get_task(task_id)
                if task["status"] != "completed" or not task.get("approved_path"):
                    raise ValueError("Task must have approved ideas before scriptwriting.")
                command_id = self.queue.create_worker_command(
                    task_id,
                    "write_scripts",
                    {"stage": "script_review"},
                )
                worker = self.controller.start_worker()
                self._send_json(
                    202,
                    {
                        "command_id": command_id,
                        "message": "Scriptwriting task submitted to the worker.",
                        "worker": worker,
                    },
                )
                return

            self._send_error_json(404, "Route not found.")
        except KeyError as error:
            self._send_error_json(404, str(error))
        except (ValueError, json.JSONDecodeError) as error:
            self._send_error_json(400, str(error))
        except Exception as error:
            LOGGER.exception("Dashboard request failed.")
            self._send_error_json(500, f"{type(error).__name__}: {error}")

    def log_message(self, format_string: str, *args) -> None:
        LOGGER.info("Dashboard %s - %s", self.address_string(), format_string % args)


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    queue: TaskQueue | None = None,
    controller: DashboardController | None = None,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("The dashboard must bind to a loopback address.")

    queue = queue or TaskQueue(QUEUE_DATABASE)
    controller = controller or DashboardController(queue)

    class BoundDashboardHandler(DashboardRequestHandler):
        pass

    BoundDashboardHandler.queue = queue
    BoundDashboardHandler.controller = controller
    server = ThreadingHTTPServer((host, port), BoundDashboardHandler)
    server.daemon_threads = True
    server.dashboard_controller = controller
    return server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = create_server()
    host, port = server.server_address[:2]
    print(f"Dashboard available at http://{host}:{port}")
    print("Press Ctrl+C to stop the dashboard and request a graceful worker stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.dashboard_controller.request_stop()
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
