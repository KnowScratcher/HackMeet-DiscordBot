"""
Google Drive utility functions using OAuth2 for personal account authentication.
"""
import os
import pickle
import logging
import time
import asyncio
from datetime import datetime, timedelta
import gc
from typing import Optional, Dict, List, Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

# Global service cache to prevent creating too many connections
_SERVICE_CACHE = {
    "service": None,
    "last_refresh": None,
    "error_count": 0,
    "quota_error_time": None
}

# Constants for service management
SERVICE_REFRESH_INTERVAL = 3600  # 1 hour
QUOTA_COOLDOWN = 300  # 5 minutes
ERROR_THRESHOLD = 10  # After this many errors, force refresh
MAX_BATCH_SIZE = 3  # Max files to upload in a single batch

# OAuth scopes for Google Drive
SCOPES = ['https://www.googleapis.com/auth/drive.file']

async def get_drive_service(force_refresh: bool = False):
    """
    Get a Google Drive service object for API access using OAuth2 authentication.
    
    Args:
        force_refresh (bool): Whether to force a refresh of the service
        
    Returns:
        Google Drive service object
    """
    # Check for ongoing quota error cooldown
    if _SERVICE_CACHE["quota_error_time"] is not None:
        elapsed = time.time() - _SERVICE_CACHE["quota_error_time"]
        if elapsed < QUOTA_COOLDOWN:
            logger.info(
                "Quota error cooldown active. %.1f seconds remaining.",
                QUOTA_COOLDOWN - elapsed
            )
            return None
        else:
            logger.info("Quota error cooldown period over. Resetting connection.")
            _SERVICE_CACHE["quota_error_time"] = None
            force_refresh = True

    # Check if we need to refresh the service
    if not force_refresh and _SERVICE_CACHE["service"] is not None:
        # Check if we've exceeded the error threshold
        if _SERVICE_CACHE["error_count"] >= ERROR_THRESHOLD:
            logger.warning(
                "Error threshold reached (%d errors). Forcing service refresh.",
                _SERVICE_CACHE["error_count"]
            )
            force_refresh = True
        # Check if the service is too old
        elif _SERVICE_CACHE["last_refresh"] is not None:
            elapsed = time.time() - _SERVICE_CACHE["last_refresh"]
            if elapsed > SERVICE_REFRESH_INTERVAL:
                logger.info(
                    "Service is %.1f hours old. Refreshing.",
                    elapsed / 3600
                )
                force_refresh = True

    # Return cached service if available and valid
    if not force_refresh and _SERVICE_CACHE["service"] is not None:
        return _SERVICE_CACHE["service"]

    # Force garbage collection before creating a new service
    gc.collect()
    
    # Create a new service
    logger.info("Creating new Google Drive service connection")
    try:
        creds = None
        token_path = os.path.join(os.path.dirname(__file__), '..', '..', 'token.pickle')
        client_secrets_path = os.getenv('GOOGLE_OAUTH_CREDENTIALS', 'client_secrets.json')
        
        # Load existing credentials if available
        if os.path.exists(token_path):
            with open(token_path, 'rb') as token:
                creds = pickle.load(token)
        
        # Refresh or create new credentials
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                def _refresh_creds():
                    creds.refresh(Request())
                    return creds
                
                loop = asyncio.get_event_loop()
                creds = await loop.run_in_executor(None, _refresh_creds)
            else:
                # Need user authentication - this will open a browser window
                if not os.path.exists(client_secrets_path):
                    logger.error(f"Client secrets file not found: {client_secrets_path}")
                    return None
                    
                def _get_creds():
                    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, SCOPES)
                    return flow.run_local_server(port=0)
                
                logger.info("Opening browser for OAuth authentication...")
                loop = asyncio.get_event_loop()
                creds = await loop.run_in_executor(None, _get_creds)
                
                # Save the credentials for the next run
                with open(token_path, 'wb') as token:
                    pickle.dump(creds, token)
                
                logger.info("Authentication complete and credentials saved.")
        
        def _create_service():
            return build('drive', 'v3', credentials=creds)
        
        loop = asyncio.get_event_loop()
        service = await loop.run_in_executor(None, _create_service)
        
        # Update cache
        _SERVICE_CACHE["service"] = service
        _SERVICE_CACHE["last_refresh"] = time.time()
        _SERVICE_CACHE["error_count"] = 0
        
        return service
        
    except Exception as e:
        logger.error("Failed to create Google Drive service: %s", e)
        return None

def is_quota_exceeded_error(error: Exception) -> bool:
    """
    Check if an error is related to quota exceeded.
    
    Args:
        error (Exception): The error to check
        
    Returns:
        bool: True if it's a quota exceeded error
    """
    if isinstance(error, HttpError):
        # Check for storageQuotaExceeded error
        if error.reason and "storageQuotaExceeded" in error.reason:
            return True
        
        # Check for quota limit reached in the error message
        error_message = str(error).lower()
        quota_keywords = [
            "quota exceeded", 
            "user rate limit exceeded",
            "rate limit exceeded",
            "storage quota"
        ]
        
        for keyword in quota_keywords:
            if keyword in error_message:
                return True
    
    return False

async def handle_drive_error(error: Exception) -> bool:
    """
    Handle Google Drive errors, particularly quota errors.
    
    Args:
        error (Exception): The error that occurred
        
    Returns:
        bool: True if the operation should be retried, False otherwise
    """
    # Increment error count
    _SERVICE_CACHE["error_count"] += 1
    
    # Check for quota exceeded error
    if is_quota_exceeded_error(error):
        logger.warning("Google Drive quota exceeded. Setting cooldown period.")
        _SERVICE_CACHE["quota_error_time"] = time.time()
        return False
    
    # For other errors, consider retrying based on error count
    if _SERVICE_CACHE["error_count"] < ERROR_THRESHOLD:
        logger.warning(
            "Google Drive error (count: %d/%d): %s",
            _SERVICE_CACHE["error_count"],
            ERROR_THRESHOLD,
            str(error)
        )
        return True
    else:
        # Too many errors, reset the service
        logger.error(
            "Too many Google Drive errors (%d). Resetting service.",
            _SERVICE_CACHE["error_count"]
        )
        await reset_drive_service()
        return False

async def create_drive_folder(folder_name: str, parent_folder_id: Optional[str] = None) -> Optional[str]:
    """
    Create a folder in Google Drive.
    
    Args:
        folder_name (str): Name of the folder to create
        parent_folder_id (str, optional): ID of the parent folder
        
    Returns:
        str: ID of the created folder, or None if creation failed
    """
    service = await get_drive_service()
    if not service:
        logger.error("Failed to get Drive service for folder creation")
        return None
    
    # Set up folder metadata
    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder'
    }
    
    # Set parent folder if provided
    if parent_folder_id:
        folder_metadata['parents'] = [parent_folder_id]
    
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            def _create_folder():
                return service.files().create(
                    body=folder_metadata,
                    fields='id'
                ).execute()
            
            loop = asyncio.get_event_loop()
            folder = await loop.run_in_executor(None, _create_folder)
            
            folder_id = folder.get('id')
            logger.info("Created folder '%s' with ID: %s", folder_name, folder_id)
            return folder_id
            
        except Exception as e:
            logger.error(
                "Attempt %d/%d: Failed to create folder: %s",
                attempt, max_attempts, str(e)
            )
            
            # Handle the error
            retry = await handle_drive_error(e)
            if not retry:
                break
                
            # Get a fresh service for retry
            service = await get_drive_service(force_refresh=(attempt == max_attempts - 1))
            if not service:
                break
                
            # Wait before retrying
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    return None

async def upload_to_drive(
    file_path: str,
    folder_id: Optional[str] = None,
    custom_name: Optional[str] = None
) -> Optional[str]:
    """
    Upload a file to Google Drive.
    
    Args:
        file_path (str): Path to the file to upload
        folder_id (str, optional): ID of the folder to upload to
        custom_name (str, optional): Custom name for the file
        
    Returns:
        str: ID of the uploaded file, or None if upload failed
    """
    service = await get_drive_service()
    if not service:
        logger.error("Failed to get Drive service for file upload")
        return None
    
    # Set up file metadata
    file_name = custom_name if custom_name else os.path.basename(file_path)
    file_metadata = {'name': file_name}
    
    # Set parent folder if provided
    if folder_id:
        file_metadata['parents'] = [folder_id]
    
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            # Use MediaFileUpload with resumable upload strategy
            media = MediaFileUpload(
                file_path,
                resumable=True,
                chunksize=1024*1024  # 1MB chunks
            )
            
            def _upload_file():
                request = service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id'
                )
                
                # Use resumable upload with progress tracking
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        logger.debug(
                            "Uploaded %d%% of %s",
                            int(status.progress() * 100),
                            file_name
                        )
                
                return response
            
            # Execute the upload in a separate thread
            loop = asyncio.get_event_loop()
            file = await loop.run_in_executor(None, _upload_file)
            
            file_id = file.get('id')
            logger.info("Uploaded file '%s' with ID: %s", file_name, file_id)
            return file_id
            
        except Exception as e:
            logger.error(
                "Attempt %d/%d: Failed to upload file %s: %s",
                attempt, max_attempts, file_name, str(e)
            )
            
            # Handle the error
            retry = await handle_drive_error(e)
            if not retry:
                break
                
            # Get a fresh service for retry
            service = await get_drive_service(force_refresh=(attempt == max_attempts - 1))
            if not service:
                break
                
            # Wait before retrying
            await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
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
    Upload all meeting-related files to a new folder in Google Drive.
    
    Args:
        meeting_folder_name (str): Name of the folder to create for the meeting
        file_paths (Dict[str, str]): Dictionary of file paths (metadata, transcript, etc.)
        audio_files (Dict[int, List[str]]): Dictionary of audio files by user ID
        user_names (Dict[int, str]): Dictionary of user names by user ID
        parent_folder_id (str, optional): ID of the parent folder
        local_folder (str, optional): Path to the local folder (for cleanup)
        
    Returns:
        bool: True if upload succeeds, False otherwise
    """
    try:
        # Create the meeting folder
        meeting_folder_id = await create_drive_folder(
            meeting_folder_name,
            parent_folder_id or os.getenv("GOOGLE_DRIVE_FOLDER_ID")
        )
        
        if not meeting_folder_id:
            logger.error("Failed to create meeting folder in Google Drive")
            return False
        
        # Upload metadata files (processed in small batches to avoid quota issues)
        metadata_files = {k: v for k, v in file_paths.items() if k != "audio_files"}
        total_files = len(metadata_files)
        uploaded_count = 0
        failed_count = 0
        
        # Process metadata files in batches
        for batch_index, batch_items in enumerate(chunks(metadata_files, MAX_BATCH_SIZE)):
            logger.info(
                "Processing metadata files batch %d/%d (%d files)",
                batch_index + 1,
                (total_files + MAX_BATCH_SIZE - 1) // MAX_BATCH_SIZE,
                len(batch_items)
            )
            
            # Add a small delay between batches to avoid rate limits
            if batch_index > 0:
                await asyncio.sleep(2)
            
            # Upload files in this batch
            for file_type, file_path in batch_items.items():
                if os.path.exists(file_path):
                    custom_name = f"{meeting_folder_name}_{file_type}{os.path.splitext(file_path)[1]}"
                    file_id = await upload_to_drive(
                        file_path,
                        meeting_folder_id,
                        custom_name
                    )
                    
                    if file_id:
                        uploaded_count += 1
                    else:
                        failed_count += 1
                        logger.error("Failed to upload %s file", file_type)
        
        # Create a folder for audio files
        audio_folder_id = None
        if any(audio_files.values()):
            audio_folder_name = f"{meeting_folder_name}_audio"
            audio_folder_id = await create_drive_folder(
                audio_folder_name,
                meeting_folder_id
            )
            
            if not audio_folder_id:
                logger.error("Failed to create audio folder in Google Drive")
                # Continue with partial success, we uploaded the metadata files
        
        # Upload audio files if the audio folder was created
        if audio_folder_id:
            # Upload per-user audio files
            for user_id, user_audio_files in audio_files.items():
                if not user_audio_files:
                    continue
                
                user_name = user_names.get(user_id, str(user_id))
                
                # Create a folder for this user's audio files
                user_folder_name = f"{user_name}_audio"
                user_folder_id = await create_drive_folder(
                    user_folder_name,
                    audio_folder_id
                )
                
                if not user_folder_id:
                    logger.error("Failed to create audio folder for user %s", user_name)
                    continue
                
                # Upload each audio file for this user (in batches)
                for batch_idx in range(0, len(user_audio_files), MAX_BATCH_SIZE):
                    batch = user_audio_files[batch_idx:batch_idx + MAX_BATCH_SIZE]
                    
                    # Add a small delay between batches
                    if batch_idx > 0:
                        await asyncio.sleep(2)
                    
                    for audio_file in batch:
                        if os.path.exists(audio_file):
                            file_id = await upload_to_drive(
                                audio_file,
                                user_folder_id
                            )
                            
                            if file_id:
                                uploaded_count += 1
                            else:
                                failed_count += 1
                                logger.error("Failed to upload audio file: %s", audio_file)
        
        # Report upload results
        logger.info(
            "Uploaded %d files to Google Drive folder '%s'. %d files failed.",
            uploaded_count, meeting_folder_name, failed_count
        )
        
        # Clean up local files if requested and all uploads succeeded
        if local_folder and failed_count == 0:
            try:
                if os.path.exists(local_folder):
                    shutil.rmtree(local_folder)
                    logger.info("Cleaned up local folder: %s", local_folder)
            except Exception as ex:
                logger.error("Failed to clean up local folder: %s", ex)
        
        return failed_count == 0
        
    except Exception as e:
        logger.error("Failed to upload meeting files: %s", e)
        return False

def chunks(data: dict, size: int):
    """Split dictionary into chunks of specified size."""
    items = list(data.items())
    for i in range(0, len(items), size):
        yield dict(items[i:i + size])

async def reset_drive_service():
    """
    Explicitly reset the Google Drive service connection.
    This helps prevent memory leaks from long-running connections.
    """
    if _SERVICE_CACHE["service"] is not None:
        try:
            # Set service to None to allow garbage collection
            _SERVICE_CACHE["service"] = None
            
            # Force garbage collection
            gc.collect()
            
            logger.info("Google Drive service reset complete")
        except Exception as e:
            logger.error("Error resetting Google Drive service: %s", e)
    
    # Reset error counts
    _SERVICE_CACHE["error_count"] = 0 