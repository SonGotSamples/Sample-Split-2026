"""
Branding utilities for video rendering.
Handles intro cards, watermarks, stem icons, and image resizing for video clips.

Section 2: Branding Requirements
- SGS/SGS2 watermarks on all channels
- Stem icons (Acapella, Drums, Bass, Melody, Instrumental) top-right
- Consistent branding across all channels
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

# Section 2: Branding asset paths (files in assets/assets/ directory)
BRANDING_ASSETS_DIR = Path("assets/assets")

# Watermark/label paths - map channels to label files
# Files are in assets/assets/label/ directory
# - Main channel: SGS (sgs.png)
# - Backup channel: SGS 2 (sgs_2.png)
# - Acapella channel: Son Got Acapellas (son_got_acappellas.png)
# - Drum channel: Son Got Drums (son_got_drums.png)
# - Sample Split channel: Sample Split (sample_split.png)
WATERMARK_PATHS = {
    # Main channel variations - uses SGS watermark
    "main": BRANDING_ASSETS_DIR / "label" / "sgs.png",
    "Main": BRANDING_ASSETS_DIR / "label" / "sgs.png",  # Capital M variant
    "main_channel": BRANDING_ASSETS_DIR / "label" / "sgs.png",
    "mainchannel": BRANDING_ASSETS_DIR / "label" / "sgs.png",
    
    # Backup/SGS2 channel - uses SGS 2 watermark
    "back up": BRANDING_ASSETS_DIR / "label" / "sgs_2.png",
    "backup": BRANDING_ASSETS_DIR / "label" / "sgs_2.png",
    "sgs_2": BRANDING_ASSETS_DIR / "label" / "sgs_2.png",
    "sgs2": BRANDING_ASSETS_DIR / "label" / "sgs_2.png",
    
    # Drums channel - uses Son Got Drums watermark
    "drum": BRANDING_ASSETS_DIR / "label" / "son_got_drums.png",
    "drums": BRANDING_ASSETS_DIR / "label" / "son_got_drums.png",
    "son_got_drums": BRANDING_ASSETS_DIR / "label" / "son_got_drums.png",
    "songotdrums": BRANDING_ASSETS_DIR / "label" / "son_got_drums.png",
    
    # Vocal/Acapella channel - uses Son Got Acapellas watermark
    "vocal": BRANDING_ASSETS_DIR / "label" / "son_got_acappellas.png",
    "vocals": BRANDING_ASSETS_DIR / "label" / "son_got_acappellas.png",
    "son_got_acapellas": BRANDING_ASSETS_DIR / "label" / "son_got_acappellas.png",
    "son_got_acappellas": BRANDING_ASSETS_DIR / "label" / "son_got_acappellas.png",
    "songotacapellas": BRANDING_ASSETS_DIR / "label" / "son_got_acappellas.png",
    
    # Sample Split channel - uses Sample Split watermark
    "sample split": BRANDING_ASSETS_DIR / "label" / "sample_split.png",
    "sample_split": BRANDING_ASSETS_DIR / "label" / "sample_split.png",
    "samplesplit": BRANDING_ASSETS_DIR / "label" / "sample_split.png",
    
    # TikTok channel - uses TikTok watermark
    "tiktok": BRANDING_ASSETS_DIR / "label" / "tik_tok.png",
    "tiktok_channel": BRANDING_ASSETS_DIR / "label" / "tik_tok.png",
    "tik tok": BRANDING_ASSETS_DIR / "label" / "tik_tok.png",
    "tiktokchannel": BRANDING_ASSETS_DIR / "label" / "tik_tok.png",
}

# Stem icon paths - map stem types to icon files
# Files are in assets/assets/icons/ directory
STEM_ICONS = {
    "acapella": BRANDING_ASSETS_DIR / "icons" / "acapella.png",
    "drums": BRANDING_ASSETS_DIR / "icons" / "drums.png",
    "bass": BRANDING_ASSETS_DIR / "icons" / "bass.png",
    "melody": BRANDING_ASSETS_DIR / "icons" / "melody.png",
    "instrumental": BRANDING_ASSETS_DIR / "icons" / "instrumental.png",
}


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


def _get_watermark_path(channel: str) -> Optional[Path]:
    """
    Section 2: Get watermark/label path based on channel.
    Maps channel names to label files in assets/ directory.
    Main channel uses sgs_watermark.png (Section 2: reuse SGS2 asset, remove '2' and replace with 'SGS').
    """
    channel_lower = channel.lower().strip()
    
    # Try direct lookup first
    if channel_lower in WATERMARK_PATHS:
        path = WATERMARK_PATHS[channel_lower]
        if path.exists():
            return path
    
    # Try partial matches for channel names
    for key, path in WATERMARK_PATHS.items():
        if key in channel_lower or channel_lower in key:
            if path.exists():
                return path
    
    # Fallback: try common patterns for Main channel
    if "main" in channel_lower:
        # Try sgs.png in label directory
        fallback = BRANDING_ASSETS_DIR / "label" / "sgs.png"
        if fallback.exists():
            return fallback
        # If sgs.png doesn't exist, try sgs_2.png as backup
        fallback = BRANDING_ASSETS_DIR / "label" / "sgs_2.png"
        if fallback.exists():
            return fallback
    
    return None

def _get_stem_icon_path(stem_type: str) -> Optional[Path]:
    """Section 2: Get stem icon path for top-right placement."""
    stem_key = stem_type.lower()
    icon_path = STEM_ICONS.get(stem_key)
    if icon_path and icon_path.exists():
        return icon_path
    return None

def create_watermark_clip(channel: str, duration: float, clip_width: int = 1280, clip_height: int = 720) -> Optional["ImageClip"]:
    """
    Create watermark clip that can be cached and reused.
    Watermark is channel-specific but same for all stems of that channel.
    
    Args:
        channel: Channel name
        duration: Duration of the video in seconds
        clip_width: Width of the target clip (for positioning)
        clip_height: Height of the target clip (for positioning)
        
    Returns:
        Positioned watermark clip, or None if unavailable
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    watermark_path = _get_watermark_path(channel)
    if not watermark_path:
        return None
    
    try:
        print(f"   Loading watermark from assets: {watermark_path.name}")
        watermark = ImageClip(str(watermark_path)).with_duration(duration)
        
        # Resize watermark while preserving aspect ratio
        target_width = 200
        max_height = 80
        
        original_w, original_h = watermark.w, watermark.h
        aspect_ratio = original_w / original_h
        
        if original_w > original_h:
            new_width = min(target_width, original_w)
            new_height = int(new_width / aspect_ratio)
            if new_height > max_height:
                new_height = max_height
                new_width = int(new_height * aspect_ratio)
        else:
            new_height = min(max_height, original_h)
            new_width = int(new_height * aspect_ratio)
            if new_width > target_width:
                new_width = target_width
                new_height = int(new_width / aspect_ratio)
        
        watermark = apply_moviepy_resize(watermark, new_size=(new_width, new_height))
        
        # Position: bottom-left with margin
        margin_x, margin_y = 20, 20
        watermark = watermark.with_position((margin_x, clip_height - watermark.h - margin_y))
        
        return watermark
    except Exception as e:
        print(f"Warning: Could not create watermark clip: {e}")
        return None


def add_watermark(clip, channel: str, duration: float, watermark_clip: Optional["ImageClip"] = None) -> Optional["CompositeVideoClip"]:
    """
    Section 2: Add SGS/SGS2 watermark to video clip.
    Watermark appears on ALL channels and ALL uploads.
    
    Args:
        clip: Base video clip to add watermark to
        channel: Channel name
        duration: Duration of the video in seconds
        watermark_clip: Optional cached watermark clip to reuse
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    # Use cached watermark clip if provided, otherwise create new one
    if watermark_clip is None:
        watermark_clip = create_watermark_clip(channel, duration, clip.w, clip.h)
        if watermark_clip is None:
            # Only warn once per channel to avoid spam
            if not hasattr(add_watermark, '_warned_channels'):
                add_watermark._warned_channels = set()
            if channel not in add_watermark._warned_channels:
                # Watermark not found (using default)
                print(f"   Add watermark to: assets/assets/label/sgs.png (required) or assets/assets/label/sgs_2.png (optional)")
                print(f"   See assets/README.md for details")
                add_watermark._warned_channels.add(channel)
            return None
    
    try:
        # Adjust duration if needed
        if watermark_clip.duration != duration:
            watermark_clip = watermark_clip.with_duration(duration)
        # Set position (always set to ensure it's correct)
        watermark_clip = watermark_clip.with_position((20, clip.h - watermark_clip.h - 20))
        
        return CompositeVideoClip([clip, watermark_clip])
    except Exception as e:
        print(f"Warning: Could not add watermark: {e}")
        return None

def create_stem_icon_clip(stem_type: str, duration: float, clip_width: int = 1280) -> Optional["ImageClip"]:
    """
    Create stem icon clip that can be cached and reused.
    Icon is stem-specific but same for all channels using that stem.
    
    Args:
        stem_type: Type of stem (e.g., "Acapella", "Drums")
        duration: Duration of the video in seconds
        clip_width: Width of the target clip (for positioning)
        
    Returns:
        Positioned icon clip, or None if unavailable
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    icon_path = _get_stem_icon_path(stem_type)
    if not icon_path:
        return None
    
    try:
        print(f"   Loading icon from assets: {icon_path.name}")
        icon = ImageClip(str(icon_path)).with_duration(duration)
        # Resize icon
        icon = apply_moviepy_resize(icon, new_size=(80, 80))
        # Position: top-right with margin
        margin_x, margin_y = 20, 20
        icon = icon.with_position((clip_width - icon.w - margin_x, margin_y))
        
        return icon
    except Exception as e:
        print(f"Warning: Could not create stem icon clip: {e}")
        return None


def add_stem_icon(clip, stem_type: str, duration: float, icon_clip: Optional["ImageClip"] = None) -> Optional["CompositeVideoClip"]:
    """
    Section 2: Add stem icon to top-right of video.
    Five icons: Acapella, Drums, Bass, Melody, Instrumental.
    Must appear top-right consistently.
    
    Args:
        clip: Base video clip to add icon to
        stem_type: Type of stem (e.g., "Acapella", "Drums")
        duration: Duration of the video in seconds
        icon_clip: Optional cached icon clip to reuse
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    # Use cached icon clip if provided, otherwise create new one
    if icon_clip is None:
        icon_clip = create_stem_icon_clip(stem_type, duration, clip.w)
        if icon_clip is None:
            # Only warn once per stem type to avoid spam
            if not hasattr(add_stem_icon, '_warned_stems'):
                add_stem_icon._warned_stems = set()
            if stem_type not in add_stem_icon._warned_stems:
                # Stem icon not found (using default)
                print(f"   Add icon to: assets/assets/icons/{stem_type.lower()}.png")
                print(f"   See assets/README.md for details")
                add_stem_icon._warned_stems.add(stem_type)
            return None
    
    try:
        # Adjust duration if needed
        if icon_clip.duration != duration:
            icon_clip = icon_clip.with_duration(duration)
        # Set position (always set to ensure it's correct)
        icon_clip = icon_clip.with_position((clip.w - icon_clip.w - 20, 20))
        
        return CompositeVideoClip([clip, icon_clip])
    except Exception as e:
        print(f"Warning: Could not add stem icon: {e}")
        return None

def create_base_clip(duration: float, thumb_path: Optional[str] = None) -> Optional[Union["CompositeVideoClip", "ColorClip"]]:
    """
    Create base clip (background + thumbnail) that can be cached and reused.
    This is the same for all stems of the same track.
    Background is loaded from assets/assets/background.png if available, otherwise uses black.
    
    Args:
        duration: Duration of the video in seconds
        thumb_path: Optional path to thumbnail image
        
    Returns:
        Base clip with background and thumbnail, or None if moviepy unavailable
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    try:
        # Try to load background from assets first
        background_path = BRANDING_ASSETS_DIR / "background.png"
        background = None
        
        if background_path.exists():
            try:
                print(f"   Loading background from: {background_path}")
                background = ImageClip(str(background_path)).with_duration(duration)
                # Resize to standard video size (1280x720)
                background = apply_moviepy_resize(background, new_size=(1280, 720))
            except Exception as e:
                print(f"Warning: Could not load background image: {e}")
                background = None
        
        # Fallback to black ColorClip if background image not available
        if background is None:
            background = ColorClip(
                size=(1280, 720),
                color=(0, 0, 0),
                duration=duration
            )
        
        clips = [background]
        
        # Add thumbnail if available
        if thumb_path and os.path.exists(thumb_path):
            try:
                thumb_clip = ImageClip(thumb_path).with_duration(duration)
                thumb_clip = apply_moviepy_resize(thumb_clip, new_size=(720, 720))
                thumb_clip = thumb_clip.with_position("center")
                clips.append(thumb_clip)
            except Exception as e:
                print(f"Warning: Could not add thumbnail to base clip: {e}")
        
        # Composite base clip
        base_clip = CompositeVideoClip(clips, size=(1280, 720))
        return base_clip
        
    except Exception as e:
        print(f"Warning: Could not create base clip: {e}")
        return None


def add_intro_card(duration: float, channel: str, thumb_path: Optional[str] = None, stem_type: str = "", base_clip: Optional[Union["CompositeVideoClip", "ColorClip"]] = None, watermark_clip: Optional["ImageClip"] = None, icon_clip: Optional["ImageClip"] = None) -> Optional[Union["CompositeVideoClip", "ColorClip"]]:
    """
    Create an intro card for videos with branding.
    Section 2: Includes watermark and stem icon.
    
    Args:
        duration: Duration of the video in seconds
        channel: Channel name
        thumb_path: Optional path to thumbnail image (used if base_clip not provided)
        stem_type: Type of stem (e.g., "Acapella", "Drums")
        base_clip: Optional cached base clip (background + thumbnail) to reuse
        
    Returns:
        MoviePy clip with intro card, watermark, and stem icon, or None if moviepy unavailable
    """
    if not MOVIEPY_AVAILABLE:
        return None
    
    try:
        # Use cached base clip if provided, otherwise create new one
        if base_clip is None:
            base_clip = create_base_clip(duration, thumb_path)
            if base_clip is None:
                return None
        
        # Section 2: Add watermark (on ALL channels)
        # Use cached watermark clip if provided
        watermarked = add_watermark(base_clip, channel, duration, watermark_clip=watermark_clip)
        if watermarked:
            base_clip = watermarked
        
        # Section 2: Add stem icon (top-right)
        if stem_type:
            # Use cached icon clip if provided
            iconed = add_stem_icon(base_clip, stem_type, duration, icon_clip=icon_clip)
            if iconed:
                base_clip = iconed
        
        return base_clip
        
    except Exception as e:
        print(f"Warning: Could not create intro card: {e}")
        return None



