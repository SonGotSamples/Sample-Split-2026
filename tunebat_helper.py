from playwright.sync_api import sync_playwright, TimeoutError
from bs4 import BeautifulSoup
import time
import os
import json
import hashlib

TUNEBAT_CACHE_FILE = "tunebat_cache.json"
MAX_RETRIES = 3
BACKOFF_BASE = 2


class TunebatHelper:
    def __init__(self):
        self.cache = self._load_cache()
        self.retry_counts = {}

    def _load_cache(self):
        if os.path.exists(TUNEBAT_CACHE_FILE):
            try:
                with open(TUNEBAT_CACHE_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_cache(self):
        with open(TUNEBAT_CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2)

    def _get_cache_key(self, track_name: str, artist_name: str, track_id: str) -> str:
        key_str = f"{artist_name}:{track_name}:{track_id}".lower()
        return hashlib.md5(key_str.encode()).hexdigest()

    def _exponential_backoff(self, attempt: int) -> float:
        return min(BACKOFF_BASE ** attempt, 60)

    def fetch_bpm_key(self, track_name: str, artist_name: str, track_id: str) -> tuple:
        cache_key = self._get_cache_key(track_name, artist_name, track_id)
        
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            print(f"💾 Tunebat cache hit: {artist_name} - {track_name}")
            return cached['bpm'], cached['key']

        def slugify(text):
            return text.strip().replace(" ", "-")

        name_slug = slugify(track_name)
        artist_slug = slugify(artist_name)
        url = f"https://tunebat.com/Info/{name_slug}-{artist_slug}/{track_id}"

        print(f"🔗 Constructed Tunebat URL: {url}")

        for attempt in range(MAX_RETRIES):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=False,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage"
                        ]
                    )
                    page = browser.new_page()
                    print(f" Navigating to page (attempt {attempt + 1}/{MAX_RETRIES})...")
                    
                    try:
                        page.goto(url, timeout=120000)
                    except TimeoutError:
                        print(f" Navigation timeout, retrying...")
                        browser.close()
                        wait_time = self._exponential_backoff(attempt)
                        print(f"⏳ Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        continue

                    selector = "div.yIPfN span.ant-typography-secondary"
                    print("⏳ Waiting for BPM/Key block...")

                    try:
                        page.wait_for_selector(selector, timeout=30000)
                        print(" Element found!")
                    except TimeoutError:
                        print(" Selector timeout, retrying...")
                        browser.close()
                        wait_time = self._exponential_backoff(attempt)
                        print(f"⏳ Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        continue

                    html = page.content()

                    debug_dir = "tunebat_debug"
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_path = os.path.join(debug_dir, f"{track_id}.html")
                    with open(debug_path, "w", encoding="utf-8") as f:
                        f.write(html)

                    browser.close()
                    print(" Page loaded successfully")

                    soup = BeautifulSoup(html, "html.parser")
                    bpm = 0
                    key = "Unknown"

                    blocks = soup.find_all("div", class_="yIPfN")
                    for block in blocks:
                        label = block.find("span", class_="ant-typography-secondary")
                        value = block.find("h3")
                        if not label or not value:
                            continue

                        label_text = label.text.strip().lower()
                        value_text = value.text.strip()

                        if label_text == "bpm":
                            try:
                                bpm = int(value_text)
                            except ValueError:
                                bpm = 0
                        elif label_text == "key":
                            key = value_text

                    print(f"🎼 Extracted BPM: {bpm}, Key: {key}")
                    
                    self.cache[cache_key] = {
                        "bpm": bpm,
                        "key": key,
                        "timestamp": time.time()
                    }
                    self._save_cache()
                    
                    return bpm, key

            except Exception as e:
                print(f"❌ Attempt {attempt + 1} failed: {e}")
                if attempt < MAX_RETRIES - 1:
                    wait_time = self._exponential_backoff(attempt)
                    print(f"⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)

        print(f"⚠️ All {MAX_RETRIES} attempts failed for {artist_name} - {track_name}")
        return 0, "Unknown"

    def clear_cache(self):
        self.cache = {}
        if os.path.exists(TUNEBAT_CACHE_FILE):
            os.remove(TUNEBAT_CACHE_FILE)
        print("🗑️ Tunebat cache cleared")

    def get_cache_size(self) -> int:
        return len(self.cache)


_helper = TunebatHelper()


def get_bpm_key(track_name: str, artist_name: str, track_id: str) -> tuple[int, str]:
    return _helper.fetch_bpm_key(track_name, artist_name, track_id)