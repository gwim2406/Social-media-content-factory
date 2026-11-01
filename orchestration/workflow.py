import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.content_consolidator import ContentConsolidator
from agents.content_researcher import ContentResearcher
from agents.scriptwriter import Scriptwriter

ActivityCallback = Callable[[str, str, str | None], None]


class ContentTaskState(TypedDict, total=False):
    task_id: str
    topic: str
    task_dir: str
    research_path: str
    consolidated_path: str
    approved_path: str
    scripts_path: str
    approved_scripts_path: str
    report: dict[str, Any]
    scripts: list[dict[str, Any]]
    approval_decision: str
    approved_ideas: list[dict[str, Any]]
    approved_scripts: list[dict[str, Any]]
    idea_approval_decision: str
    script_approval_decision: str
    script_only: bool


def research_node(
    state: ContentTaskState,
    on_stage: ActivityCallback | None = None,
) -> dict[str, str]:
    if on_stage:
        on_stage(state["task_id"], "research", "Gemini Web Researcher")
    task_dir = Path(state["task_dir"])
    ContentResearcher(output_dir=task_dir).run(
        state["topic"],
        on_agent=(
            lambda agent: on_stage(state["task_id"], "research", agent)
            if on_stage
            else None
        ),
    )
    return {"research_path": str(task_dir / "latest_content_research.json")}


def consolidate_node(
    state: ContentTaskState,
    on_stage: ActivityCallback | None = None,
) -> dict[str, Any]:
    if on_stage:
        on_stage(state["task_id"], "idea_consolidation", "Idea Consolidator")
    task_dir = Path(state["task_dir"])
    report = ContentConsolidator(
        research_file=state["research_path"],
        output_dir=task_dir,
    ).consolidate()
    if not report["ideas"]:
        raise RuntimeError(
            "Research completed, but no structured ideas could be extracted. "
            "Inspect the provider responses before retrying."
        )
    return {
        "consolidated_path": str(task_dir / "latest_consolidated_ideas.json"),
        "report": report,
    }


def approval_node(
    state: ContentTaskState,
    on_stage: ActivityCallback | None = None,
) -> dict[str, Any]:
    if on_stage:
        on_stage(state["task_id"], "idea_selection", None)
    report = state["report"]
    decision = interrupt(
        {
            "stage": "idea_selection",
            "task_id": state["task_id"],
            "topic": report["topic"],
            "ideas": [
                {
                    "rank": idea["rank"],
                    "title": idea["title"],
                    "score": idea["score"],
                    "providers": idea.get("provider_mentions", []),
                }
                for idea in report["ideas"]
            ],
            "question": "Approve selected idea ranks, or reject all ideas.",
            "options": ["approve", "reject"],
        }
    )

    if not isinstance(decision, dict):
        raise ValueError("Approval response must include a decision and idea ranks.")

    decision_name = decision.get("decision")
    if decision_name == "reject":
        approved_ideas: list[dict[str, Any]] = []
        final_decision = "rejected"
    elif decision_name == "approve":
        ranks = decision.get("ranks")
        if (
            not isinstance(ranks, list)
            or not ranks
            or any(not isinstance(rank, int) for rank in ranks)
        ):
            raise ValueError("Approval must include one or more integer idea ranks.")
        if len(set(ranks)) != len(ranks):
            raise ValueError("Idea ranks must not be repeated.")
        ideas_by_rank = {idea["rank"]: idea for idea in report["ideas"]}
        invalid_ranks = sorted(set(ranks) - ideas_by_rank.keys())
        if invalid_ranks:
            raise ValueError(f"Unknown idea rank(s): {invalid_ranks}")
        approved_ideas = [ideas_by_rank[rank] for rank in ranks]
        final_decision = "approved"
    else:
        raise ValueError("Decision must be 'approve' or 'reject'.")

    approved_path = Path(state["task_dir"]) / "approved_ideas.json"
    approved_path.write_text(
        json.dumps(
            {
                "topic": report["topic"],
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "decision": final_decision,
                "approved_ideas": approved_ideas,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "idea_approval_decision": final_decision,
        "approved_ideas": approved_ideas,
        "approved_path": str(approved_path),
    }


def route_from_start(state: ContentTaskState) -> str:
    return "scriptwriting" if state.get("script_only") else "research"


def route_after_idea_approval(state: ContentTaskState) -> str:
    return (
        "scriptwriting"
        if state.get("idea_approval_decision") == "approved"
        else "end"
    )


def scriptwriting_node(
    state: ContentTaskState,
    on_stage: ActivityCallback | None = None,
) -> dict[str, Any]:
    if on_stage:
        on_stage(state["task_id"], "scriptwriting", "Scriptwriter (Gemini Browser)")
    if not state.get("approved_ideas"):
        raise ValueError("Scriptwriting requires approved ideas.")

    scriptwriter = Scriptwriter(output_dir=state["task_dir"])
    result = scriptwriter.run(
        approved_ideas=state["approved_ideas"],
        research_file=state["research_path"],
    )
    return {
        "scripts": result["scripts"],
        "scripts_path": str(Path(state["task_dir"]) / "scripts.json"),
    }


def script_approval_node(
    state: ContentTaskState,
    on_stage: ActivityCallback | None = None,
) -> dict[str, Any]:
    if on_stage:
        on_stage(state["task_id"], "script_review", None)
    scripts = state.get("scripts", [])
    decision = interrupt(
        {
            "stage": "script_review",
            "task_id": state["task_id"],
            "topic": state["topic"],
            "scripts": [
                {
                    "rank": script["rank"],
                    "title": script["title"],
                    "idea_rank": script.get("idea_rank"),
                }
                for script in scripts
            ],
            "question": "Approve selected script ranks, or reject all scripts.",
            "options": ["approve", "reject"],
        }
    )

    if not isinstance(decision, dict):
        raise ValueError("Script approval response must include a decision and ranks.")

    decision_name = decision.get("decision")
    if decision_name == "reject":
        approved_scripts: list[dict[str, Any]] = []
        final_decision = "rejected"
    elif decision_name == "approve":
        ranks = decision.get("ranks")
        if (
            not isinstance(ranks, list)
            or not ranks
            or any(not isinstance(rank, int) for rank in ranks)
        ):
            raise ValueError("Script approval must include one or more integer ranks.")
        if len(set(ranks)) != len(ranks):
            raise ValueError("Script ranks must not be repeated.")
        scripts_by_rank = {script["rank"]: script for script in scripts}
        invalid_ranks = sorted(set(ranks) - scripts_by_rank.keys())
        if invalid_ranks:
            raise ValueError(f"Unknown script rank(s): {invalid_ranks}")
        approved_scripts = [scripts_by_rank[rank] for rank in ranks]
        final_decision = "approved"
    else:
        raise ValueError("Decision must be 'approve' or 'reject'.")

    approved_scripts_path = Path(state["task_dir"]) / "approved_scripts.json"
    approved_scripts_path.write_text(
        json.dumps(
            {
                "topic": state["topic"],
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "decision": final_decision,
                "approved_scripts": approved_scripts,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return {
        "script_approval_decision": final_decision,
        "approved_scripts": approved_scripts,
        "approved_scripts_path": str(approved_scripts_path),
    }


def build_workflow(
    checkpointer: SqliteSaver,
    on_stage: ActivityCallback | None = None,
):
    builder = StateGraph(ContentTaskState)
    builder.add_node("research", lambda state: research_node(state, on_stage))
    builder.add_node("consolidate", lambda state: consolidate_node(state, on_stage))
    builder.add_node("idea_approval", lambda state: approval_node(state, on_stage))
    builder.add_node("scriptwriting", lambda state: scriptwriting_node(state, on_stage))
    builder.add_node("script_approval", lambda state: script_approval_node(state, on_stage))
    builder.add_conditional_edges(
        START,
        route_from_start,
        {"research": "research", "scriptwriting": "scriptwriting"},
    )
    builder.add_edge("research", "consolidate")
    builder.add_edge("consolidate", "idea_approval")
    builder.add_conditional_edges(
        "idea_approval",
        route_after_idea_approval,
        {"scriptwriting": "scriptwriting", "end": END},
    )
    builder.add_edge("scriptwriting", "script_approval")
    builder.add_edge("script_approval", END)
    return builder.compile(checkpointer=checkpointer)


def task_config(task_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": task_id}}


def resume_task_approval(
    workflow,
    task_id: str,
    decision: dict[str, Any],
    workflow_thread_id: str | None = None,
) -> dict[str, Any]:
    config = task_config(workflow_thread_id or task_id)
    return workflow.invoke(Command(resume=decision), config=config)
