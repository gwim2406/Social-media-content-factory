import argparse
import json
import logging
import sys
from datetime import timedelta
from pathlib import Path

from orchestration.runner import (
    QUEUE_DATABASE,
    approve_task,
    run_worker,
    start_scriptwriting_for_completed_task,
)
from orchestration.task_queue import TaskQueue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Queue and schedule human-supervised content research tasks."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    enqueue_parser = subparsers.add_parser("enqueue", help="Queue an immediate task.")
    enqueue_parser.add_argument("--topic", required=True)

    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Create a recurring research schedule (default: daily).",
    )
    schedule_parser.add_argument("--topic", required=True)
    schedule_parser.add_argument("--every-hours", type=float, default=24)
    schedule_parser.add_argument(
        "--run-now",
        action="store_true",
        help="Make the first scheduled run due immediately.",
    )

    worker_parser = subparsers.add_parser(
        "worker",
        help="Poll for queued tasks and due schedules.",
    )
    worker_parser.add_argument("--poll-seconds", type=float, default=30)
    worker_parser.add_argument(
        "--once",
        action="store_true",
        help="Process at most one task, then exit.",
    )

    subparsers.add_parser("list", help="List recent tasks.")

    show_parser = subparsers.add_parser("show", help="Show a task's idea report.")
    show_parser.add_argument("task_id")

    approve_parser = subparsers.add_parser(
        "approve",
        help="Approve selected ideas/scripts, or explicitly reject them.",
    )
    approve_parser.add_argument("task_id")
    decision_group = approve_parser.add_mutually_exclusive_group(required=True)
    decision_group.add_argument(
        "--ideas",
        help="For idea approval: comma-separated idea ranks, or 'all'.",
    )
    decision_group.add_argument(
        "--scripts",
        help="For script approval: comma-separated script ranks, or 'all'.",
    )
    decision_group.add_argument(
        "--reject",
        action="store_true",
        help="Reject the current approval stage.",
    )

    script_parser = subparsers.add_parser(
        "write-scripts",
        help="Start scriptwriting from ideas approved by an earlier workflow.",
    )
    script_parser.add_argument("task_id")

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Run the local web dashboard.",
    )
    dashboard_parser.add_argument("--host", default="127.0.0.1")
    dashboard_parser.add_argument("--port", type=int, default=8765)

    return parser


def _read_report(task: dict) -> dict:
    report_path = Path(task["consolidated_path"])
    if not report_path.is_file():
        raise FileNotFoundError(f"Consolidated report not found: {report_path}")
    with report_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_scripts(task: dict) -> dict:
    scripts_path = Path(task["scripts_path"])
    if not scripts_path.is_file():
        raise FileNotFoundError(f"Scripts file not found: {scripts_path}")
    with scripts_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_idea_ranks(value: str, report: dict, key: str = "ideas") -> list[int]:
    ideas = report.get(key, [])
    if value.strip().lower() == "all":
        return [idea["rank"] for idea in ideas]

    try:
        ranks = [int(part.strip()) for part in value.split(",") if part.strip()]
    except ValueError as error:
        raise ValueError("Idea ranks must be integers separated by commas.") from error

    if not ranks:
        raise ValueError("Select at least one idea rank, or use --reject.")
    if len(set(ranks)) != len(ranks):
        raise ValueError("Idea ranks must not be repeated.")

    available_ranks = {idea["rank"] for idea in ideas}
    invalid_ranks = sorted(set(ranks) - available_ranks)
    if invalid_ranks:
        raise ValueError(f"Unknown idea rank(s): {', '.join(map(str, invalid_ranks))}")
    return ranks


def _show_task(queue: TaskQueue, task_id: str) -> None:
    task = queue.get_task(task_id)
    print(f"Task: {task['id']}")
    print(f"Status: {task['status']}")
    print(f"Topic: {task['topic']}")
    if task["error"]:
        print(f"Error: {task['error']}")
    if task["status"] != "awaiting_approval":
        return

    if task.get("approval_stage") == "script_review":
        scripts_data = _read_scripts(task)
        print(f"\nScripts awaiting review: {scripts_data['script_count']}")
        for script in scripts_data["scripts"]:
            print(
                f"\n{script['rank']}. {script['title']} "
                f"(for approved idea {script.get('idea_rank', 'unknown')})"
            )
            print(script["script"])
        return

    report = _read_report(task)
    print(f"\nRanked ideas: {report['idea_count']}")
    for idea in report["ideas"]:
        providers = ", ".join(idea.get("provider_mentions", [])) or "unknown"
        sources = ", ".join(idea.get("supporting_sources", [])) or "No direct source list"
        print(
            f"\n{idea['rank']}. {idea['title']} "
            f"(editorial score {idea['score']}/100; providers: {providers})"
        )
        print(f"   Story: {idea.get('central_story') or 'Not provided'}")
        print(f"   Hook: {idea.get('opening_hook') or 'Not provided'}")
        print(f"   Audience: {idea.get('target_audience') or 'Not provided'}")
        print(f"   Appeal: {idea.get('emotional_appeal') or 'Not provided'}")
        print(f"   Format: {idea.get('suggested_video_format') or 'Not provided'}")
        print(f"   Evidence: {idea.get('evidence_status', 'unknown')}")
        print(f"   Sources: {sources}")
        print(
            "   Fact checks: "
            f"{idea.get('potential_problems_or_fact_checks') or 'Not provided'}"
        )
    if report.get("missing_evidence"):
        print("\nResearch gaps:")
        for item in report["missing_evidence"]:
            print(f"- {item}")


def _approve_task(
    queue: TaskQueue,
    task_id: str,
    ranks: list[int],
    rejected: bool,
) -> dict:
    final_state = approve_task(
        queue,
        task_id,
        ranks=ranks,
        rejected=rejected,
    )
    if final_state.get("awaiting_approval"):
        print(
            f"Ideas approved. Scripts are ready for review in "
            f"{final_state['scripts_path']}."
        )
        return final_state

    approved_path = (
        final_state.get("approved_scripts_path")
        or final_state.get("approved_path")
    )
    if rejected:
        print(f"Task {task_id} rejected. Saved decision to {approved_path}")
    else:
        stage_name = "script" if final_state.get("script_approval_decision") else "idea"
        print(
            f"Approved {stage_name} rank(s) {', '.join(str(rank) for rank in ranks)} "
            f"for task {task_id}. Saved to {approved_path}"
        )
    return final_state


def _interactive_approval(queue: TaskQueue, task: dict) -> None:
    while True:
        _show_task(queue, task["id"])
        is_script_review = task.get("approval_stage") == "script_review"
        approval_kind = "script" if is_script_review else "idea"
        try:
            selection = input(
                f"\nApprove {approval_kind} number(s), comma-separated "
                "(e.g. 1,3), 'all', or 'reject': "
            ).strip()
        except EOFError:
            print(
                "\nNo interactive input available. The task remains awaiting approval."
            )
            return

        if selection.lower() == "reject":
            _approve_task(queue, task["id"], [], rejected=True)
            return

        report = _read_scripts(task) if is_script_review else _read_report(task)
        try:
            ranks = _parse_idea_ranks(
                selection,
                report,
                key="scripts" if is_script_review else "ideas",
            )
        except ValueError as error:
            print(f"Invalid selection: {error}")
            continue

        result = _approve_task(queue, task["id"], ranks, rejected=False)
        if result.get("awaiting_approval"):
            task = queue.get_task(task["id"])
            continue
        return


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = build_parser().parse_args()
    queue = TaskQueue(QUEUE_DATABASE)

    if args.command == "enqueue":
        task_id = queue.enqueue(args.topic)
        print(f"Queued task {task_id}.")
        return 0

    if args.command == "schedule":
        interval_seconds = int(args.every_hours * 60 * 60)
        schedule_id = queue.add_schedule(
            args.topic,
            interval_seconds,
            run_immediately=args.run_now,
        )
        interval = timedelta(seconds=interval_seconds)
        print(f"Created schedule {schedule_id} to run every {interval}.")
        if args.run_now:
            print("The first run is due now; start the worker to process it.")
        return 0

    if args.command == "worker":
        return run_worker(
            queue,
            poll_seconds=args.poll_seconds,
            once=args.once,
            approval_handler=_interactive_approval if sys.stdin.isatty() else None,
        )

    if args.command == "list":
        for task in queue.list_tasks():
            print(
                f"{task['id']}  {task['status']:<18} "
                f"{task['origin']:<8} {task['topic']}"
            )
        return 0

    if args.command == "show":
        _show_task(queue, args.task_id)
        return 0

    if args.command == "approve":
        if args.reject:
            _approve_task(queue, args.task_id, [], rejected=True)
        else:
            task = queue.get_task(args.task_id)
            if task["status"] != "awaiting_approval":
                raise ValueError(
                    f"Task {args.task_id} is {task['status']}, "
                    "not awaiting idea approval."
                )
            is_script_review = task.get("approval_stage") == "script_review"
            if is_script_review and not args.scripts:
                raise ValueError("This task needs script approval; use --scripts.")
            if not is_script_review and args.scripts:
                raise ValueError("This task needs idea approval; use --ideas.")
            report = _read_scripts(task) if is_script_review else _read_report(task)
            ranks = _parse_idea_ranks(
                args.scripts if is_script_review else args.ideas,
                report,
                key="scripts" if is_script_review else "ideas",
            )
            _approve_task(queue, args.task_id, ranks, rejected=False)
        return 0

    if args.command == "write-scripts":
        task = start_scriptwriting_for_completed_task(queue, args.task_id)
        if sys.stdin.isatty():
            _interactive_approval(queue, task)
        else:
            print(
                f"Scripts are ready for approval. Review them with: "
                f"python -m orchestration.cli show {args.task_id}"
            )
        return 0

    if args.command == "dashboard":
        from orchestration.dashboard import create_server

        server = create_server(host=args.host, port=args.port)
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
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
