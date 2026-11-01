
from pathlib import Path
from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = BASE_DIR / "browser_data" / "agent_profile"
DOWNLOAD_DIR = BASE_DIR / "workspace" / "downloads"
SCREENSHOT_DIR = BASE_DIR / "workspace" / "screenshots"

for directory in [PROFILE_DIR, DOWNLOAD_DIR, SCREENSHOT_DIR]:
    directory.mkdir(parents=True, exist_ok=True)


class BrowserManager:

    def __init__(self):
        self.playwright = None
        self.context = None
        self.pages = []

    def start(self):
        self.playwright = sync_playwright().start()

        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            accept_downloads=True,
            downloads_path=str(DOWNLOAD_DIR),
            viewport={"width": 1280, "height": 800}
        )

        self.pages = self.context.pages

        if not self.pages:
            self.pages.append(self.context.new_page())

        print("[Browser] Started successfully")

    def new_tab(self, url="about:blank"):
        page = self.context.new_page()
        page.goto(url)
        self.pages.append(page)
        return page

    def navigate(self, url, tab=0):
        page = self.pages[tab]
        page.goto(url, wait_until="domcontentloaded")
        return page.title()

    def screenshot(self, filename, tab=0):
        page = self.pages[tab]
        path = SCREENSHOT_DIR / filename
        page.screenshot(path=str(path), full_page=True)
        return str(path)

    def close_tab(self, tab=0):
        if tab < len(self.pages):
            self.pages[tab].close()
            self.pages = [
                page for page in self.pages if not page.is_closed()
            ]

    def close(self):
        if self.context:
            self.context.close()

        if self.playwright:
            self.playwright.stop()

        print("[Browser] Closed successfully")


if __name__ == "__main__":
    browser = BrowserManager()

    try:
        browser.start()

        browser.navigate("https://www.google.com")

        browser.new_tab("https://www.wikipedia.org")

        print("Screenshot:", browser.screenshot("google.png"))

        input("Press ENTER to close all browser sessions...")

    finally:
        browser.close()
