# File: content_download_main_fixed.py
"""Main channel stem processor.

This module prepares acapella, drums, and instrumental stems for the main channel.
It mirrors the structure used by other stem processors so that progress reporting,
folder layouts, and YouTube uploads behave consistently.
"""

from __future__ import annotations

import os
import shutil
from typing import Any, Dict, List, Optional

try:
    from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip
    MOVIEPY_AVAILABLE = True
    MOVIEPY_IMPORT_ERROR: Optional[Exception] = None
except ModuleNotFoundError as exc:  # pragma: no cover - optional dependency
    AudioFileClip = ColorClip = ImageClip = None  # type: ignore[assignment]
    MOVIEPY_AVAILABLE = False
    MOVIEPY_IMPORT_ERROR = exc
from mutagen.easyid3 import EasyID3
from mutagen.id3 import COMM, ID3, TIT2
from mutagen.mp3 import MP3
from pydub import AudioSegment

from branding_utils import add_intro_card, apply_moviepy_resize
from content_base import (
    ContentBase,
    DEFAULT_DESCRIPTION,
    DEFAULT_TAGS,
    format_bpm_label,
    normalize_genre,
)
from shared_state import get_progress, set_progress

# Register custom comment tag once.
if "comment" not in EasyID3.valid_keys:
    EasyID3.RegisterTXXXKey("comment", "comment")


class Content_download_main(ContentBase):
    """Prepare acapella, drums, and instrumental stems for the main channel."""

    STEM_DEFINITIONS: Dict[str, Dict[str, object]] = {
        "Acapella": {"stem_key": "acapella", "sources": ["vocals.mp3"]},
        "Drums": {"stem_key": "drums", "sources": ["drums.mp3"]},
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
        print(f"[PROGRESS] {self.session_id} → {message} ({update['percent']}%)")

    def _build_folder_title(self, artist: str, title: str, stem_type: str, bpm: str, key: str) -> str:
        if stem_type.lower() == "drums":
            return self.sanitize_name(f"{artist} - {title} {stem_type} [{bpm} BPM]")
        return self.sanitize_name(f"{artist} - {title} {stem_type} [{bpm} BPM {key}]")

    def _tag_stem(self, file_path: str, stem_type: str, bpm: str, key: str) -> None:
        """Write ID3 tags for exported stems."""
        comment_text = f"BPM: {bpm}" if stem_type.lower() == "drums" else f"Key: {key}, BPM: {bpm}"
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
                "⚠️ moviepy is not installed; skipping video rendering for "
                f"{stem_type}. Install via `{error_hint}` to enable video exports{detail}."
            )
            return None

        try:
            channel = self.channel_label
            genre = self.genre_folder
            folder_title = self._build_folder_title(artist, track_title, stem_type, bpm, key)
            filename = f"{folder_title}.mp4"
            out_dir = os.path.join("MP4", channel, genre, stem_type, folder_title)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, filename)

            audio_clip = AudioFileClip(audio_path)
            branded_clip = add_intro_card(audio_clip.duration, channel, thumb_path, stem_type)
            if not branded_clip:
                thumb = thumb_path if thumb_path and os.path.exists(thumb_path) else None
                if thumb:
                    thumb_clip = ImageClip(thumb).with_duration(audio_clip.duration)
                    thumb_clip = _clip_resize(thumb_clip, new_size=(720, 720))
                    thumb_clip = thumb_clip.with_position("center")
                    background = ColorClip(
                        size=(1280, 720), color=(0, 0, 0), duration=audio_clip.duration
                    )
                    branded_clip = CompositeVideoClip(
                        [background, thumb_clip], size=(1280, 720)
                    )
                else:
                    branded_clip = ColorClip(
                        size=(1280, 720), color=(0, 0, 0), duration=audio_clip.duration
                    )

            final_video = branded_clip.with_audio(audio_clip)
            final_video.write_videofile(out_path, fps=1, codec="libx264", audio_codec="aac")
            return out_path
        except Exception as exc:
            print(f"❌ Failed to render {stem_type} video: {exc}")
            return None

    def _mix_sources(self, source_paths: List[str]) -> Optional[AudioSegment]:
        """Combine multiple stems into a single AudioSegment."""
        combined: Optional[AudioSegment] = None
        for src in source_paths:
            seg = AudioSegment.from_file(src)
            seg = seg.set_channels(2).set_frame_rate(44100) - 1.5
            combined = seg if combined is None else combined.overlay(seg)

        if combined is None:
            return None

        peak = combined.max_dBFS
        target = -1.0
        if peak > target:
            combined = combined.apply_gain(target - peak)
        return combined

    def _prepare_audio(self, base_folder: str, config: Dict[str, object]) -> Optional[str]:
        sources = [os.path.join(self.stem_base_path, name) for name in config.get("sources", [])]
        missing = [path for path in sources if not os.path.exists(path)]
        if missing:
            print(f"⚠️ Missing sources for {config.get('stem_key')}: {missing}")
            if config.get("mix"):
                return None
            if len(missing) == len(sources):
                return None
            sources = [p for p in sources if os.path.exists(p)]

        stem_key = str(config.get("stem_key", "stem"))
        audio_path = os.path.join(base_folder, f"{os.path.basename(base_folder)}.mp3")
        os.makedirs(base_folder, exist_ok=True)

        try:
            if config.get("mix"):
                segment = self._mix_sources(sources)
                if segment is None:
                    return None
                segment.export(audio_path, format="mp3")
            else:
                shutil.copy(sources[0], audio_path)
        except Exception as exc:
            print(f"❌ Failed to prepare audio for {stem_key}: {exc}")
            return None

        return audio_path if os.path.exists(audio_path) else None

    def process_stem(
        self,
        stem_type: str,
        config: Dict[str, object],
        track: Dict[str, Any],
        thumb_path: Optional[str],
        step_offset: float,
        total_steps: int,
    ) -> bool:
        bpm = format_bpm_label(track.get("tempo", 0))
        key = track.get("key", "Unknown")
        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")
        channel = self.channel_label

        meta = self.build_meta(stem_type, channel, track)
        folder_title = self._build_folder_title(artist, title, stem_type, bpm, key)
        base_folder = os.path.join(channel, self.genre_folder, stem_type, folder_title)

        self.incremental_progress(
            f"🎧 Preparing {stem_type} audio...",
            step_offset,
            total_steps,
            meta,
        )
        audio_path = self._prepare_audio(base_folder, config)
        if not audio_path:
            self.update_progress(f"❌ {stem_type} audio unavailable", meta)
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
                    "❌ moviepy not installed; install with `pip install moviepy` to render videos",
                    meta,
                )
            else:
                self.update_progress(f"❌ Failed to render {stem_type} video", meta)
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
        if not (self.args.get("yt") and self.video_paths):
            return

        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")
        bpm = format_bpm_label(track.get("tempo", 0))
        key = track.get("key", "Unknown")

        key_text = str(key).strip() if key else ""

        # ✅ FIXED: Added hyphen between artist and song name
        title_map = {
            "acapella": f"{artist} - {title} Acapella [{bpm} BPM{(' ' + key_text) if key_text else ''}]",
            "drums": f"{artist} - {title} Drums [{bpm} BPM]",
            "instrumental": f"{artist} - {title} Instrumental [{bpm} BPM{(' ' + key_text) if key_text else ''}]",
        }

        tags = list({
            *DEFAULT_TAGS,
            artist,
            title,
            "acapella",
            "drums",
            "instrumental",
        })

        from yt_video_multi import upload_all_stems

        self.update_progress("📤 Uploading main channel stems to YouTube...", {"artist": artist})
        upload_all_stems(
            stem_files=self.video_paths,
            title_map=title_map,
            description=DEFAULT_DESCRIPTION,
            tags=tags,
            category_id="10",
            playlist=self.args.get("playlist"),
            privacy=self.args.get("privacy", "private"),
            publish_at=self.args.get("publish_at"),
            tz=self.args.get("tz", "America/Chicago"),
            made_for_kids=False,
            lang="en",
            thumbnail_map=self.thumbnail_map,
            comment=None,
            dry_run=self.args.get("dry_run", False),
            channel_override=self.args.get("channel"),
        )

    def download(self, track_id: str):
        track = self.track_info or self.get_track_info(track_id)
        if not track:
            self.fail_progress_with_meta("❌ track_info unavailable", "Acapella", self.channel_label, {"id": track_id})
            return

        if not self.stem_base_path or not os.path.isdir(self.stem_base_path):
            self.fail_progress_with_meta("❌ stem_base_path missing", "Acapella", self.channel_label, track)
            return

        total_steps = 2 + len(self.STEM_DEFINITIONS) * 4
        self.progress_with_meta("🔍 Preparing main channel stems...", 1, total_steps, "Acapella", self.channel_label, track)

        bpm = format_bpm_label(track.get("tempo", 0))
        key = track.get("key", "Unknown")
        artist = track.get("artist", "Unknown Artist")
        title = track.get("name", "Unknown Track")

        self.progress_with_meta("🖼️ Fetching thumbnail...", 2, total_steps, "Acapella", self.channel_label, track)
        thumb_path = self.download_thumbnail(track.get("img"), artist=artist, title=title, bpm=bpm, key=key)

        processed_any = False
        for idx, (stem_type, config) in enumerate(self.STEM_DEFINITIONS.items(), start=1):
            base_step = 2 + (idx - 1) * 4 + 1
            success = self.process_stem(stem_type, config, track, thumb_path, base_step, total_steps)
            processed_any = processed_any or success

        if not processed_any:
            self.fail_progress_with_meta("❌ No stems were processed for the main channel", "Acapella", self.channel_label, track)
            return

        if self.args.get("yt"):
            self.upload_batch_to_youtube(track)
        else:
            meta = self.build_meta("Acapella", self.channel_label, track)
            self.update_progress("⏭️ Skipping YouTube upload (yt flag disabled)", meta)

        self.progress_with_meta("✅ Main channel stems complete!", total_steps, total_steps, "Acapella", self.channel_label, track)
        self.mark_complete_with_meta("✅ Main channel upload ready", "Acapella", self.channel_label, track)


# Helper to normalize resize availability across environments
def _clip_resize(clip, *args, **kwargs):
    return apply_moviepy_resize(clip, *args, **kwargs)
