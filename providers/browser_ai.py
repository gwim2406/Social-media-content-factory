


from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time


BASE_DIR = Path(__file__).resolve().parent.parent

PROVIDERS = {
    "gemini": {
        "url": "https://gemini.google.com/",
        "input": 'div[role="textbox"][aria-label="Enter a prompt for Gemini"]',
        "response_selectors": [
            "model-response",
            ".model-response-text",
            "[data-test-id='model-response']",
        ],
    },
    "chatgpt": {
    	"url": "https://chatgpt.com/",
    	"input": 'textarea[aria-label="Chat with ChatGPT"]',
    	"response_selectors": [
        	'li[data-message-role="assistant"] [data-assistant-markdown]',
    	],
    },
}


@dataclass(frozen=True)
class BrowserAIResponse:
    text: str
    citations: list[dict[str, str]]


def get_response_locator(page, provider):
    """Find the provider's assistant response elements."""
    for selector in PROVIDERS[provider]["response_selectors"]:
        locator = page.locator(selector)
        if locator.count() > 0:
            return locator

    return None


def extract_citations(response):
    """Return unique external links rendered inside an assistant response."""
    links = response.locator("a[href]")
    citations = []
    seen_urls = set()

    for index in range(min(links.count(), 30)):
        link = links.nth(index)
        url = link.get_attribute("href") or ""
        if urlsplit(url).scheme not in {"http", "https"} or url in seen_urls:
            continue

        seen_urls.add(url)
        title = link.inner_text().strip() or link.get_attribute("title") or url
        citations.append({"title": title, "url": url})

    return citations


def wait_for_response(page, provider, previous_count, timeout=180):

    start = time.time()
    last_text = ""
    stable_checks = 0

    while time.time() - start < timeout:

        locator = get_response_locator(page, provider)

        if locator and locator.count() > previous_count:

            response = locator.last

            current_text = response.inner_text().strip()

            # Check whether the assistant is still generating.
            busy_container = response.locator("xpath=..")
            is_busy = busy_container.get_attribute("aria-busy")

            if is_busy == "true":
                stable_checks = 0
                last_text = current_text
                time.sleep(2)
                continue

            if current_text and current_text == last_text:
                stable_checks += 1
            else:
                stable_checks = 0

            last_text = current_text

            # Require stable text across multiple checks.
            if current_text and stable_checks >= 3:
                return current_text

        time.sleep(2)

    raise TimeoutError(
        f"{provider} did not produce a complete response "
        f"within {timeout} seconds."
    )


def ask_ai_with_sources(provider, prompt, timeout=180):
    """Send a prompt and return the assistant text with cited web links."""
    if provider not in PROVIDERS:
        raise ValueError("Provider must be 'gemini' or 'chatgpt'.")

    if not prompt.strip():
        raise ValueError("Prompt cannot be empty.")

    profile_dir = BASE_DIR / "browser_data" / provider

    if not profile_dir.exists():
        raise FileNotFoundError(
            f"Browser profile not found: {profile_dir}. Run login_setup.py first."
        )

    config = PROVIDERS[provider]

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            accept_downloads=False,
            viewport={"width": 1280, "height": 800},
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            if provider == "gemini":
                page.goto(config["url"], wait_until="domcontentloaded")
            else:
                page.goto(config["url"], wait_until="domcontentloaded")

            input_box = page.locator(config["input"])
            input_box.wait_for(state="visible", timeout=30000)

            # Count existing responses so we can identify the new one.
            existing = get_response_locator(page, provider)
            previous_count = existing.count() if existing else 0

            input_box.fill(prompt)
            input_box.press("Enter")

            response = wait_for_response(
                page, provider, previous_count, timeout
            )

            response_locator = get_response_locator(page, provider)
            citations = (
                extract_citations(response_locator.last)
                if provider == "gemini" and response_locator
                else []
            )
            return BrowserAIResponse(text=response, citations=citations)

        except PlaywrightTimeoutError as error:
            page.screenshot(
                path=str(BASE_DIR / f"{provider}_timeout.png")
            )
            raise RuntimeError(
                f"{provider} timed out. Check the browser window and login."
            ) from error

        finally:
            context.close()


def ask_ai(provider, prompt, timeout=120):
    """Send a prompt to Gemini or ChatGPT and return the response text."""
    return ask_ai_with_sources(provider, prompt, timeout).text


if __name__ == "__main__":
    provider = input("Choose provider (gemini/chatgpt): ").strip().lower()

    test_prompt = """
        Give me five unusual automotive story ideas for Facebook Reels.

        For each idea include:
        1. Title
        2. Short story description
        3. Opening hook
        4. Why people might watch it

        Do not give a short summary. Provide all five ideas in detail.
        """

    print(f"\nSending test prompt to {provider}...\n")

    try:
        result = ask_ai(provider, test_prompt)
        print("AI RESPONSE:")
        print(result)
    except Exception as error:
        print(f"ERROR: {error}")
