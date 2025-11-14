"""
Branding utilities for video rendering.
Handles intro cards, image resizing, and watermarks for video clips.
"""

from typing import Optional, Union
from pathlib import Path
import os

try:
    from moviepy import ColorClip, CompositeVideoClip, ImageClip, TextClip
    MOVIEPY_AVAILABLE = True
except ImportError:
    MOVIEPY_AVAILABLE = False
    ColorClip = CompositeVideoClip = ImageClip = TextClip = None


WATERMARK_CONFIG = {
    "Main": {"text": "SGS", "opacity": 0.8, "font_size": 60},
    "Back Up": {"text": "SGS2", "opacity": 0.8, "font_size": 60},
    "Drum": {"text": "SGS", "opacity": 0.8, "font_size": 60},
    "Vocal": {"text": "SGS", "opacity": 0.8, "font_size": 60},
    "Sample Split": {"text": "SGS", "opacity": 0.8, "font_size": 60},
    "Tik Tok": {"text": "SGS", "opacity": 0.8, "font_size": 60},
}


def apply_moviepy_resize(clip, new_size=None, *args, **kwargs):
    """
    Resize a moviepy clip.
    
    Args:
        clip: MoviePy clip to resize
        new_size: Tuple of (width, height) or None
        *args, **kwargs: Additional arguments for resized
        
    Returns:
        Resized clip
    """
    if not MOVIEPY_AVAILABLE or clip is None:
        return clip
    
    if new_size:
        return clip.resized(new_size)
    elif args or kwargs:
        return clip.resized(*args, **kwargs)
    return clip


def add_intro_card(duration: float, channel: str, thumb_path: Optional[str] = None, stem_type: str = "") -> Optional[Union["CompositeVideoClip", "ColorClip"]]:
    """
    Create an intro card for videos.
    
    Args:
        duration: Duration of the video in seconds
        channel: Channel name
        thumb_path: Optional path to thumbnail image
        stem_type: Type of stem (e.g., "Acapella", "Drums")
        
    Returns:
        MoviePy clip with intro card, or None if moviepy unavailable
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    try:
        # Create background
        background = ColorClip(
            size=(1280, 720),
            color=(0, 0, 0),
            duration=duration
        )
        
        # Add thumbnail if available
        if thumb_path and os.path.exists(thumb_path):
            try:
                thumb_clip = ImageClip(thumb_path).with_duration(duration)
                thumb_clip = apply_moviepy_resize(thumb_clip, new_size=(720, 720))
                thumb_clip = thumb_clip.with_position("center")
                
                # Composite thumbnail on background
                return CompositeVideoClip([background, thumb_clip], size=(1280, 720))
            except Exception as e:
                print(f"Warning: Could not add thumbnail to intro card: {e}")
                return background
        
        return background
        
    except Exception as e:
        print(f"Warning: Could not create intro card: {e}")
        return None


def add_watermark(clip: "CompositeVideoClip", channel: str) -> "CompositeVideoClip":
    """
    Add bottom-left watermark to a video clip.
    
    Args:
        clip: MoviePy video clip
        channel: Channel name (e.g., "Main", "Back Up")
        
    Returns:
        Composite clip with watermark, or original clip if moviepy unavailable
    """
    if not MOVIEPY_AVAILABLE or clip is None:
        return clip
    
    try:
        config = WATERMARK_CONFIG.get(channel, WATERMARK_CONFIG.get("Main"))
        watermark_text = config["text"]
        opacity = config["opacity"]
        font_size = config["font_size"]
        
        watermark = TextClip(
            text=watermark_text,
            font_size=font_size,
            color="white",
            method="label"
        ).with_duration(clip.duration)
        
        watermark = watermark.with_opacity(opacity)
        
        margin = 20
        watermark = watermark.with_position((margin, clip.size[1] - font_size - (margin * 2)))
        
        return CompositeVideoClip([clip, watermark])
        
    except Exception as e:
        print(f"Warning: Could not add watermark: {e}")
        return clip

