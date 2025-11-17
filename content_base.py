# File: content_base.py
import os
import re
import json
import time
import requests
from typing import Optional
from yt_dlp import YoutubeDL
from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
try:
    from upload_ec2 import Uploader
except ModuleNotFoundError:
    Uploader = None

from shared_state import get_progress, set_progress
from dotenv import load_dotenv
from yt_video_multi import upload_all_stems
from concurrent.futures import ThreadPoolExecutor

#  Channel name map (maps channel keys to display names for folder structure)
CHANNEL_NAME_MAP = {
    "main_channel": "Main",
    "sgs_2": "SGS 2",
    "son_got_drums": "Drum",
    "son_got_acapellas": "Acappella",
    "sample_split": "Sample Split",
    "tiktok_channel": "Tik Tok",
}

load_dotenv()

#  Simplified YouTube auth setup
YT_TOKEN_PATH = os.path.join("yt_tokens", "main_v2.json")
CLIENT_SECRETS_PATH = YT_TOKEN_PATH  # Same file used for both client secret and refresh token

# --- Default YouTube metadata constants ---
DEFAULT_DESCRIPTION = (
    "Access all stems and extracts https://songotsamples.com/collections/monthly-pack\n\n"
    "Follow backup channels to keep up with other stems and media.\n"
    "https://www.youtube.com/@Songotsamples2  https://www.youtube.com/@SonGotAcapellas"
)

DEFAULT_TAGS = [
    "acapella", "beatmaker", "beats", "boombap", "drums", "extractions",
    "hiphop", "instrumentals", "rap", "samples", "sampling", "soul",
    "soulsamples", "songotsamples", "stems", "stemseparation",
    "musicproduction", "beatmaking", "samplepack", "producercommunity",
    "oldschoolhiphop", "lofibeats", "vinylsamples", "soulfulbeats",
    "undergroundhiphop", "sampleflip", "remix", "drumbreaks",
    "drumloops", "melodystems", "isolatedvocals", "mixing", "mastering",
    "audioengineering", "producerlife", "beatstars", "freestems",
    "typebeat", "boombapbeats", "soulfulhiphop", "lofivibes"
]

GENRE_SLUG_MAP = {
    "hiphop": "Hip-Hop",
    "hip-hop": "Hip-Hop",
    "rnb": "R&B",
    "r&b": "R&B",
    "jazz": "Jazz",
    "soul": "Soul",
    "rock": "Rock",
    "pop": "Pop",
    "electronic": "Electronic",
    "world": "World",
    "other": "Other",
}


def normalize_genre(value: Optional[str], default: str = "Other") -> str:
    if not value:
        return default
    key = value.strip()
    if not key:
        return default
    lookup = GENRE_SLUG_MAP.get(key.lower())
    if lookup:
        return lookup
    return key.title()

def format_bpm_label(bpm) -> str:
    """
    Half-time rule (only if BPM ≥ 140):
      - if even → bpm / 2  (e.g. 160 → 80)
      - if odd  → bpm / 2 with .5 (e.g. 163 → 81.5)
      - if bpm < 140 → keep original value
    """
    try:
        n = int(round(float(bpm)))
        if n >= 140:
            if n % 2:     # odd BPM
                return f"{n/2:.1f}"
            return str(n // 2)
        return str(n)
    except Exception:
        return str(bpm)

class ContentBase:
    def __init__(self, args: dict, track_info: dict = None):
        self.args = args
        self.session_id = args.get("session_id", "default")
        self.track_info = track_info or args.get("track_info")
        self.channel_key = args.get("channel")
        self.channel_label = CHANNEL_NAME_MAP.get(self.channel_key, self.channel_key)
        self.trim_track = args.get("trim_track", False)
        self.trim_length = args.get("trim_length", 72)

        self.universal_id = args.get("universal_id")
        # Use htdemucs_ft (high-quality) as default stem path
        # Falls back to htdemucs_6s or htdemucs if htdemucs_ft unavailable
        self.stem_base_path = args.get("stem_base_path") or (
            os.path.join("Separated", "htdemucs_ft", self.universal_id) if self.universal_id else ""
        )

        self.selected_genre = normalize_genre(args.get("genre"))
        self.genre_folder = self._sanitize_folder_name(self.selected_genre)
        self.video_paths = {}

        print(f"\n ContentBase initialized with session_id: {self.session_id}")
        print(f" Received BPM: {args.get('bpm')} | Key: {args.get('key')}")
        print(f" Track info present: {'Yes' if self.track_info else 'No'}\n")

        self.CLIENT_ID = args.get("client_id") or os.getenv("SPOTIFY_CLIENT_ID")
        self.CLIENT_SECRET = args.get("client_secret") or os.getenv("SPOTIFY_CLIENT_SECRET")
        
        if not self.CLIENT_ID or not self.CLIENT_SECRET:
            raise ValueError("Spotify credentials missing: provide client_id and client_secret")
        
        self.sp = Spotify(auth_manager=SpotifyClientCredentials(
            client_id=self.CLIENT_ID,
            client_secret=self.CLIENT_SECRET
        ))

    def get_stem_path(self, stem_name: str) -> str:
        return os.path.join(self.stem_base_path, f"{stem_name}.mp3") if self.stem_base_path else ""

    def _sanitize_folder_name(self, value: Optional[str], fallback: str = "General") -> str:
        cleaned = self.sanitize_name(value or "")
        return cleaned if cleaned else fallback

    def sanitize_name(self, name: str) -> str:
        cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", name or "")
        cleaned = re.sub(r"[_-]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or "Untitled"

    def build_meta(self, stem_type: str, channel: str, track: dict) -> dict:
        return {
            "track_id": track.get("id"),
            "stem": stem_type.lower(),
            "channel": channel,
            "artist": track.get("artist"),
            "title": track.get("name"),
            "bpm": int(track.get("tempo", 0)),
            "key": track.get("key"),
        }

    def update_progress(self, message: str, metadata: dict = None, step_percent: float = None):
        current = get_progress(self.session_id)
        meta = current.get("meta", {}) if current else {}
        percent = current.get("percent", 0) if current else 0

        if step_percent is not None:
            percent = max(percent, min(100, step_percent))

        enriched_meta = {
            "stem": meta.get("stem"),
            "channel": meta.get("channel"),
            "artist": meta.get("artist"),
            "track": meta.get("track"),
            "bpm": meta.get("bpm"),
            "key": meta.get("key"),
            "title": meta.get("title"),
            **(metadata or {})
        }

        set_progress(self.session_id, {
            "message": message,
            "percent": percent,
            "meta": enriched_meta
        })
        print(f"[UPDATE] {self.session_id} → {message} ({percent}%)")

    def mark_step_complete(self, message: str, extra_meta: dict = None):
        progress = get_progress(self.session_id)
        if not progress:
            return

        meta = progress.get("meta", {})
        completed = meta.get("completed", 0) + 1
        total = meta.get("total", 1)
        percent = int((completed / total) * 100)

        enriched_meta = {
            "completed": completed,
            "total": total,
            "stem": meta.get("stem"),
            "channel": meta.get("channel"),
            "artist": meta.get("artist"),
            "track": meta.get("track"),
            "bpm": meta.get("bpm"),
            "key": meta.get("key"),
            "title": meta.get("title"),
            **(extra_meta or {})
        }

        set_progress(self.session_id, {
            "message": message,
            "percent": percent,
            "meta": enriched_meta
        })
        print(f"[DONE] {self.session_id}: {percent}% ({completed}/{total}) → {message}")

    def progress_with_meta(self, message: str, step: int, total: int, stem: str, channel: str, track: dict):
        meta = self.build_meta(stem, channel, track)
        self.incremental_progress(message, step, total, meta)

    def fail_progress_with_meta(self, message: str, stem: str, channel: str, track: dict):
        meta = self.build_meta(stem, channel, track)
        self.update_progress(message, meta)

    def mark_complete_with_meta(self, message: str, stem: str, channel: str, track: dict):
        meta = self.build_meta(stem, channel, track)
        self.mark_step_complete(message, meta)

    # FIXED VERSION: Proper indentation + structure
    def upload_batch_to_youtube(self, track):
        try:
            artist = track.get("artist")
            title  = track.get("name")
            key    = track.get("key")
            bpm    = track.get("tempo")
            bpm_str = format_bpm_label(bpm)

            # Build titles for each stem type
            title_map = {}
            key_text = str(key).strip() if key else ""
            for stem_type in self.video_paths.keys():
                display_stem = stem_type.replace("_", " ").title()
                bracket_parts = [f"BPM {bpm_str}"]
                if stem_type.lower() != "drums" and key_text:
                    bracket_parts.append(key_text)
                bracket = f"[{' '.join(part for part in bracket_parts if part)}]"
                title_parts = [artist, title, display_stem, bracket]
                yt_title = " ".join(part for part in title_parts if part)
                title_map[stem_type] = yt_title

            self.update_progress("📤 Uploading all stems to YouTube...", {"artist": artist})

            upload_all_stems(
                stem_files=self.video_paths,         # stem_type -> mp4 path
                title_map=title_map,                 # custom titles
                description=DEFAULT_DESCRIPTION,     # your constant
                tags=DEFAULT_TAGS,                   # your constant
                category_id="10",
                playlist=None,
                privacy=self.args.get("privacy", "private"),
                publish_at=self.args.get("publish_at"),
                tz=self.args.get("tz", "America/Chicago"),
                made_for_kids=False,
                lang="en",
                thumbnail_map=None,
                comment=None,
                dry_run=self.args.get("dry_run", False),
                channel_override=None,
                artist_file_map=None
            )

            self.update_progress(" All stem videos uploaded", {"artist": artist})

        except Exception as e:
            self.update_progress(f" Batch upload failed: {e}", {"artist": track.get("artist")})

    def upload_to_youtube(self, stem_type, video_path, title, track):
        try:
            if stem_type and video_path:
                self.video_paths[stem_type] = video_path
        except Exception as e:
            self.update_progress(f"load tracking failed: {e}", {"stem": stem_type})

    def upload_to_ec2_if_needed(self, local_path):
        if self.args.get("ec2"):
            try:
                self.update_progress(" Uploading to EC2...", {"path": local_path})
                uploader = Uploader()
                uploader.upload_to_ec2(local_path)
                self.update_progress(" Upload to EC2 complete", {"path": local_path})
            except Exception as e:
                self.update_progress(f" EC2 upload failed: {e}", {"path": local_path})

    def download_audio(self, title, artist):
        """
        Enhanced audio download with strict filtering and verification.
        Implements Section 1 requirements: strict source filtering, duration matching,
        fuzzy verification, multi-stage verification, and proactive safeguards.
        """
        from mutagen.mp3 import MP3 as MutagenMP3
        
        official_duration = None
        
        # Get official duration from track info if available
        if self.track_info:
            # Try to get duration from track info (may be set during get_track_info)
            official_duration = self.track_info.get("duration_seconds")
            if not official_duration:
                # Try to get from Spotify API
                try:
                    track_id = self.track_info.get("id")
                    if track_id:
                        track = self.sp.track(track_id)
                        # Duration in milliseconds, convert to seconds
                        official_duration = track.get("duration_ms", 0) / 1000.0
                except Exception:
                    pass
        
        # Multi-stage search with priority order
        # Clean artist and title for search (remove special characters that might break search)
        clean_artist = artist.replace("$", "").replace("$$", "").strip()
        clean_title = title.replace("$", "").replace("$$", "").strip()
        
        search_terms = [
            f"{title} - {artist} topic",
            f"{clean_title} - {clean_artist} topic",
            f"{title} - {artist} official audio",
            f"{clean_title} - {clean_artist} official audio",
            f"{title} - {artist} album",
            f"{clean_title} - {clean_artist} album",
            f"{title} - {artist}",
            f"{clean_title} - {clean_artist}",
            f"{artist} {title}",  # Alternative format
            f"{clean_artist} {clean_title}",  # Alternative format without special chars
        ]
        
        # Reject patterns (music videos, live, unofficial)
        reject_patterns = [
            "music video", "mv", "live", "performance", "concert",
            "unofficial", "remix", "edit", "cover", "karaoke",
            "instrumental", "acapella", "extended", "version"
        ]
        
        candidates = []
        max_candidates = 10
        
        # Search with multiple terms and collect candidates using yt-dlp
        for search_term in search_terms:
            try:
                # Use yt-dlp to search YouTube
                ydl_opts_search = {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": True,  # Don't download, just get metadata
                    "default_search": "ytsearch",  # Search YouTube
                    "max_downloads": max_candidates,  # Limit results
                }
                
                with YoutubeDL(ydl_opts_search) as ydl:
                    # yt-dlp search returns a list of video info dicts
                    search_results = ydl.extract_info(f"ytsearch{max_candidates}:{search_term}", download=False)
                    
                if not search_results or "entries" not in search_results:
                    print(f" No results found for search term: {search_term}")
                    continue
                
                videos_list = search_results.get("entries", [])
                if not videos_list:
                    continue
                
                for video in videos_list:
                    if not video:
                        continue
                    video_title = video.get("title", "").lower()
                    video_id = video.get("id")
                    
                    # Reject if matches reject patterns
                    should_reject = any(pattern in video_title for pattern in reject_patterns)
                    if should_reject:
                        continue
                    
                    # Prioritize Topic/Official Audio/Album sources
                    priority = 0
                    if "topic" in video_title:
                        priority = 3
                    elif "official audio" in video_title or "official" in video_title:
                        priority = 2
                    elif "album" in video_title:
                        priority = 1
                    
                    candidates.append({
                        "id": video_id,
                        "title": video.get("title", ""),
                        "priority": priority,
                        "search_term": search_term,
                        "duration": video.get("duration", 0),  # yt-dlp provides duration
                        "uploader": video.get("uploader", "") or video.get("channel", ""),  # yt-dlp provides uploader
                    })
                    
                    if len(candidates) >= max_candidates:
                        break
                        
            except Exception as e:
                print(f" Search term '{search_term}' failed: {e}")
                continue
        
        if not candidates:
            print(" No valid candidates found")
            return None, None
        
        # Sort by priority (higher first)
        candidates.sort(key=lambda x: x["priority"], reverse=True)
        
        # Track best candidate for fallback if all strict checks fail
        best_fallback = None
        best_fallback_score = 0
        
        # Multi-stage verification: try candidates until one passes all checks
        for candidate in candidates:
            video_id = candidate["id"]
            candidate_title = candidate["title"]
            
            try:
                # Extract full info without downloading first
                # yt-dlp search with extract_flat might not have all fields, so we fetch full info
                ydl_opts_info = {
                    "quiet": True,
                    "no_warnings": True,
                    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                }
                
                with YoutubeDL(ydl_opts_info) as ydl:
                    info = ydl.extract_info(video_id, download=False)
                    
                    # Duration matching (mandatory if official duration available)
                    # Use percentage-based tolerance: ±2 seconds OR ±5% (whichever is larger)
                    # This handles YouTube videos with intros/outros that extend the track
                    if official_duration and info.get("duration"):
                        candidate_duration = info.get("duration")
                        duration_diff = abs(candidate_duration - official_duration)
                        percentage_diff = (duration_diff / official_duration) * 100
                        # Allow ±2 seconds OR ±5% (whichever is larger)
                        # For tracks > 3 minutes, allow up to 10% difference (capped at 30s) to handle intros/outros
                        # For tracks > 4 minutes, allow up to 15% difference (capped at 40s) for very long tracks
                        base_tolerance = max(2.0, official_duration * 0.05)
                        if official_duration > 240:  # Tracks longer than 4 minutes
                            extended_tolerance = max(base_tolerance, official_duration * 0.15)
                            max_tolerance = min(extended_tolerance, 40.0)  # Cap at 40 seconds for very long tracks
                        elif official_duration > 180:  # Tracks longer than 3 minutes
                            extended_tolerance = max(base_tolerance, official_duration * 0.10)
                            max_tolerance = min(extended_tolerance, 30.0)  # Cap at 30 seconds for longer tracks
                        else:
                            max_tolerance = min(base_tolerance, 10.0)  # Cap at 10 seconds for shorter tracks
                        
                        if duration_diff > max_tolerance:
                            print(f" Duration mismatch: {candidate_duration:.1f}s vs {official_duration:.1f}s (diff: {duration_diff:.1f}s, {percentage_diff:.1f}%, tolerance: {max_tolerance:.1f}s)")
                            continue
                    
                    # Artist + Title Pair Verification with stricter fuzzy matching
                    info_title = info.get("title", "")
                    info_uploader = info.get("uploader", "")
                    
                    # Use rapidfuzz directly for better control
                    from rapidfuzz import fuzz
                    
                    # Normalize strings for better matching (remove special chars, normalize spaces)
                    def normalize_for_match(s):
                        if not s:
                            return ""
                        # Remove special characters but keep spaces and hyphens
                        import re
                        s = re.sub(r'[^\w\s-]', '', s.lower())
                        # Normalize multiple spaces to single space
                        s = re.sub(r'\s+', ' ', s).strip()
                        return s
                    
                    # Fuzzy match both artist and title separately
                    # Use normalized versions for better matching with special characters
                    normalized_artist = normalize_for_match(artist)
                    normalized_title = normalize_for_match(title)
                    normalized_uploader = normalize_for_match(info_uploader) if info_uploader else ""
                    normalized_info_title = normalize_for_match(info_title) if info_title else ""
                    
                    artist_score = fuzz.token_set_ratio(normalized_artist, normalized_uploader) if normalized_uploader else 0
                    title_score = fuzz.token_set_ratio(normalized_title, normalized_info_title) if normalized_info_title else 0
                    
                    # Also try matching with original strings (in case normalization removes important info)
                    artist_score_orig = fuzz.token_set_ratio(artist.lower(), info_uploader.lower()) if info_uploader else 0
                    title_score_orig = fuzz.token_set_ratio(title.lower(), info_title.lower()) if info_title else 0
                    
                    # Use the better score from normalized or original
                    artist_score = max(artist_score, artist_score_orig)
                    title_score = max(title_score, title_score_orig)
                    
                    # More flexible thresholds: require both artist AND title to match well
                    # Allow slightly lower combined score if title is perfect (100%)
                    min_artist_score = 65  # More relaxed for artist variations (handles aliases, special chars)
                    min_title_score = 70   # More relaxed for title match (handles variations)
                    min_combined_score = 75  # More relaxed combined score
                    combined_score = (artist_score + title_score) / 2
                    
                    # Track best candidate for fallback
                    if combined_score > best_fallback_score:
                        best_fallback = candidate
                        best_fallback_score = combined_score
                    
                    # Special case: if title is perfect (100%), allow lower artist score
                    if title_score >= 95 and combined_score >= 75:
                        # Title is very close, allow lower artist match
                        pass
                    # Special case: if artist is perfect (100%), allow lower title score
                    elif artist_score >= 95 and combined_score >= 75:
                        # Artist is very close, allow lower title match
                        pass
                    elif artist_score < min_artist_score or title_score < min_title_score or combined_score < min_combined_score:
                        print(f" Fuzzy match too low: artist={artist_score:.1f}%, title={title_score:.1f}%, combined={combined_score:.1f}%")
                        continue
                    
                    # Proactive safeguards: check for MV skits/dialogue indicators
                    description = info.get("description", "").lower()
                    if any(indicator in description for indicator in ["skit", "dialogue", "interview", "behind the scenes"]):
                        print(f" Rejected: contains MV skits/dialogue indicators")
                        continue
                    
                    # All checks passed - download this candidate
                    print(f" ✓ Verified candidate: {info_title} (duration: {info.get('duration', 0):.1f}s, fuzzy: {combined_score:.1f}%)")
                    
                    os.makedirs("MP3", exist_ok=True)
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "format": "bestaudio/best",
                        "outtmpl": "%(uploader)s - %(id)s.%(ext)s",
                        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                        "postprocessors": [{
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192"
                        }]
                    }
                    
                    with YoutubeDL(ydl_opts) as ydl:
                        uid = f"{info['uploader']} - {info['id']}"
                        temp_mp3 = f"{uid}.mp3"
                        final_path = f"MP3/{uid}.mp3"
                        
                        ydl.download([video_id])
                        
                        if os.path.exists(temp_mp3):
                            # Verify downloaded file duration matches (use same tolerance as pre-download check)
                            try:
                                audio = MutagenMP3(temp_mp3)
                                downloaded_duration = audio.info.length
                                if official_duration:
                                    duration_diff = abs(downloaded_duration - official_duration)
                                    # Use same extended tolerance logic as pre-download check
                                    base_tolerance = max(2.0, official_duration * 0.05)
                                    if official_duration > 240:  # Tracks longer than 4 minutes
                                        extended_tolerance = max(base_tolerance, official_duration * 0.15)
                                        max_tolerance = min(extended_tolerance, 40.0)
                                    elif official_duration > 180:  # Tracks longer than 3 minutes
                                        extended_tolerance = max(base_tolerance, official_duration * 0.10)
                                        max_tolerance = min(extended_tolerance, 30.0)
                                    else:
                                        max_tolerance = min(base_tolerance, 10.0)
                                    if duration_diff > max_tolerance:
                                        print(f" Downloaded file duration mismatch: {downloaded_duration:.1f}s vs {official_duration:.1f}s (diff: {duration_diff:.1f}s, tolerance: {max_tolerance:.1f}s)")
                                        os.remove(temp_mp3)
                                        continue
                            except Exception:
                                pass
                            
                            os.rename(temp_mp3, final_path)
                            return uid, final_path
                        else:
                            print(f" MP3 not found after download: {temp_mp3}")
                            continue
                            
            except Exception as e:
                print(f" Candidate verification failed: {e}")
                continue
        
        # If all strict checks failed, try fallback with relaxed criteria
        if best_fallback and best_fallback_score >= 75:
            print(f" ⚠️ All strict checks failed, trying best fallback candidate (score: {best_fallback_score:.1f}%)")
            video_id = best_fallback["id"]
            try:
                ydl_opts_info = {
                    "quiet": True,
                    "no_warnings": True,
                    "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                }
                
                with YoutubeDL(ydl_opts_info) as ydl:
                    info = ydl.extract_info(video_id, download=False)
                    
                    # Relaxed duration check: ±10 seconds OR ±10% (whichever is larger, max 20 seconds)
                    if official_duration and info.get("duration"):
                        candidate_duration = info.get("duration")
                        duration_diff = abs(candidate_duration - official_duration)
                        tolerance = max(10.0, official_duration * 0.10)
                        max_tolerance = min(tolerance, 20.0)
                        
                        if duration_diff > max_tolerance:
                            print(f" Fallback candidate also fails duration check: {duration_diff:.1f}s > {max_tolerance:.1f}s")
                            return None, None
                    
                    # Download fallback candidate
                    print(f" ✓ Using fallback candidate: {info.get('title', 'Unknown')}")
                    os.makedirs("MP3", exist_ok=True)
                    ydl_opts = {
                        "quiet": True,
                        "no_warnings": True,
                        "format": "bestaudio/best",
                        "outtmpl": "%(uploader)s - %(id)s.%(ext)s",
                        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
                        "postprocessors": [{
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192"
                        }]
                    }
                    
                    with YoutubeDL(ydl_opts) as ydl:
                        uid = f"{info['uploader']} - {info['id']}"
                        temp_mp3 = f"{uid}.mp3"
                        final_path = f"MP3/{uid}.mp3"
                        
                        ydl.download([video_id])
                        
                        if os.path.exists(temp_mp3):
                            os.rename(temp_mp3, final_path)
                            return uid, final_path
            except Exception as e:
                print(f" Fallback candidate download failed: {e}")
        
        print(" All candidates failed verification (including fallback)")
        return None, None

    def download_thumbnail(self, url, artist=None, title=None, bpm=None, key=None):
        try:
            if not url:
                print(" Thumbnail URL is None/empty")
                return None
                
            artist = artist or self.track_info.get("artist", "Unknown")
            title = title or self.track_info.get("name", "Unknown")
            bpm = bpm or self.track_info.get("tempo", 0)
            key = key or self.track_info.get("key", "Unknown")

            folder_title = self.sanitize_name(f"{artist} {title} [BPM {bpm} {key}]")
            thumb_dir = os.path.join("Thumbnails", folder_title)
            os.makedirs(thumb_dir, exist_ok=True)

            thumb_path = os.path.join(thumb_dir, "cover.png")

            if os.path.exists(thumb_path):
                print(f"usng cached thumbnail: {thumb_path}")
                return thumb_path

            print(f" Downloading thumbnail from: {url}")
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            
            with open(thumb_path, "wb") as f:
                f.write(response.content)

            print(f" Thumbnail saved: {thumb_path}")
            return thumb_path
        except Exception as e:
            print(f" Thumbnail download failed: {e} (URL: {url})")
            return None

    def get_track_info(self, track_id):
        if self.track_info:
            print(f"[CACHE] Reusing track info for {self.session_id}")
            return self.track_info

        try:
            track = self.sp.track(track_id)
            artist = track['artists'][0]['name']
            title = track['name']
            album_images = track["album"]["images"]
            img_url = album_images[0]["url"] if album_images else ""

            genre_items = self.sp.search(q=f"artist:{artist}", type="artist").get("artists", {}).get("items", [])
            genre = genre_items[0]["genres"][0] if genre_items and genre_items[0].get("genres") else "Other"

            bpm = self.args.get("bpm") or self.args.get("track_info", {}).get("tempo", 0)
            key = self.args.get("key") or self.args.get("track_info", {}).get("key", "Unknown")

            if not bpm or not key or key == "Unknown":
                try:
                    feat = self.sp.audio_features([track_id])[0]
                    if not bpm:
                        bpm = round(feat.get('tempo', 0))
                    if not key or key == "Unknown":
                        key_index = feat.get('key', 0)
                        key_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
                        key = key_names[key_index] if 0 <= key_index < len(key_names) else key
                except Exception as e:
                    print(f" Spotify fallback failed: {e}")

            # Get duration from Spotify track
            duration_seconds = track.get("duration_ms", 0) / 1000.0 if track.get("duration_ms") else None
            
            track_info = {
                "name": title,
                "artist": artist,
                "album": track["album"]["name"],
                "category": [genre.title().replace(" ", "_")],
                "release_date": track["album"]["release_date"],
                "popularity": track["popularity"],
                "img": img_url,
                "tempo": bpm,
                "key": key,
                "duration_seconds": duration_seconds,
                "id": track_id
            }

            self.track_info = track_info
            print(f" Final track info: BPM={track_info.get('tempo')} | Key={track_info.get('key')}\n")
            return track_info

        except Exception as e:
            print(f" Track info error: {e}")
            return None

    def trim_audio(self, path: str, duration: int) -> str:
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_file(path)
            trimmed = audio[:duration * 1000]
            trimmed.export(path, format="mp3")
            return path
        except Exception as e:
            print(f" Failed to trim audio: {e}")
            return path

    def stems_already_exist(self):
        if not self.stem_base_path:
            return False
        try:
            files = os.listdir(self.stem_base_path)
            return len([f for f in files if f.endswith(".mp3")]) >= 4
        except Exception:
            return False