# File: content_base.py
import os
import re
import json
import time
import requests
from typing import Optional, Dict, List, Any
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
from fuzzy_matcher import FuzzyMatcher

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

        # Initialization messages suppressed for cleaner output

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
        # Progress update suppressed

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
        # Step complete

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
        Download audio using direct YouTube search strategy:
        1. Search YouTube directly with focused queries
        2. Filter to Topic channels only
        3. Fuzzy match track to video
        4. Download matched video
        """
        from mutagen.mp3 import MP3 as MutagenMP3
        from fuzzy_matcher import find_best_topic_video_for_track
        
        # Get official duration from track info
        official_duration = None
        if self.track_info:
            official_duration = self.track_info.get("duration_seconds")
            if not official_duration:
                try:
                    track_id = self.track_info.get("id")
                    if track_id:
                        track = self.sp.track(track_id)
                        official_duration = track.get("duration_ms", 0) / 1000.0
                except Exception:
                    pass
        
        if official_duration:
            print(f" Official duration from Spotify: {official_duration:.1f}s")
        
        # Get BPM and key from track info if available (from Tunebat)
        bpm = self.track_info.get("tempo") if self.track_info else None
        key = self.track_info.get("key") if self.track_info else None
        
        if bpm and bpm > 0:
            print(f" Using BPM from Tunebat: {bpm}")
        if key and key != "Unknown":
            print(f" Using Key from Tunebat: {key}")
        
        # Step 1: Search YouTube and find best video match
        # Fuzzy matching uses: title + artist + duration + BPM + key
        match_id, score = find_best_topic_video_for_track(
            title=title,
            artist=artist,
            duration_seconds=official_duration,
            bpm=bpm,
            key=key,
            max_results=25,
        )
        
        if not match_id:
            print(f" No matching video found (best score: {score:.1f}%)")
            return None, None
        
        print(f" Matched video: {match_id} (score: {score:.1f}%)")
        
        # Step 2: Download matched video
        try:
            ydl_opts_info = {
                "quiet": True,
                "no_warnings": True,
                "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
            }
            
            with YoutubeDL(ydl_opts_info) as ydl:
                info = ydl.extract_info(match_id, download=False)
            
            # Verify duration if available
            if official_duration and info.get("duration"):
                candidate_duration = info.get("duration")
                duration_diff = abs(candidate_duration - official_duration)
                base_tolerance = max(2.0, official_duration * 0.05)
                if official_duration > 240:
                    max_tolerance = min(max(base_tolerance, official_duration * 0.20), 60.0)
                elif official_duration > 180:
                    max_tolerance = min(max(base_tolerance, official_duration * 0.18), 50.0)
                else:
                    max_tolerance = min(base_tolerance, 10.0)
                
                if duration_diff > max_tolerance:
                    print(f" Duration mismatch: {candidate_duration:.1f}s vs {official_duration:.1f}s (diff: {duration_diff:.1f}s, tolerance: {max_tolerance:.1f}s)")
                    return None, None
            
            # Download video
            print(f" Downloading: {info.get('title', 'Unknown')}")
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
                
                ydl.download([match_id])
                
                if os.path.exists(temp_mp3):
                    # Verify downloaded file duration
                    if official_duration:
                        try:
                            audio = MutagenMP3(temp_mp3)
                            downloaded_duration = audio.info.length
                            duration_diff = abs(downloaded_duration - official_duration)
                            base_tolerance = max(2.0, official_duration * 0.05)
                            if official_duration > 240:
                                max_tolerance = min(max(base_tolerance, official_duration * 0.20), 60.0)
                            elif official_duration > 180:
                                max_tolerance = min(max(base_tolerance, official_duration * 0.18), 50.0)
                            else:
                                max_tolerance = min(base_tolerance, 10.0)
                            if duration_diff > max_tolerance:
                                print(f" Downloaded file duration mismatch: {downloaded_duration:.1f}s vs {official_duration:.1f}s")
                                os.remove(temp_mp3)
                                return None, None
                        except Exception:
                            pass
                    
                    os.rename(temp_mp3, final_path)
                    print(f" ✓ Downloaded: {info.get('title', 'Unknown')}")
                    return uid, final_path
                else:
                    print(f" MP3 not found after download: {temp_mp3}")
                    return None, None
                    
        except Exception as e:
            print(f" Download failed: {e}")
            import traceback
            traceback.print_exc()
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
            # Using cached track info
            return self.track_info

        try:
            # Suppress spotipy HTTP error logging for 404s
            import logging
            spotipy_logger = logging.getLogger("spotipy")
            original_level = spotipy_logger.level
            spotipy_logger.setLevel(logging.CRITICAL)  # Suppress ERROR level
            
            try:
                track = self.sp.track(track_id)
            finally:
                spotipy_logger.setLevel(original_level)  # Restore original level
            artist = track['artists'][0]['name']
            title = track['name']
            album_images = track["album"]["images"]
            img_url = album_images[0]["url"] if album_images else ""

            genre_items = self.sp.search(q=f"artist:{artist}", type="artist").get("artists", {}).get("items", [])
            genre = genre_items[0]["genres"][0] if genre_items and genre_items[0].get("genres") else "Other"

            bpm = self.args.get("bpm") or self.args.get("track_info", {}).get("tempo", 0)
            key = self.args.get("key") or self.args.get("track_info", {}).get("key", "Unknown")

            # Note: BPM/Key are fetched from Tunebat in dispatch_stem_processing
            # Tunebat provides official BPM/Key data matching their website

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
            # Final track info message suppressed for cleaner output
            return track_info

        except Exception as e:
            # Suppress 404 errors (track not found) - don't spam console
            error_str = str(e).lower()
            if "404" in error_str or "not found" in error_str or "resource not found" in error_str:
                print(f" Track not found: {track_id} (skipping)")
            else:
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