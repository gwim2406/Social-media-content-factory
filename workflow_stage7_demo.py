import sys
from datetime import datetime, timezone
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agents.content_consolidator import ContentConsolidator


class Stage7State(TypedDict):
    topic: str
    research_path: str
    consolidated_ideas: list
    approved_ideas: list
    decision: str


def load_research(state: Stage7State):
    consolidator = ContentConsolidator(state.get("research_path"))
    report = consolidator.consolidate()

    return {
        "topic": report["topic"],
        "consolidated_ideas": report["ideas"],
    }


def request_approval(state: Stage7State):
    idea_summaries = []
    for idea in state.get("consolidated_ideas", []):
        idea_summaries.append({
            "rank": idea.get("rank"),
            "title": idea.get("title"),
            "score": idea.get("score"),
            "providers": idea.get("provider_mentions", []),
        })

    decision = interrupt({
        "stage": "research_consolidation_approval",
        "topic": state.get("topic"),
        "ideas": idea_summaries,
        "question": "Select the ideas to approve or reject.",
        "options": ["approve_selected", "request_changes", "reject"],
    })

    normalized = str(decision).strip().lower()
    if normalized in {"approve", "approve_selected"}:
        return {"decision": "approved", "approved_ideas": state.get("consolidated_ideas", [])}
    if normalized in {"reject", "rejected"}:
        return {"decision": "rejected", "approved_ideas": []}
    return {"decision": "changes_requested", "approved_ideas": []}


def finalize(state: Stage7State):
    return {"decision": state.get("decision", "approved")}


def build_workflow(checkpointer):
    builder = StateGraph(Stage7State)

    builder.add_node("research", load_research)
    builder.add_node("approval", request_approval)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "research")
    builder.add_edge("research", "approval")
    builder.add_edge("approval", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


def main():
    thread_id = f"stage7-demo-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    config = {"configurable": {"thread_id": thread_id}}

    with SqliteSaver.from_conn_string("workflow.sqlite") as checkpointer:
        workflow = build_workflow(checkpointer)

        if len(sys.argv) > 1 and sys.argv[1] == "resume":
            decision = input("Enter approval decision (approve/reject/request_changes): ").strip().lower()
            result = workflow.invoke(Command(resume=decision), config=config)
            print("Final state:", result)
            return

        result = workflow.invoke({
            "topic": "Unusual automotive stories suitable for Facebook Reels",
            "research_path": "workspace/research/latest_content_research.json",
            "consolidated_ideas": [],
            "approved_ideas": [],
            "decision": "",
        }, config=config)

        if "__interrupt__" in result:
            print("\n[STAGE 7 APPROVAL REQUIRED]")
            print(result["__interrupt__"][0].value)
        else:
            print("\nWorkflow completed.")
            print(result)


if __name__ == "__main__":
    main()
