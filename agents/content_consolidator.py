import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
RESEARCH_DIR = BASE_DIR / "workspace" / "research"
DEFAULT_RESEARCH_FILE = RESEARCH_DIR / "latest_content_research.json"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_key(value: Any) -> str:
    text = normalize_text(value).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class ContentConsolidator:
    def __init__(
        self,
        research_file: str | Path | None = None,
        output_dir: str | Path | None = None,
    ):
        self.research_file = Path(research_file) if research_file else DEFAULT_RESEARCH_FILE
        self.output_dir = Path(output_dir) if output_dir else RESEARCH_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def load_research(self) -> dict[str, Any]:
        if not self.research_file.exists():
            raise FileNotFoundError(
                f"Research file not found: {self.research_file}. "
                "Run the content researcher first."
            )

        with self.research_file.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _parse_field(self, text: str, field_names: list[str]) -> str:
        for field_name in field_names:
            patterns = [
                rf"{field_name}\s*:\s*(.*?)(?=\n\s*(?:[A-Z][A-Za-z0-9 /&\-]+\s*:)|\Z)",
                rf"{field_name}\s*:\s*(.*)",
            ]
            for pattern in patterns:
                match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
                if match:
                    value = match.group(1).strip()
                    if value:
                        return value

        return ""

    def _extract_idea_blocks(self, provider_name: str, response_text: str) -> list[dict[str, Any]]:
        cleaned = normalize_text(response_text)
        if not cleaned:
            return []

        idea_blocks: list[str] = []

        idea_pattern = r"(?is)(?:^|\n)\s*Idea\s+\d+\s*:\s*(.*?)(?=(?:\n\s*Idea\s+\d+\s*:)|\Z)"
        for match in re.finditer(idea_pattern, cleaned):
            block = match.group(0).strip()
            if block:
                idea_blocks.append(block)

        if not idea_blocks:
            numbered_blocks = re.findall(
                r"(?is)(?:^|\n)\s*\d+\.\s*(.*?)(?=(?:\n\s*\d+\.\s+)|\Z)",
                cleaned,
            )
            for block in numbered_blocks:
                candidate = block.strip()
                if candidate:
                    idea_blocks.append(candidate)

        if not idea_blocks:
            title_matches = list(
                re.finditer(r"(?im)^[ \t]*Title\s*:", cleaned)
            )
            for index, match in enumerate(title_matches):
                end = (
                    title_matches[index + 1].start()
                    if index + 1 < len(title_matches)
                    else len(cleaned)
                )
                block = cleaned[match.start():end]
                block = re.split(r"(?im)^[ \t]*Sources[ \t]*$", block, maxsplit=1)[0]
                if block.strip():
                    idea_blocks.append(block.strip())

        if not idea_blocks:
            idea_blocks = [cleaned]

        meaningful_ideas: list[str] = []
        for block in idea_blocks:
            lowered = block.lower()
            field_markers = [
                "title:",
                "central story:",
                "opening hook:",
                "target audience:",
                "emotional appeal:",
                "why people might watch:",
                "suggested video format:",
                "supporting sources:",
                "potential problems or fact checks:",
                "potential problems:",
                "fact checks:",
            ]
            marker_count = sum(1 for marker in field_markers if marker in lowered)
            if marker_count >= 2:
                meaningful_ideas.append(block)
                continue

            first_line = block.splitlines()[0].strip().lower()
            if first_line in {"gemini said", "chatgpt said"}:
                continue
            if any(token in first_line for token in ["opening hook", "target audience", "emotional appeal", "central story", "research assessment", "content opportunity analysis", "final recommendation"]):
                continue
            if len(block) > 160 and any(token in lowered for token in ["story", "hook", "audience", "video", "watch"]):
                meaningful_ideas.append(block)

        if not meaningful_ideas:
            return []

        ideas: list[dict[str, Any]] = []

        for block in meaningful_ideas:
            block = block.strip()
            if not block:
                continue

            title = block.splitlines()[0].strip()
            if title.lower().startswith("idea "):
                title = re.sub(r"(?is)^\s*idea\s+\d+\s*:\s*", "", title).strip()
            else:
                title = re.sub(r"(?i)^\s*Title\s*:\s*", "", title).strip()

            generic_title_tokens = {
                "gemini said",
                "chatgpt said",
                "central story",
                "opening hook",
                "target audience",
                "emotional appeal",
                "why people might watch",
                "suggested video format",
                "supporting sources",
                "potential problems or fact checks",
                "research assessment",
                "content opportunity analysis",
                "comparative content opportunity assessment",
                "recommended content direction",
                "important research gaps",
                "final recommendation",
            }
            if not title or normalize_key(title) in generic_title_tokens:
                continue

            body = "\n".join(block.splitlines()[1:]).strip()
            if not body:
                body = block

            idea = {
                "provider": provider_name,
                "title": title,
                "central_story": self._parse_field(
                    body,
                    ["central story", "central_story"],
                ),
                "opening_hook": self._parse_field(
                    body,
                    ["opening hook", "opening_hook"],
                ),
                "target_audience": self._parse_field(
                    body,
                    ["target audience", "target_audience"],
                ),
                "emotional_appeal": self._parse_field(
                    body,
                    ["emotional appeal", "emotional_appeal"],
                ),
                "why_people_might_watch": self._parse_field(
                    body,
                    ["why people might watch", "why_people_might_watch"],
                ),
                "suggested_video_format": self._parse_field(
                    body,
                    ["suggested video format", "suggested_video_format"],
                ),
                "supporting_sources": self._parse_field(
                    body,
                    ["supporting sources", "supporting_sources"],
                ),
                "potential_problems_or_fact_checks": self._parse_field(
                    body,
                    [
                        "potential problems or fact checks",
                        "potential_problems_or_fact_checks",
                        "potential problems",
                        "fact checks",
                    ],
                ),
                "raw_text": body,
            }

            title_match = re.search(
                r"(?is)title\s*:\s*(.+?)(?=\n\s*(?:central story|opening hook|target audience|emotional appeal|why people might watch|suggested video format|supporting sources|potential problems|fact checks)|\Z)",
                body,
            )
            if title_match:
                idea["title"] = title_match.group(1).strip().strip("- ")

            if idea["title"] and normalize_key(idea["title"]) not in generic_title_tokens:
                ideas.append(idea)

        return ideas

    def _merge_ideas(self, ideas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}

        for idea in ideas:
            title = normalize_text(idea.get("title"))
            if not title:
                continue

            key = normalize_key(title)
            if not key:
                continue

            bucket = merged.setdefault(
                key,
                {
                    "title": title,
                    "providers": [],
                    "supporting_sources": [],
                    "scores": [],
                    "notes": [],
                },
            )

            provider_name = normalize_text(idea.get("provider"))
            if provider_name and provider_name not in bucket["providers"]:
                bucket["providers"].append(provider_name)

            for field_name in [
                "central_story",
                "opening_hook",
                "target_audience",
                "emotional_appeal",
                "why_people_might_watch",
                "suggested_video_format",
                "potential_problems_or_fact_checks",
            ]:
                existing_value = normalize_text(bucket.get(field_name))
                incoming_value = normalize_text(idea.get(field_name))
                if incoming_value and not existing_value:
                    bucket[field_name] = incoming_value
                elif incoming_value and existing_value and normalize_key(existing_value) != normalize_key(incoming_value):
                    bucket["notes"].append(
                        f"Provider disagreement on {field_name.replace('_', ' ')}."
                    )

            support_value = normalize_text(idea.get("supporting_sources"))
            if support_value:
                for source in re.split(r"\n|;|,\s*", support_value):
                    cleaned = source.strip(" -•*")
                    if cleaned and cleaned not in bucket["supporting_sources"]:
                        bucket["supporting_sources"].append(cleaned)

            if idea.get("raw_text"):
                bucket.setdefault("raw_texts", []).append(idea["raw_text"])

        ranked: list[dict[str, Any]] = []
        for item in merged.values():
            item["provider_mentions"] = item.pop("providers")
            item["supporting_sources"] = item.pop("supporting_sources")
            score = self._score_idea(item)
            item["score"] = score
            item["evidence_status"] = "partial" if item.get("supporting_sources") else "missing"
            ranked.append(item)

        return sorted(ranked, key=lambda entry: entry["score"], reverse=True)

    def _score_idea(self, idea: dict[str, Any]) -> int:
        score = 20

        if idea.get("title"):
            score += 10
        if idea.get("opening_hook"):
            score += 12
        if idea.get("central_story"):
            score += 12
        if idea.get("target_audience"):
            score += 8
        if idea.get("emotional_appeal"):
            score += 8
        if idea.get("why_people_might_watch"):
            score += 8
        if idea.get("suggested_video_format"):
            score += 10
        if idea.get("supporting_sources"):
            score += 8
        if len(idea.get("provider_mentions", [])) > 1:
            score += 12
        if idea.get("potential_problems_or_fact_checks"):
            score += 5

        title = normalize_key(idea.get("title", ""))
        curiosity_terms = {"weird", "strange", "mystery", "forgotten", "story", "history", "shocking", "unbelievable"}
        if title:
            title_tokens = set(title.split())
            if curiosity_terms.intersection(title_tokens):
                score += 8

        return min(score, 100)

    def consolidate(self) -> dict[str, Any]:
        research_data = self.load_research()
        provider_analyses = research_data.get("provider_analyses", {})
        all_ideas: list[dict[str, Any]] = []

        for provider_name, result in provider_analyses.items():
            response_text = normalize_text(result.get("response")) if isinstance(result, dict) else ""
            if not response_text:
                continue
            all_ideas.extend(self._extract_idea_blocks(provider_name, response_text))

        merged_ideas = self._merge_ideas(all_ideas)

        for index, idea in enumerate(merged_ideas, start=1):
            idea["rank"] = index
            idea["provider_mentions"] = list(idea.get("provider_mentions", []))
            idea["supporting_sources"] = list(idea.get("supporting_sources", []))
            idea["notes"] = list(idea.get("notes", []))

        missing_evidence = []
        if not merged_ideas:
            missing_evidence.append("No structured ideas could be extracted from the provider responses.")
        else:
            for idea in merged_ideas:
                if idea.get("evidence_status") == "missing":
                    missing_evidence.append(f"{idea['title']}: missing supporting evidence or source verification.")

        report = {
            "topic": research_data.get("topic") or "Unknown topic",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "research_file": str(self.research_file),
            "source_providers": sorted(
                provider_name for provider_name in provider_analyses if provider_analyses[provider_name].get("status") == "success"
            ),
            "idea_count": len(merged_ideas),
            "ideas": merged_ideas,
            "missing_evidence": missing_evidence,
            "disagreements": [
                note for idea in merged_ideas for note in idea.get("notes", [])
            ],
        }

        output_path = self.output_dir / "latest_consolidated_ideas.json"
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report

    def prompt_for_approval(self, report: dict[str, Any]) -> dict[str, Any]:
        print("\n===================================")
        print("RESEARCH CONSOLIDATION REPORT")
        print("===================================")
        print(f"Topic: {report['topic']}")
        print(f"Ideas ranked: {report['idea_count']}")

        for idea in report["ideas"]:
            providers = ", ".join(idea.get("provider_mentions", [])) or "unknown"
            print(f"\n{idea['rank']}. {idea['title']}")
            print(f"   Score: {idea['score']}/100 | Providers: {providers}")
            print(f"   Hook: {normalize_text(idea.get('opening_hook')) or 'Not provided'}")
            print(f"   Sources: {', '.join(idea.get('supporting_sources', [])) if idea.get('supporting_sources') else 'No direct source list'}")

        raw_selection = input(
            "\nApprove idea numbers (comma separated), 'all', or 'none' [all]: "
        ).strip()

        choices = list(range(1, report["idea_count"] + 1)) if not raw_selection or raw_selection.lower() == "all" else []
        if raw_selection and raw_selection.lower() not in {"all", "none"}:
            values = [part.strip() for part in raw_selection.split(",") if part.strip()]
            choices = []
            for value in values:
                try:
                    index = int(value)
                except ValueError:
                    continue
                if 1 <= index <= report["idea_count"]:
                    choices.append(index)

        approved = []
        if raw_selection and raw_selection.lower() == "none":
            choices = []

        if choices:
            for index in sorted(set(choices)):
                for idea in report["ideas"]:
                    if idea["rank"] == index:
                        approved.append(idea)
                        break

        approved_path = self.output_dir / "approved_ideas.json"
        approved_path.write_text(
            json.dumps({
                "topic": report["topic"],
                "approved_at": datetime.now(timezone.utc).isoformat(),
                "approved_ideas": approved,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\nSaved approved ideas: {approved_path}")
        return {"approved_ideas": approved, "selection": choices}


def main() -> None:
    script = ContentConsolidator()

    try:
        report = script.consolidate()
    except FileNotFoundError as error:
        print(f"\nNo research has been run yet.\n{error}\n")
        print("Run: python -m agents.content_researcher")
        raise SystemExit(1)

    script.prompt_for_approval(report)


if __name__ == "__main__":
    main()
