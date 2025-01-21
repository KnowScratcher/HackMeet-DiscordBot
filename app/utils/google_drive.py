# app/utils/google_drive.py
"""
Google Drive utility functions for uploading files.
"""
import os
import logging
from typing import Optional, Dict, List
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

def get_drive_service():
    """
    Initialize Google Drive service with service account credentials.
    
    Returns:
        google.discovery.Resource: Google Drive service instance
    """
    credentials_path = os.getenv("GOOGLE_DRIVE_CREDENTIALS")
    if not credentials_path:
        raise ValueError("GOOGLE_DRIVE_CREDENTIALS environment variable not set")
    
    credentials = service_account.Credentials.from_service_account_file(
        credentials_path,
        scopes=['https://www.googleapis.com/auth/drive.file']
    )
    
    return build('drive', 'v3', credentials=credentials)

async def create_drive_folder(folder_name: str, parent_folder_id: Optional[str] = None) -> Optional[str]:
    """
    Create a new folder in Google Drive.
    
    Args:
        folder_name (str): Name of the folder to create
        parent_folder_id (Optional[str]): ID of the parent folder
        
    Returns:
        Optional[str]: Folder ID if creation successful, None otherwise
    """
    try:
        service = get_drive_service()
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id] if parent_folder_id else []
        }
        
        folder = service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()
        
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
    Upload a file to Google Drive.
    
    Args:
        file_path (str): Path to the file to upload
        folder_id (Optional[str]): ID of the folder to upload to. If None, uploads to root
        custom_name (Optional[str]): Custom name for the file in Drive. If None, uses original name
        
    Returns:
        Optional[str]: File ID if upload successful, None otherwise
    """
    try:
        service = get_drive_service()
        file_metadata = {
            'name': custom_name or os.path.basename(file_path),
            'parents': [folder_id] if folder_id else []
        }
        
        media = MediaFileUpload(
            file_path,
            resumable=True
        )
        
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id'
        ).execute()
        
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
    parent_folder_id: Optional[str] = None
) -> bool:
    """
    Upload all meeting files to a new folder in Google Drive.
    
    Args:
        meeting_folder_name (str): Name for the meeting folder
        file_paths (Dict[str, str]): Dictionary of file type to file path mappings
        audio_files (Dict[int, List[str]]): Dictionary of user ID to list of audio file paths
        user_names (Dict[int, str]): Dictionary of user ID to user name mappings
        parent_folder_id (Optional[str]): Parent folder ID in Google Drive
        
    Returns:
        bool: True if all uploads successful, False otherwise
    """
    try:
        # Create meeting folder
        folder_id = await create_drive_folder(meeting_folder_name, parent_folder_id)
        if not folder_id:
            return False
            
        # Upload regular files
        for file_type, file_path in file_paths.items():
            if os.path.exists(file_path):
                await upload_to_drive(file_path, folder_id, f"{file_type}.txt")
                
        # Upload audio files with user names
        for user_id, audio_paths in audio_files.items():
            if isinstance(audio_paths, list):
                # Handle multiple audio files for one user
                for i, audio_path in enumerate(audio_paths):
                    if os.path.exists(audio_path):
                        user_name = user_names.get(user_id, str(user_id))
                        # If there's only one file, don't add part number
                        file_name = f"{user_name}.mp3" if len(audio_paths) == 1 else f"{user_name}_part{i+1}.mp3"
                        await upload_to_drive(audio_path, folder_id, file_name)
            elif isinstance(audio_paths, str) and os.path.exists(audio_paths):
                # Handle single audio file
                user_name = user_names.get(user_id, str(user_id))
                await upload_to_drive(audio_paths, folder_id, f"{user_name}.mp3")
                
        return True
        
    except Exception as e:
        logger.error("Failed to upload meeting files: %s", e)
        return False 
