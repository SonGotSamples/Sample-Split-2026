# dispatch_download.py
import os
import sys
import importlib
import traceback
import torch
import subprocess
import threading
import time

def validate_stems(base_dir: str):
    required = ["vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"]
    problems = {}
    for s in required:
        if not os.path.exists(os.path.join(base_dir, s)):
            problems[s] = "missing"
    return {"ok": not problems, "problems": problems}

from random import uniform
from concurrent.futures import ThreadPoolExecutor
from shared_state import set_progress, get_progress
from content_base import ContentBase, CHANNEL_NAME_MAP
from tunebat_helper import get_bpm_key

# --------------------------------------------------------------------------------------
# GPU Auto-Detection
# --------------------------------------------------------------------------------------
def get_optimal_device():
    """Auto-detect available GPU, fallback to CPU with detailed logging."""
    if torch.cuda.is_available():
        device_count = torch.cuda.device_count()
        device_name = torch.cuda.get_device_name(0)
        print(f" GPU detected: {device_name} (Found {device_count} device(s))")
        return "cuda:0"
    else:
        print(" No GPU available — Using CPU (install CUDA/cuDNN for GPU acceleration)")
        return "cpu"

# --------------------------------------------------------------------------------------
# Path setup
# --------------------------------------------------------------------------------------
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

stem_processing_path = os.path.join(project_root, "stem_processing")
if stem_processing_path not in sys.path:
    sys.path.insert(0, stem_processing_path)

# --------------------------------------------------------------------------------------
# Channel routing
# --------------------------------------------------------------------------------------
UI_TO_CHANNEL_MAP = {
    "main": "main_channel",
    "backup": "sgs_2",
    "drum": "son_got_drums",
    "vocal": "son_got_acapellas",
    "acappella": "son_got_acapellas",
    "samplesplit": "sample_split",
    "tiktok": "tiktok_channel",
    "mainchannel": "main_channel",
    "sgs2": "sgs_2",
    "songotdrums": "son_got_drums",
    "songotacapellas": "son_got_acapellas",
}

CHANNEL_MODULE_MAP = {
    "main_channel": ("content_download_main", "Content_download_main"),
    "sgs_2": ("content_download_main", "Content_download_main"),
    "son_got_drums": ("content_download_main", "Content_download_main"),
    "son_got_acapellas": ("content_download_main", "Content_download_main"),
    "sample_split": ("content_download_main", "Content_download_main"),
    "tiktok_channel": ("content_download_main", "Content_download_main"),
}

# --------------------------------------------------------------------------------------
# Helpers: pre-process, demucs runners, fallbacks
# --------------------------------------------------------------------------------------

# Model order: htdemucs_ft (best quality) first, then fallbacks
# htdemucs_ft: high-quality separation with improved instrument isolation
# htdemucs_6s: faster, medium quality fallback
# htdemucs: legacy fallback for compatibility
FALLBACK_MODELS = ["htdemucs_ft", "htdemucs_6s", "htdemucs"]

def _prepared_copy_path(uid: str) -> str:
    os.makedirs("MP3", exist_ok=True)
    return os.path.join("MP3", f"{uid}__prep.mp3")

def prepare_input_for_demucs(src_mp3: str, prepared_path: str) -> bool:
    """
    Pre-process input to reduce extraction failures:
      - force 44.1kHz, stereo
      - normalize loudness approx to -14 LUFS (ffmpeg loudnorm)
    Returns True if prepared file is created and looks non-trivial.
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", src_mp3,
            "-ac", "2",
            "-ar", "44100",
            "-af", "loudnorm=I=-14:TP=-2:LRA=11",
            prepared_path
        ]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        return os.path.exists(prepared_path) and os.path.getsize(prepared_path) > 150_000
    except Exception as e:
        print(f"[PREP] ffmpeg pre-process failed: {e}")
        return False

def demucs_outdir_for_input(model_name: str, input_mp3: str) -> str:
    """Demucs names output folder after the input basename (e.g., uid OR uid__prep)."""
    base = os.path.splitext(os.path.basename(input_mp3))[0]
    return os.path.join("Separated", model_name, base)

def run_demucs_with_model_stream(mp3_path: str, device: str, model_name: str):
    """
    Run demucs and stream stdout to terminal in real time (so clients see progress).
    Filters out progress bar lines to reduce clutter.
    Returns (returncode, tail_text).
    """
    try:
        # Running Demucs model
        process = subprocess.Popen(
            ["demucs", "--mp3", "-n", model_name, "--shifts", "0", "-d", device, mp3_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        last_lines = []
        for line in iter(process.stdout.readline, ""):
            if line:
                # Filter out progress bar lines (contain %| or progress indicators)
                # Keep important messages like errors, warnings, or completion status
                stripped = line.strip()
                is_progress_bar = (
                    "%|" in stripped or
                    "seconds/s" in stripped or
                    (stripped and stripped[0] in "0123456789" and "%" in stripped) or
                    ("/" in stripped and "[" in stripped and "]" in stripped and any(c.isdigit() for c in stripped))
                )
                
                if not is_progress_bar:
                    # Only print non-progress-bar lines
                    sys.stdout.write(f"[DEMUCS][{model_name}] {line}")
                    sys.stdout.flush()
                
                # Always keep last lines for error detection (even progress bars)
                last_lines.append(line)
                if len(last_lines) > 200:
                    last_lines.pop(0)

        process.stdout.close()
        process.wait()
        rc = process.returncode
        # Demucs finished
        return rc, "".join(last_lines)
    except Exception as e:
        print(f"[DEMUCS] Failed to invoke demucs for {model_name}: {e}")
        return None, ""

def run_demucs_with_fallbacks(mp3_path: str, device: str, session_id: str):
    """
    Try models in FALLBACK_MODELS until validation passes.
    Returns (model_used, stem_base_path, validation) or (None, None, {"ok": False, ...})
    """
    for idx, model in enumerate(FALLBACK_MODELS, start=1):
        set_progress(session_id, {"message": f" Separating with {model} (attempt {idx})…", "percent": 12})

        rc, tail = run_demucs_with_model_stream(mp3_path, device, model)
        out_dir = demucs_outdir_for_input(model, mp3_path)

        if rc is None:
            # Model failed, trying next
            continue

        if rc != 0 or not os.path.exists(out_dir):
            # Model failed
            # If GPU OOM, try CPU once
            if device.startswith("cuda") and ("CUDA out of memory" in tail or "CUDA error" in tail):
                # CUDA OOM, retrying on CPU
                rc2, _ = run_demucs_with_model_stream(mp3_path, "cpu", model)
                if rc2 == 0 and os.path.exists(out_dir):
                    v = validate_stems(out_dir)
                    if v.get("ok"):
                        return model, out_dir, v
            continue

        # Validate stems
        validation = validate_stems(out_dir)
        if validation.get("ok"):
            # Stems validated
            return model, out_dir, validation

        # Validation problems, trying next model
        set_progress(session_id, {
            "message": f"🔁 Fallback: {model} produced weak stems; trying next…",
            "percent": 20
        })

    return None, None, {"ok": False, "problems": {"_": "all_models_failed"}}

# --------------------------------------------------------------------------------------
# Utility
# --------------------------------------------------------------------------------------
def is_sane_audio(path: str) -> bool:
    """Catch empty/corrupt downloads early."""
    try:
        return os.path.getsize(path) > 150_000  # ~150KB
    except Exception:
        return False

def recover_stem_dir(universal_id: str) -> str | None:
    """Recover the stems folder across known models and __prep/no-suffix variants."""
    if not universal_id:
        return None
    candidates = []
    for model in FALLBACK_MODELS:
        for suffix in ("", "__prep"):
            cand = os.path.abspath(os.path.join("Separated", model, f"{universal_id}{suffix}"))
            candidates.append(cand)
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None

# --------------------------------------------------------------------------------------
# Main dispatch
# --------------------------------------------------------------------------------------
def dispatch_stem_processing(track_id: str, selected_channels: list, args: dict, session_id: str = "default"):
    """
    Args may optionally include:
      - start_jitter_sec: float|tuple -> random initial sleep to stagger concurrent runs (default (0.5, 2.0))
      - per_track_cooldown_sec: float|tuple -> sleep after finishing a track (default 0)
      - retry_backoff_sec: float|tuple -> sleep before a single re-download attempt (default (15, 30))
    """
    # Processing channels
    
    # Determine upload destinations (respect yt flag; don't force based on channel selection)
    args["yt"] = args.get("yt", False)
    args["tiktok"] = args.get("tiktok", False) or "tiktok" in selected_channels

    # Gentle jitter to avoid bursty starts when running concurrently
    jitter = args.get("start_jitter_sec", (0.5, 2.0))
    if isinstance(jitter, (tuple, list)) and len(jitter) == 2:
        time.sleep(uniform(jitter[0], jitter[1]))
    elif isinstance(jitter, (int, float)) and jitter > 0:
        time.sleep(float(jitter))

    print(f"\n Dispatching stem processing for track: {track_id}")
    
    # Use pre-fetched track_info from args if available (from playlist processing)
    # Otherwise, create ContentBase and fetch it
    pre_fetched_track_info = args.get("track_info")
    if pre_fetched_track_info and pre_fetched_track_info.get("id") == track_id:
        # Use pre-fetched track info (from playlist processing in tk.py)
        track_info = pre_fetched_track_info
        # Ensure track_id is set correctly
        track_info["id"] = track_id
        base = ContentBase({**args, "session_id": session_id, "track_info": track_info})
    else:
        # Fetch track info fresh (single track processing)
        base = ContentBase({**args, "session_id": session_id})
        track_info = base.get_track_info(track_id)
        if not track_info:
            set_progress(session_id, {"message": " Failed to get track info", "percent": 0})
            print("[ERROR] Failed to get track info")
            return
        # Ensure track_id is set correctly
        track_info["id"] = track_id

    args["track_info"] = track_info

    # Get BPM/Key from Tunebat BEFORE download (needed for fuzzy matching)
    current_bpm = args.get("bpm", 0)
    current_key = args.get("key", "Unknown")
    if not current_bpm or current_bpm == 0 or not current_key or current_key == "Unknown":
        track_title = args.get("track_title") or track_info.get("name", "")
        track_artist = args.get("track_artist") or track_info.get("artist", "")
        # Get BPM/Key from Tunebat (uses cache automatically)
        bpm, key = get_bpm_key(track_title, track_artist, track_id)
        if bpm and bpm > 0 and key and key != "Unknown":
            args["bpm"] = bpm
            args["key"] = key
            # Update track_info with Tunebat values so they're available for fuzzy matching
            track_info["tempo"] = bpm
            track_info["key"] = key
            args["track_info"] = track_info
            base.track_info = track_info  # Also update ContentBase's track_info
        else:
            # Set defaults if Tunebat failed
            track_info["tempo"] = 0
            track_info["key"] = "Unknown"
            args["track_info"] = track_info
    else:
        # Update track_info with provided values
        track_info["tempo"] = current_bpm
        track_info["key"] = current_key
        args["track_info"] = track_info
        base.track_info = track_info  # Also update ContentBase's track_info

    # Download audio
    base.update_progress(" Downloading track audio…", {"track_id": track_id})
    
    # Use track_title/track_artist from args if available (more reliable for playlists)
    # Otherwise fall back to track_info
    download_title = args.get("track_title") or track_info.get("name", "")
    download_artist = args.get("track_artist") or track_info.get("artist", "")
    
    # Debug: Log what we're searching for
    print(f" Searching YouTube for: '{download_artist} - {download_title}'")
    
    uid, mp3_path = base.download_audio(download_title, download_artist)

    # If download failed/too small, do a single backoff+retry (lets ContentBase rotate clients)
    if (not uid) or (not mp3_path) or (not os.path.exists(mp3_path)) or (not is_sane_audio(mp3_path)):
        backoff = args.get("retry_backoff_sec", (15, 30))
        wait_s = uniform(backoff[0], backoff[1]) if isinstance(backoff, (tuple, list)) and len(backoff) == 2 else float(backoff or 0)
        # Download failed, retrying
        time.sleep(max(0.0, wait_s))
        uid, mp3_path = base.download_audio(track_info["name"], track_info["artist"])

    if not uid or not os.path.exists(mp3_path) or not is_sane_audio(mp3_path):
        set_progress(session_id, {"message": " Audio download failed or file too small", "percent": 0})
        print(f"[ERROR] Download failed or small: uid={uid} mp3_path={mp3_path}")
        return

    args["universal_id"] = uid
    args["mp3_path"] = mp3_path
    
    # BPM/Key were already fetched from Tunebat before download (for fuzzy matching)
    # They're already in args["bpm"], args["key"], and track_info["tempo"], track_info["key"]

    # Section 4: Performance Optimization - Fast Mode vs Enhanced Mode
    # Fast Mode (Upload=OFF): Skip preprocessing except loudnorm
    # Enhanced Mode (Upload=ON): Full preprocessing
    upload_enabled = args.get("yt", False)
    fast_mode = not upload_enabled
    
    if fast_mode:
        # Fast mode: skipping preprocessing
        # Fast Mode: Only apply loudnorm, skip other preprocessing
        prep_path = _prepared_copy_path(uid)
        try:
            # Minimal preprocessing: only loudness normalization
            cmd = [
                "ffmpeg", "-y",
                "-i", mp3_path,
                "-af", "loudnorm=I=-14:TP=-2:LRA=11",
                prep_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
            if os.path.exists(prep_path) and os.path.getsize(prep_path) > 150_000:
                mp3_for_split = prep_path
                # Applied loudnorm
            else:
                mp3_for_split = mp3_path
                # Loudnorm failed, using original
        except Exception as e:
            # Loudnorm failed, using original
            mp3_for_split = mp3_path
    else:
        # Enhanced Mode: Full preprocessing
        prep_path = _prepared_copy_path(uid)
        if prepare_input_for_demucs(mp3_path, prep_path):
            mp3_for_split = prep_path
            # Using prepared audio
        else:
            mp3_for_split = mp3_path
            # Using original audio

    # If cached validated stems exist (original or __prep basename), reuse first valid.
    cached_dir = None
    cached_model = None
    original_base = os.path.splitext(os.path.basename(mp3_path))[0]
    prepared_base = os.path.splitext(os.path.basename(prep_path))[0]
    for model in FALLBACK_MODELS:
        for base_name in (original_base, prepared_base):
            candidate = os.path.join("Separated", model, base_name)
            if os.path.exists(candidate):
                v = validate_stems(candidate)
                if v.get("ok"):
                    cached_dir = candidate
                    cached_model = model
                    break
        if cached_dir:
            break

    if cached_dir:
        args["stem_base_path"] = os.path.abspath(cached_dir)  # <- absolute
        set_progress(session_id, {"message": f" Using cached stems ({cached_model})", "percent": 45})
        # Using cached stems
    else:
        # Run Demucs with fallbacks and validate
        set_progress(session_id, {"message": " Separating stems…", "percent": 12})
        device = get_optimal_device()
        model_used, stem_base_path, validation = run_demucs_with_fallbacks(mp3_for_split, device, session_id)
        if not model_used:
            msg = " Stem separation failed on all models"
            set_progress(session_id, {"message": msg, "percent": 0})
            print("[ERROR] Separation failed on all models")
            return

        args["stem_base_path"] = os.path.abspath(stem_base_path)  # <- absolute
        set_progress(session_id, {"message": f" Separation complete with {model_used}", "percent": 45})
        # Separation complete

    # Progress metas
    progress = get_progress(session_id)
    if progress:
        progress.update({
            "message": " Processing channels…",
            "meta": {"completed": 0, "total": len(selected_channels)},
            "percent": 46
        })
        set_progress(session_id, progress)

    # Pin the stem path per track (single frozen absolute path)
    fixed_sbp = os.path.abspath(args.get("stem_base_path", ""))
    # Fixed stem_base_path

    # PHASE 1: Process all channels - create all MP4s first (batch processing)
    # This is faster: all videos are created before any uploads start
    processors = []
    for idx, channel_ui in enumerate(selected_channels):
        channel_key = UI_TO_CHANNEL_MAP.get(channel_ui, channel_ui)
        if channel_key not in CHANNEL_MODULE_MAP:
            # Unknown channel, skipping
            continue

        # Use pinned path; attempt recovery if missing
        sbp = fixed_sbp
        if not os.path.isdir(sbp):
            rec = recover_stem_dir(args.get("universal_id", ""))
            if rec:
                # Recovered stem path
                sbp = rec
                fixed_sbp = rec  # keep consistent for later channels
            else:
                print(f"[ERROR] stem_base_path invalid for {channel_key}: {sbp}")
                set_progress(session_id, {"message": f" stem_base_path invalid for {channel_key}: {sbp}", "percent": 100})
                continue

        try:
            progress = get_progress(session_id) or {}
            meta = progress.get("meta", {}) if progress else {}
            if progress:
                channel_display = CHANNEL_NAME_MAP.get(channel_key, channel_key)
                progress["message"] = f" Processing {channel_display}…"
                meta["channel"] = channel_display
                progress["meta"] = meta
                set_progress(session_id, progress)

            module_name, class_name = CHANNEL_MODULE_MAP[channel_key]
            module = importlib.import_module(module_name)
            processor_class = getattr(module, class_name)

            # IMPORTANT: pass the pinned/recovered path explicitly
            processor = processor_class({**args, "channel": channel_key, "session_id": session_id, "stem_base_path": sbp})

            # Process all stems for this channel (create MP4s, but don't upload yet)
            processor.download(track_id)
            
            # Store processor for batch upload later
            processors.append((processor, channel_key))

            # Step done
            progress = get_progress(session_id) or {}
            meta = progress.get("meta", {})
            meta["completed"] = int(meta.get("completed", 0)) + 1
            channel_display = CHANNEL_NAME_MAP.get(channel_key, channel_key)
            meta["channel"] = channel_display
            total = int(meta.get("total", 1))
            progress["meta"] = meta
            progress["percent"] = 46 + int((meta["completed"] / total) * 54)
            progress["message"] = f" {channel_display} processed"
            set_progress(session_id, progress)

        except Exception as e:
            traceback.print_exc()
            print(f"[ERROR] Channel processing error for {channel_key}: {e}")
            print(f"[ERROR] Full traceback:")
            import sys
            exc_info = sys.exc_info()
            traceback.print_exception(*exc_info)
            progress = get_progress(session_id) or {}
            progress["message"] = f" Error processing {channel_key.upper()} — continuing"
            set_progress(session_id, progress)
            continue
    
    # PHASE 2: Batch upload all channels to YouTube (after all MP4s are created)
    # This is faster: all videos ready, then upload all at once
    if args.get("yt", False) and processors:
        set_progress(session_id, {"message": " Uploading all channels to YouTube…", "percent": 90})
        for processor, channel_key in processors:
            try:
                if hasattr(processor, 'video_paths') and processor.video_paths:
                    channel_display = CHANNEL_NAME_MAP.get(channel_key, channel_key)
                    set_progress(session_id, {"message": f" Uploading {channel_display} to YouTube…", "percent": 90})
                    processor.upload_batch_to_youtube(track_info)
            except Exception as e:
                print(f"[ERROR] Upload error for {channel_key}: {e}")
                continue
        
        if args.get("tiktok", False):
            for processor, channel_key in processors:
                try:
                    if hasattr(processor, 'video_paths') and processor.video_paths:
                        processor.upload_batch_to_tiktok(track_info)
                except Exception as e:
                    print(f"[ERROR] TikTok upload error for {channel_key}: {e}")
                    continue

    # Optional cooldown after a whole track finishes (to reduce burstiness in big batches)
    cooldown = args.get("per_track_cooldown_sec", 0)
    if isinstance(cooldown, (tuple, list)) and len(cooldown) == 2:
        time.sleep(uniform(cooldown[0], cooldown[1]))
    elif isinstance(cooldown, (int, float)) and cooldown > 0:
        time.sleep(float(cooldown))

    # Finalize
    final = get_progress(session_id) or {}
    final["message"] = " All processing complete"
    final["percent"] = 100
    final["done"] = True
    set_progress(session_id, final)
    # All processing complete

# --------------------------------------------------------------------------------------
# Batch runner
# --------------------------------------------------------------------------------------
def process_all_tracks(
    track_ids: list,
    selected_channels: list,
    args: dict = None,
    session_id: str = "batch",
    max_concurrent: int = 2,
    per_track_args: dict = None
):
    """
    Run multiple tracks with limited concurrency.
    If a track fails, it prints the error and other tracks continue.

    TIP to avoid 403s:
      - keep max_concurrent modest (e.g., 1–3)
      - set args['start_jitter_sec']=(0.5, 2.0)
      - set args['per_track_cooldown_sec']=(20, 45) for large batches
    """
    # Processing tracks
    semaphore = threading.Semaphore(max_concurrent)

    def run_with_semaphore(track_id, sess_id):
        with semaphore:
            merged_args = args.copy() if args else {}
            if per_track_args and track_id in per_track_args:
                merged_args.update(per_track_args[track_id])
            try:
                dispatch_stem_processing(track_id, selected_channels, merged_args, sess_id)
            except Exception as e:
                traceback.print_exc()
                print(f"[ERROR] Uncaught error for {track_id}: {e}")
                set_progress(sess_id, {"message": f" Uncaught error for {track_id} — continuing", "percent": 0})

    workers = max(1, min(len(track_ids) or 1, max_concurrent))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []
        for track_id in track_ids:
            full_session_id = f"{session_id}__{track_id}"
            future = executor.submit(run_with_semaphore, track_id, full_session_id)
            futures.append(future)
        
        # Wait for all tasks to complete
        for future in futures:
            try:
                future.result()  # Wait for completion and raise any exceptions
            except Exception as e:
                # Error already logged in run_with_semaphore
                pass
