from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        args=["--no-sandbox"]
    )

    page = browser.new_page(
        viewport={"width": 1280, "height": 720}
    )

    page.goto("https://www.google.com")

    print("Page title:", page.title())

    page.screenshot(path="browser_test.png")

    input("Press ENTER to close the browser...")

    browser.close()
