# dispatch_download.py
import os
import sys
import importlib
import traceback
import torch
import subprocess
import threading
import time

# ✅ Quick fix for missing utils import
current_dir = os.path.dirname(os.path.abspath(__file__))
utils_path = os.path.join(current_dir, "utils")
if utils_path not in sys.path:
    sys.path.append(utils_path)

try:
    from utils.validators import validate_stems
except ModuleNotFoundError:
    # fallback inline function
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
from content_base import ContentBase
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
CHANNEL_MODULE_MAP = {
    "son_got_acapellas": ("content_download_vocal", "Content_download_vocal"),
    "son_got_drums": ("content_download_drum", "Content_download_drum"),
    "main_channel": ("content_download_main", "Content_download_main"),
    "sgs_2": ("content_download_backup", "Content_download_backup"),
    "sample_split": ("content_download_sample_split", "Content_download_split"),
}

# --------------------------------------------------------------------------------------
# Helpers: pre-process, demucs runners, fallbacks
# --------------------------------------------------------------------------------------

# Order matters (first valid wins)
FALLBACK_MODELS = ["htdemucs_6s", "htdemucs_ft", "htdemucs"]

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
    return os.path.join("separated", model_name, base)

def run_demucs_with_model_stream(mp3_path: str, device: str, model_name: str):
    """
    Run demucs and stream stdout to terminal in real time (so clients see progress).
    Returns (returncode, tail_text).
    """
    try:
        print(f"\n[DEMUCS] ▶ Running model: {model_name} on {device}")
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
                sys.stdout.write(f"[DEMUCS][{model_name}] {line}")
                sys.stdout.flush()
                last_lines.append(line)
                if len(last_lines) > 200:
                    last_lines.pop(0)

        process.stdout.close()
        process.wait()
        rc = process.returncode
        print(f"[DEMUCS] ◀ Model {model_name} finished with rc={rc}")
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
        set_progress(session_id, {"message": f"🌀 Separating with {model} (attempt {idx})…", "percent": 12})

        rc, tail = run_demucs_with_model_stream(mp3_path, device, model)
        out_dir = demucs_outdir_for_input(model, mp3_path)

        if rc is None:
            print(f"[DEMUCS] Could not start model {model}; trying next…")
            continue

        if rc != 0 or not os.path.exists(out_dir):
            print(f"[DEMUCS] Model {model} failed. rc={rc} out_dir={out_dir} exists={os.path.exists(out_dir)}")
            # If GPU OOM, try CPU once
            if device.startswith("cuda") and ("CUDA out of memory" in tail or "CUDA error" in tail):
                print("[DEMUCS] CUDA OOM detected — retrying on CPU…")
                rc2, _ = run_demucs_with_model_stream(mp3_path, "cpu", model)
                if rc2 == 0 and os.path.exists(out_dir):
                    v = validate_stems(out_dir)
                    if v.get("ok"):
                        return model, out_dir, v
            continue

        # Validate stems
        validation = validate_stems(out_dir)
        if validation.get("ok"):
            print(f"[VALIDATE] {model} stems OK ✅ at {out_dir}")
            return model, out_dir, validation

        print(f"[VALIDATE] Problems with {model}: {validation.get('problems')}")
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
            cand = os.path.abspath(os.path.join("separated", model, f"{universal_id}{suffix}"))
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
    # Gentle jitter to avoid bursty starts when running concurrently
    jitter = args.get("start_jitter_sec", (0.5, 2.0))
    if isinstance(jitter, (tuple, list)) and len(jitter) == 2:
        time.sleep(uniform(jitter[0], jitter[1]))
    elif isinstance(jitter, (int, float)) and jitter > 0:
        time.sleep(float(jitter))

    print(f"\n🚀 Dispatching stem processing for track: {track_id}")
    base = ContentBase({**args, "session_id": session_id})

    # Fetch track info
    track_info = base.get_track_info(track_id)
    if not track_info:
        set_progress(session_id, {"message": "❌ Failed to get track info", "percent": 0})
        print("[ERROR] Failed to get track info")
        return

    args["track_info"] = track_info

    # Download audio
    base.update_progress("🎵 Downloading track audio…", {"track_id": track_id})
    uid, mp3_path = base.download_audio(track_info["name"], track_info["artist"])

    # If download failed/too small, do a single backoff+retry (lets ContentBase rotate clients)
    if (not uid) or (not mp3_path) or (not os.path.exists(mp3_path)) or (not is_sane_audio(mp3_path)):
        backoff = args.get("retry_backoff_sec", (15, 30))
        wait_s = uniform(backoff[0], backoff[1]) if isinstance(backoff, (tuple, list)) and len(backoff) == 2 else float(backoff or 0)
        print(f"[RETRY] First download failed/too small. Backing off for {wait_s:.1f}s, then retrying…")
        time.sleep(max(0.0, wait_s))
        uid, mp3_path = base.download_audio(track_info["name"], track_info["artist"])

    if not uid or not os.path.exists(mp3_path) or not is_sane_audio(mp3_path):
        set_progress(session_id, {"message": "❌ Audio download failed or file too small", "percent": 0})
        print(f"[ERROR] Download failed or small: uid={uid} mp3_path={mp3_path}")
        return

    args["universal_id"] = uid
    args["mp3_path"] = mp3_path

    # Pre-process input (resample + loudness normalize)
    prep_path = _prepared_copy_path(uid)
    if prepare_input_for_demucs(mp3_path, prep_path):
        mp3_for_split = prep_path
        print(f"[PREP] Using prepared audio at {prep_path}")
    else:
        mp3_for_split = mp3_path
        print(f"[PREP] Using original audio (prep failed or skipped)")

    # If cached validated stems exist (original or __prep basename), reuse first valid.
    cached_dir = None
    cached_model = None
    original_base = os.path.splitext(os.path.basename(mp3_path))[0]
    prepared_base = os.path.splitext(os.path.basename(prep_path))[0]
    for model in FALLBACK_MODELS:
        for base_name in (original_base, prepared_base):
            candidate = os.path.join("separated", model, base_name)
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
        set_progress(session_id, {"message": f"✅ Using cached stems ({cached_model})", "percent": 45})
        print(f"[CACHE] Using cached stems at {args['stem_base_path']} (model={cached_model})")
    else:
        # Run Demucs with fallbacks and validate
        set_progress(session_id, {"message": "🌀 Separating stems…", "percent": 12})
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        model_used, stem_base_path, validation = run_demucs_with_fallbacks(mp3_for_split, device, session_id)
        if not model_used:
            msg = "❌ Stem separation failed on all models"
            set_progress(session_id, {"message": msg, "percent": 0})
            print("[ERROR] Separation failed on all models")
            return

        args["stem_base_path"] = os.path.abspath(stem_base_path)  # <- absolute
        set_progress(session_id, {"message": f"✅ Separation complete with {model_used}", "percent": 45})
        print(f"[OK] Separation complete with {model_used} at {args['stem_base_path']}")

    # Progress metas
    progress = get_progress(session_id)
    if progress:
        progress.update({
            "message": "🟢 Processing channels…",
            "meta": {"completed": 0, "total": len(selected_channels)},
            "percent": 46
        })
        set_progress(session_id, progress)

    # Pin the stem path per track (single frozen absolute path)
    fixed_sbp = os.path.abspath(args.get("stem_base_path", ""))
    print(f"[TRACK] Fixed stem_base_path={fixed_sbp} | exists={os.path.isdir(fixed_sbp)} | CWD={os.getcwd()}")

    # Process each selected channel; on failure, print and continue
    for channel_key in selected_channels:
        if channel_key not in CHANNEL_MODULE_MAP:
            print(f"[WARN] Unknown channel key: {channel_key} — skipping")
            continue

        # Use pinned path; attempt recovery if missing
        sbp = fixed_sbp
        if not os.path.isdir(sbp):
            rec = recover_stem_dir(args.get("universal_id", ""))
            if rec:
                print(f"[RECOVER] Recovered stem_base_path → {rec}")
                sbp = rec
                fixed_sbp = rec  # keep consistent for later channels
            else:
                print(f"[ERROR] stem_base_path invalid for {channel_key}: {sbp}")
                set_progress(session_id, {"message": f"❌ stem_base_path invalid for {channel_key}: {sbp}", "percent": 100})
                continue

        try:
            progress = get_progress(session_id) or {}
            meta = progress.get("meta", {}) if progress else {}
            if progress:
                progress["message"] = f"⚙️ Uploading {channel_key.upper()}…"
                meta["channel"] = channel_key
                progress["meta"] = meta
                set_progress(session_id, progress)

            module_name, class_name = CHANNEL_MODULE_MAP[channel_key]
            module = importlib.import_module(module_name)
            processor_class = getattr(module, class_name)

            # IMPORTANT: pass the pinned/recovered path explicitly
            processor = processor_class({**args, "channel": channel_key, "session_id": session_id, "stem_base_path": sbp})

            print(f"[CHANNEL] ▶ Processing {channel_key}… | stem_base_path={sbp}")
            processor.download(track_id)
            print(f"[CHANNEL] ◀ {channel_key} done")

            # Step done
            progress = get_progress(session_id) or {}
            meta = progress.get("meta", {})
            meta["completed"] = int(meta.get("completed", 0)) + 1
            meta["channel"] = channel_key
            total = int(meta.get("total", 1))
            progress["meta"] = meta
            progress["percent"] = 46 + int((meta["completed"] / total) * 54)
            progress["message"] = f"✅ {channel_key.upper()} done"
            set_progress(session_id, progress)

        except Exception as e:
            traceback.print_exc()
            print(f"[ERROR] Channel processing error for {channel_key}: {e}")
            progress = get_progress(session_id) or {}
            progress["message"] = f"❌ Error processing {channel_key.upper()} — continuing"
            set_progress(session_id, progress)
            continue

    # Optional cooldown after a whole track finishes (to reduce burstiness in big batches)
    cooldown = args.get("per_track_cooldown_sec", 0)
    if isinstance(cooldown, (tuple, list)) and len(cooldown) == 2:
        time.sleep(uniform(cooldown[0], cooldown[1]))
    elif isinstance(cooldown, (int, float)) and cooldown > 0:
        time.sleep(float(cooldown))

    # Finalize
    final = get_progress(session_id) or {}
    final["message"] = "✅ All processing complete"
    final["percent"] = 100
    final["done"] = True
    set_progress(session_id, final)
    print("[DONE] All processing complete")

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
                set_progress(sess_id, {"message": f"❌ Uncaught error for {track_id} — continuing", "percent": 0})

    workers = max(1, min(len(track_ids) or 1, max_concurrent))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for track_id in track_ids:
            full_session_id = f"{session_id}__{track_id}"
            executor.submit(run_with_semaphore, track_id, full_session_id)
