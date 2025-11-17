# File: content_download_main.py
"""Main channel stem processor with advanced audio mixing.

High-quality stem separation and processing:
- Acapella (vocals only)
- Drums (percussion)
- Instrumental (other + drums + bass combined with loudness normalization)

Features:
- Uses htdemucs_ft model for best quality
- Proper stem mixing with normalization
- Consistent loudness across all stems
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

try:
    from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip
    MOVIEPY_AVAILABLE = True
    MOVIEPY_IMPORT_ERROR: Optional[Exception] = None
    print(" moviepy available — MP4 rendering enabled")
except ModuleNotFoundError as exc:
    AudioFileClip = ColorClip = ImageClip = None
    MOVIEPY_AVAILABLE = False
    MOVIEPY_IMPORT_ERROR = exc
    print(f" moviepy not available: {exc}")
    print("   Install via: pip install moviepy")
    print("   Requires: ffmpeg (install separately if missing)")
from mutagen.easyid3 import EasyID3
from mutagen.id3 import COMM, ID3, TIT2
from mutagen.mp3 import MP3
from pydub import AudioSegment

from branding_utils import add_intro_card, apply_moviepy_resize, create_base_clip, create_watermark_clip, create_stem_icon_clip
from content_base import (
    ContentBase,
    DEFAULT_DESCRIPTION,
    DEFAULT_TAGS,
    format_bpm_label,
    normalize_genre,
)
from shared_state import get_progress, set_progress
from stem_processor import StemProcessor
from tiktok_uploader import TikTokUploader

# Register custom comment tag once.
if "comment" not in EasyID3.valid_keys:
    EasyID3.RegisterTXXXKey("comment", "comment")

# Cache for available FFmpeg encoders (checked once per session)
_available_ffmpeg_encoders: Optional[List[str]] = None

def test_encoder_availability(encoder: str) -> bool:
    """
    Test if an encoder is actually available and usable by FFmpeg.
    Uses a minimal test encode to verify the encoder works.
    
    Args:
        encoder: Encoder name to test
        
    Returns:
        True if encoder is available and usable, False otherwise
    """
    try:
        # Create a minimal 1x1 test image
        test_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "color=c=black:s=1x1:d=0.1",
            "-c:v", encoder,
            "-frames:v", "1",
            "-f", "null",
            "-"
        ]
        
        # Add encoder-specific minimal options
        if encoder == "libx264":
            test_cmd.insert(-2, "-preset")
            test_cmd.insert(-2, "ultrafast")
        elif encoder == "h264_nvenc":
            test_cmd.insert(-2, "-preset")
            test_cmd.insert(-2, "p1")
        elif encoder == "h264_amf":
            test_cmd.insert(-2, "-quality")
            test_cmd.insert(-2, "speed")
        
        result = subprocess.run(
            test_cmd,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Check if encoder error occurred
        error_output = result.stderr.lower() if result.stderr else ""
        if "unknown encoder" in error_output or "not found" in error_output or "invalid" in error_output:
            return False
        
        # If return code is 0, encoder works
        return result.returncode == 0
        
    except (subprocess.TimeoutExpired, Exception):
        return False


def get_available_ffmpeg_encoders() -> List[str]:
    """
    Check which video encoders are available and actually usable in FFmpeg.
    Tests each encoder to verify it works, not just that it's listed.
    Returns a list of verified available encoder names.
    Caches the result to avoid repeated checks.
    """
    global _available_ffmpeg_encoders
    
    if _available_ffmpeg_encoders is not None:
        return _available_ffmpeg_encoders
    
    available = []
    target_encoders = ["libx264", "h264_nvenc", "h264_amf", "mpeg4", "mjpeg", "libx264rgb"]
    
    try:
        # First, check if encoders are listed (quick check)
        result = subprocess.run(
            ["ffmpeg", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        listed_encoders = []
        if result.returncode == 0:
            output = result.stdout + result.stderr
            # Parse encoder list - look for video encoders (lines starting with " V")
            for line in output.split('\n'):
                line_stripped = line.strip()
                # FFmpeg format: " V..... libx264" - V means video encoder
                if line_stripped.startswith('V') or ' V' in line_stripped:
                    for encoder in target_encoders:
                        # Check if encoder name appears in the line (usually at the end)
                        parts = line_stripped.split()
                        if parts and encoder.lower() in parts[-1].lower():
                            if encoder not in listed_encoders:
                                listed_encoders.append(encoder)
        
        # Now test each listed encoder to verify it actually works
        for encoder in listed_encoders:
            if test_encoder_availability(encoder):
                available.append(encoder)
        
        # If no encoders were found via listing, try testing common ones directly
        if not available:
            # Test most common encoders directly
            common_encoders = ["libx264", "mpeg4", "mjpeg"]
            for encoder in common_encoders:
                if encoder not in available and test_encoder_availability(encoder):
                    available.append(encoder)
        
        # Silently cache results, no output
        
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        # Silently fall back to MoviePy
        pass
    
    # Cache the result (even if empty)
    _available_ffmpeg_encoders = available if available else []
    return _available_ffmpeg_encoders


class Content_download_main(ContentBase):
    """Prepare acapella, drums, and instrumental stems for the main channel.
    
    Stem mapping:
    - Acapella: vocals only
    - Drums: percussion instruments
    - Instrumental: other + drums + bass (full instruments without vocals)
    """

    STEM_DEFINITIONS: Dict[str, Dict[str, object]] = {
        # Acapella: vocals stem directly from model
        "Acapella": {
            "stem_key": "acapella",
            "sources": ["vocals.mp3"],
            "mix": False,
            "description": "Vocals-only stem"
        },
        # Drums: percussion directly from model
        "Drums": {
            "stem_key": "drums",
            "sources": ["drums.mp3"],
            "mix": False,
            "description": "Drums/percussion stem"
        },
        # Bass: bass stem directly from model
        "Bass": {
            "stem_key": "bass",
            "sources": ["bass.mp3"],
            "mix": False,
            "description": "Bass-only stem"
        },
        # Melody: other/melody stem directly from model
        "Melody": {
            "stem_key": "melody",
            "sources": ["other.mp3"],
            "mix": False,
            "description": "Melody/other instruments stem"
        },
        # Instrumental: combination of other + drums + bass (no vocals)
        "Instrumental": {
            "stem_key": "instrumental",
            "sources": ["other.mp3", "drums.mp3", "bass.mp3"],
            "mix": True,
            "description": "Full instrumental (other + drums + bass)"
        },
    }

    def __init__(self, args: Dict):
        super().__init__(args)
        self.track_info = args.get("track_info", {})
        self.stem_base_path = args.get("stem_base_path", "")
        self.selected_genre = normalize_genre(args.get("genre"))
        self.genre_folder = self._sanitize_folder_name(self.selected_genre)
        self.trim_track = args.get("trim_track", False)
        self.trim_length = args.get("trim_length", 72)
        self.thumbnail_map: Dict[str, str] = {}
        # Cache for video clips (reuse across stems for same track)
        self._cached_background: Optional[Any] = None
        self._cached_thumbnail_clip: Optional[Any] = None
        self._cached_base_clip: Optional[Any] = None
        self._cached_duration: Optional[float] = None
        self._cached_thumb_path: Optional[str] = None
        # Cache for watermark clips (per channel)
        self._cached_watermarks: Dict[str, Any] = {}  # key: channel
        # Cache for icon clips (per stem type)
        self._cached_icons: Dict[str, Any] = {}  # key: stem_type

    # ------------------------------------------------------------------
    # Progress helpers (same structure as other processors)
    # ------------------------------------------------------------------
    def incremental_progress(self, message, step_index, total_steps, metadata=None):
        progress_data = get_progress(self.session_id) or {}
        meta = progress_data.get("meta", {})
        completed = meta.get("completed", 0)
        total = meta.get("total", 1)
        base = (completed / total) * 100 if total else 0
        step_size = 100 / total / total_steps if total_steps else 0
        step_percent = base + step_index * step_size

        update = {
            "message": message,
            "percent": min(100, round(step_percent, 2)),
            "meta": {**meta, **(metadata or {})},
        }
        set_progress(self.session_id, update)
        # Progress update suppressed

    def _sanitize_filename(self, name: str) -> str:
        """
        Sanitize filename while preserving hyphens, spaces, and brackets.
        Only removes truly problematic characters for file systems.
        """
        import re
        # Remove only truly problematic characters: \ / : * ? " < > |
        cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", name or "")
        # Collapse multiple spaces but preserve single spaces
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned or "Untitled"
    
    def _build_folder_title(self, artist: str, title: str, stem_type: str, bpm: str, key: str) -> str:
        """
        Build folder title in format: Artist - Song StemType [BPM Key]
        Example: "Sean Kingston - Beautiful Girls Acapella [130 C♯ minor]"
        """
        # Clean artist and title (keep spaces, just trim)
        artist_clean = artist.strip()
        title_clean = title.strip()
        
        # Format stem type (title case, spaces)
        stem_display = stem_type.replace("_", " ").title()
        
        # Build base: Artist - Song StemType
        base = f"{artist_clean} - {title_clean} {stem_display}"
        
        # Build bracket content: [BPM Key] or [BPM] for drums
        # Rule: Drums channel (son_got_drums) NEVER shows key, regardless of stem type
        # Rule: Drums stem type NEVER shows key, regardless of channel
        bracket_parts = [f"BPM {bpm}"]
        channel_key = self.args.get("channel", "").lower()
        is_drum_channel = channel_key in ["son_got_drums", "drum", "drums", "songotdrums"]
        is_drum_stem = stem_type.lower() == "drums"
        
        # Only add key if NOT drum channel AND NOT drum stem
        if not is_drum_channel and not is_drum_stem and key and key != "Unknown":
            bracket_parts.append(key)
        
        bracket = f" [{' '.join(bracket_parts)}]"
        folder_title = base + bracket
        
        return self._sanitize_filename(folder_title)

    def _tag_stem(self, file_path: str, stem_type: str, bpm: str, key: str) -> None:
        """
        Write ID3 tags for exported stems.
        Section 3 rules: Drums channel = BPM only, Drums stem = BPM only, all others = BPM + Key
        """
        # Rule: Drums channel (son_got_drums) NEVER shows key, regardless of stem type
        # Rule: Drums stem type NEVER shows key, regardless of channel
        channel_key = self.args.get("channel", "").lower()
        is_drum_channel = channel_key in ["son_got_drums", "drum", "drums", "songotdrums"]
        is_drum_stem = stem_type.lower() == "drums"
        
        # Only add key if NOT drum channel AND NOT drum stem
        if is_drum_channel or is_drum_stem:
            comment_text = f"BPM: {bpm}"
        else:
            key_text = key if key and key != "Unknown" else ""
            if key_text:
                comment_text = f"BPM: {bpm}, Key: {key_text}"
            else:
                comment_text = f"BPM: {bpm}"
        
        try:
            audio = EasyID3(file_path)
            audio["title"] = f"{stem_type} stem"
            audio["comment"] = comment_text
            audio.save()
        except Exception:
            audio = MP3(file_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text=f"{stem_type} stem"))
            audio.tags.add(COMM(encoding=3, lang="eng", desc="desc", text=comment_text))
            audio.save()

    def _render_video(
        self,
        audio_path: str,
        thumb_path: Optional[str],
        stem_type: str,
        bpm: str,
        key: str,
        artist: str,
        track_title: str,
    ) -> Optional[str]:
        if not MOVIEPY_AVAILABLE:
            error_hint = "pip install moviepy"
            detail = f" ({MOVIEPY_IMPORT_ERROR})" if MOVIEPY_IMPORT_ERROR else ""
            print(
                f" Cannot render {stem_type} video: moviepy not available{detail}\n"
                f"   Install: {error_hint}\n"
                f"   Also need: ffmpeg (install separately if missing)"
            )
            return None

        try:
            if not os.path.exists(audio_path):
                print(f" Audio file not found: {audio_path}")
                return None
            
            channel = self.channel_label
            genre = self.genre_folder
            stem_display = stem_type.replace("_", " ").title()
            folder_title = self._build_folder_title(artist, track_title, stem_type, bpm, key)
            filename = f"{folder_title}.mp4"
            out_dir = os.path.join("MP4", channel, genre, stem_display, folder_title)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, filename)

            print(f"🎬 Rendering video for {stem_type}...")
            print(f"   Audio: {audio_path}")
            print(f"   Output: {out_path}")
            
            audio_clip = AudioFileClip(audio_path)
            print(f"   Duration: {audio_clip.duration}s")
            
            # Cache base clip (background + thumbnail) for reuse across stems
            # Only recreate if duration or thumbnail path changed
            duration = audio_clip.duration
            # Normalize thumb_path for comparison (handle None and empty strings)
            normalized_thumb_path = thumb_path if thumb_path else None
            
            # Check if we can reuse the cached base clip
            can_reuse = (
                self._cached_base_clip is not None and
                self._cached_duration == duration and
                self._cached_thumb_path == normalized_thumb_path
            )
            
            if not can_reuse:
                print(f"   Creating base clip (background + thumbnail)...")
                if normalized_thumb_path:
                    print(f"      Thumbnail: {normalized_thumb_path}")
                else:
                    print(f"      No thumbnail (black background only)")
                self._cached_base_clip = create_base_clip(duration, normalized_thumb_path)
                self._cached_duration = duration
                self._cached_thumb_path = normalized_thumb_path
            else:
                print(f"   ✓ Reusing cached base clip (background + thumbnail) - same track, same duration")
                # Adjust duration if needed (should be same for all stems of same track)
                if self._cached_base_clip.duration != duration:
                    print(f"      Adjusting cached base clip duration from {self._cached_base_clip.duration}s to {duration}s")
                    self._cached_base_clip = self._cached_base_clip.with_duration(duration)
            
            # Cache watermark clip (channel-specific, same for all stems of that channel)
            # Watermark is loaded from assets/assets/label/ and cached per channel
            if channel not in self._cached_watermarks:
                print(f"   Creating watermark clip for channel: {channel} (loading from assets)")
                self._cached_watermarks[channel] = create_watermark_clip(channel, duration, 1280, 720)
            else:
                print(f"   ✓ Reusing cached watermark clip for channel: {channel} (loaded from assets)")
                # Adjust duration if needed
                cached_wm = self._cached_watermarks[channel]
                if cached_wm and cached_wm.duration != duration:
                    self._cached_watermarks[channel] = cached_wm.with_duration(duration)
            
            # Cache icon clip (stem-specific, same for all channels using that stem)
            # Icon is loaded from assets/assets/icons/ and cached per stem type
            watermark_clip = self._cached_watermarks.get(channel)
            icon_clip = None
            if stem_type:
                if stem_type not in self._cached_icons:
                    print(f"   Creating icon clip for stem: {stem_type} (loading from assets)")
                    self._cached_icons[stem_type] = create_stem_icon_clip(stem_type, duration, 1280)
                else:
                    print(f"   ✓ Reusing cached icon clip for stem: {stem_type} (loaded from assets)")
                    # Adjust duration if needed
                    cached_icon = self._cached_icons[stem_type]
                    if cached_icon and cached_icon.duration != duration:
                        self._cached_icons[stem_type] = cached_icon.with_duration(duration)
                icon_clip = self._cached_icons.get(stem_type)
            
            # Section 2: Enhanced branding with watermark and stem icon
            # Pass cached clips to avoid recreating them
            branded_clip = add_intro_card(duration, channel, thumb_path, stem_type, 
                                         base_clip=self._cached_base_clip,
                                         watermark_clip=watermark_clip,
                                         icon_clip=icon_clip)
            
            if not branded_clip:
                # Fallback: create basic clip if add_intro_card fails
                thumb = thumb_path if thumb_path and os.path.exists(thumb_path) else None
                if thumb:
                    print(f"   Using thumbnail: {thumb}")
                    thumb_clip = ImageClip(thumb).with_duration(audio_clip.duration)
                    thumb_clip = apply_moviepy_resize(thumb_clip, new_size=(720, 720))
                    thumb_clip = thumb_clip.with_position("center")
                    # Try to load background from assets, fallback to black
                    from pathlib import Path
                    background_path = Path("assets/assets/background.png")
                    if background_path.exists():
                        try:
                            background = ImageClip(str(background_path)).with_duration(audio_clip.duration)
                            background = apply_moviepy_resize(background, new_size=(1280, 720))
                        except Exception as e:
                            print(f"   Warning: Could not load background image, using black: {e}")
                            background = ColorClip(
                                size=(1280, 720), color=(0, 0, 0), duration=audio_clip.duration
                            )
                    else:
                        background = ColorClip(
                            size=(1280, 720), color=(0, 0, 0), duration=audio_clip.duration
                        )
                    branded_clip = CompositeVideoClip(
                        [background, thumb_clip], size=(1280, 720)
                    )
                    
                    # Add branding to fallback clip (use cached clips if available)
                    from branding_utils import add_watermark, add_stem_icon
                    fallback_wm = self._cached_watermarks.get(channel)
                    watermarked = add_watermark(branded_clip, channel, audio_clip.duration, watermark_clip=fallback_wm)
                    if watermarked:
                        branded_clip = watermarked
                    if stem_type:
                        fallback_icon = self._cached_icons.get(stem_type)
                        iconed = add_stem_icon(branded_clip, stem_type, audio_clip.duration, icon_clip=fallback_icon)
                        if iconed:
                            branded_clip = iconed
                else:
                    print(f"   No thumbnail, using background from assets")
                    # Try to load background from assets, fallback to black
                    from pathlib import Path
                    background_path = Path("assets/assets/background.png")
                    if background_path.exists():
                        try:
                            background_clip = ImageClip(str(background_path)).with_duration(audio_clip.duration)
                            background_clip = apply_moviepy_resize(background_clip, new_size=(1280, 720))
                            branded_clip = background_clip
                        except Exception as e:
                            print(f"   Warning: Could not load background image, using black: {e}")
                            branded_clip = ColorClip(
                                size=(1280, 720), color=(0, 0, 0), duration=audio_clip.duration
                            )
                    else:
                        branded_clip = ColorClip(
                            size=(1280, 720), color=(0, 0, 0), duration=audio_clip.duration
                        )
                    
                    # Add branding to background (use cached clips if available)
                    from branding_utils import add_watermark, add_stem_icon
                    fallback_wm = self._cached_watermarks.get(channel)
                    watermarked = add_watermark(branded_clip, channel, audio_clip.duration, watermark_clip=fallback_wm)
                    if watermarked:
                        branded_clip = watermarked
                    if stem_type:
                        fallback_icon = self._cached_icons.get(stem_type)
                        iconed = add_stem_icon(branded_clip, stem_type, audio_clip.duration, icon_clip=fallback_icon)
                        if iconed:
                            branded_clip = iconed

            # For static images, use FFmpeg directly - no frame processing needed!
            # Extract a single frame from the composite clip and combine with audio using FFmpeg
            # Get a single frame at t=0 (static image, so any frame is the same)
            frame_image = branded_clip.get_frame(0)
            
            # Save frame as temporary PNG
            tmp_img_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_img:
                    tmp_img_path = tmp_img.name
                    from PIL import Image
                    Image.fromarray(frame_image).save(tmp_img_path)
                
                # Verify inputs exist
                if not os.path.exists(tmp_img_path):
                    raise Exception(f"Temporary image file not found: {tmp_img_path}")
                if not os.path.exists(audio_path):
                    raise Exception(f"Audio file not found: {audio_path}")
                
                # Normalize paths for Windows (handle special characters)
                tmp_img_path = os.path.normpath(tmp_img_path)
                audio_path = os.path.normpath(audio_path)
                out_path = os.path.normpath(out_path)
                
                # Ensure output directory exists
                out_dir = os.path.dirname(out_path)
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)
                    print(f"   Created output directory: {out_dir}")
                
                # FFmpeg command for static image + audio (using auto-detect encoder)
                # Let FFmpeg automatically choose the best available video encoder
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1",
                    "-framerate", "1",
                    "-i", tmp_img_path,
                    "-i", audio_path,
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-shortest",
                    out_path
                ]
                
                print(f"   Running FFmpeg command (auto-detect encoder)...")
                result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=300)
                
                if result.returncode != 0:
                    # Get full error message
                    error_msg = result.stderr if result.stderr else result.stdout if result.stdout else "Unknown error (no output)"
                    
                    # Show last 30 lines of error (skip first few lines which are usually version info)
                    error_lines = error_msg.split('\n')
                    # Skip first 5 lines (usually version/build info) and show last 30
                    relevant_lines = error_lines[5:] if len(error_lines) > 5 else error_lines
                    error_summary = '\n'.join(relevant_lines[-30:])
                    
                    # Show command for debugging
                    cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd)
                    full_error = f"FFmpeg encoding failed (return code: {result.returncode})\n\nCommand:\n{cmd_str}\n\nError output:\n{error_summary}"
                    raise Exception(full_error)
            finally:
                # Clean up temporary image
                if tmp_img_path and os.path.exists(tmp_img_path):
                    os.unlink(tmp_img_path)
            
            # Verify output file exists and return path
            if os.path.exists(out_path):
                return out_path
            else:
                print(f" ✗ Video file not created: {out_path}")
                return None
        except Exception as exc:
            print(f" ✗ Failed to render {stem_type} video: {exc}")
            import traceback
            traceback.print_exc()
            return None

    def _mix_sources(self, source_paths: List[str]) -> Optional[AudioSegment]:
        """
        Combine multiple stems using advanced mixing with loudness normalization.
        
        Uses StemProcessor for:
        - Format standardization (2-channel, 44.1kHz)
        - Per-stem gain reduction to prevent clipping
        - Combined loudness normalization
        - Fade in/out for smooth transitions
        """
        if not source_paths:
            return None

        # Use StemProcessor for professional-quality mixing
        mixed = StemProcessor.mix_stems(
            stem_paths=source_paths,
            target_loudness=-1.0,
            apply_fade=True
        )
        return mixed

    def _prepare_audio(self, base_folder: str, config: Dict[str, object]) -> Optional[str]:
        print(f"\n🎧 Preparing audio: {config.get('stem_key')}")
        print(f"   stem_base_path: {self.stem_base_path}")
        print(f"   exists: {os.path.isdir(self.stem_base_path) if self.stem_base_path else 'N/A'}")
        
        sources = [os.path.join(self.stem_base_path, name) for name in config.get("sources", [])]
        print(f"   Looking for sources: {config.get('sources')}")
        
        missing = [path for path in sources if not os.path.exists(path)]
        if missing:
            print(f"   ⚠ Missing: {missing}")
            if os.path.isdir(self.stem_base_path):
                print(f"   📁 Files in stem_base_path: {os.listdir(self.stem_base_path)}")
            if config.get("mix"):
                return None
            if len(missing) == len(sources):
                return None
            sources = [p for p in sources if os.path.exists(p)]
        else:
            print(f"   ✓ All sources found")

        stem_key = str(config.get("stem_key", "stem"))
        audio_path = os.path.join(base_folder, f"{os.path.basename(base_folder)}.mp3")
        os.makedirs(base_folder, exist_ok=True)

        try:
            if config.get("mix"):
                print(f"   Mixing {len(sources)} sources...")
                segment = self._mix_sources(sources)
                if segment is None:
                    print(f"   ✗ Failed to mix sources")
                    return None
                segment.export(audio_path, format="mp3")
                print(f"   ✓ Mixed and exported: {audio_path}")
            else:
                # Copying audio file
                shutil.copy(sources[0], audio_path)
                print(f"   ✓ Copied: {audio_path}")
        except Exception as exc:
            print(f"   ✗ Failed to prepare audio for {stem_key}: {exc}")
            import traceback
            traceback.print_exc()
            return None

        if os.path.exists(audio_path):
            print(f"   ✓ Audio ready: {audio_path} ({os.path.getsize(audio_path)} bytes)")
            return audio_path
        else:
            print(f"   ✗ Audio file not created")
            return None

    def process_stem(
        self,
        stem_type: str,
        config: Dict[str, object],
        track: Dict[str, Any],
        thumb_path: Optional[str],
        step_offset: float,
        total_steps: int,
    ) -> bool:
        print(f"\n{'='*60}")
        print(f"Processing Stem: {stem_type}")
        print(f"{'='*60}")
        
        # Get BPM/Key from args first (has analyzed values), then fallback to track_info
        # Priority: args["bpm"]/args["key"] (analyzed) > track["tempo"]/track["key"] (original)
        bpm_value = self.args.get("bpm") or track.get("tempo", 0)
        key_value = self.args.get("key") or track.get("key", "Unknown")
        
        # Ensure we have valid values (analyzed values should be set by dispatch_stem_processing)
        if not bpm_value or bpm_value == 0:
            # Fallback: try track_info from args
            track_info = self.args.get("track_info", {})
            bpm_value = track_info.get("tempo") or track.get("tempo", 0)
        if not key_value or key_value == "Unknown":
            # Fallback: try track_info from args
            track_info = self.args.get("track_info", {})
            key_value = track_info.get("key") or track.get("key", "Unknown")
        
        bpm = format_bpm_label(bpm_value)
        key = str(key_value) if key_value and key_value != "Unknown" else "Unknown"
        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")
        channel = self.channel_label

        meta = self.build_meta(stem_type, channel, track)
        folder_title = self._build_folder_title(artist, title, stem_type, bpm, key)
        base_folder = os.path.join(channel, self.genre_folder, stem_type, folder_title)
        
        # Verify BPM/Key are being used correctly for MP4 filename
        # Expected format: "Artist - Song StemType [BPM 95 D major]"
        print(f"Artist: {artist}")
        print(f"Title: {title}")
        print(f"Channel: {channel}")
        print(f"BPM: {bpm} | Key: {key}")
        print(f"MP4 filename will be: {folder_title}.mp4")

        self.incremental_progress(
            f"🎧 Preparing {stem_type} audio...",
            step_offset,
            total_steps,
            meta,
        )
        audio_path = self._prepare_audio(base_folder, config)
        if not audio_path:
            self.update_progress(f" {stem_type} audio unavailable", meta)
            return False

        if self.trim_track:
            audio_path = self.trim_audio(audio_path, self.trim_length)

        self.incremental_progress(
            f"🏷️ Tagging {stem_type} metadata...",
            step_offset + 0.2,
            total_steps,
            meta,
        )
        self._tag_stem(audio_path, stem_type, bpm, key)

        # Section 4: Fast Mode - Skip video rendering, branding, thumbnails when Upload=OFF
        # But allow MP4 creation if render_videos flag is set (for local use without upload)
        upload_enabled = self.args.get("yt", False)
        render_videos = upload_enabled or self.args.get("render_videos", False)
        fast_mode = not render_videos
        
        if fast_mode:
            # Fast mode: skipping video rendering
            self.incremental_progress(
                f"⏩ Fast Mode: Skipping video rendering for {stem_type}...",
                step_offset + 0.4,
                total_steps,
                meta,
            )
            # Fast Mode: Just save audio, no video
            stem_key = str(config.get("stem_key", stem_type.lower()))
            # Store audio path instead of video path for fast mode
            self.video_paths[stem_key] = audio_path
            
            self.incremental_progress(
                f"✓ Fast Mode: {stem_type} audio ready",
                step_offset + 0.6,
                total_steps,
                meta,
            )
            return True
        else:
            # Enhanced Mode: Full video rendering with branding
            self.incremental_progress(
                f"🎬 Rendering {stem_type} video...",
                step_offset + 0.4,
                total_steps,
                meta,
            )
            video_path = self._render_video(audio_path, thumb_path, stem_type, bpm, key, artist, title)
            if not video_path:
                if not MOVIEPY_AVAILABLE:
                    self.update_progress(
                        " moviepy not installed; install with `pip install moviepy` to render videos",
                        meta,
                    )
                else:
                    self.update_progress(f" Failed to render {stem_type} video", meta)
                return False

            stem_key = str(config.get("stem_key", stem_type.lower()))
            self.video_paths[stem_key] = video_path
            if thumb_path:
                self.thumbnail_map[stem_key] = thumb_path

            self.incremental_progress(
                f"☁️ Syncing {stem_type} assets...",
                step_offset + 0.6,
                total_steps,
                meta,
            )
            self.upload_to_ec2_if_needed(base_folder)
            return True

    def upload_batch_to_youtube(self, track: Dict[str, Any]):
        """Upload all stems to YouTube with metadata and per-channel comments."""
        if not (self.args.get("yt") and self.video_paths):
            return

        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")
        # Get BPM/Key from args first (has analyzed values), then fallback to track_info
        bpm_value = self.args.get("bpm") or track.get("tempo", 0)
        key_value = self.args.get("key") or track.get("key", "Unknown")
        bpm = format_bpm_label(bpm_value)
        key = str(key_value) if key_value and key_value != "Unknown" else "Unknown"

        key_text = str(key).strip() if key else ""

        # Build titles with artist, track name, stem type, and BPM/Key
        # Section 3: Drums channel = BPM only, Drums stem = BPM only, others = BPM + Key
        # BPM label comes before the number
        channel_key = self.args.get("channel", "").lower()
        is_drum_channel = channel_key in ["son_got_drums", "drum", "drums", "songotdrums"]
        
        # Drums channel: never show key, regardless of stem type
        # Drums stem: never show key, regardless of channel
        title_map = {}
        for stem_key in ["acapella", "drums", "bass", "melody", "instrumental"]:
            stem_display = stem_key.title()
            is_drum_stem = stem_key.lower() == "drums"
            # Only add key if NOT drum channel AND NOT drum stem
            if is_drum_channel or is_drum_stem:
                title_map[stem_key] = f"{artist} - {title} {stem_display} [BPM {bpm}]"
            else:
                title_map[stem_key] = f"{artist} - {title} {stem_display} [BPM {bpm}{(' ' + key_text) if key_text else ''}]"

        # Merge default tags with artist/title/stem types
        tags = list({
            *DEFAULT_TAGS,
            artist,
            title,
            "acapella",
            "drums",
            "instrumental",
        })

        # Use provided description or DEFAULT_DESCRIPTION
        description = self.args.get("description") or DEFAULT_DESCRIPTION

        # Get per-channel comment (if available)
        comments_map = self.args.get("comments", {})
        channel_key = self.args.get("channel", "")
        comment = comments_map.get(channel_key) if comments_map else None

        # Privacy defaults to "public"
        privacy = self.args.get("privacy", "public")

        from yt_video_multi import upload_all_stems

        self.update_progress(" Uploading main channel stems to YouTube...", {"artist": artist})
        upload_all_stems(
            stem_files=self.video_paths,
            title_map=title_map,
            description=description,
            tags=tags,
            category_id="10",
            playlist=self.args.get("playlist"),
            privacy=privacy,
            publish_at=self.args.get("publish_at"),
            tz=self.args.get("tz", "America/Chicago"),
            made_for_kids=self.args.get("made_for_kids", False),
            lang="en",
            thumbnail_map=self.thumbnail_map,
            comment=comment,  # Per-channel comment support
            dry_run=self.args.get("dry_run", False),
            channel_override=self.args.get("channel"),
        )

    def upload_batch_to_tiktok(self, track: Dict[str, Any]):
        """Upload all stems to TikTok with metadata and per-stem captions."""
        if not self.video_paths:
            return

        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")
        # Get BPM/Key from args first (has analyzed values), then fallback to track_info
        bpm_value = self.args.get("bpm") or track.get("tempo", 0)
        key_value = self.args.get("key") or track.get("key", "Unknown")
        bpm = format_bpm_label(bpm_value)
        key = str(key_value) if key_value and key_value != "Unknown" else "Unknown"

        key_text = str(key).strip() if key else ""

        try:
            uploader = TikTokUploader()
            if not uploader.is_authenticated():
                self.update_progress(" TikTok not authenticated — skipping TikTok upload", {"artist": artist})
                return

            # Merge default tags
            tags = list({
                *DEFAULT_TAGS,
                artist,
                title,
                "acapella",
                "drums",
                "instrumental",
            })

            description = self.args.get("description", DEFAULT_DESCRIPTION)
            comments_map = self.args.get("comments", {})
            channel_key = self.args.get("channel", "")
            comment = comments_map.get(channel_key) if comments_map else None

            self.update_progress(" Uploading stems to TikTok...", {"artist": artist})

            # Upload each stem as separate TikTok video
            for stem_type, video_path in self.video_paths.items():
                if not os.path.exists(video_path):
                    print(f" Skipping TikTok upload for {stem_type} — file not found")
                    continue

                # Rule: Drums channel (son_got_drums) NEVER shows key, regardless of stem type
                # Rule: Drums stem type NEVER shows key, regardless of channel
                channel_key = self.args.get("channel", "").lower()
                is_drum_channel = channel_key in ["son_got_drums", "drum", "drums", "songotdrums"]
                is_drum_stem = stem_type.lower() == "drums"
                
                # Only add key if NOT drum channel AND NOT drum stem
                if is_drum_channel or is_drum_stem:
                    stem_title = f"{artist} - {title} {stem_type.title()} [BPM {bpm}]"
                else:
                    stem_title = f"{artist} - {title} {stem_type.title()} [BPM {bpm}{(' ' + key_text) if key_text else ''}]"
                
                # Uploading to TikTok
                video_id = uploader.upload_video(
                    video_path=video_path,
                    title=stem_title,
                    description=description,
                    tags=tags,
                    thumbnail_path=self.thumbnail_map.get(stem_type)
                )

                if video_id and comment:
                    # Posting TikTok comment
                    uploader.post_comment(video_id, comment)

                self.update_progress(f" {stem_type.title()} uploaded to TikTok", {"artist": artist})

        except Exception as e:
            self.update_progress(f" TikTok batch upload failed: {e}", {"artist": artist})

    def download(self, track_id: str):
        track = self.track_info or self.get_track_info(track_id)
        if not track:
            self.fail_progress_with_meta(" track_info unavailable", "Acapella", self.channel_label, {"id": track_id})
            return

        if not self.stem_base_path or not os.path.isdir(self.stem_base_path):
            self.fail_progress_with_meta(" stem_base_path missing", "Acapella", self.channel_label, track)
            return

        total_steps = 2 + len(self.STEM_DEFINITIONS) * 4
        self.progress_with_meta("🔍 Preparing main channel stems...", 1, total_steps, "Acapella", self.channel_label, track)

        # Get BPM/Key from args first (has analyzed values), then fallback to track_info
        bpm_value = self.args.get("bpm") or track.get("tempo", 0)
        key_value = self.args.get("key") or track.get("key", "Unknown")
        bpm = format_bpm_label(bpm_value)
        key = str(key_value) if key_value and key_value != "Unknown" else "Unknown"
        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")

        # Section 4: Fast Mode - Skip thumbnail generation when Upload=OFF
        # But allow if render_videos flag is set
        upload_enabled = self.args.get("yt", False)
        render_videos = upload_enabled or self.args.get("render_videos", False)
        fast_mode = not render_videos
        
        if fast_mode:
            # Fast mode: skipping thumbnail
            thumb_path = None
        else:
            self.progress_with_meta(" Fetching thumbnail...", 2, total_steps, "Acapella", self.channel_label, track)
            thumb_path = self.download_thumbnail(track.get("img"), artist=artist, title=title, bpm=bpm, key=key)

        processed_any = False
        selected_stems_map = self.args.get("selected_stems", {})
        channel_key = self.args.get("channel", "")
        selected_stems_for_channel = selected_stems_map.get(channel_key, list(self.STEM_DEFINITIONS.keys()))
        
        for idx, (stem_type, config) in enumerate(self.STEM_DEFINITIONS.items(), start=1):
            stem_lower = stem_type.lower()
            if stem_lower not in [s.lower() for s in selected_stems_for_channel]:
                # Silently skip unselected stems - no message needed
                continue
            base_step = 2 + (idx - 1) * 4 + 1
            success = self.process_stem(stem_type, config, track, thumb_path, base_step, total_steps)
            processed_any = processed_any or success

        if not processed_any:
            self.fail_progress_with_meta(" No stems were processed for the main channel", "Acapella", self.channel_label, track)
            return

        # Store video paths for batch upload after all channels are processed
        # Upload will happen after all channels finish processing (in dispatch_download.py)
        # This allows all MP4s to be created first, then upload all at once (faster)
        render_videos = self.args.get("yt", False) or self.args.get("render_videos", False)
        
        if not render_videos and not self.args.get("yt"):
            meta = self.build_meta("Acapella", self.channel_label, track)
            # Videos not needed - processing complete
            pass
        # Note: Upload is deferred to batch upload after all channels process
        # This is handled in dispatch_download.py after all channels finish

        self.progress_with_meta(" Main channel stems complete!", total_steps, total_steps, "Acapella", self.channel_label, track)
        self.mark_complete_with_meta(" Main channel upload ready", "Acapella", self.channel_label, track)


# Helper to normalize resize availability across environments
def _clip_resize(clip, *args, **kwargs):
    return apply_moviepy_resize(clip, *args, **kwargs)
