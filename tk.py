from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional, Dict
from concurrent.futures import ThreadPoolExecutor
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import asyncio
import shutil
import os
import json
import traceback
import copy
import webbrowser
import threading
import logging
from datetime import datetime, timedelta

# Suppress spotipy HTTP error logging (especially 404s)
logging.getLogger("spotipy").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from shared_state import set_progress, get_progress, delete_progress
from dispatch_download import process_all_tracks

app = FastAPI(title="Stem Splitter & YouTube Scheduler")
templates = Jinja2Templates(directory="templates")

CLIENT_ID = "fbf9f3a2da0b44758a496ca7fa8a9290"
CLIENT_SECRET = "c47363028a7c478285fe1e27ecb4428f"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UI_CHANNEL_DISPLAY_MAP = {
    "mainchannel": "Main",
    "sgs2": "SGS 2",
    "songotdrums": "Drum",
    "songotacapellas": "Acappella",
    "samplesplit": "Sample Split",
    "tiktok": "Tik Tok",
}

class StemRequest(BaseModel):
    """Request model for stem splitting with YouTube scheduling and comments."""
    track_id: str
    channels: List[str]
    selected_stems: Optional[Dict[str, List[str]]] = None  # {channel_key: [stem1, stem2, ...]}
    yt: bool = True  # Section 5: Default to ON
    tiktok: bool = False  # Upload to TikTok
    render_videos: bool = False  # Create MP4s even without upload (for local use)
    ec2: bool = False
    trim: bool = False
    dry_run: bool = False
    genre: Optional[str] = None

    # Metadata fields (auto-fill if empty)
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    privacy: str = "public"  # Default to public
    made_for_kids: bool = False
    monetize: bool = False

    # Per-channel comments
    comments: Optional[Dict[str, str]] = None  # {channel_key: comment_text}

    # Scheduling fields
    startTime: Optional[str] = None
    interval: Optional[str] = "Every Hour"
    tz: Optional[str] = "America/Chicago"

    # Legacy compatibility
    schedule_start_time: Optional[str] = None
    schedule_interval_minutes: Optional[int] = 60
    timezone: Optional[str] = "UTC"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/progress/{session_id}")
async def progress_stream(session_id: str):
    async def event_generator():
        while True:
            data = get_progress(session_id)
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/reset-progress/{session_id}")
def reset_progress(session_id: str):
    delete_progress(session_id)
    return {"message": "Progress reset for " + session_id}


def extract_spotify_id(raw: str) -> str:
    return raw.split("/")[-1].split("?")[0] if "spotify.com" in raw else raw


def get_all_track_ids(playlist_id: str) -> List[str]:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    ))
    ids = []
    offset = 0
    while True:
        items = sp.playlist_tracks(playlist_id, limit=100, offset=offset).get("items", [])
        if not items:
            break
        for item in items:
            track = item.get("track")
            if track and track.get("id"):
                ids.append(track["id"])
        offset += 100
    return ids


@app.post("/cleanup")
async def cleanup_files():
    try:
        for folder in ["MP3", "Separated", "Thumbnails", "tunebat_debug"]:
            path = os.path.join(BASE_DIR, folder)
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
        cache_file = os.path.join(BASE_DIR, ".cache")
        if os.path.exists(cache_file):
            os.remove(cache_file)
        return {"status": "success", "message": "Cleaned up!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.post("/split")
def split_and_schedule(request: StemRequest):
    try:
        input_id = extract_spotify_id(request.track_id)

        # --- Convert selected_stems and comments keys from UI IDs to channel_keys ---
        # Frontend sends stems/comments keyed by UI ID (e.g., "main", "backup"), but backend needs channel_keys
        from dispatch_download import UI_TO_CHANNEL_MAP
        converted_stems = {}
        if request.selected_stems:
            for ui_id, stems in request.selected_stems.items():
                channel_key = UI_TO_CHANNEL_MAP.get(ui_id, ui_id)
                converted_stems[channel_key] = stems
        
        converted_comments = {}
        if request.comments:
            for ui_id, comment_text in request.comments.items():
                channel_key = UI_TO_CHANNEL_MAP.get(ui_id, ui_id)
                converted_comments[channel_key] = comment_text
        
        # --- Shared arguments for all tracks ---
        # Auto-fill: use provided values or defaults
        # Section 5: Default upload to ON if not explicitly set
        upload_enabled = request.yt if hasattr(request, 'yt') and request.yt is not None else True
        tiktok_enabled = getattr(request, 'tiktok', False)
        shared_args = {
            "yt": upload_enabled,
            "tiktok": tiktok_enabled,
            "render_videos": request.render_videos if hasattr(request, 'render_videos') else False,
            "ec2": request.ec2,
            "trim": request.trim,
            "dry_run": request.dry_run,
            # Spotify credentials
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            # Privacy defaults to "public" unless explicitly set
            "privacy": request.privacy or "public",
            "made_for_kids": request.made_for_kids or False,
            # Tags: use provided or default empty list (populated from DEFAULT_TAGS if needed)
            "tags": request.tags or [],
            # Description: use provided or empty string (auto-filled by YouTube upload handler)
            "description": request.description or "",
            "monetize": request.monetize or False,
            "genre": request.genre or "Hip-Hop",
            "trim_track": getattr(request, "trim_track", False),
            "trim_length": getattr(request, "trim_length", 72),
            # Per-channel comments: map of channel_key -> comment_text
            "comments": converted_comments,
            # Selected stems per channel (keyed by channel_key, not UI ID)
            "selected_stems": converted_stems,
        }

        # --- Scheduling Fix ---
        start_time_str = getattr(request, "schedule_start_time", None) or getattr(request, "startTime", None)
        tz_name = getattr(request, "tz", None) or getattr(request, "timezone", None) or "America/Chicago"
        interval_minutes = getattr(request, "schedule_interval_minutes", None)
        try:
            interval_minutes = int(interval_minutes) if interval_minutes is not None else None
        except (TypeError, ValueError):
            interval_minutes = None

        interval_label_raw = getattr(request, "interval", None) or getattr(request, "schedule_mode", None)
        interval_label = interval_label_raw.lower() if isinstance(interval_label_raw, str) else ""

        if not interval_minutes or interval_minutes <= 0:
            interval_minutes = 60
            if interval_label:
                if "2" in interval_label:
                    interval_minutes = 120
                elif "4" in interval_label:
                    interval_minutes = 240
                elif "day" in interval_label or "daily" in interval_label:
                    interval_minutes = 1440
            else:
                interval_label = "every hour"

        label_map = {
            60: "Every Hour",
            120: "Every 2 Hours",
            240: "Every 4 Hours",
            1440: "Daily",
        }
        interval_label_display = label_map.get(interval_minutes, f"Every {interval_minutes} Minutes")
        print(f" Interval set to {interval_minutes} minutes ({interval_label_display})")

        normalized_start = ""
        if start_time_str:
            normalized_start = start_time_str.strip().replace("T", " ")
            if len(normalized_start) >= 16:
                normalized_start = normalized_start[:16]

        shared_args["base_start_local"] = normalized_start
        shared_args["interval_minutes"] = interval_minutes
        shared_args["schedule_mode"] = interval_label_display
        shared_args["tz"] = tz_name

        # --- Initialize Spotify client ---
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET
        ))

        # --- Automatically detect if input is track, playlist, or artist ---
        # Try track first (most common), then playlist, then artist
        track_ids = []
        is_playlist = False
        detected_type = None
        
        # Clean the input ID first
        clean_input_id = input_id.strip()
        if clean_input_id.startswith("spotify:track:"):
            clean_input_id = clean_input_id.replace("spotify:track:", "").strip()
        elif clean_input_id.startswith("spotify:playlist:"):
            clean_input_id = clean_input_id.replace("spotify:playlist:", "").strip()
        elif clean_input_id.startswith("spotify:artist:"):
            clean_input_id = clean_input_id.replace("spotify:artist:", "").strip()
        elif "/track/" in clean_input_id:
            clean_input_id = clean_input_id.split("/track/")[-1].split("?")[0].strip()
        elif "/playlist/" in clean_input_id:
            clean_input_id = clean_input_id.split("/playlist/")[-1].split("?")[0].strip()
        elif "/artist/" in clean_input_id:
            clean_input_id = clean_input_id.split("/artist/")[-1].split("?")[0].strip()
        
        # Try single track first (most common use case)
        try:
            track_info = sp.track(clean_input_id)
            track_ids = [clean_input_id]
            is_playlist = False
            detected_type = "track"
            # Detected single track
        except Exception as track_error:
            # Track lookup failed, try playlist
            try:
                playlist = sp.playlist(clean_input_id)
                track_ids = get_all_track_ids(clean_input_id)
                is_playlist = True
                detected_type = "playlist"
                # Detected playlist
            except Exception as playlist_error:
                # Playlist lookup failed, try artist
                try:
                    artist = sp.artist(clean_input_id)
                    artist_name = artist.get('name', 'Unknown Artist')
                    detected_type = "artist"
                    # Detected artist
                    
                    # Get artist's top tracks
                    top_tracks = sp.artist_top_tracks(clean_input_id, country='US')
                    if top_tracks and top_tracks.get('tracks'):
                        track_ids = [track['id'] for track in top_tracks['tracks'] if track.get('id')]
                        # Found top tracks
                    else:
                        # Fallback: get tracks from artist's albums
                        albums = sp.artist_albums(clean_input_id, album_type='album', limit=5)
                        track_ids = []
                        for album in albums.get('items', []):
                            album_tracks = sp.album_tracks(album['id'])
                            for track_item in album_tracks.get('items', []):
                                if track_item.get('id'):
                                    track_ids.append(track_item['id'])
                        # Found tracks from albums
                    
                    if not track_ids:
                        raise ValueError(f"No tracks found for artist {artist_name}")
                        
                except Exception as artist_error:
                    # All lookups failed - provide helpful error message
                    error_msg = f"Could not identify input as track, playlist, or artist: {clean_input_id}"
                    print(f"[ERROR] {error_msg}")
                    print(f"[ERROR] Track error: {track_error}")
                    print(f"[ERROR] Playlist error: {playlist_error}")
                    print(f"[ERROR] Artist error: {artist_error}")
                    raise ValueError(error_msg)
        
        if not track_ids:
            raise ValueError(f"No tracks found for input: {clean_input_id}")
        
        # Processing tracks

        batch = []
        sessions = []
        
        # Received channels

        for idx, track_id in enumerate(track_ids):
            session_id = f"{input_id}__{track_id}"
            sessions.append(session_id)
            
            # Track IDs are already cleaned from the detection phase above
            # Just ensure it's a valid format
            clean_track_id = track_id.strip()
            
            # Clean any remaining invalid characters (keep alphanumeric, hyphens, underscores)
            import re
            clean_track_id = re.sub(r'[^a-zA-Z0-9_-]', '', clean_track_id)
            
            if not clean_track_id:
                raise ValueError(f"Empty track ID provided at index {idx}")
            
            # Fetch track info (should work since we already validated it during detection)
            try:
                info = sp.track(clean_track_id)
            except Exception as track_error:
                # If it fails here, something went wrong (shouldn't happen after detection)
                print(f"[ERROR] Failed to fetch track info for {clean_track_id}: {track_error}")
                raise ValueError(f"Could not fetch track info for ID: {clean_track_id}")
            
            artist, title = info["artists"][0]["name"], info["name"]
            batch.append((title, artist, clean_track_id))
            channel_displays = [UI_CHANNEL_DISPLAY_MAP.get(ch, ch) for ch in request.channels]
            set_progress(session_id, {
                "message": " Preparing track metadata...",
                "percent": 0,
                "meta": {
                    "track_id": track_id,
                    "title": title,
                    "artist": artist,
                    "channels": channel_displays,
                    "playlist_id": input_id,
                    "index": idx + 1,
                    "total_tracks": len(track_ids)
                }
            })

        def run_full_pipeline():
            # BPM/Key will be fetched from Tunebat after download (in dispatch_stem_processing)
            # Build full track_info objects for playlist tracks to ensure consistency
            
            per_track_args = {}
            for idx, (title, artist, track_id) in enumerate(batch):
                args = copy.deepcopy(shared_args)
                # BPM/Key will be set from Tunebat after download
                # If not fetched, defaults to 0/"Unknown" which will trigger Tunebat fetch
                args["bpm"] = args.get("bpm", 0)
                args["key"] = args.get("key", "Unknown")
                args["global_artist_index"] = idx
                args["track_title"] = title
                args["track_artist"] = artist
                
                # Build full track_info object for playlist tracks
                # This ensures consistency and includes all necessary fields (duration, id, etc.)
                try:
                    # Fetch full track info from Spotify to get duration and other metadata
                    track = sp.track(track_id)
                    album_images = track.get("album", {}).get("images", [])
                    img_url = album_images[0]["url"] if album_images else ""
                    
                    # Get genre
                    genre_items = sp.search(q=f"artist:{artist}", type="artist").get("artists", {}).get("items", [])
                    genre = genre_items[0]["genres"][0] if genre_items and genre_items[0].get("genres") else "Other"
                    
                    # Get duration from Spotify track
                    duration_seconds = track.get("duration_ms", 0) / 1000.0 if track.get("duration_ms") else None
                    
                    # Build complete track_info object matching ContentBase.get_track_info format
                    track_info = {
                        "name": title,
                        "artist": artist,
                        "album": track.get("album", {}).get("name", ""),
                        "category": [genre.title().replace(" ", "_")],
                        "release_date": track.get("album", {}).get("release_date", ""),
                        "popularity": track.get("popularity", 0),
                        "img": img_url,
                        "tempo": 0,  # Will be set from Tunebat
                        "key": "Unknown",  # Will be set from Tunebat
                        "duration_seconds": duration_seconds,
                        "id": track_id
                    }
                    
                    # Pass track_info in args so it's available in dispatch_stem_processing
                    args["track_info"] = track_info
                except Exception as e:
                    print(f"[WARNING] Failed to build track_info for {track_id}: {e}")
                    # Fallback: create minimal track_info
                    args["track_info"] = {
                        "name": title,
                        "artist": artist,
                        "id": track_id,
                        "duration_seconds": None,
                        "tempo": 0,
                        "key": "Unknown"
                    }

                start_local = args.get("base_start_local", "")
                interval_minutes = int(args.get("interval_minutes", 0) or 0)
                if start_local:
                    parsed = None
                    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
                        try:
                            parsed = datetime.strptime(start_local, fmt)
                            break
                        except ValueError:
                            continue
                    if parsed:
                        scheduled_dt = parsed + timedelta(minutes=interval_minutes * idx)
                        args["publish_at"] = scheduled_dt.strftime("%Y-%m-%d %H:%M")
                    else:
                        args.pop("publish_at", None)
                else:
                    args.pop("publish_at", None)
                per_track_args[track_id] = args

            # --- Run all tracks sequentially (1 song → all stems) ---
            # Starting processing
            process_all_tracks(
                track_ids,
                request.channels,
                args=shared_args,
                per_track_args=per_track_args,
                session_id=input_id,
                max_concurrent=1
            )

        threading.Thread(target=run_full_pipeline).start()

        channel_displays = [UI_CHANNEL_DISPLAY_MAP.get(ch, ch) for ch in request.channels]
        return {
            "message": "Playlist processing started" if is_playlist else "Single track processing started",
            "tracks_processed": len(track_ids),
            "channels": channel_displays,
            "session_ids": sessions
        }
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"[ERROR] Failed to process request: {e}")
        print(f"[ERROR] Traceback: {error_details}")
        raise HTTPException(status_code=500, detail=f"Failed to process request: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading
    import time

    def open_browser():
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:8000")

    threading.Thread(target=open_browser).start()

    print(" Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)