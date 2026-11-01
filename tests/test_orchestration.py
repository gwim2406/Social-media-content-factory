import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver

from agents.content_researcher import ContentResearcher
from agents.content_consolidator import ContentConsolidator
from agents.scriptwriter import Scriptwriter
from providers import browser_ai
from providers.browser_ai import (
    BrowserAIResponse,
    ask_ai_with_sources,
    extract_citations,
)
from orchestration import runner
from orchestration import dashboard
from orchestration.runner import exclusive_worker_lock
from orchestration.task_queue import TaskQueue
from orchestration.workflow import (
    build_workflow,
    resume_task_approval,
    task_config,
)
from orchestration.cli import _interactive_approval


class ContentConsolidatorTests(unittest.TestCase):
    def test_extracts_ideas_from_repeated_title_labels(self):
        response = (
            "Gemini said\n\n"
            "Title: The Street-Legal V8 Wagon\n\n"
            "Central Story: A builder made a street-legal wagon with a V8 engine.\n\n"
            "Opening Hook: What happens when a toy wagon gets a V8?\n\n"
            "Supporting Sources: Motor1.com - https://example.test/wagon\n\n"
            "Potential Problems or Fact Checks: Verify its road legality.\n\n"
            "Title: The Upside-Down Electric Car\n\n"
            "Central Story: An electric track car drove upside down.\n\n"
            "Opening Hook: Can a car drive on a ceiling?\n\n"
            "Supporting Sources: The Car Guide - https://example.test/car\n\n"
            "Potential Problems or Fact Checks: Verify the test conditions.\n\n"
            "Sources\n"
            "Motor1.com: https://example.test/wagon\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            research_path = root / "research.json"
            research_path.write_text(
                json.dumps({
                    "topic": "Unusual automotive stories",
                    "provider_analyses": {
                        "gemini": {"status": "success", "response": response}
                    },
                }),
                encoding="utf-8",
            )

            report = ContentConsolidator(
                research_file=research_path,
                output_dir=root,
            ).consolidate()

        self.assertEqual(report["idea_count"], 2)
        self.assertEqual(report["ideas"][0]["title"], "The Street-Legal V8 Wagon")
        self.assertEqual(
            report["ideas"][0]["central_story"],
            "A builder made a street-legal wagon with a V8 engine.",
        )
        self.assertIn(
            "https://example.test/wagon",
            report["ideas"][0]["supporting_sources"][0],
        )
        self.assertNotIn(
            "Sources\n",
            report["ideas"][1]["potential_problems_or_fact_checks"],
        )


class TaskQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.queue = TaskQueue(Path(self.temp_dir.name) / "queue.sqlite")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manual_task_can_be_claimed_and_wait_for_approval(self):
        task_id = self.queue.enqueue("Automotive stories")

        task = self.queue.claim_next()
        self.assertEqual(task["id"], task_id)
        self.assertEqual(task["status"], "running")

        self.queue.mark_awaiting_approval(
            task_id,
            "task/research.json",
            "task/ideas.json",
        )
        self.assertEqual(self.queue.get_task(task_id)["status"], "awaiting_approval")

    def test_due_schedule_is_enqueued_once_per_interval(self):
        schedule_id = self.queue.add_schedule(
            "Automotive stories",
            interval_seconds=60,
            run_immediately=True,
        )
        now = time.time() + 1

        task_ids = self.queue.enqueue_due_schedules(now=now)
        self.assertEqual(len(task_ids), 1)
        self.assertEqual(self.queue.enqueue_due_schedules(now=now), [])

        task = self.queue.get_task(task_ids[0])
        self.assertEqual(task["schedule_id"], schedule_id)
        self.assertEqual(task["origin"], "schedule")

    def test_interrupted_task_is_marked_failed_on_worker_recovery(self):
        task_id = self.queue.enqueue("Automotive stories")
        self.queue.claim_next()

        failed_count = self.queue.fail_orphaned_running_tasks()

        self.assertEqual(failed_count, 1)
        self.assertEqual(self.queue.get_task(task_id)["status"], "failed")

    def test_dashboard_command_queue_is_durable_and_claimed_once(self):
        task_id = self.queue.enqueue("Automotive stories")
        command_id = self.queue.create_worker_command(
            task_id,
            "approve",
            {"stage": "idea_selection", "decision": "approve", "ranks": [1]},
        )
        command = self.queue.claim_worker_command()

        self.assertEqual(command["id"], command_id)
        self.assertEqual(command["payload"]["ranks"], [1])
        self.assertIsNone(self.queue.claim_worker_command())
        self.queue.finish_worker_command(command_id)
        self.assertEqual(self.queue.list_worker_commands()[0]["status"], "completed")

    def test_activity_updates_running_stage_and_agent(self):
        task_id = self.queue.enqueue("Automotive stories")
        self.queue.claim_next()

        self.queue.set_task_activity(task_id, "research", "Gemini Web Researcher")

        task = self.queue.get_task(task_id)
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["active_stage"], "research")
        self.assertEqual(task["active_agent"], "Gemini Web Researcher")
        self.assertIsNotNone(task["active_since"])

    def test_only_one_worker_can_hold_the_lock(self):
        lock_path = Path(self.temp_dir.name) / "worker.lock"
        with exclusive_worker_lock(lock_path):
            with self.assertRaises(RuntimeError):
                with exclusive_worker_lock(lock_path):
                    pass

    def test_worker_prompts_handler_for_existing_pending_approval(self):
        task_id = self.queue.enqueue("Automotive stories")
        self.queue.claim_next()
        self.queue.mark_awaiting_approval(
            task_id,
            "task/research.json",
            "task/ideas.json",
        )
        handled_tasks = []

        with patch.object(runner, "WORKER_LOCK", Path(self.temp_dir.name) / "worker.lock"):
            result = runner.run_worker(
                self.queue,
                once=True,
                approval_handler=lambda queue, task: handled_tasks.append(task["id"]),
            )

        self.assertEqual(result, 0)
        self.assertEqual(handled_tasks, [task_id])

    def test_worker_stops_cleanly_when_interrupted_during_idle_wait(self):
        with (
            patch.object(runner, "WORKER_LOCK", Path(self.temp_dir.name) / "worker.lock"),
            patch.object(runner, "process_next_task", return_value=None),
            patch.object(runner.time, "sleep", side_effect=KeyboardInterrupt),
        ):
            result = runner.run_worker(self.queue, poll_seconds=1)

        self.assertEqual(result, 130)

    def test_worker_sleeps_when_approval_is_pending_without_interactive_handler(self):
        task_id = self.queue.enqueue("Automotive stories")
        self.queue.claim_next()
        self.queue.mark_awaiting_approval(
            task_id,
            "task/research.json",
            "task/ideas.json",
        )

        with (
            patch.object(runner, "WORKER_LOCK", Path(self.temp_dir.name) / "worker.lock"),
            patch.object(runner.time, "sleep", side_effect=KeyboardInterrupt) as sleep,
        ):
            result = runner.run_worker(self.queue, poll_seconds=1)

        self.assertEqual(result, 130)
        sleep.assert_called_once_with(1)


class DashboardTests(unittest.TestCase):
    class FakeController:
        def __init__(self, queue):
            self.queue = queue
            self.start_calls = 0
            self.stop_calls = 0

        def start_worker(self):
            self.start_calls += 1
            self.queue.set_stop_requested(False)
            return self.status()

        def request_stop(self):
            self.stop_calls += 1
            self.queue.set_stop_requested(True)
            return self.status()

        def status(self):
            return {
                "running": self.start_calls > self.stop_calls,
                "stop_requested": self.queue.is_stop_requested(),
                "last_exit_code": None,
                "last_error": None,
            }

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.queue = TaskQueue(root / "queue.sqlite")
        self.controller = self.FakeController(self.queue)
        self.tasks_patch = patch.object(dashboard, "TASKS_DIR", root / "tasks")
        self.tasks_patch.start()
        self.server = dashboard.create_server(
            host="127.0.0.1",
            port=0,
            queue=self.queue,
            controller=self.controller,
        )
        self.server_thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.tasks_patch.stop()
        self.temp_dir.cleanup()

    def _request_json(self, path, payload=None):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"} if payload is not None else {},
            method="POST" if payload is not None else "GET",
        )
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    def test_dashboard_serves_page_and_manual_start_prompts_by_topic(self):
        with urlopen(self.base_url + "/", timeout=3) as response:
            html = response.read().decode("utf-8")
        self.assertIn("Start a new research task", html)
        self.assertIn("What topic should the agents research?", html)
        self.assertIn("Stop worker", html)

        status, result = self._request_json(
            "/api/start",
            {"topic": "Unusual automotive stories"},
        )

        self.assertEqual(status, 202)
        self.assertEqual(
            self.queue.get_task(result["task_id"])["topic"],
            "Unusual automotive stories",
        )
        self.assertEqual(self.controller.start_calls, 1)

    def test_dashboard_stop_sets_persistent_worker_stop_request(self):
        status, _ = self._request_json("/api/worker/stop", {})

        self.assertEqual(status, 200)
        self.assertTrue(self.queue.is_stop_requested())
        self.assertEqual(self.controller.stop_calls, 1)

    def test_dashboard_refuses_network_bind(self):
        with self.assertRaises(ValueError):
            dashboard.create_server(host="0.0.0.0", port=0, queue=self.queue)

    def test_dashboard_approval_validates_and_queues_selection(self):
        task_id = self.queue.enqueue("Automotive stories")
        self.queue.claim_next()
        task_dir = Path(dashboard.TASKS_DIR) / task_id
        task_dir.mkdir(parents=True)
        report_path = task_dir / "ideas.json"
        report_path.write_text(
            json.dumps({"ideas": [{"rank": 1}, {"rank": 2}]}),
            encoding="utf-8",
        )
        self.queue.mark_awaiting_approval(
            task_id,
            task_dir / "research.json",
            report_path,
        )

        with self.assertRaises(HTTPError) as error:
            self._request_json(
                f"/api/tasks/{task_id}/approve",
                {"stage": "idea_selection", "decision": "approve", "ranks": [9]},
            )
        self.assertEqual(error.exception.code, 400)
        self.assertIsNone(self.queue.claim_worker_command())

        status, result = self._request_json(
            f"/api/tasks/{task_id}/approve",
            {"stage": "idea_selection", "decision": "approve", "ranks": [1, 2]},
        )
        self.assertEqual(status, 202)
        command = self.queue.claim_worker_command()
        self.assertEqual(command["id"], result["command_id"])
        self.assertEqual(command["payload"]["ranks"], [1, 2])


class GeminiBrowserTests(unittest.TestCase):
    class FakeLink:
        def __init__(self, href, text="", title=""):
            self.attributes = {"href": href, "title": title}
            self.text = text

        def get_attribute(self, name):
            return self.attributes.get(name)

        def inner_text(self):
            return self.text

    class FakeLinks:
        def __init__(self, links):
            self.links = links

        def count(self):
            return len(self.links)

        def nth(self, index):
            return self.links[index]

    class FakeResponse:
        def __init__(self, links):
            self.links = GeminiBrowserTests.FakeLinks(links)

        def locator(self, selector):
            if selector != "a[href]":
                raise AssertionError(f"Unexpected locator: {selector}")
            return self.links

    def test_extracts_unique_http_source_links_from_gemini_response(self):
        response = self.FakeResponse(
            [
                self.FakeLink("https://example.test/story", "Story title"),
                self.FakeLink("https://example.test/story", "Duplicate title"),
                self.FakeLink("/relative/path", "Relative link"),
                self.FakeLink("javascript:alert(1)", "Invalid scheme"),
                self.FakeLink("https://example.test/second", title="Second source"),
            ]
        )

        self.assertEqual(
            extract_citations(response),
            [
                {
                    "title": "Story title",
                    "url": "https://example.test/story",
                },
                {
                    "title": "Second source",
                    "url": "https://example.test/second",
                },
            ],
        )

    def test_gemini_request_uses_saved_profile_and_closes_browser(self):
        class FakeInput:
            def __init__(self):
                self.prompt = None
                self.submitted = False

            def wait_for(self, **kwargs):
                self.wait_options = kwargs

            def fill(self, prompt):
                self.prompt = prompt

            def press(self, key):
                self.submitted = key == "Enter"

        class FakePage:
            def __init__(self):
                self.input = FakeInput()
                self.url = None

            def goto(self, url, **kwargs):
                self.url = url
                self.navigation_options = kwargs

            def locator(self, selector):
                self.input_selector = selector
                return self.input

        class FakeResponses:
            last = object()

            def count(self):
                return 1

        class FakeContext:
            def __init__(self):
                self.page = FakePage()
                self.pages = [self.page]
                self.closed = False

            def close(self):
                self.closed = True

        class FakeChromium:
            def __init__(self, context):
                self.context = context
                self.launch_options = None

            def launch_persistent_context(self, **kwargs):
                self.launch_options = kwargs
                return self.context

        class FakePlaywright:
            def __init__(self, chromium):
                self.chromium = chromium

        class FakePlaywrightManager:
            def __init__(self, playwright):
                self.playwright = playwright

            def __enter__(self):
                return self.playwright

            def __exit__(self, *_):
                return False

        with tempfile.TemporaryDirectory() as temp_dir:
            profile_dir = Path(temp_dir) / "browser_data" / "gemini"
            profile_dir.mkdir(parents=True)
            context = FakeContext()
            chromium = FakeChromium(context)
            playwright = FakePlaywright(chromium)
            result_citations = [
                {"title": "Source", "url": "https://example.test/source"}
            ]
            with (
                patch.object(browser_ai, "BASE_DIR", Path(temp_dir)),
                patch(
                    "providers.browser_ai.sync_playwright",
                    return_value=FakePlaywrightManager(playwright),
                ),
                patch(
                    "providers.browser_ai.get_response_locator",
                    return_value=FakeResponses(),
                ),
                patch(
                    "providers.browser_ai.wait_for_response",
                    return_value="Gemini research response",
                ),
                patch(
                    "providers.browser_ai.extract_citations",
                    return_value=result_citations,
                ) as extract_mock,
            ):
                result = ask_ai_with_sources(
                    "gemini",
                    "Search the web for a topic",
                    timeout=45,
                )

        self.assertEqual(
            result,
            BrowserAIResponse("Gemini research response", result_citations),
        )
        self.assertEqual(chromium.launch_options["user_data_dir"], str(profile_dir))
        self.assertFalse(chromium.launch_options["headless"])
        self.assertEqual(context.page.url, browser_ai.PROVIDERS["gemini"]["url"])
        self.assertEqual(context.page.input.prompt, "Search the web for a topic")
        self.assertTrue(context.page.input.submitted)
        self.assertTrue(context.closed)
        extract_mock.assert_called_once_with(FakeResponses.last)


class AgentInstructionTests(unittest.TestCase):
    def test_research_prompt_loads_role_and_skill_markdown(self):
        with tempfile.TemporaryDirectory() as output_dir:
            researcher = ContentResearcher(output_dir=output_dir)
            prompt = researcher.build_analysis_prompt(
                "Unusual cars",
                {"results": []},
            )

        self.assertIn("Content Researcher", prompt)
        self.assertIn("Web Research Skill", prompt)
        self.assertIn("Editorial Idea Analysis Skill", prompt)

    def test_scriptwriter_loads_instructions_and_saves_generated_scripts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            research_path = root / "research.json"
            research_path.write_text(
                json.dumps({
                    "topic": "Unusual cars",
                    "research": {
                        "results": [
                            {
                                "title": "Source title",
                                "url": "https://example.test/source",
                                "snippet": "A research lead",
                            }
                        ]
                    },
                }),
                encoding="utf-8",
            )
            idea = {
                "rank": 1,
                "title": "Title: A sourced automotive story",
                "central_story": "Malformed value containing several fields",
                "raw_texts": [
                    "Central Story: A broad forgotten-car theme."
                    "Opening Hook: What happened to this car?"
                    "Target Audience: Automotive fans."
                ],
            }
            writer = Scriptwriter(output_dir=root / "task")

            with patch(
                "agents.scriptwriter.ask_ai",
                return_value="Opening hook\n\nScene-by-scene script",
            ) as ask_ai_mock:
                result = writer.run([idea], research_path)

            self.assertEqual(result["script_count"], 1)
            self.assertEqual(result["scripts"][0]["idea_rank"], 1)
            self.assertTrue((root / "task" / "scripts.json").is_file())
            self.assertTrue((root / "task" / "scripts" / "script_01.md").is_file())
            prompt = ask_ai_mock.call_args.kwargs["prompt"]
            self.assertIn("Short-Form Scriptwriter", prompt)
            self.assertIn("Reel Scriptwriting Skill", prompt)
            self.assertIn("A research lead", prompt)
            self.assertIn('"title": "A sourced automotive story"', prompt)
            self.assertIn('"central_story": "A broad forgotten-car theme."', prompt)
            self.assertIn('"opening_hook": "What happened to this car?"', prompt)
            self.assertNotIn("raw_texts", prompt)

    def test_content_research_uses_gemini_browser_and_saves_sources(self):
        response = BrowserAIResponse(
            text=(
                "Idea 1: A sourced automotive story\n"
                "Title: A sourced automotive story\n"
                "Central Story: A historical car story.\n"
                "Opening Hook: What happened to this car?\n"
                "Supporting Sources: Source title"
            ),
            citations=[
                {"title": "Source title", "url": "https://example.test/story"}
            ],
        )
        agents = []
        with tempfile.TemporaryDirectory() as output_dir:
            researcher = ContentResearcher(output_dir=output_dir)
            with patch(
                "agents.content_researcher.ask_ai_with_sources",
                return_value=response,
            ) as browser_request:
                result = researcher.run(
                    "Unusual automotive stories",
                    on_agent=agents.append,
                )

            self.assertEqual(agents, ["Gemini Web Researcher"])
            self.assertEqual(result["successful_providers"], ["gemini"])
            self.assertEqual(
                result["research"]["results"],
                [
                    {
                        "title": "Source title",
                        "url": "https://example.test/story",
                        "snippet": "",
                    }
                ],
            )
            browser_request.assert_called_once()
            self.assertEqual(
                browser_request.call_args.kwargs["provider"],
                "gemini",
            )
            self.assertTrue(
                (Path(output_dir) / "latest_content_research.json").is_file()
            )

    def test_content_research_saves_and_surfaces_gemini_failure_details(self):
        with tempfile.TemporaryDirectory() as output_dir:
            researcher = ContentResearcher(output_dir=output_dir)
            with (
                patch(
                    "agents.content_researcher.ask_ai_with_sources",
                    side_effect=RuntimeError("API access denied"),
                ),
                self.assertRaisesRegex(
                    RuntimeError,
                    "Gemini browser research failed: API access denied",
                ),
            ):
                researcher.run("Unusual automotive stories")

            saved = json.loads(
                (Path(output_dir) / "latest_content_research.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                saved["provider_analyses"]["gemini"]["error"],
                "RuntimeError: API access denied",
            )

    def test_interactive_approval_accepts_comma_separated_ranks(self):
        task = {"id": "test-task", "status": "awaiting_approval", "topic": "Cars"}
        report = {
            "ideas": [
                {"rank": 1},
                {"rank": 2},
                {"rank": 3},
            ]
        }
        queue = object()

        with (
            patch("orchestration.cli._show_task"),
            patch("orchestration.cli._read_report", return_value=report),
            patch(
                "orchestration.cli._approve_task",
                return_value={"awaiting_approval": False},
            ) as approve,
            patch("builtins.input", return_value="1,3"),
        ):
            _interactive_approval(queue, task)

        approve.assert_called_once_with(queue, "test-task", [1, 3], rejected=False)


class WorkflowApprovalTests(unittest.TestCase):
    def test_workflow_pauses_then_resumes_with_selected_ideas(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            task_id = "test-task"
            task_dir = Path(temp_dir) / task_id
            database = Path(temp_dir) / "workflow.sqlite"

            def fake_research(self, topic, max_results=8, on_agent=None):
                result = {
                    "topic": topic,
                    "provider_analyses": {
                        "gemini": {
                            "status": "success",
                            "response": (
                                "1. Forgotten automotive history\n\n"
                                "Title: A Forgotten Car\n"
                                "Central Story: A car story with a source.\n"
                                "Opening Hook: What happened to this car?\n"
                                "Supporting Sources: Example source"
                            ),
                        }
                    },
                }
                (Path(self.output_dir) / "latest_content_research.json").write_text(
                    json.dumps(result),
                    encoding="utf-8",
                )
                return result

            with (
                patch.object(ContentResearcher, "run", fake_research),
                patch(
                    "agents.scriptwriter.ask_ai",
                    return_value="Draft script",
                ),
            ):
                with SqliteSaver.from_conn_string(str(database)) as checkpointer:
                    workflow = build_workflow(checkpointer)
                    initial_result = workflow.invoke(
                        {
                            "task_id": task_id,
                            "topic": "Unusual automotive stories",
                            "task_dir": str(task_dir),
                        },
                        config=task_config(task_id),
                    )

                    self.assertIn("__interrupt__", initial_result)

                    script_pause = resume_task_approval(
                        workflow,
                        task_id,
                        {"decision": "approve", "ranks": [1]},
                    )
                    self.assertIn("__interrupt__", script_pause)
                    self.assertEqual(
                        script_pause["__interrupt__"][0].value["stage"],
                        "script_review",
                    )

                    final_state = resume_task_approval(
                        workflow,
                        task_id,
                        {"decision": "approve", "ranks": [1]},
                    )

            self.assertEqual(final_state["script_approval_decision"], "approved")
            self.assertEqual(len(final_state["approved_ideas"]), 1)
            self.assertEqual(len(final_state["approved_scripts"]), 1)
            self.assertTrue(Path(final_state["approved_scripts_path"]).is_file())

    def test_existing_completed_idea_task_can_start_scriptwriting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_id = "previously-approved-task"
            queue = TaskQueue(root / "queue.sqlite")
            queue.enqueue("Unusual automotive stories")
            queue.claim_next()

            task_dir = root / "tasks" / task_id
            task_dir.mkdir(parents=True)
            research_path = task_dir / "latest_content_research.json"
            research_path.write_text(
                json.dumps({
                    "topic": "Unusual automotive stories",
                    "research": {"results": []},
                }),
                encoding="utf-8",
            )
            consolidated_path = task_dir / "latest_consolidated_ideas.json"
            consolidated_path.write_text("{}", encoding="utf-8")
            approved_path = task_dir / "approved_ideas.json"
            approved_path.write_text(
                json.dumps({
                    "topic": "Unusual automotive stories",
                    "approved_ideas": [
                        {"rank": 1, "title": "A previously approved idea"}
                    ],
                }),
                encoding="utf-8",
            )
            task = queue.list_tasks()[0]
            queue.mark_awaiting_approval(
                task["id"],
                research_path,
                consolidated_path,
            )
            queue.finish_approval(task["id"], approved_path)

            with (
                patch.object(runner, "WORKFLOW_DATABASE", root / "workflow.sqlite"),
                patch(
                    "agents.scriptwriter.ask_ai",
                    return_value="Draft script",
                ),
            ):
                pending = runner.start_scriptwriting_for_completed_task(
                    queue,
                    task["id"],
                )
                self.assertEqual(pending["approval_stage"], "script_review")
                runner.approve_task(queue, task["id"], ranks=[1])

            finished = queue.get_task(task["id"])
            self.assertEqual(finished["status"], "completed")
            self.assertTrue(Path(finished["approved_scripts_path"]).is_file())

    def test_queued_task_runs_to_approval_then_completes_after_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            queue = TaskQueue(root / "queue.sqlite")
            task_id = queue.enqueue("Unusual automotive stories")

            def fake_research(self, topic, max_results=8, on_agent=None):
                result = {
                    "topic": topic,
                    "provider_analyses": {
                        "gemini": {
                            "status": "success",
                            "response": (
                                "1. Forgotten automotive history\n\n"
                                "Title: A Forgotten Car\n"
                                "Central Story: A sourced historical story.\n"
                                "Opening Hook: What became of this forgotten car?\n"
                                "Supporting Sources: Example source"
                            ),
                        }
                    },
                }
                (Path(self.output_dir) / "latest_content_research.json").write_text(
                    json.dumps(result),
                    encoding="utf-8",
                )
                return result

            with (
                patch.object(ContentResearcher, "run", fake_research),
                patch(
                    "agents.scriptwriter.ask_ai",
                    return_value="Draft script",
                ),
                patch.object(runner, "TASKS_DIR", root / "tasks"),
                patch.object(runner, "WORKFLOW_DATABASE", root / "workflow.sqlite"),
            ):
                task = runner.process_next_task(queue)
                self.assertEqual(task["status"], "awaiting_approval")

                script_pause = runner.approve_task(queue, task_id, ranks=[1])
                self.assertTrue(script_pause["awaiting_approval"])
                self.assertEqual(
                    queue.get_task(task_id)["approval_stage"],
                    "script_review",
                )
                runner.approve_task(queue, task_id, ranks=[1])

            self.assertEqual(queue.get_task(task_id)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
