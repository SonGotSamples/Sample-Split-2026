# Sample Split - Implementation & Deployment Guide

## Quick Start

### 1. Install Updated Requirements
```bash
pip install -r requirements.txt
# This ensures demucs is at version 4.0.1+
```

### 2. Test Demucs Model
```bash
# Download and test htdemucs_ft model
python -c "from demucs.pretrained import get_model; m = get_model('htdemucs_ft'); print(' Model ready')"
```

### 3. Start Backend Server
```bash
cd c:\Users\simba\Desktop\Sample-Split
python tk.py
# Opens http://127.0.0.1:8000 in browser
```

---

## Architecture Overview

```
Frontend (index.html)
    ↓ JSON POST /split
Backend (tk.py)
    ↓
dispatch_download.py
    ├─ Get track info
    ├─ Download audio
    ├─ Run Demucs (htdemucs_ft primary)
    └─ Route to channels
    
Each Channel (content_download_main.py, etc.)
    ├─ Load stem files
    ├─ Mix stems (StemProcessor)
    ├─ Render videos
    └─ Upload to YouTube (yt_video_multi.py)
        ├─ Post metadata
        ├─ Set thumbnail
        ├─ Add to playlist
        └─ Post comment
```

---

## Key Files & Their Roles

### Core Processing:
| File | Purpose | Status |
|------|---------|--------|
| `stem_processor.py` | Audio mixing & loudness normalization |  NEW |
| `content_download_main.py` | Main channel stem processor |  UPDATED |
| `dispatch_download.py` | Demucs orchestration with fallbacks |  UPDATED |
| `content_base.py` | Base processor class |  UPDATED |

### Backend API:
| File | Purpose | Status |
|------|---------|--------|
| `tk.py` | FastAPI server, StemRequest model |  UPDATED |
| `yt_video_multi.py` | YouTube upload orchestration |  READY (no changes needed) |

### Frontend:
| File | Purpose | Status |
|------|---------|--------|
| `index.html` | Web UI with new fields |  UPDATED |

---

## Configuration Changes

### Model Prioritization
**Before:**
```python
FALLBACK_MODELS = ["htdemucs_6s", "htdemucs_ft", "htdemucs"]
```

**After:**
```python
FALLBACK_MODELS = ["htdemucs_ft", "htdemucs_6s", "htdemucs"]
```
Location: `dispatch_download.py:59-63`

### Stem Definitions
**Added Instrumental Mixing:**
```python
"Instrumental": {
    "stem_key": "instrumental",
    "sources": ["other.mp3", "drums.mp3", "bass.mp3"],
    "mix": True  # Enable advanced mixing
}
```
Location: `content_download_main.py:75-80`

### Privacy Default
**Location:** `tk.py:130`
```python
"privacy": request.privacy or "public"  # Default to public
```

### Comment Support
**StemRequest now includes:**
```python
comments: Optional[Dict[str, str]] = None  # {channel_key: comment_text}
```
Location: `tk.py:47`

---

## API Endpoint Reference

### POST /split
**Description:** Process track and upload stems to YouTube

**Request Format:**
```json
{
  "track_id": "6rqhFgbbKwnb9MLmUQDvDm",
  "channels": ["main_channel", "son_got_acapellas"],
  "description": "Custom description (optional)",
  "tags": ["hip-hop", "remix"],
  "privacy": "public",
  "made_for_kids": false,
  "monetize": false,
  "comments": {
    "main_channel": "Check out our samples!",
    "son_got_acapellas": "Isolated vocals"
  },
  "startTime": "2025-01-15T10:00",
  "interval": "Every Hour",
  "tz": "America/Chicago",
  "yt": true,
  "ec2": false,
  "trim": false,
  "genre": "hip-hop"
}
```

**Response:**
```json
{
  "message": "Playlist processing started",
  "tracks_processed": 5,
  "channels": ["main_channel", "son_got_acapellas"],
  "session_ids": ["playlist_id__track1_id", ...]
}
```

### GET /progress/{session_id}
**Description:** Server-sent events stream for real-time progress

**Response (Event Stream):**
```json
{
  "message": " Separating with htdemucs_ft (attempt 1)…",
  "percent": 25,
  "meta": {
    "track_id": "...",
    "title": "...",
    "artist": "...",
    "channels": ["main_channel"],
    "completed": 1,
    "total": 2
  }
}
```

---

## Data Flow Examples

### Example 1: Minimal Request
**User Action:** Enter track ID, select 1 channel, click "Start"

**Flow:**
1. Request uses all defaults
2. Demucs model: htdemucs_ft
3. Privacy: public
4. Description: DEFAULT_DESCRIPTION
5. Tags: DEFAULT_TAGS
6. Comment: none
7. Output: 3 stems (acapella, drums, instrumental)

### Example 2: Full Custom Setup
**User Action:** Fill all fields including per-channel comments

**Flow:**
1. Custom description sent
2. Custom tags merged with DEFAULT_TAGS
3. Privacy: public (user selected)
4. Different comment per channel
5. Made for Kids: enabled
6. Monetization: enabled
7. Scheduled upload: 1 hour interval
8. Result: stems uploaded with custom metadata + comments

---

## Stem Processing Details

### Mixing Process (StemProcessor)
```
Input: [other.mp3, drums.mp3, bass.mp3]
  ↓
1. Load each file
  ↓
2. Standardize format (44.1kHz, 2-channel)
  ↓
3. Reduce per-stem volume (-2dB to prevent clipping)
  ↓
4. Overlay stems (accumulate audio)
  ↓
5. Normalize final mix (-1.0 dBFS target)
  ↓
6. Apply fade in/out (500ms)
  ↓
Output: Professional instrumental stem
```

### Audio Specification
- **Bit Depth:** 16-bit (standard MP3)
- **Sample Rate:** 44.1 kHz
- **Channels:** 2 (stereo)
- **Bitrate:** 192 kbps (MP3)
- **Loudness Target:** -1.0 dBFS (peak)
- **Duration:** Consistent with original (±100ms tolerance)

---

## Error Handling

### Demucs Failures
```
htdemucs_ft fails
  ↓ (try fallback)
htdemucs_6s fails
  ↓ (try fallback)
htdemucs fails
  ↓ (GPU OOM detected?)
  ├─ Retry on CPU
  └─ If still fails: abort, report error
```

### Audio Processing Failures
```
Mix fails
  → Log error, attempt with stemming disabled
Format conversion fails
  → Try alternative codec
  → If all fail: skip stem, continue with others
```

### YouTube Upload Failures
```
Transient error (429, 500-504, quota)
  → Exponential backoff (max 6 retries)
  → Sleep: 1.2s, 1.44s, 1.73s, 2.07s, 2.49s, 2.99s
  
Permanent error (auth, file)
  → Log and skip that upload
  → Continue with other stems
```

---

## Performance Tuning

### For Speed:
```
1. Set FALLBACK_MODELS to ["htdemucs_6s", "htdemucs"]
   (skips htdemucs_ft)
2. Disable fade: StemProcessor.mix_stems(..., apply_fade=False)
3. Increase max_concurrent in dispatch_download.py
```

### For Quality:
```
1. Keep htdemucs_ft first (default)
2. Enable fade: apply_fade=True (default)
3. Use normalize_loudness: True (default)
4. Set max_concurrent = 1 (default, for stability)
```

### For GPU Memory:
```
1. Enable pre-processing in dispatch_download.py
2. Use --mp3 flag with demucs (converts on-the-fly)
3. Monitor VRAM during demucs runs
4. Fallback to CPU if GPU OOM detected
```

---

## Deployment Checklist

- [ ] Python 3.10+ installed
- [ ] pip dependencies installed
- [ ] ffmpeg installed and in PATH
- [ ] ImageMagick installed (for thumbnails)
- [ ] YouTube OAuth tokens in `yt_tokens/` folder
- [ ] Spotify credentials in `.env` file
- [ ] Test track processed successfully
- [ ] UI displays properly at http://127.0.0.1:8000
- [ ] Comment posts successfully to test video
- [ ] Privacy defaults to "public"
- [ ] Description auto-fills correctly
- [ ] Multi-channel upload works
- [ ] Logging shows htdemucs_ft usage

---

## Maintenance & Monitoring

### Log Files to Check:
```
dispatch_download.py: "[DEMUCS] ▶ Running model: htdemucs_ft"
yt_video_multi.py: " Authenticated YouTube client for [Channel]"
content_download_main.py: "[PROGRESS] ... % "
```

### Health Checks:
```bash
# Test demucs model availability
python -c "from demucs.pretrained import get_model; get_model('htdemucs_ft')"

# Test YouTube auth
python -c "from yt_video_multi import get_youtube_client; get_youtube_client('main_v2.json')"

# Test audio processing
python -c "from stem_processor import StemProcessor; print(' Ready')"
```

### Monitoring Metrics:
1. **Success Rate:** % of tracks processed without errors
2. **Average Processing Time:** minutes per track
3. **VRAM Usage:** peak memory during demucs
4. **Upload Time:** YouTube posting duration
5. **Comment Success Rate:** % with comments posted

---

## Troubleshooting Guide

### Issue: "htdemucs_ft not found"
```
Solution:
1. Delete ~/.cache/demucs/
2. Re-run: get_model('htdemucs_ft')
3. If fails: check disk space, internet connection
4. Falls back to htdemucs_6s automatically
```

### Issue: "CUDA out of memory"
```
Solution (automatic):
1. Demucs detects "CUDA out of memory" error
2. Retries on CPU automatically
3. Takes longer but completes successfully
Manual: Reduce batch size or use CPU flag
```

### Issue: "Stem mixing produces distortion"
```
Solution:
1. Check StemProcessor.mix_stems() per-stem gain
2. Reduce gain reduction from -2dB to -3dB if needed
3. Verify audio files are not already clipped
4. Test with test track: "test_audio.mp3"
```

### Issue: "Comment not posted"
```
Solution:
1. Verify comment field is not None/empty
2. Check YouTube credentials valid (run auth test)
3. Verify video uploaded successfully first
4. Check YouTube API quota not exceeded
5. Try again in 5 minutes (rate limit)
```

### Issue: "Privacy still showing private"
```
Solution:
1. Check if publish_at timestamp is set
2. YouTube forces private if scheduling
3. Either: remove publish_at OR schedule uploads later
4. Or: change schedule_mode to "no schedule"
```

---

## Support Resources

### Documentation:
- `CHANGES.md` - Comprehensive change summary
- `README.md` - Setup & running instructions
- Code comments in `stem_processor.py` - Detailed function docs

### Key Classes & Methods:
```python
# Stem processing
StemProcessor.mix_stems(paths, target_loudness=-1.0, apply_fade=True)
StemProcessor.normalize_loudness(audio, target_dbfs=-1.0)
StemProcessor.validate_stems(stem_base_path)

# Content processing
ContentBase.upload_batch_to_youtube(track)
Content_download_main._mix_sources(source_paths)

# YouTube upload
upload_all_stems(stem_files, description, tags, comment=None, ...)
post_comment(youtube, video_id, text)
```

---

## FAQ

**Q: Can I use older Demucs models?**
A: Yes, fallback chain handles: htdemucs_ft → htdemucs_6s → htdemucs

**Q: What if user leaves description empty?**
A: Auto-filled with DEFAULT_DESCRIPTION (system default)

**Q: Can I post different comments per channel?**
A: Yes, use `comments: {channel_key: comment_text}` in request

**Q: Is privacy always public now?**
A: Default is "public", but user can override to "private"/"unlisted"

**Q: How long does processing take?**
A: ~2-3 min per track (GPU) or ~5-10 min (CPU) + 5-10 min upload

**Q: Can I retry failed uploads?**
A: Yes, exponential backoff handles transient failures automatically

**Q: What about codec compatibility?**
A: Standardized to MP3 192kbps, compatible with all platforms

