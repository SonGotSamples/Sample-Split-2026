from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional
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
from datetime import datetime, timedelta

from shared_state import set_progress, get_progress, delete_progress
from dispatch_download import process_all_tracks
from tunebat_helper import get_bpm_key

app = FastAPI(title="Stem Splitter & YouTube Scheduler")
templates = Jinja2Templates(directory="templates")

CLIENT_ID = "fbf9f3a2da0b44758a496ca7fa8a9290"
CLIENT_SECRET = "c47363028a7c478285fe1e27ecb4428f"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

class StemRequest(BaseModel):
    track_id: str
    channels: List[str]
    yt: bool = False
    ec2: bool = False
    trim: bool = False
    genre: Optional[str] = None

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
        for folder in ["MP3", "separated", "Thumbnails", "tunebat_debug"]:
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
    input_id = extract_spotify_id(request.track_id)

    # --- Shared arguments for all tracks ---
    shared_args = {
        "yt": request.yt,
        "ec2": request.ec2,
        "trim": request.trim,
        "privacy": getattr(request, "privacy", "private"),
        "made_for_kids": getattr(request, "made_for_kids", False),
        "tags": getattr(request, "tags", []),
        "description": getattr(request, "description", ""),
        "monetize": getattr(request, "monetize", False),
        "genre": (getattr(request, "genre", None) or "Hip-Hop"),
        "trim_track": getattr(request, "trim_track", False),
        "trim_length": getattr(request, "trim_length", 72),
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
    print(f"🕐 Interval set to {interval_minutes} minutes ({interval_label_display})")

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

    # --- Determine if input is playlist or single track ---
    try:
        playlist = sp.playlist(input_id)
        track_ids = get_all_track_ids(input_id)
        is_playlist = True
    except:
        track_ids = [input_id]
        is_playlist = False

    batch = []
    sessions = []

    for idx, track_id in enumerate(track_ids):
        session_id = f"{input_id}__{track_id}"
        sessions.append(session_id)
        info = sp.track(track_id)
        artist, title = info["artists"][0]["name"], info["name"]
        batch.append((title, artist, track_id))
        set_progress(session_id, {
            "message": "🟡 Preparing track metadata...",
            "percent": 0,
            "meta": {
                "track_id": track_id,
                "title": title,
                "artist": artist,
                "channels": request.channels,
                "playlist_id": input_id,
                "index": idx + 1,
                "total_tracks": len(track_ids)
            }
        })

    def run_full_pipeline():
        bpm_key_map = {}

        for title, artist, track_id in batch:
            bpm, key = get_bpm_key(title, artist, track_id)
            bpm_key_map[track_id] = (bpm, key)

        per_track_args = {}
        for idx, (title, artist, track_id) in enumerate(batch):
            bpm, key = bpm_key_map.get(track_id, (0, "Unknown"))
            args = copy.deepcopy(shared_args)
            args["bpm"] = bpm
            args["key"] = key
            args["global_artist_index"] = idx

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
        process_all_tracks(
            track_ids,
            request.channels,
            args=shared_args,
            per_track_args=per_track_args,
            session_id=input_id,
            max_concurrent=1
        )

    threading.Thread(target=run_full_pipeline).start()

    return {
        "message": "Playlist processing started" if is_playlist else "Single track processing started",
        "tracks_processed": len(track_ids),
        "channels": request.channels,
        "session_ids": sessions
    }


if __name__ == "__main__":
    import uvicorn
    import webbrowser
    import threading
    import time

    def open_browser():
        time.sleep(1)
        webbrowser.open("http://127.0.0.1:8000")

    threading.Thread(target=open_browser).start()

    print("🌐 Starting FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)