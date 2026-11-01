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

        page.locator('textarea[aria-label="Chat with ChatGPT"]').wait_for(
            state="visible", timeout=30000
        )

        print("\nChatGPT is ready.")

        input("\nPress ENTER to send the diagnostic prompt...")

        prompt = "Reply with exactly this text and nothing else: PROVIDER_TEST_OK"

        page.locator('textarea[aria-label="Chat with ChatGPT"]').fill(prompt)
        page.locator('textarea[aria-label="Chat with ChatGPT"]').press("Enter")

        print("\nWaiting for the response...")
        time.sleep(15)

        matches = page.get_by_text("PROVIDER_TEST_OK", exact=False)

        print("\nMatching elements:", matches.count())

        for i in range(matches.count()):
            element = matches.nth(i)

            print(f"\n--- MATCH {i + 1} ---")
            print("TAG:", element.evaluate("(el) => el.tagName"))
            print("CLASS:", element.get_attribute("class"))
            print("TEXT:", element.inner_text()[:1000])

            print("\nHTML:")
            print(
                element.evaluate("(el) => el.outerHTML")[:3000]
            )

        print("\n--- PAGE INFORMATION ---")
        print("TITLE:", page.title())
        print("URL:", page.url)

        input("\nPress ENTER to close...")

    finally:
        context.close()
