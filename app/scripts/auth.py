import os
from playwright.sync_api import sync_playwright

# Resolve relative to this script so it works both locally and in Docker
STATE_FILE = os.environ.get(
    "AUTH_STATE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "auth", "browser.json")
)

def login():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # MUST be false first time
        context = browser.new_context()

        page = context.new_page()
        page.goto("https://accounts.google.com/")

        print("\n👉 Login to your Google account (YouTube Music)")
        print("👉 Then navigate to https://music.youtube.com")
        input("\nPress ENTER when fully logged in...")

        context.storage_state(path=STATE_FILE)

        print(f"\n✅ Session saved to {STATE_FILE}")
        browser.close()

if __name__ == "__main__":
    login()