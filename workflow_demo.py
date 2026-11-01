
import sys
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver


class WorkflowState(TypedDict):
    topic: str
    draft: str
    approved: bool


def prepare_content(state: WorkflowState):
    print("[Agent] Preparing content...")

    draft = f"Sample social media story about: {state['topic']}"

    return {"draft": draft}


def request_approval(state: WorkflowState):
    decision = interrupt({
        "stage": "content_review",
        "draft": state["draft"],
        "question": "Do you approve this content?",
        "options": ["approve", "reject"]
    })

    return {"approved": decision == "approve"}


def approved_node(state: WorkflowState):
    print("[Workflow] Content approved.")
    return {}


def rejected_node(state: WorkflowState):
    print("[Workflow] Content rejected.")
    return {}


def build_workflow(checkpointer):

    builder = StateGraph(WorkflowState)

    builder.add_node("prepare", prepare_content)
    builder.add_node("approval", request_approval)
    builder.add_node("approved", approved_node)
    builder.add_node("rejected", rejected_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "approval")

    builder.add_conditional_edges(
        "approval",
        lambda state: "approved" if state["approved"] else "rejected",
        {
            "approved": "approved",
            "rejected": "rejected"
        }
    )

    builder.add_edge("approved", END)
    builder.add_edge("rejected", END)

    return builder.compile(checkpointer=checkpointer)


def main():

    thread_id = "social-media-demo-001"

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    with SqliteSaver.from_conn_string("workflow.sqlite") as checkpointer:

        workflow = build_workflow(checkpointer)

        if len(sys.argv) > 1 and sys.argv[1] == "resume":

            decision = input("Enter approve or reject: ").strip().lower()

            if decision not in ["approve", "reject"]:
                print("Invalid decision.")
                return

            result = workflow.invoke(
                Command(resume=decision),
                config=config
            )

            print("Final status:", 
                  "APPROVED" if result["approved"] else "REJECTED")

        else:

            result = workflow.invoke(
                {
                    "topic": "Why consistency matters",
                    "draft": "",
                    "approved": False
                },
                config=config
            )

            if "__interrupt__" in result:
                print("\n[APPROVAL REQUIRED]")
                print(result["__interrupt__"][0].value)


if __name__ == "__main__":
    main()
