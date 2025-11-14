import os
import json
import time
import requests
from typing import Optional, Dict, Any, List
from pathlib import Path


TIKTOK_TOKENS_DIR = Path("yt_tokens")
TIKTOK_CONFIG_FILE = TIKTOK_TOKENS_DIR / "tiktok_config.json"
TIKTOK_API_RETRY_LIMIT = 5
TIKTOK_BACKOFF_BASE = 1.5

TIKTOK_CAPTION_LIMIT = 2048
TIKTOK_COMMENT_LIMIT = 150
TIKTOK_MAX_TAGS_IN_CAPTION = 20
TIKTOK_MAX_FILE_SIZE = 287661056


def _build_tiktok_caption(
    title: str,
    description: str,
    tags: List[str],
    max_length: int = TIKTOK_CAPTION_LIMIT
) -> str:
    """
    Build TikTok caption from title, description, and tags.
    Respects TikTok's 2048 character limit.
    
    Args:
        title: Video title
        description: Video description
        tags: List of tags/hashtags
        max_length: Maximum caption length (default: 2048)
        
    Returns:
        Properly formatted caption within character limit
    """
    title = str(title).strip()[:100]
    description = str(description).strip()[:500]
    
    parts = [title]
    if description:
        parts.append(description)
    
    caption_base = "\n".join(parts)
    
    tag_strings = [f"#{tag.strip().replace(' ', '')}" for tag in tags if tag.strip()]
    tag_strings = tag_strings[:TIKTOK_MAX_TAGS_IN_CAPTION]
    
    if tag_strings:
        caption_base += "\n" + " ".join(tag_strings)
    
    if len(caption_base) > max_length:
        truncated = caption_base[:max_length - 3] + "..."
        return truncated
    
    return caption_base


class TikTokUploader:
    def __init__(self):
        self.config = self._load_config()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    def _load_config(self) -> Dict:
        if os.path.exists(TIKTOK_CONFIG_FILE):
            try:
                with open(TIKTOK_CONFIG_FILE, "r") as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _save_config(self):
        TIKTOK_TOKENS_DIR.mkdir(parents=True, exist_ok=True)
        with open(TIKTOK_CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=2)

    def _exponential_backoff(self, attempt: int) -> float:
        return min(TIKTOK_BACKOFF_BASE ** attempt, 60)

    def authenticate(self, access_token: str, user_id: str) -> bool:
        try:
            self.config["access_token"] = access_token
            self.config["user_id"] = user_id
            self.config["authenticated"] = True
            self.config["auth_timestamp"] = time.time()
            self._save_config()
            print(" TikTok authentication configured")
            return True
        except Exception as e:
            print(f" TikTok authentication failed: {e}")
            return False

    def is_authenticated(self) -> bool:
        return self.config.get("authenticated", False)

    def upload_video(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: List[str],
        thumbnail_path: Optional[str] = None,
        allow_download: bool = True,
        allow_duet: bool = True,
        allow_stitch: bool = True
    ) -> Optional[str]:
        """
        Upload video to TikTok with metadata.
        
        Metadata is reused from YouTube structure but adapted for TikTok limits:
        - Caption (2048 chars max): title + description + tags
        - Tags: Limited to 20, formatted as hashtags
        - File size: 287MB max
        - No support: scheduling, privacy, playlists
        """
        if not os.path.exists(video_path):
            print(f" Video file not found: {video_path}")
            return None

        if not self.is_authenticated():
            print(" TikTok not authenticated")
            return None

        file_size = os.path.getsize(video_path)

        if file_size > TIKTOK_MAX_FILE_SIZE:
            print(f" Video file too large: {file_size} bytes (max: {TIKTOK_MAX_FILE_SIZE})")
            return None

        for attempt in range(TIKTOK_API_RETRY_LIMIT):
            try:
                print(f" Uploading to TikTok (attempt {attempt + 1}/{TIKTOK_API_RETRY_LIMIT})...")

                caption = _build_tiktok_caption(title, description, tags, TIKTOK_CAPTION_LIMIT)

                with open(video_path, "rb") as f:
                    files = {
                        "video": (os.path.basename(video_path), f, "video/mp4")
                    }
                    data = {
                        "caption": caption,
                        "allow_download": "true" if allow_download else "false",
                        "allow_duet": "true" if allow_duet else "false",
                        "allow_stitch": "true" if allow_stitch else "false"
                    }

                    headers = {
                        "Authorization": f"Bearer {self.config.get('access_token')}"
                    }

                    response = self.session.post(
                        f"https://open.tiktok.com/oauth/video/publish/",
                        files=files,
                        data=data,
                        headers=headers,
                        timeout=300
                    )

                    if response.status_code == 200:
                        result = response.json()
                        video_id = result.get("data", {}).get("video_id")
                        print(f" TikTok upload successful: {video_id}")
                        return video_id
                    elif response.status_code in [429, 500, 502, 503, 504]:
                        wait_time = self._exponential_backoff(attempt)
                        print(f" Rate limited/Server error, waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        continue
                    else:
                        print(f" TikTok upload failed: {response.status_code} - {response.text}")
                        return None

            except Exception as e:
                print(f" TikTok upload attempt {attempt + 1} error: {e}")
                if attempt < TIKTOK_API_RETRY_LIMIT - 1:
                    wait_time = self._exponential_backoff(attempt)
                    print(f" Waiting {wait_time}s before retry...")
                    time.sleep(wait_time)

        print(f" Failed to upload to TikTok after {TIKTOK_API_RETRY_LIMIT} attempts")
        return None

    def post_comment(self, video_id: str, comment_text: str) -> bool:
        """
        Post comment on TikTok video.
        Comments are limited to 150 characters.
        """
        if not self.is_authenticated():
            print(" TikTok not authenticated")
            return False

        try:
            headers = {
                "Authorization": f"Bearer {self.config.get('access_token')}"
            }
            data = {
                "text": str(comment_text).strip()[:TIKTOK_COMMENT_LIMIT]
            }

            response = self.session.post(
                f"https://open.tiktok.com/v1/video/{video_id}/comment/create/",
                json=data,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                print(f" TikTok comment posted successfully")
                return True
            else:
                print(f" TikTok comment posting failed: {response.status_code}")
                return False

        except Exception as e:
            print(f" Error posting TikTok comment: {e}")
            return False

    def get_video_analytics(self, video_id: str) -> Optional[Dict]:
        if not self.is_authenticated():
            print(" TikTok not authenticated")
            return None

        try:
            headers = {
                "Authorization": f"Bearer {self.config.get('access_token')}"
            }

            response = self.session.get(
                f"https://open.tiktok.com/v1/video/{video_id}/query/",
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                return response.json().get("data", {})
            else:
                print(f" Failed to get analytics: {response.status_code}")
                return None

        except Exception as e:
            print(f" Error fetching analytics: {e}")
            return None


_uploader = TikTokUploader()


def upload_to_tiktok(
    video_path: str,
    title: str,
    description: str,
    tags: List[str],
    thumbnail_path: Optional[str] = None,
    category_id: Optional[str] = None,
    playlist: Optional[str] = None,
    privacy: Optional[str] = None,
    publish_at: Optional[str] = None,
    made_for_kids: Optional[bool] = None,
    **kwargs
) -> Optional[str]:
    """
    Upload to TikTok with YouTube-compatible metadata structure.
    
    Args:
        video_path: Path to MP4 video file
        title: Video title (100 chars max for YouTube, 2048 for TikTok caption)
        description: Video description (5000 chars for YouTube, 2048 for TikTok caption)
        tags: List of tags (normalized for both platforms)
        thumbnail_path: Optional thumbnail path (supported by both)
        category_id: YouTube only (ignored for TikTok)
        playlist: YouTube only (ignored for TikTok)
        privacy: YouTube only (ignored for TikTok)
        publish_at: YouTube only (ignored for TikTok)
        made_for_kids: YouTube only (ignored for TikTok)
        **kwargs: Additional platform-specific options
        
    Returns:
        Video ID if successful, None otherwise
    """
    return _uploader.upload_video(
        video_path,
        title,
        description,
        tags,
        thumbnail_path,
        **kwargs
    )


def tiktok_is_ready() -> bool:
    return _uploader.is_authenticated()
