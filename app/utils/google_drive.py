# app/utils/google_drive.py
"""
Google Drive utility functions for uploading files.
"""
import os
import shutil
import logging
from typing import Optional, Dict, List
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import asyncio

logger = logging.getLogger(__name__)

async def get_drive_service():
    """
    Initialize Google Drive service with service account credentials asynchronously.
    
    Returns:
        google.discovery.Resource: Google Drive service instance
    """
    credentials_path = os.getenv("GOOGLE_DRIVE_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_DRIVE_CREDENTIALS environment variable not set")
    
    def _create_service():
        credentials = service_account.Credentials.from_service_account_file(
            credentials_path,
            scopes=['https://www.googleapis.com/auth/drive.file']
        )
        return build('drive', 'v3', credentials=credentials)
    
    return await asyncio.to_thread(_create_service)

async def create_drive_folder(folder_name: str, parent_folder_id: Optional[str] = None) -> Optional[str]:
    """
    Create a new folder in Google Drive asynchronously.
    
    Args:
        folder_name (str): Name of the folder to create
        parent_folder_id (Optional[str]): ID of the parent folder
        
    Returns:
        Optional[str]: Folder ID if creation successful, None otherwise
    """
    try:
        service = await get_drive_service()
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id] if parent_folder_id else []
        }
        
        async def _create_folder():
            return service.files().create(
                body=file_metadata,
                fields='id'
            ).execute()
        
        folder = await asyncio.to_thread(_create_folder)
        
        logger.info("Created folder '%s' in Google Drive. Folder ID: %s", folder_name, folder.get('id'))
        return folder.get('id')
        
    except Exception as e:
        logger.error("Failed to create folder in Google Drive: %s", e)
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
    try:
        service = await get_drive_service()
        file_metadata = {
            'name': custom_name or os.path.basename(file_path),
            'parents': [folder_id] if folder_id else []
        }
        
        async def _upload_file():
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
        logger.error("Failed to upload file to Google Drive: %s", e)
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
        # Create meeting folder
        folder_id = await create_drive_folder(meeting_folder_name, parent_folder_id)
        if not folder_id:
            return False
            
        upload_success = True
        upload_tasks = []
            
        # Prepare regular file upload tasks
        for file_type, file_path in file_paths.items():
            if os.path.exists(file_path):
                task = upload_to_drive(file_path, folder_id, f"{file_type}.txt")
                upload_tasks.append(task)
                
        # Prepare audio file upload tasks
        for user_id, audio_paths in audio_files.items():
            if isinstance(audio_paths, list):
                # Handle multiple audio files for one user
                for i, audio_path in enumerate(audio_paths):
                    if os.path.exists(audio_path):
                        user_name = user_names.get(user_id, str(user_id))
                        file_name = f"{user_name}.mp3" if len(audio_paths) == 1 else f"{user_name}_part{i+1}.mp3"
                        task = upload_to_drive(audio_path, folder_id, file_name)
                        upload_tasks.append(task)
            elif isinstance(audio_paths, str) and os.path.exists(audio_paths):
                # Handle single audio file
                user_name = user_names.get(user_id, str(user_id))
                task = upload_to_drive(audio_paths, folder_id, f"{user_name}.mp3")
                upload_tasks.append(task)
        
        # Execute all upload tasks concurrently
        results = await asyncio.gather(*upload_tasks, return_exceptions=True)
        
        # Check results
        for result in results:
            if isinstance(result, Exception) or result is None:
                upload_success = False
                logger.error("One or more files failed to upload: %s", result)
        
        # Clean up local folder if upload was successful
        if upload_success and local_folder and os.path.exists(local_folder):
            try:
                await asyncio.to_thread(shutil.rmtree, local_folder)
                logger.info("Successfully cleaned up local folder: %s", local_folder)
            except Exception as e:
                logger.error("Failed to clean up local folder %s: %s", local_folder, e)
                
        return upload_success
        
    except Exception as e:
        logger.error("Failed to upload meeting files: %s", e)
        return False 
