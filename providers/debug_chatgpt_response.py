from pathlib import Path
from playwright.sync_api import sync_playwright
import time

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE = BASE_DIR / "browser_data" / "chatgpt"

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=False,
        accept_downloads=False,
        viewport={"width": 1280, "height": 800},
    )

    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://chatgpt.com/", wait_until="domcontentloaded")

        textbox = page.locator(
            'textarea[aria-label="Chat with ChatGPT"]'
        )
        textbox.wait_for(state="visible", timeout=30000)

        print("ChatGPT is ready.")

        prompt = (
            "Give me five unusual automotive story ideas for "
            "Facebook Reels. Include a short explanation for each. "
            "End your answer with CHATGPT_CONTAINER_TEST"
        )

        input("\nPress ENTER to send the diagnostic prompt...")

        textbox.fill(prompt)
        textbox.press("Enter")

        print("\nWaiting for ChatGPT response...")

        response_marker = page.get_by_text(
            "CHATGPT_CONTAINER_TEST",
            exact=True
        )

        response_marker.wait_for(timeout=120000)

        time.sleep(3)

        print("\n--- PAGE DETAILS ---")
        print("URL:", page.url)
        print("Title:", page.title())

        print("\n--- MATCHING PARAGRAPHS ---")

        matches = page.locator("p").filter(
            has_text="CHATGPT_CONTAINER_TEST"
        )

        print("Matches:", matches.count())

        for i in range(matches.count()):
            element = matches.nth(i)

            print(f"\nMATCH {i + 1}")
            print("TEXT:", element.inner_text())

            for level in range(1, 6):
                ancestor = element.locator(
                    "xpath=" + "/.." * level
                )

                print(f"\nANCESTOR LEVEL {level}")
                print(
                    "TAG:",
                    ancestor.evaluate("(el) => el.tagName")
                )
                print(
                    "ATTRIBUTES:",
                    ancestor.evaluate("""
                        el => Array.from(el.attributes).map(
                            a => [a.name, a.value]
                        )
                    """)
                )
                print("TEXT:", ancestor.inner_text()[:2000])

        input("\nPress ENTER to close the browser...")

    finally:
        context.close()
