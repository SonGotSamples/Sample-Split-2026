from playwright.sync_api import sync_playwright, TimeoutError
from bs4 import BeautifulSoup
import time
import os
import json
import hashlib

TUNEBAT_CACHE_FILE = "tunebat_cache.json"
MAX_RETRIES = 2  # Reduced from 3 to 2 for faster failure
BACKOFF_BASE = 1.5  # Reduced backoff time
SESSION_REFRESH_RETRIES = 1  # Reduced from 2 to 1


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

    def _refresh_session(self, browser, page):
        """
        Section 6: Session refresh logic - reset request session on captcha or bad response.
        Clears cookies and reloads page to bypass captcha.
        """
        try:
            print("🔄 Refreshing session (clearing cookies)...")
            context = browser.contexts[0] if browser.contexts else None
            if context:
                context.clear_cookies()
            # Use longer timeout and less strict wait condition
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)  # 60s timeout, less strict wait
            except Exception:
                # If reload fails, try navigating fresh
                page.goto(page.url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)  # Longer pause after refresh
            return True
        except Exception as e:
            print(f"⚠️ Session refresh failed: {e}")
            return False

    def _check_captcha(self, page) -> bool:
        """Check if page contains captcha indicators."""
        try:
            html = page.content().lower()
            captcha_indicators = ["captcha", "recaptcha", "verify you are human", "challenge"]
            return any(indicator in html for indicator in captcha_indicators)
        except Exception:
            return False

    def fetch_bpm_key(self, track_name: str, artist_name: str, track_id: str, spotify_client=None) -> tuple:
        """
        Section 6: Optimized BPM/Key fetch - uses Spotify audio_features as primary (fast),
        falls back to Tunebat only if needed.
        """
        cache_key = self._get_cache_key(track_name, artist_name, track_id)
        
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            print(f"💾 Cache hit: {artist_name} - {track_name} (BPM: {cached['bpm']}, Key: {cached['key']})")
            return cached['bpm'], cached['key']
        
        # Try Spotify audio_features first (much faster and more reliable)
        if spotify_client:
            try:
                print(f"🎵 Fetching BPM/Key from Spotify for {artist_name} - {track_name}...")
                features = spotify_client.audio_features([track_id])
                if features and features[0]:
                    feat = features[0]
                    bpm = round(feat.get('tempo', 0)) if feat.get('tempo') else 0
                    key_index = feat.get('key', -1)
                    mode = feat.get('mode', 0)  # 0 = minor, 1 = major
                    
                    if bpm > 0 and key_index >= 0:
                        key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
                        key_name = key_names[key_index] if 0 <= key_index < len(key_names) else "Unknown"
                        # Add mode (minor/major)
                        if mode == 0:
                            key = f"{key_name} minor"
                        else:
                            key = f"{key_name} major"
                        
                        print(f"✅ Spotify: BPM={bpm}, Key={key}")
                        
                        # Cache the result
                        self.cache[cache_key] = {
                            "bpm": bpm,
                            "key": key,
                            "timestamp": time.time(),
                            "source": "spotify"
                        }
                        self._save_cache()
                        return bpm, key
                    else:
                        print(f"⚠️ Spotify audio_features incomplete, trying Tunebat...")
            except Exception as e:
                print(f"⚠️ Spotify fetch failed: {e}, trying Tunebat...")

        def slugify(text):
            return text.strip().replace(" ", "-")

        name_slug = slugify(track_name)
        artist_slug = slugify(artist_name)
        url = f"https://tunebat.com/Info/{name_slug}-{artist_slug}/{track_id}"

        print(f"🔗 Constructed Tunebat URL: {url}")

        # Section 6: Automatic retry logic with session refresh
        for attempt in range(MAX_RETRIES):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,  # Faster - run in background
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-gpu",
                            "--disable-images"  # Faster loading
                        ]
                    )
                    page = browser.new_page()
                    print(f" Navigating to page (attempt {attempt + 1}/{MAX_RETRIES})...")
                    
                    try:
                        # Use less strict wait condition to avoid timeouts
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)  # Reduced from 120s to 30s
                    except TimeoutError:
                        print(f" Navigation timeout, retrying...")
                        browser.close()
                        wait_time = self._exponential_backoff(attempt)
                        print(f"⏳ Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        continue

                    # Section 6: Check for captcha and refresh session if needed
                    if self._check_captcha(page):
                        print("⚠️ Captcha detected, refreshing session...")
                        if self._refresh_session(browser, page):
                            # Retry after refresh
                            for refresh_attempt in range(SESSION_REFRESH_RETRIES):
                                try:
                                    page.goto(url, wait_until="domcontentloaded", timeout=120000)
                                    if not self._check_captcha(page):
                                        break
                                    time.sleep(3)  # Longer wait between refresh attempts
                                except Exception as e:
                                    print(f"⚠️ Refresh retry {refresh_attempt + 1} failed: {e}")
                        else:
                            browser.close()
                            continue

                    selector = "div.yIPfN span.ant-typography-secondary"
                    print("⏳ Waiting for BPM/Key block...")

                    try:
                        page.wait_for_selector(selector, timeout=30000)
                        print(" Element found!")
                    except TimeoutError:
                        # Section 6: Check if captcha appeared during wait
                        if self._check_captcha(page):
                            print("⚠️ Captcha appeared during wait, refreshing...")
                            if self._refresh_session(browser, page):
                                try:
                                    page.wait_for_selector(selector, timeout=30000)
                                except TimeoutError:
                                    browser.close()
                                    wait_time = self._exponential_backoff(attempt)
                                    print(f"⏳ Waiting {wait_time}s before retry...")
                                    time.sleep(wait_time)
                                    continue
                            else:
                                browser.close()
                                wait_time = self._exponential_backoff(attempt)
                                print(f"⏳ Waiting {wait_time}s before retry...")
                                time.sleep(wait_time)
                                continue
                        else:
                            print(" Selector timeout, retrying...")
                            browser.close()
                            wait_time = self._exponential_backoff(attempt)
                            print(f"⏳ Waiting {wait_time}s before retry...")
                            time.sleep(wait_time)
                            continue

                    html = page.content()

                    # Section 6: Failure logging for developer diagnosis
                    debug_dir = "tunebat_debug"
                    os.makedirs(debug_dir, exist_ok=True)
                    debug_path = os.path.join(debug_dir, f"{track_id}.html")
                    with open(debug_path, "w", encoding="utf-8") as f:
                        f.write(html)
                    
                    # Log attempt info
                    log_path = os.path.join(debug_dir, f"{track_id}_log.json")
                    log_data = {
                        "track_name": track_name,
                        "artist_name": artist_name,
                        "track_id": track_id,
                        "url": url,
                        "attempt": attempt + 1,
                        "timestamp": time.time()
                    }
                    with open(log_path, "w", encoding="utf-8") as f:
                        json.dump(log_data, f, indent=2)

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

                    # Section 6: Validate extracted data
                    if bpm == 0 and key == "Unknown":
                        print("⚠️ No BPM/Key extracted, may indicate bad response")
                        if attempt < MAX_RETRIES - 1:
                            wait_time = self._exponential_backoff(attempt)
                            print(f"⏳ Waiting {wait_time}s before retry...")
                            time.sleep(wait_time)
                            continue

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
                # Section 6: Failure logging
                try:
                    debug_dir = "tunebat_debug"
                    os.makedirs(debug_dir, exist_ok=True)
                    error_log = os.path.join(debug_dir, f"{track_id}_error.json")
                    error_data = {
                        "track_name": track_name,
                        "artist_name": artist_name,
                        "track_id": track_id,
                        "attempt": attempt + 1,
                        "error": str(e),
                        "timestamp": time.time()
                    }
                    with open(error_log, "w", encoding="utf-8") as f:
                        json.dump(error_data, f, indent=2)
                except Exception:
                    pass
                
                if attempt < MAX_RETRIES - 1:
                    wait_time = self._exponential_backoff(attempt)
                    print(f"⏳ Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)

        # Section 6: Fallback metadata pull (return 0/Unknown but log for manual review)
        print(f"⚠️ All {MAX_RETRIES} attempts failed for {artist_name} - {track_name}")
        print(f"📝 Check tunebat_debug/{track_id}_*.json for failure details")
        return 0, "Unknown"

    def clear_cache(self):
        self.cache = {}
        if os.path.exists(TUNEBAT_CACHE_FILE):
            os.remove(TUNEBAT_CACHE_FILE)
        print("🗑️ Tunebat cache cleared")

    def get_cache_size(self) -> int:
        return len(self.cache)


_helper = TunebatHelper()


def get_bpm_key(track_name: str, artist_name: str, track_id: str, spotify_client=None) -> tuple[int, str]:
    return _helper.fetch_bpm_key(track_name, artist_name, track_id, spotify_client)