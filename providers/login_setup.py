
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent

PROVIDERS = {
    "gemini": "https://gemini.google.com/",
    "chatgpt": "https://chatgpt.com/"
}


def setup_login(provider):

    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")

    profile = BASE_DIR / "browser_data" / provider
    profile.mkdir(parents=True, exist_ok=True)
    profile.chmod(0o700)

    print(f"Opening {provider}...")

    with sync_playwright() as p:

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=False,
            accept_downloads=False,
            viewport={"width": 1280, "height": 800}
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            page.goto(
                PROVIDERS[provider],
                wait_until="domcontentloaded"
            )

            print(f"\nPlease log into {provider} manually.")
            print("Do not share your credentials with the agent.")
            input("Press ENTER after login is complete...")

            print(f"{provider} profile initialized.")

        finally:
            context.close()


if __name__ == "__main__":

    provider = input(
        "Choose provider (gemini/chatgpt): "
    ).strip().lower()

    setup_login(provider)
