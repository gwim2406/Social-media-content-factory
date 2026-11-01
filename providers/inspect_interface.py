
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent

PROVIDERS = {
    "gemini": "https://gemini.google.com/",
    "chatgpt": "https://chatgpt.com/"
}


def inspect(provider):

    profile = BASE_DIR / "browser_data" / provider

    with sync_playwright() as p:

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            viewport={"width": 1280, "height": 800}
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(
                PROVIDERS[provider],
                wait_until="domcontentloaded"
            )

            page.wait_for_timeout(5000)

            elements = page.locator(
                "textarea, [contenteditable='true'], input"
            )

            print(f"\n--- {provider.upper()} INTERFACE ---")

            for i in range(elements.count()):

                element = elements.nth(i)

                if not element.is_visible():
                    continue

                details = element.evaluate("""
                    el => ({
                        tag: el.tagName,
                        placeholder: el.getAttribute('placeholder'),
                        aria_label: el.getAttribute('aria-label'),
                        role: el.getAttribute('role'),
                        contenteditable: el.getAttribute('contenteditable')
                    })
                """)

                print(f"\nElement {i}:")
                print(details)

            input("\nPress ENTER to close...")

        finally:
            context.close()


if __name__ == "__main__":

    for provider in ["gemini", "chatgpt"]:

        input(f"\nPress ENTER to inspect {provider}...")

        inspect(provider)
