# Branding Assets Setup Guide

## Overview

The system requires branding assets (watermarks and stem icons) to apply branding to videos. These assets are **optional** - the system works without them, but videos won't have watermarks or icons.

## Current Status

⚠️ **Assets Missing** - You're seeing warnings because the assets haven't been added yet.

## Required Assets

### 1. Watermarks (Required for branding)

**Location:** `assets/` directory

**Files Needed:**
- `sgs2_watermark.png` - **REQUIRED** (used for all channels, fallback for main)
- `sgs_watermark.png` - **OPTIONAL** (used for main channel, falls back to SGS2 if missing)

**Specifications:**
- Size: 200x60 pixels (recommended)
- Format: PNG with transparency
- Placement: Bottom-right corner (20px margin)
- Opacity: Match your brand guidelines

### 2. Stem Icons (Required for branding)

**Location:** `assets/` directory

**Files Needed:**
- `icon_acapella.png` - Acapella stem icon
- `icon_drums.png` - Drums stem icon
- `icon_bass.png` - Bass stem icon
- `icon_melody.png` - Melody stem icon
- `icon_instrumental.png` - Instrumental stem icon

**Specifications:**
- Size: 80x80 pixels
- Format: PNG with transparency
- Placement: Top-right corner (20px margin)
- Style: Consistent across all icons

## Setup Instructions

### Step 1: Create Assets Directory

The `assets/` directory should already exist. If not, create it:

```bash
mkdir assets
```

### Step 2: Add Watermark Files

1. Create or obtain your SGS2 watermark image
2. Save as `assets/sgs2_watermark.png`
3. (Optional) Create SGS watermark for main channel
4. Save as `assets/sgs_watermark.png`

### Step 3: Add Stem Icons

1. Create or obtain 5 stem icons (one for each stem type)
2. Save them as:
   - `assets/icon_acapella.png`
   - `assets/icon_drums.png`
   - `assets/icon_bass.png`
   - `assets/icon_melody.png`
   - `assets/icon_instrumental.png`

### Step 4: Verify

After adding assets, restart the application. The warnings should disappear and branding will be applied to videos.

## Asset Requirements Summary

| Asset | Status | Location | Priority |
|-------|--------|----------|----------|
| `sgs2_watermark.png` | ⚠️ Missing | `assets/` | Required |
| `sgs_watermark.png` | ⚠️ Missing | `assets/` | Optional |
| `icon_acapella.png` | ⚠️ Missing | `assets/` | Required |
| `icon_drums.png` | ⚠️ Missing | `assets/` | Required |
| `icon_bass.png` | ⚠️ Missing | `assets/` | Required |
| `icon_melody.png` | ⚠️ Missing | `assets/` | Required |
| `icon_instrumental.png` | ⚠️ Missing | `assets/` | Required |

## Current Warnings Explained

The warnings you're seeing:
```
⚠️ Watermark not found for channel: Main
⚠️ Stem icon not found for: Drums
```

These are **informational only** - the system continues to work. Videos are created successfully, just without branding.

## Testing Branding

Once assets are added:

1. Restart the application
2. Process a track with "Upload to YouTube" enabled (or "Create MP4 Videos" checked)
3. Check the generated MP4 files - they should have:
   - Watermark in bottom-right corner
   - Stem icon in top-right corner

## Troubleshooting

**Q: Videos still don't have branding after adding assets?**
- Make sure files are in `assets/` directory (not subdirectories)
- Verify file names match exactly (case-sensitive)
- Check file format is PNG
- Restart the application

**Q: Can I use different sizes?**
- Yes, but recommended sizes ensure proper placement
- System will resize automatically, but quality may vary

**Q: Do I need all assets?**
- Minimum: `sgs2_watermark.png` + all 5 icons for full branding
- System works without any assets (just no branding)

## Notes

- Missing assets are handled gracefully (warnings only, no errors)
- System works without assets, but branding won't appear
- All assets are optional for functionality, but required for full branding per Section 2 requirements

