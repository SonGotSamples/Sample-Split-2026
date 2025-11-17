import json
import os
import time
from typing import Dict, Tuple, Optional, List, Any

from rapidfuzz import fuzz
from yt_dlp import YoutubeDL


CACHE_FILE = "fuzzy_cache.json"
RETRY_QUEUE_FILE = "retry_queue.json"


def _norm_str(value: Optional[str]) -> str:
    if not value:
        return ""
    import re
    s = str(value).strip().lower()
    s = s.replace("$", "s")
    s = re.sub(r"[^\w\s'-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


class FuzzyMatcher:
    """
    Robust song matcher using:
    - Text similarity (artist + title)
    - Optional duration similarity
    - Optional BPM similarity
    - Optional key match

    Candidates are dicts of:
        {
            "id": "...",
            "artist": "...",
            "title": "...",
            "duration": 262.0,
            "bpm": 65.0,
            "key": "B major"
        }
    """

    def __init__(
        self,
        min_accept_score: float = 90.0,
        min_soft_accept_score: float = 75.0,
        max_duration_diff_sec: float = 10.0,
    ) -> None:
        self.min_accept_score = min_accept_score
        self.min_soft_accept_score = min_soft_accept_score
        self.max_duration_diff_sec = max_duration_diff_sec

        self.cache = self._load_cache()
        self.retry_queue = self._load_retry_queue()

    def _load_cache(self) -> Dict[str, Any]:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_cache(self) -> None:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2)

    def _load_retry_queue(self) -> Dict[str, Any]:
        if os.path.exists(RETRY_QUEUE_FILE):
            try:
                with open(RETRY_QUEUE_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_retry_queue(self) -> None:
        with open(RETRY_QUEUE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.retry_queue, f, indent=2)

    @staticmethod
    def _norm_str(value: Optional[str]) -> str:
        return _norm_str(value)

    def _compute_composite_score(
        self,
        title: str,
        artist: str,
        duration: Optional[float],
        bpm: Optional[float],
        key: Optional[str],
        candidate: Dict[str, Any],
    ) -> Tuple[float, Optional[str]]:
        cand_id = candidate.get("id")
        cand_artist = self._norm_str(candidate.get("artist"))
        cand_title = self._norm_str(candidate.get("title"))
        cand_duration = candidate.get("duration")
        cand_bpm = candidate.get("bpm")
        cand_key = candidate.get("key")

        query_text = f"{artist} {title}".strip().lower()
        cand_text = f"{cand_artist} {cand_title}".strip().lower()

        token_set_score = fuzz.token_set_ratio(query_text, cand_text)
        token_sort_score = fuzz.token_sort_ratio(query_text, cand_text)
        ratio_score = fuzz.ratio(query_text, cand_text)

        artist_score = fuzz.token_set_ratio(artist, cand_artist) if cand_artist else 0
        title_score = fuzz.token_set_ratio(title, cand_title) if cand_title else 0
        combined_separate = (artist_score + title_score) / 2

        text_score = max(token_set_score, token_sort_score, ratio_score, combined_separate)

        duration_score = 100.0
        if duration is not None and cand_duration is not None:
            try:
                duration = float(duration)
                cand_duration = float(cand_duration)
                diff = abs(duration - cand_duration)

                if diff <= self.max_duration_diff_sec:
                    duration_score = 100.0
                elif diff <= self.max_duration_diff_sec * 2:
                    duration_score = 80.0
                else:
                    duration_score = max(
                        0.0,
                        80.0 - (diff - self.max_duration_diff_sec * 2) * 2.0,
                    )
            except Exception:
                duration_score = 80.0

        bpm_score = 100.0
        if bpm is not None and cand_bpm is not None:
            try:
                bpm = float(bpm)
                cand_bpm = float(cand_bpm)
                diff = abs(bpm - cand_bpm)

                if diff <= 1.0:
                    bpm_score = 100.0
                elif diff <= 3.0:
                    bpm_score = 90.0
                elif diff <= 6.0:
                    bpm_score = 75.0
                else:
                    bpm_score = max(0.0, 75.0 - (diff - 6.0) * 3.0)
            except Exception:
                bpm_score = 85.0

        key_score = 100.0
        if key and cand_key:
            if self._norm_str(key) == self._norm_str(cand_key):
                key_score = 100.0
            else:
                key_score = 80.0
        else:
            key_score = 90.0

        if text_score >= 90:
            weights = {
                "text": 0.8,
                "duration": 0.15,
                "bpm": 0.03,
                "key": 0.02,
            }
        else:
            weights = {
                "text": 0.7,
                "duration": 0.2,
                "bpm": 0.05,
                "key": 0.05,
            }

        composite = (
            text_score * weights["text"]
            + duration_score * weights["duration"]
            + bpm_score * weights["bpm"]
            + key_score * weights["key"]
        )

        return composite, str(cand_id) if cand_id is not None else None

    def match_song(
        self,
        title: str,
        artist: str,
        candidates: List[Dict[str, Any]],
        duration: Optional[float] = None,
        bpm: Optional[float] = None,
        key: Optional[str] = None,
    ) -> Tuple[Optional[str], float]:
        cache_key = f"{artist}:{title}".lower()

        if cache_key in self.cache:
            cached = self.cache[cache_key]
            print(f" Fuzzy cache hit: {cache_key} → {cached['match_id']}")
            return cached["match_id"], cached["score"]

        best_match_id: Optional[str] = None
        best_score = 0.0

        query_title = self._norm_str(title)
        query_artist = self._norm_str(artist)

        for candidate in candidates:
            score, cand_id = self._compute_composite_score(
                title=query_title,
                artist=query_artist,
                duration=duration,
                bpm=bpm,
                key=key,
                candidate=candidate,
            )

            if cand_id is None:
                continue

            if score > best_score:
                best_score = score
                best_match_id = cand_id

        if best_score >= self.min_accept_score:
            self.cache[cache_key] = {
                "match_id": best_match_id,
                "score": best_score,
                "timestamp": time.time(),
            }
            self._save_cache()
            print(
                f" Fuzzy match: {cache_key} → {best_match_id} "
                f"(score: {best_score:.2f}%)"
            )
            return best_match_id, best_score

        if cache_key not in self.retry_queue:
            self.retry_queue[cache_key] = {
                "title": title,
                "artist": artist,
                "attempts": 0,
                "last_attempt": time.time(),
                "last_best_score": best_score,
            }
            self._save_retry_queue()
            print(
                f" Fuzzy match low confidence: {cache_key} "
                f"(score: {best_score:.2f}%)"
            )

        if best_score >= self.min_soft_accept_score:
            return best_match_id, best_score

        return None, best_score

    def get_cached_match(self, title: str, artist: str) -> Optional[str]:
        cache_key = f"{artist}:{title}".lower()
        data = self.cache.get(cache_key)
        if data:
            return data["match_id"]
        return None

    def clear_cache(self) -> None:
        self.cache = {}
        if os.path.exists(CACHE_FILE):
            os.remove(CACHE_FILE)
        print(" Fuzzy cache cleared")

    def get_retry_queue_count(self) -> int:
        return len(self.retry_queue)

    def clear_retry_queue(self) -> None:
        self.retry_queue = {}
        if os.path.exists(RETRY_QUEUE_FILE):
            os.remove(RETRY_QUEUE_FILE)
        print(" Retry queue cleared")


def _is_topic_uploader_for_artist(artist: str, uploader: str) -> bool:
    """
    Decide if a given uploader (e.g. 'Joe Budden - Topic') should be treated
    as the Topic channel for a given artist (e.g. 'Joe').

    This fixes cases like Joe vs Joe Budden - Topic.
    """
    artist_norm = _norm_str(artist)
    uploader_norm = _norm_str(uploader)

    if "topic" not in uploader_norm:
        return False

    base = uploader_norm.replace("- topic", "").strip()
    base_tokens = [t for t in base.split() if t]

    if len(artist_norm) <= 4:
        if base == artist_norm:
            return True
        if base.startswith(artist_norm + " ") or base.endswith(" " + artist_norm):
            if len(base_tokens) > 1 and base_tokens[0] == artist_norm:
                return False
            return True
        return False

    score = fuzz.token_set_ratio(artist_norm, base)
    return score >= 85


def _search_topic_candidates_with_ytdlp(
    artist: str,
    title: str,
    max_results: int = 25,
) -> List[Dict[str, Any]]:
    """
    Use yt-dlp to search YouTube and return candidates that look like
    they come from the correct Topic channel for this artist.
    """
    candidates: List[Dict[str, Any]] = []

    queries = [
        f"{artist} - {title} topic",
        f"{title} {artist} topic",
        f"{artist} {title} topic",
    ]

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "noplaylist": True,
    }

    with YoutubeDL(ydl_opts) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(
                    f"ytsearch{max_results}:{q}",
                    download=False,
                )
            except Exception as e:
                print(f" yt-dlp Topic search failed for '{q}': {e}")
                continue

            entries = info.get("entries") or []
            for e in entries:
                if not e:
                    continue

                video_id = e.get("id")
                if not video_id:
                    continue

                uploader = e.get("uploader") or ""
                if not _is_topic_uploader_for_artist(artist, uploader):
                    continue

                title_text = e.get("title") or ""
                duration = e.get("duration")

                candidates.append(
                    {
                        "id": video_id,
                        "artist": uploader,
                        "title": title_text,
                        "duration": float(duration) if duration is not None else None,
                        "bpm": None,
                        "key": None,
                    }
                )

            if candidates:
                break

    return candidates


def _search_general_candidates_with_ytdlp(
    artist: str,
    title: str,
    max_results: int = 25,
) -> List[Dict[str, Any]]:
    """
    Fallback search when no Topic candidates were found.
    """
    candidates: List[Dict[str, Any]] = []

    queries = [
        f"{artist} - {title}",
        f"{title} {artist}",
    ]

    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "noplaylist": True,
    }

    artist_norm = _norm_str(artist)
    title_norm = _norm_str(title)

    with YoutubeDL(ydl_opts) as ydl:
        for q in queries:
            try:
                info = ydl.extract_info(
                    f"ytsearch{max_results}:{q}",
                    download=False,
                )
            except Exception as e:
                print(f" yt-dlp general search failed for '{q}': {e}")
                continue

            entries = info.get("entries") or []
            for e in entries:
                if not e:
                    continue

                video_id = e.get("id")
                if not video_id:
                    continue

                uploader = e.get("uploader") or ""
                video_title = e.get("title") or ""
                duration = e.get("duration")

                full_text = f"{uploader} {video_title}"
                text_score = fuzz.token_set_ratio(
                    f"{artist_norm} {title_norm}",
                    _norm_str(full_text),
                )
                if text_score < 80:
                    continue

                candidates.append(
                    {
                        "id": video_id,
                        "artist": uploader,
                        "title": video_title,
                        "duration": float(duration) if duration is not None else None,
                        "bpm": None,
                        "key": None,
                    }
                )

            if candidates:
                break

    return candidates


def find_best_topic_video_for_track(
    title: str,
    artist: str,
    duration_seconds: Optional[float],
    bpm: Optional[float] = None,
    key: Optional[str] = None,
    max_results: int = 25,
) -> Tuple[Optional[str], float]:
    """
    High-level helper to be used from your pipeline:

    Search YouTube and fuzzy-match on title + artist + duration (+ optional bpm/key).
    No Topic channel filtering - uses general search.
    """
    candidates = _search_general_candidates_with_ytdlp(
        artist=artist,
        title=title,
        max_results=max_results,
    )

    if not candidates:
        print(f" No candidates found for: {artist} - {title}")
        return None, 0.0

    matcher = FuzzyMatcher()
    video_id, score = matcher.match_song(
        title=title,
        artist=artist,
        candidates=candidates,
        duration=duration_seconds,
        bpm=bpm,
        key=key,
    )
    return video_id, score
