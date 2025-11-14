import json
import os
import time
from typing import Dict, Tuple, Optional
from rapidfuzz import fuzz
from pathlib import Path


CACHE_FILE = "fuzzy_cache.json"
RETRY_QUEUE_FILE = "retry_queue.json"


class FuzzyMatcher:
    def __init__(self):
        self.cache = self._load_cache()
        self.retry_queue = self._load_retry_queue()

    def _load_cache(self) -> Dict:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_cache(self):
        with open(CACHE_FILE, "w") as f:
            json.dump(self.cache, f, indent=2)

    def _load_retry_queue(self) -> Dict:
        if os.path.exists(RETRY_QUEUE_FILE):
            try:
                with open(RETRY_QUEUE_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_retry_queue(self):
        with open(RETRY_QUEUE_FILE, "w") as f:
            json.dump(self.retry_queue, f, indent=2)

    def match_song(self, title: str, artist: str, candidates: list) -> Tuple[Optional[str], float]:
        cache_key = f"{artist}:{title}".lower()
        
        if cache_key in self.cache:
            cached = self.cache[cache_key]
            print(f" Fuzzy cache hit: {cache_key} → {cached['match_id']}")
            return cached['match_id'], cached['score']

        best_match = None
        best_score = 0
        
        query = f"{artist} {title}".lower()
        
        for candidate in candidates:
            if isinstance(candidate, dict):
                artist_part = candidate.get('artist', '')
                title_part = candidate.get('title', '')
                cand_text = f"{artist_part} {title_part}".lower()
                cand_id = candidate.get('id')
            else:
                cand_text = str(candidate).lower()
                cand_id = candidate

            score = fuzz.token_set_ratio(query, cand_text)
            
            if score > best_score:
                best_score = score
                best_match = cand_id

        if best_score >= 85:
            self.cache[cache_key] = {
                "match_id": best_match,
                "score": best_score,
                "timestamp": time.time()
            }
            self._save_cache()
            print(f" ✓ Fuzzy match: {query} → {best_match} (score: {best_score}%)")
            return best_match, best_score

        if cache_key not in self.retry_queue:
            self.retry_queue[cache_key] = {
                "title": title,
                "artist": artist,
                "attempts": 0,
                "last_attempt": time.time()
            }
            self._save_retry_queue()
            print(f" ⚠ Fuzzy match low confidence: {query} (score: {best_score}%)")

        return best_match if best_score >= 70 else None, best_score

    def get_cached_match(self, title: str, artist: str) -> Optional[str]:
        cache_key = f"{artist}:{title}".lower()
        if cache_key in self.cache:
            return self.cache[cache_key]['match_id']
        return None

    def clear_cache(self):
        self.cache = {}
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        print(" Fuzzy cache cleared")

    def get_retry_queue_count(self) -> int:
        return len(self.retry_queue)

    def clear_retry_queue(self):
        self.retry_queue = {}
        if os.path.exists(RETRY_QUEUE_FILE):
            os.remove(RETRY_QUEUE_FILE)
        print(" Retry queue cleared")
