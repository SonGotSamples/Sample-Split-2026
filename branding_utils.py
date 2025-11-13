"""
Branding utilities for video rendering.
Handles intro cards and image resizing for video clips.
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

