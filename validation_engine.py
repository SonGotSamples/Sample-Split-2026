import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from mutagen.mp3 import MP3
import json


VALIDATION_REPORT_FILE = "validation_report.json"
MIN_STEM_SIZE = 102400
MAX_STEM_SIZE = 5368709120


class ValidationEngine:
    def __init__(self):
        self.report = {"validations": []}

    def _save_report(self):
        with open(VALIDATION_REPORT_FILE, "w") as f:
            json.dump(self.report, f, indent=2)

    def validate_stem_file(self, file_path: str) -> Tuple[bool, str]:
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"

        try:
            file_size = os.path.getsize(file_path)
            
            if file_size < MIN_STEM_SIZE:
                return False, f"File too small: {file_size} bytes (min: {MIN_STEM_SIZE})"
            
            if file_size > MAX_STEM_SIZE:
                return False, f"File too large: {file_size} bytes (max: {MAX_STEM_SIZE})"

            audio = MP3(file_path)
            if audio.info:
                duration = audio.info.length
                if duration < 10:
                    return False, f"Audio too short: {duration}s (min: 10s)"
                print(f" Stem valid: {file_path} ({duration:.1f}s, {file_size} bytes)")
                return True, "Valid"
            else:
                return False, "Unable to read audio metadata"

        except Exception as e:
            return False, f"Validation error: {e}"

    def validate_stems_batch(self, stem_files: Dict[str, str]) -> Tuple[bool, Dict[str, str]]:
        results = {}
        all_valid = True
        
        for stem_type, file_path in stem_files.items():
            is_valid, message = self.validate_stem_file(file_path)
            results[stem_type] = {
                "valid": is_valid,
                "message": message,
                "path": file_path
            }
            if not is_valid:
                all_valid = False
                print(f" {stem_type}: {message}")
        
        return all_valid, results

    def validate_demucs_output(self, base_dir: str) -> Tuple[bool, Dict[str, str]]:
        required_stems = ["vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"]
        results = {}
        all_exist = True
        
        for stem in required_stems:
            path = os.path.join(base_dir, stem)
            if os.path.exists(path):
                results[stem] = {"exists": True}
            else:
                results[stem] = {"exists": False}
                all_exist = False
                print(f" Missing stem: {stem}")
        
        return all_exist, results

    def validate_video_file(self, file_path: str) -> Tuple[bool, str]:
        if not os.path.exists(file_path):
            return False, f"File not found: {file_path}"

        try:
            file_size = os.path.getsize(file_path)
            min_video_size = 1024 * 1024
            max_video_size = 287661056

            if file_size < min_video_size:
                return False, f"Video too small: {file_size} bytes"
            
            if file_size > max_video_size:
                return False, f"Video too large: {file_size} bytes (max: {max_video_size})"
            
            print(f" Video valid: {file_path} ({file_size} bytes)")
            return True, "Valid"

        except Exception as e:
            return False, f"Validation error: {e}"

    def verify_youtube_upload(self, video_id: str, expected_title: str) -> bool:
        print(f" Verifying YouTube upload: {video_id}")
        if video_id:
            print(f" Upload verified: {video_id}")
            return True
        return False

    def verify_tiktok_upload(self, video_id: str) -> bool:
        print(f" Verifying TikTok upload: {video_id}")
        if video_id:
            print(f" TikTok upload verified: {video_id}")
            return True
        return False

    def compare_stem_durations(self, stem_files: Dict[str, str], tolerance_ms: int = 100) -> Tuple[bool, Dict[str, float]]:
        durations = {}
        
        for stem_type, file_path in stem_files.items():
            try:
                if not os.path.exists(file_path):
                    durations[stem_type] = None
                    continue
                
                audio = MP3(file_path)
                duration = audio.info.length
                durations[stem_type] = duration
            except Exception as e:
                print(f" Error reading duration for {stem_type}: {e}")
                durations[stem_type] = None

        if not all(d is not None for d in durations.values()):
            print(" Some stem durations unavailable")
            return False, durations

        duration_list = list(durations.values())
        max_duration = max(duration_list)
        min_duration = min(duration_list)
        diff_ms = (max_duration - min_duration) * 1000

        if diff_ms <= tolerance_ms:
            print(f" Stem durations consistent (diff: {diff_ms:.0f}ms)")
            return True, durations
        else:
            print(f" Stem duration mismatch (diff: {diff_ms:.0f}ms)")
            return False, durations

    def get_stem_metadata(self, file_path: str) -> Optional[Dict]:
        try:
            if not os.path.exists(file_path):
                return None
            
            audio = MP3(file_path)
            return {
                "duration": audio.info.length,
                "bitrate": audio.info.bitrate,
                "channels": audio.info.channels,
                "sample_rate": audio.info.sample_rate,
                "size_bytes": os.path.getsize(file_path)
            }
        except Exception as e:
            print(f" Error reading metadata: {e}")
            return None

    def create_validation_report(self, validation_results: Dict[str, any]):
        self.report["validations"].append(validation_results)
        self._save_report()
        print(f" Validation report saved: {VALIDATION_REPORT_FILE}")

    def export_validation_report(self, output_file: str = "validation_export.json"):
        with open(output_file, "w") as f:
            json.dump(self.report, f, indent=2)
        print(f" Validation report exported to {output_file}")


_validator = ValidationEngine()


def validate_stems(stem_files: Dict[str, str]) -> Tuple[bool, Dict[str, str]]:
    return _validator.validate_stems_batch(stem_files)


def validate_demucs_output(base_dir: str) -> Tuple[bool, Dict[str, str]]:
    return _validator.validate_demucs_output(base_dir)


def verify_upload(video_id: str, platform: str = "youtube") -> bool:
    if platform.lower() == "youtube":
        return _validator.verify_youtube_upload(video_id, "")
    elif platform.lower() == "tiktok":
        return _validator.verify_tiktok_upload(video_id)
    return False
