# Memory Management and Google Drive Quota Fix

This update addresses issues with memory leaks and Google Drive quota errors that occur when the application runs for extended periods.

## Key Improvements

1. **Google Drive Service Management**
   - Implemented a service cache with automatic refresh
   - Added specific handling for quota exceeded errors
   - Added cooldown periods after quota errors
   - Batch processing of uploads to reduce API load

2. **Memory Management**
   - Added periodic garbage collection
   - Implemented memory usage monitoring
   - Added service connection reset to prevent memory leaks
   - Preserved local files when uploads fail

3. **Error Handling**
   - Improved retry mechanisms with exponential backoff
   - Better error logging and recovery
   - Automatic service reset after serious errors

## Installation

1. Install the required psutil package for memory monitoring:

```bash
python install_psutil.py
```

Or manually:

```bash
pip install psutil>=5.9.0
```

2. Restart your application:

```bash
python run.py
```

## How It Works

### Google Drive Quota Handling

When a Google Drive quota error occurs:
1. The system detects the "storageQuotaExceeded" error
2. It enters a cooldown period (5 minutes by default)
3. During cooldown, it preserves local files in a backup folder
4. After cooldown, it creates a fresh connection to Google Drive
5. Future uploads are processed in smaller batches

### Memory Management

The application now includes:
1. Hourly memory usage monitoring
2. Forced garbage collection every 4 hours
3. Service connection reset every 4 hours
4. Automatic cleanup of unused resources

### Logs to Watch For

Look for these log messages to confirm the fixes are working:

```
INFO: Memory usage: RSS=XX.XX MB, VMS=XX.XX MB, Peak=XX.XX MB, Uptime=X.X hours
INFO: Performing periodic service reset
INFO: Google Drive quota exceeded. Setting cooldown period.
INFO: Quota error cooldown period over. Resetting connection.
INFO: Creating new Google Drive service connection
```

## Troubleshooting

If you still encounter issues:

1. **Memory usage continues to grow**: Try reducing the service reset interval in `app/main.py` from 4 hours to 2 hours.

2. **Google Drive quota errors persist**: Check your Google Drive storage usage and consider:
   - Manually deleting old files
   - Upgrading your storage plan
   - Modifying the `QUOTA_COOLDOWN` in `app/utils/google_drive.py` to a longer period

3. **Application crashes**: Check the logs for specific errors and ensure all dependencies are installed correctly. 