# Caching Behavior Across Channels

## Current Architecture

When processing multiple channels for the same track, **each channel creates its own processor instance**:

```python
# In dispatch_download.py, line 408
for channel in selected_channels:
    processor = processor_class({**args, "channel": channel_key, ...})
    processor.download(track_id)  # Each channel gets a NEW instance
```

## Caching Scope Per Channel Instance

### ✅ **Base Clip (Background + Thumbnail)**
- **Cached**: Per channel instance
- **Shared**: Across all stems of that channel
- **Not Shared**: Across different channels (each channel reloads)
- **Why**: Each channel is a separate instance, so each has its own cache

### ✅ **Watermark Clip**
- **Cached**: Per channel instance
- **Shared**: Across all stems of that channel
- **Not Shared**: Across different channels (each channel has different watermark anyway)
- **Why**: Each channel uses a different watermark (Main → SGS, Backup → SGS2, etc.)

### ✅ **Icon Clip**
- **Cached**: Per channel instance
- **Shared**: Across all stems of that channel
- **Not Shared**: Across different channels (each channel reloads icons)
- **Why**: Each channel instance has its own icon cache

## Example: Processing 1 Track Across 3 Channels

**Track**: "Song Name" by Artist  
**Channels**: Main, Backup, Drums  
**Stems per channel**: 5 (Acapella, Drums, Bass, Melody, Instrumental)

### Channel 1: Main
```
Instance 1 created:
  ✓ Load background.png → CACHED (for all Main stems)
  ✓ Load thumbnail → CACHED (for all Main stems)
  ✓ Load watermark (sgs.png) → CACHED (for all Main stems)
  ✓ Load icon (acapella.png) → CACHED (for all Main stems)
  
Stem 2-5: Reuse all cached clips
```

### Channel 2: Backup
```
Instance 2 created (NEW instance):
  ✓ Load background.png → CACHED (for all Backup stems)
  ✓ Load thumbnail → CACHED (for all Backup stems)
  ✓ Load watermark (sgs_2.png) → CACHED (for all Backup stems)
  ✓ Load icon (acapella.png) → CACHED (for all Backup stems)
  
Stem 2-5: Reuse all cached clips
```

### Channel 3: Drums
```
Instance 3 created (NEW instance):
  ✓ Load background.png → CACHED (for all Drums stems)
  ✓ Load thumbnail → CACHED (for all Drums stems)
  ✓ Load watermark (son_got_drums.png) → CACHED (for all Drums stems)
  ✓ Load icon (acapella.png) → CACHED (for all Drums stems)
  
Stem 2-5: Reuse all cached clips
```

## Performance Impact

### Within Each Channel (5 stems):
- **Without caching**: 5 × 4 operations = 20 operations
- **With caching**: 4 operations (first stem) + 0 operations (stems 2-5) = 4 operations
- **Gain**: 80% reduction per channel

### Across All Channels (3 channels × 5 stems):
- **Without caching**: 3 × 5 × 4 = 60 operations
- **With caching**: 3 × 4 = 12 operations (each channel loads once)
- **Gain**: 80% reduction overall

## Summary

✅ **Caching works perfectly WITHIN each channel** - all stems of the same channel share the cache

✅ **Each channel has its own cache instance** - this is by design because:
   - Each channel uses different watermarks
   - Each channel is processed independently
   - This prevents cross-channel cache conflicts

✅ **Performance is still excellent** - even though each channel reloads assets, within each channel all stems reuse the cache, giving 80% performance improvement per channel

## Future Optimization (Optional)

If you want to share base clip (background + thumbnail) across ALL channels:
- Create a shared cache at the track level (not instance level)
- Pass cached base clip to each channel processor
- This would save 2 asset loads per additional channel

**Current approach is simpler and still very efficient!**

