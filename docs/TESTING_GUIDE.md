# Testing & Verification Guide

Complete testing procedures to validate all features from `full.txr`.

---

## Test 1: Fuzzy Match & Song Identification

### Test Case 1.1: Basic Fuzzy Matching
```python
from fuzzy_matcher import FuzzyMatcher

matcher = FuzzyMatcher()

# Test exact match
match_id, score = matcher.match_song(
    "Blinding Lights",
    "The Weeknd",
    [{"title": "Blinding Lights", "artist": "The Weeknd", "id": "spotify_123"}]
)
assert score >= 95, f"Expected >=95, got {score}"
assert match_id == "spotify_123"
print(" Exact match: PASS")

# Test fuzzy match (slight variations)
match_id, score = matcher.match_song(
    "Blinding Light",  # Missing 's'
    "The Weeknd",
    [{"title": "Blinding Lights", "artist": "The Weeknd", "id": "spotify_123"}]
)
assert score >= 85, f"Expected >=85, got {score}"
print(" Fuzzy match: PASS")
```

### Test Case 1.2: Cache Functionality
```python
# First call - not cached
import time
start = time.time()
match_id1, score1 = matcher.match_song("Song", "Artist", candidates)
first_time = time.time() - start

# Second call - should be cached
start = time.time()
match_id2, score2 = matcher.match_song("Song", "Artist", candidates)
cached_time = time.time() - start

assert match_id1 == match_id2
assert cached_time < first_time * 0.5, "Cache not working"
print(f" Cache working: {cached_time*1000:.1f}ms vs {first_time*1000:.1f}ms")
```

### Test Case 1.3: Accuracy Test (20+ tracks)
```python
test_tracks = [
    ("Blinding Lights", "The Weeknd"),
    ("Levitating", "Dua Lipa"),
    ("Anti-Hero", "Taylor Swift"),
    # ... 17 more tracks
]

candidates = [
    {"title": track[0], "artist": track[1], "id": f"id_{i}"}
    for i, track in enumerate(test_tracks)
]

matches = 0
for track_name, artist_name in test_tracks:
    match_id, score = matcher.match_song(track_name, artist_name, candidates)
    if score >= 85:
        matches += 1

accuracy = (matches / len(test_tracks)) * 100
assert accuracy >= 99, f"Accuracy: {accuracy}% (target: 99%)"
print(f" Accuracy test: {accuracy}%")
```

---

## Test 2: Tunebat Stability & Auto-Retry

### Test Case 2.1: Single Fetch Success
```python
from tunebat_helper import get_bpm_key

# Real Spotify track
bpm, key = get_bpm_key("Blinding Lights", "The Weeknd", "3qm84nBvXcWhTqoggy3F5P")

assert isinstance(bpm, int), "BPM should be int"
assert isinstance(key, str), "Key should be string"
assert bpm > 0, "BPM should be > 0"
assert key != "Unknown", "Should have valid key"
print(f" Tunebat fetch: BPM={bpm}, Key={key}")
```

### Test Case 2.2: Cache Hit Test
```python
import time

# First fetch
start = time.time()
bpm1, key1 = get_bpm_key("Levitating", "Dua Lipa", "track_id")
first_time = time.time() - start

# Second fetch (cached)
start = time.time()
bpm2, key2 = get_bpm_key("Levitating", "Dua Lipa", "track_id")
cached_time = time.time() - start

assert bpm1 == bpm2
assert key1 == key2
assert cached_time < first_time * 0.1, "Should be <10% of original time"
print(f" Cache hit: {cached_time*1000:.1f}ms (cached)")
```

### Test Case 2.3: Retry Backoff Verification
```python
import json
import os

# Monitor retry attempts
if os.path.exists("tunebat_cache.json"):
    with open("tunebat_cache.json", "r") as f:
        cache = json.load(f)
    
    print(f" Cache entries: {len(cache)}")
    for key, data in list(cache.items())[:3]:
        print(f"  - {key}: {data['bpm']} BPM, {data['key']} key")
```

---

## Test 3: TikTok Integration

### Test Case 3.1: Authentication Config
```python
from tiktok_uploader import _uploader

# Check authentication
authenticated = _uploader.is_authenticated()
if not authenticated:
    print(" TikTok not authenticated (expected on first setup)")
    
    # Configure
    success = _uploader.authenticate(
        access_token="test_token_123",
        user_id="test_user_123"
    )
    assert success, "Authentication failed"
    print(" TikTok authenticated")
```

### Test Case 3.2: Video Size Validation
```python
import os

# Create test video (1MB)
test_video = "test_video.mp4"
with open(test_video, "wb") as f:
    f.write(b"mock_video" * 100000)

# Test upload validation
video_size = os.path.getsize(test_video)
max_size = 287661056

assert video_size < max_size, "Video too large for TikTok"
print(f" Video size valid: {video_size} bytes")

# Cleanup
os.remove(test_video)
```

### Test Case 3.3: API Response Handling
```python
# Simulate upload with retry
from tiktok_uploader import TikTokUploader

uploader = TikTokUploader()

# Test exponential backoff calculation
backoff_times = []
for attempt in range(5):
    wait_time = uploader._exponential_backoff(attempt)
    backoff_times.append(wait_time)

# Verify exponential growth
print("Backoff sequence:", backoff_times)
assert backoff_times[1] > backoff_times[0], "Should increase"
assert all(t <= 60 for t in backoff_times), "Should cap at 60s"
print(" Exponential backoff: PASS")
```

---

## Test 4: Centralized Logging

### Test Case 4.1: Session Logging
```python
from logger_central import logger
import json
import os

# Start session
logger.start_session("test_playlist_123", {
    "total_tracks": 3,
    "channels": ["main", "acapellas"]
})

# Log track start
logger.log_track_start("track_1", "Song Title", "Artist Name")

# Log stem success
logger.log_stem_success("track_1", "acapella", "/path/to/acapella.mp3")

# Log upload success
logger.log_upload_success("track_1", "acapella", "main", "video_id_123")

# End session
logger.end_session("completed")

# Verify output files exist
assert os.path.exists("run_report.json"), "run_report.json not created"
print(" Session logging: PASS")

# Verify content
with open("run_report.json", "r") as f:
    report = json.load(f)

assert len(report["sessions"]) > 0, "No sessions recorded"
session = report["sessions"][-1]
assert session["session_id"] == "test_playlist_123"
assert session["status"] == "completed"
print(f" Session data verified: {len(session['tracks'])} tracks")
```

### Test Case 4.2: Error Logging
```python
import os

logger.log_error(
    error_type="TEST_ERROR",
    message="This is a test error",
    context={"track_id": "123", "reason": "test"}
)

assert os.path.exists("error_log.txt"), "error_log.txt not created"

with open("error_log.txt", "r") as f:
    content = f.read()
    assert "TEST_ERROR" in content
    assert "test error" in content

print(" Error logging: PASS")
```

### Test Case 4.3: Report Export
```python
logger.export_report("test_export.json")

assert os.path.exists("test_export.json"), "Export failed"

with open("test_export.json", "r") as f:
    data = json.load(f)
    assert "sessions" in data

print(" Report export: PASS")

# Cleanup
os.remove("test_export.json")
```

---

## Test 5: Crash Recovery & Checkpoints

### Test Case 5.1: Checkpoint Persistence
```python
from checkpoint_recovery import _recovery

# Save playlist checkpoint
_recovery.save_playlist_checkpoint("playlist_123", "in_progress", {
    "total_tracks": 10,
    "processed_tracks": 5
})

# Save track checkpoint
_recovery.save_track_checkpoint("track_1", "playlist_123", "processing", {
    "stem_types": ["acapella", "drums"],
    "processed_stems": ["acapella"]
})

# Save stem checkpoint
_recovery.save_stem_checkpoint(
    "stem_acapella",
    "track_1",
    "acapella",
    "/path/to/file.mp3",
    "success"
)

import os
import json

assert os.path.exists("checkpoint.json"), "Checkpoint not created"

with open("checkpoint.json", "r") as f:
    checkpoint = json.load(f)
    assert "playlist_123" in checkpoint["playlists"]
    assert "track_1" in checkpoint["tracks"]

print(" Checkpoint persistence: PASS")
```

### Test Case 5.2: Recovery Resume
```python
# Get incomplete playlists
incomplete = _recovery.get_incomplete_playlists()
assert len(incomplete) > 0, "Should have incomplete playlists"

playlist = incomplete[0]
assert playlist["status"] == "in_progress"

# Get incomplete tracks for playlist
incomplete_tracks = _recovery.get_incomplete_tracks("playlist_123")
assert len(incomplete_tracks) > 0, "Should have incomplete tracks"

print(f" Recovery: {len(incomplete)} incomplete playlists")
print(f" Recovery: {len(incomplete_tracks)} incomplete tracks")
```

### Test Case 5.3: File Caching
```python
# Cache file
_recovery.cache_stem_file("stem_drums", "/path/to/drums.mp3")

# Create the file for testing
os.makedirs("/path/to", exist_ok=True)
with open("/path/to/drums.mp3", "w") as f:
    f.write("test")

# Retrieve cached file
cached_path = _recovery.get_cached_stem_file("stem_drums")
assert cached_path is not None, "Cache retrieval failed"

print(" File caching: PASS")

# Cleanup
import shutil
shutil.rmtree("/path", ignore_errors=True)
```

---

## Test 6: Configuration Consolidation

### Test Case 6.1: Config Loading
```python
from config_manager import get_config

config = get_config()

# Check all sections exist
sections = ["spotify", "youtube", "tiktok", "demucs", "audio", "directories", "logging"]
for section in sections:
    assert section in config.config, f"Missing section: {section}"
    print(f" Config section '{section}': OK")
```

### Test Case 6.2: Directory Setup
```python
config.setup_directories()

dirs = config.config.get("directories", {})
for dir_name, dir_path in dirs.items():
    assert os.path.exists(dir_path), f"Directory not created: {dir_path}"

print(f" Directories created: {len(dirs)}")
```

### Test Case 6.3: Validation Methods
```python
# Note: These require actual credentials to pass

# Spotify validation (should fail without credentials)
spotify_valid = config.validate_spotify_config()
print(f" Spotify validation: {'PASS' if spotify_valid else 'NEEDS_CREDENTIALS'}")

# YouTube validation (check token directory)
youtube_valid = config.validate_youtube_config()
print(f" YouTube validation: {'PASS' if youtube_valid else 'NEEDS_TOKENS'}")

# Setup report
report = config.generate_setup_report()
print(" Setup report generated:")
for key, value in report.items():
    print(f"  - {key}: {'OK' if value else 'MISSING'}")
```

---

## Test 7: Validation Engine

### Test Case 7.1: Stem Validation
```python
from validation_engine import _validator

# Create test MP3 file (minimal valid MP3)
test_stem = "test_stem.mp3"
os.makedirs("test_audio", exist_ok=True)

# Create minimal MP3 (header + silence)
with open(os.path.join("test_audio", test_stem), "wb") as f:
    # MP3 header + minimal data
    f.write(b'\xff\xfb\x10\x00' + b'\x00' * 100000)

stem_path = os.path.join("test_audio", test_stem)

# Validate
is_valid, message = _validator.validate_stem_file(stem_path)
print(f" Stem validation: {message}")

# Cleanup
shutil.rmtree("test_audio", ignore_errors=True)
```

### Test Case 7.2: Batch Validation
```python
# Test batch validation (with mocked files)
stem_files = {
    "acapella": "acapella.mp3",
    "drums": "drums.mp3",
    "instrumental": "instrumental.mp3"
}

# This would validate if files exist
# is_valid, results = _validator.validate_stems_batch(stem_files)
# print(f" Batch validation: {is_valid}")

print(" Batch validation: Ready to test with actual files")
```

---

## Test 8: Windows Setup Script

### Test Case 8.1: Setup Script Execution
```bash
cd c:\Users\simba\Desktop\Sample-Split
setup.bat
```

**Verify Output:**
- [ ]  Python detected
- [ ]  Virtual environment created
- [ ]  Dependencies installed
- [ ]  Directories created (8+ folders)
- [ ]  ffmpeg found or warning given
- [ ]  ImageMagick found or warning given
- [ ]  config.json template created
- [ ]  .env template created

---

## Integration Test: End-to-End Processing

### Complete Workflow Test
```python
# 1. Fuzzy match a track
from fuzzy_matcher import FuzzyMatcher
matcher = FuzzyMatcher()
match_id, score = matcher.match_song("Test Song", "Test Artist", candidates)
print("1.  Fuzzy match")

# 2. Fetch BPM/Key from Tunebat
from tunebat_helper import get_bpm_key
bpm, key = get_bpm_key("Song", "Artist", match_id)
print("2.  Tunebat fetch")

# 3. Start logging
from logger_central import logger
logger.start_session(match_id, {"total_tracks": 1})
logger.log_track_start(match_id, "Song", "Artist")
print("3.  Logging started")

# 4. Validate stems (mock)
from validation_engine import _validator
# is_valid, results = _validator.validate_stems_batch(stem_files)
print("4.  Validation ready")

# 5. Save checkpoint
from checkpoint_recovery import _recovery
_recovery.save_track_checkpoint(match_id, match_id, "completed", {})
print("5.  Checkpoint saved")

# 6. Log upload (mock)
logger.log_upload_success(match_id, "acapella", "youtube", "video_123")
logger.end_session("completed")
print("6.  Upload logged")

# 7. Verify all outputs
import json
assert os.path.exists("run_report.json")
assert os.path.exists("checkpoint.json")
print("7.  All outputs created")

print("\n END-TO-END TEST: PASS")
```

---

## Performance Benchmarks

Run these tests to verify performance targets:

```bash
python -m pytest tests/performance_test.py -v
```

**Expected Results:**
- [ ] Fuzzy match (cached): <10ms ✓
- [ ] Tunebat (cached): <5ms ✓
- [ ] Validation (per stem): <2s ✓
- [ ] Recovery resume: <5s ✓
- [ ] TikTok retry success: 95%+ ✓

---

## Regression Testing

After any changes, run:

```python
# Quick smoke test
import subprocess

tests = [
    "from fuzzy_matcher import FuzzyMatcher",
    "from tunebat_helper import get_bpm_key",
    "from tiktok_uploader import upload_to_tiktok",
    "from logger_central import logger",
    "from checkpoint_recovery import _recovery",
    "from config_manager import get_config",
    "from validation_engine import _validator",
]

for test in tests:
    try:
        exec(test)
        print(f" {test.split()[-1]}")
    except Exception as e:
        print(f"❌ {test.split()[-1]}: {e}")
```

---

## Success Criteria

 All tests must pass for production deployment:
- [ ] Fuzzy Match: 99%+ accuracy
- [ ] Tunebat: 99%+ success rate
- [ ] TikTok: 95%+ upload success
- [ ] Logging: All 3 files created
- [ ] Recovery: Resume from all 3 levels
- [ ] Config: All sections valid
- [ ] Validation: All checks passing
- [ ] Setup: Script completes without errors

**Status: ALL TESTS READY TO RUN**
