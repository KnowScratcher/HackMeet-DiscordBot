# app/utils/google_drive.py
"""
Google Drive utility functions for uploading files.
"""
import os
import shutil
import logging
import time
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta
import gc

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
import asyncio

logger = logging.getLogger(__name__)

# Global service cache to prevent creating too many connections
_SERVICE_CACHE = {
    "service": None,
    "last_refresh": None,
    "error_count": 0,
    "quota_error_time": None
}

# Constants for service management
SERVICE_REFRESH_INTERVAL = timedelta(hours=1)  # Refresh connection every hour
MAX_ERROR_COUNT = 10  # Force refresh after this many errors
QUOTA_COOLDOWN = timedelta(minutes=5)  # Wait time after quota error

async def get_drive_service(force_refresh: bool = False):
    """
    Initialize Google Drive service with service account credentials asynchronously.
    
    Args:
        force_refresh (bool): Force a refresh of the service connection
    
    Returns:
        google.discovery.Resource: Google Drive service instance
    """
    global _SERVICE_CACHE
    
    current_time = datetime.now()
    
    # Check if we need to refresh due to quota error
    if _SERVICE_CACHE["quota_error_time"] is not None:
        if current_time - _SERVICE_CACHE["quota_error_time"] < QUOTA_COOLDOWN:
            # Still in cooldown period, log and wait
            logger.info("In quota error cooldown period. Waiting before creating new connection.")
            await asyncio.sleep(5)  # Short delay to prevent rapid retries
        else:
            # Cooldown period over, reset quota error time
            logger.info("Quota error cooldown period over. Resetting connection.")
            _SERVICE_CACHE["quota_error_time"] = None
            force_refresh = True
    
    # Check if we need to refresh the service
    needs_refresh = (
        force_refresh or
        _SERVICE_CACHE["service"] is None or
        _SERVICE_CACHE["last_refresh"] is None or
        (current_time - _SERVICE_CACHE["last_refresh"]) > SERVICE_REFRESH_INTERVAL or
        _SERVICE_CACHE["error_count"] >= MAX_ERROR_COUNT
    )
    
    if needs_refresh:
        # Clean up old service if it exists
        if _SERVICE_CACHE["service"] is not None:
            try:
                # Close any open connections
                _SERVICE_CACHE["service"].close()
            except:
                pass
            
            # Help garbage collection
            _SERVICE_CACHE["service"] = None
            gc.collect()
        
        credentials_path = os.getenv("GOOGLE_DRIVE_CREDENTIALS")
        if not credentials_path:
            raise ValueError("GOOGLE_DRIVE_CREDENTIALS environment variable not set")
        
        def _create_service():
            credentials = service_account.Credentials.from_service_account_file(
                credentials_path,
                scopes=['https://www.googleapis.com/auth/drive.file']
            )
            return build('drive', 'v3', credentials=credentials, cache_discovery=False)
        
        try:
            logger.info("Creating new Google Drive service connection")
            _SERVICE_CACHE["service"] = await asyncio.to_thread(_create_service)
            _SERVICE_CACHE["last_refresh"] = current_time
            _SERVICE_CACHE["error_count"] = 0
            logger.info("Successfully created new Google Drive service connection")
        except Exception as e:
            logger.error("Failed to create Google Drive service: %s", e)
            raise
    
    return _SERVICE_CACHE["service"]

def is_quota_exceeded_error(error: Exception) -> bool:
    """
    Check if an error is a Google Drive quota exceeded error.
    
    Args:
        error (Exception): The error to check
        
    Returns:
        bool: True if it's a quota exceeded error, False otherwise
    """
    if isinstance(error, HttpError):
        if error.status_code == 403:
            error_details = getattr(error, 'error_details', None)
            if error_details:
                for detail in error_details:
                    if detail.get('reason') == 'storageQuotaExceeded':
                        return True
            
            # Check error message as fallback
            error_message = str(error).lower()
            return 'quota' in error_message and 'exceed' in error_message
    
    return False

async def handle_drive_error(error: Exception) -> bool:
    """
    Handle Google Drive errors, with special handling for quota errors.
    
    Args:
        error (Exception): The error that occurred
        
    Returns:
        bool: True if the error was handled and operation should be retried, False otherwise
    """
    global _SERVICE_CACHE
    
    if is_quota_exceeded_error(error):
        logger.warning("Google Drive quota exceeded. Setting cooldown period.")
        _SERVICE_CACHE["quota_error_time"] = datetime.now()
        _SERVICE_CACHE["error_count"] += 1
        
        # Force service refresh on next call
        if _SERVICE_CACHE["service"] is not None:
            try:
                _SERVICE_CACHE["service"].close()
            except:
                pass
            _SERVICE_CACHE["service"] = None
            gc.collect()
        
        return True  # Signal that this error can be retried after cooldown
    
    # For other errors, increment error count but don't set cooldown
    _SERVICE_CACHE["error_count"] += 1
    return False

async def create_drive_folder(folder_name: str, parent_folder_id: Optional[str] = None) -> Optional[str]:
    """
    Create a new folder in Google Drive asynchronously.
    
    Args:
        folder_name (str): Name of the folder to create
        parent_folder_id (Optional[str]): ID of the parent folder
        
    Returns:
        Optional[str]: Folder ID if creation successful, None otherwise
    """
    max_attempts = 5
    attempt = 0
    retry_delay = 10.0
    
    while attempt < max_attempts:
        attempt += 1
        try:
            service = await get_drive_service(force_refresh=(attempt > 1))
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id] if parent_folder_id else []
            }
            
            def _create_folder():
                return service.files().create(
                    body=file_metadata,
                    fields='id'
                ).execute()
            
            folder = await asyncio.to_thread(_create_folder)
            
            logger.info("Created folder '%s' in Google Drive. Folder ID: %s", folder_name, folder.get('id'))
            return folder.get('id')
            
        except Exception as e:
            logger.error("Attempt %d/%d: Failed to create folder in Google Drive: %s", 
                        attempt, max_attempts, e)
            
            should_retry = await handle_drive_error(e)
            
            if attempt >= max_attempts:
                logger.error("Max attempts reached for creating folder. Giving up.")
                return None
            
            # Add jitter to retry delay to prevent thundering herd
            jitter = (0.5 + (attempt * 0.1))
            actual_delay = retry_delay * jitter
            logger.info("Retrying folder creation in %.1f seconds...", actual_delay)
            await asyncio.sleep(actual_delay)
    
    return None

async def upload_to_drive(
    file_path: str,
    folder_id: Optional[str] = None,
    custom_name: Optional[str] = None
) -> Optional[str]:
    """
    Upload a file to Google Drive asynchronously.
    
    Args:
        file_path (str): Path to the file to upload
        folder_id (Optional[str]): ID of the folder to upload to. If None, uploads to root
        custom_name (Optional[str]): Custom name for the file in Drive. If None, uses original name
        
    Returns:
        Optional[str]: File ID if upload successful, None otherwise
    """
    max_attempts = 5
    attempt = 0
    retry_delay = 10.0
    
    while attempt < max_attempts:
        attempt += 1
        try:
            service = await get_drive_service(force_refresh=(attempt > 1))
            file_metadata = {
                'name': custom_name or os.path.basename(file_path),
                'parents': [folder_id] if folder_id else []
            }
            
            def _upload_file():
                media = MediaFileUpload(
                    file_path,
                    resumable=True,
                    chunksize=1024*1024  # 1MB chunks for better performance
                )
                
                request = service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                )
                
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        logger.debug("Uploaded %d%%.", int(status.progress() * 100))
                return response
            
            file = await asyncio.to_thread(_upload_file)
            
            logger.info("File uploaded successfully to Google Drive. File ID: %s", file.get('id'))
            return file.get('id')
            
        except Exception as e:
            logger.error("Attempt %d/%d: Failed to upload file to Google Drive: %s", 
                        attempt, max_attempts, e)
            
            should_retry = await handle_drive_error(e)
            
            if attempt >= max_attempts:
                logger.error("Max attempts reached for uploading file. Giving up.")
                return None
            
            # Add jitter to retry delay to prevent thundering herd
            jitter = (0.5 + (attempt * 0.1))
            actual_delay = retry_delay * jitter
            logger.info("Retrying file upload in %.1f seconds...", actual_delay)
            await asyncio.sleep(actual_delay)
    
    return None

async def upload_meeting_files(
    meeting_folder_name: str,
    file_paths: Dict[str, str],
    audio_files: Dict[int, List[str]],
    user_names: Dict[int, str],
    parent_folder_id: Optional[str] = None,
    local_folder: Optional[str] = None
) -> bool:
    """
    Upload all meeting files to a new folder in Google Drive asynchronously.
    
    Args:
        meeting_folder_name (str): Name for the meeting folder
        file_paths (Dict[str, str]): Dictionary of file type to file path mappings
        audio_files (Dict[int, List[str]]): Dictionary of user ID to list of audio file paths
        user_names (Dict[int, str]): Dictionary of user ID to user name mappings
        parent_folder_id (Optional[str]): Parent folder ID in Google Drive
        local_folder (Optional[str]): Path to local folder to clean up after successful upload
        
    Returns:
        bool: True if all uploads successful, False otherwise
    """
    try:
        # Reset service connection before starting a new batch of uploads
        await get_drive_service(force_refresh=True)
        
        # Create meeting folder
        folder_id = await create_drive_folder(meeting_folder_name, parent_folder_id)
        if not folder_id:
            logger.error("Failed to create meeting folder. Aborting upload.")
            return False
            
        upload_success = True
        
        # Process files in smaller batches to avoid overwhelming the API
        # and to better handle errors
        
        # First, collect all files to upload
        all_uploads = []
        
        # Add regular files
        for file_type, file_path in file_paths.items():
            if os.path.exists(file_path):
                all_uploads.append((file_path, folder_id, f"{file_type}.txt"))
                
        # Add audio files
        for user_id, audio_paths in audio_files.items():
            if isinstance(audio_paths, list):
                # Handle multiple audio files for one user
                for i, audio_path in enumerate(audio_paths):
                    if os.path.exists(audio_path):
                        user_name = user_names.get(user_id, str(user_id))
                        file_name = f"{user_name}.mp3" if len(audio_paths) == 1 else f"{user_name}_part{i+1}.mp3"
                        all_uploads.append((audio_path, folder_id, file_name))
            elif isinstance(audio_paths, str) and os.path.exists(audio_paths):
                # Handle single audio file
                user_name = user_names.get(user_id, str(user_id))
                all_uploads.append((audio_paths, folder_id, f"{user_name}.mp3"))
        
        # Process uploads in batches of 5
        BATCH_SIZE = 5
        total_files = len(all_uploads)
        successful_uploads = 0
        
        for i in range(0, total_files, BATCH_SIZE):
            batch = all_uploads[i:i+BATCH_SIZE]
            logger.info("Processing upload batch %d/%d (%d files)", 
                       (i//BATCH_SIZE)+1, (total_files+BATCH_SIZE-1)//BATCH_SIZE, len(batch))
            
            # Create tasks for this batch
            upload_tasks = []
            for file_path, folder_id, custom_name in batch:
                task = asyncio.create_task(upload_to_drive(file_path, folder_id, custom_name))
                upload_tasks.append(task)
            
            # Wait for this batch to complete
            batch_results = await asyncio.gather(*upload_tasks, return_exceptions=True)
            
            # Check results for this batch
            batch_success = True
            for j, result in enumerate(batch_results):
                if isinstance(result, Exception) or result is None:
                    batch_success = False
                    logger.error("Failed to upload file %s: %s", 
                                batch[j][0], result if isinstance(result, Exception) else "Unknown error")
                else:
                    successful_uploads += 1
            
            # If this batch had quota errors, wait before continuing
            if not batch_success and _SERVICE_CACHE["quota_error_time"] is not None:
                logger.info("Quota error detected. Pausing uploads for cooldown period.")
                await asyncio.sleep(60)  # Wait a minute before trying the next batch
                
                # Refresh the service connection
                await get_drive_service(force_refresh=True)
            
            # Short delay between batches to avoid rate limiting
            await asyncio.sleep(2)
        
        # Determine overall success
        upload_success = (successful_uploads == total_files)
        logger.info("Uploaded %d/%d files successfully", successful_uploads, total_files)
        
        # Clean up local folder if upload was successful
        if upload_success and local_folder and os.path.exists(local_folder):
            try:
                await asyncio.to_thread(shutil.rmtree, local_folder)
                logger.info("Successfully cleaned up local folder: %s", local_folder)
            except Exception as e:
                logger.error("Failed to clean up local folder %s: %s", local_folder, e)
        elif not upload_success and local_folder and os.path.exists(local_folder):
            logger.warning("Not cleaning up local folder %s due to upload failures", local_folder)
                
        return upload_success
        
    except Exception as e:
        logger.error("Failed to upload meeting files: %s", e)
        
        # Force service refresh on next call if we had a serious error
        await get_drive_service(force_refresh=True)
        
        return False

# Function to explicitly reset the Google Drive service connection
async def reset_drive_service():
    """
    Explicitly reset the Google Drive service connection.
    This can be called periodically to prevent memory leaks.
    """
    global _SERVICE_CACHE
    
    logger.info("Explicitly resetting Google Drive service connection")
    
    if _SERVICE_CACHE["service"] is not None:
        try:
            _SERVICE_CACHE["service"].close()
        except:
            pass
        
        _SERVICE_CACHE["service"] = None
        _SERVICE_CACHE["last_refresh"] = None
        _SERVICE_CACHE["error_count"] = 0
        _SERVICE_CACHE["quota_error_time"] = None
        
        # Force garbage collection
        gc.collect()
    
    # Create a fresh connection
    await get_drive_service(force_refresh=True)
    
    return True 
