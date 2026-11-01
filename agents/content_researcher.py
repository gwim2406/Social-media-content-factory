import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from providers.browser_ai import ask_ai_with_sources


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "workspace" / "research"
AGENT_SPEC_PATH = BASE_DIR / "agent_specs" / "content_researcher.md"
SKILL_PATHS = (
    BASE_DIR / "agent_skills" / "web_research" / "SKILL.md",
    BASE_DIR / "agent_skills" / "editorial_idea_analysis" / "SKILL.md",
)


class ContentResearcher:
    def __init__(self, output_dir: str | Path | None = None):
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load_agent_instructions(self) -> str:
        instruction_paths = (AGENT_SPEC_PATH, *SKILL_PATHS)
        missing_paths = [path for path in instruction_paths if not path.is_file()]
        if missing_paths:
            missing = ", ".join(str(path) for path in missing_paths)
            raise FileNotFoundError(
                f"Required agent instruction files are missing: {missing}"
            )

        return "\n\n".join(
            path.read_text(encoding="utf-8").strip()
            for path in instruction_paths
        )

    def build_analysis_prompt(
        self,
        topic: str,
        research_data: dict[str, Any] | None = None,
    ) -> str:
        supplied_results = (research_data or {}).get("results", [])
        research_context = (
            json.dumps(supplied_results, indent=2, ensure_ascii=False)
            if supplied_results
            else (
                "Use Gemini's web search to research the topic. Prefer credible, "
                "primary sources. Include source titles and URLs with the ideas."
            )
        )
        return f"""
Follow the agent role and task skills below. Treat them as operating instructions.

{self.load_agent_instructions()}

TARGET TOPIC:
{topic}

WEB RESEARCH:
{research_context}

Search the web before answering. Use the exact field labels defined by the
skills so the result can be reviewed and parsed consistently. Identify up to 5
promising content ideas. Include actual source titles and URLs, and do not
invent facts, quotations, source titles, or URLs. End with a Sources section
that lists each source title and its full URL.
"""

    def _save_output(self, output: dict[str, Any]) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = self.output_dir / f"content_research_{timestamp}.json"
        serialized = json.dumps(output, indent=2, ensure_ascii=False)
        filename.write_text(serialized, encoding="utf-8")
        (self.output_dir / "latest_content_research.json").write_text(
            serialized,
            encoding="utf-8",
        )
        return filename

    def run(
        self,
        topic: str,
        max_results: int = 8,
        on_agent=None,
    ) -> dict[str, Any]:
        print("\n===================================")
        print("CONTENT RESEARCHER STARTED")
        print("===================================")
        print("\n[1/1] Researching and analyzing with Gemini in Chromium...")

        if on_agent:
            on_agent("Gemini Web Researcher")

        research_data: dict[str, Any] = {
            "query": topic,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "search_queries": [],
            "results": [],
        }
        provider_results: dict[str, Any] = {}
        output = {
            "topic": topic,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "research": research_data,
            "provider_analyses": provider_results,
            "successful_providers": [],
        }

        try:
            response = ask_ai_with_sources(
                provider="gemini",
                prompt=self.build_analysis_prompt(topic),
                timeout=180,
            )
            citations = response.citations[:max_results]
            if not citations:
                raise RuntimeError(
                    "Gemini returned no source links in its response. Confirm "
                    "Gemini web search is available and retry."
                )

            research_data["results"] = [
                {
                    "title": citation["title"],
                    "url": citation["url"],
                    "snippet": citation.get("snippet", ""),
                }
                for citation in citations
            ]
            provider_results["gemini"] = {
                "status": "success",
                "response": response.text,
            }
            output["successful_providers"] = ["gemini"]
        except Exception as error:
            provider_results["gemini"] = {
                "status": "failed",
                "error": f"{type(error).__name__}: {error}",
            }
            saved_path = self._save_output(output)
            raise RuntimeError(
                f"Gemini browser research failed: {error}. Failure details were saved "
                f"to {saved_path}."
            ) from error

        print(f"Research completed: {len(research_data['results'])} sources")
        print("Gemini analysis completed.")
        saved_path = self._save_output(output)

        print("\n===================================")
        print("CONTENT RESEARCH COMPLETED")
        print("===================================")
        print("Topic:", topic)
        print("Successful providers:", output["successful_providers"])
        print("Saved output:", saved_path)

        return output


if __name__ == "__main__":
    topic = input("\nEnter a content research topic: ").strip()
    if not topic:
        print("Topic cannot be empty.")
        raise SystemExit(1)

    try:
        ContentResearcher().run(topic)
    except Exception as error:
        print("\nCONTENT RESEARCH FAILED")
        print(error)
