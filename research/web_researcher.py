
import json
import sys
from pathlib import Path
from urllib.parse import quote_plus
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "workspace" / "research"


class WebResearcher:

    def __init__(self, output_dir=None):
        self.output_dir = Path(output_dir) if output_dir else OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def search(self, query, max_results=8):

        results = []

        with sync_playwright() as p:

            browser = p.chromium.launch(headless=False)

            page = browser.new_page(
                viewport={"width": 1280, "height": 800}
            )

            try:
                url = (
                    "https://html.duckduckgo.com/html/?q="
                    + quote_plus(query)
                )

                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=30000
                )

                page.wait_for_timeout(2000)

                result_links = page.locator("a.result__a")

                count = min(result_links.count(), max_results)

                for i in range(count):

                    link = result_links.nth(i)

                    title = link.inner_text().strip()
                    href = link.get_attribute("href")

                    snippet = link.evaluate("""
                        element => {
                            const result = element.closest('.result');
                            const description = result?.querySelector(
                                '.result__snippet'
                            );
                            return description?.innerText || '';
                        }
                    """)

                    if title and href:
                        results.append({
                            "title": title,
                            "url": href,
                            "snippet": snippet.strip()
                        })

            finally:
                browser.close()

        output = {
            "query": query,
            "collected_at": datetime.now(
                timezone.utc
            ).isoformat(),
            "results": results
        }

        filename = self.output_dir / "latest_research.json"

        filename.write_text(
            json.dumps(output, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        return output


if __name__ == "__main__":

    query = " ".join(sys.argv[1:])

    if not query:
        query = "viral storytelling ideas for Facebook Reels"

    researcher = WebResearcher()

    data = researcher.search(query)

    print("\nResearch completed.")
    print("Query:", data["query"])
    print("Results collected:", len(data["results"]))

    for index, item in enumerate(data["results"], 1):
        print(f"\n{index}. {item['title']}")
        print("URL:", item["url"])
        print("Snippet:", item["snippet"][:250])
