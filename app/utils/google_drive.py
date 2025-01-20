# app/utils/google_drive.py
"""
Google Drive utility functions for uploading files.
"""
import os
import logging
from typing import Optional
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

async def upload_to_drive(file_path: str, folder_id: Optional[str] = None) -> Optional[str]:
    """
    Upload a file to Google Drive.
    
    Args:
        file_path (str): Path to the file to upload
        folder_id (Optional[str]): ID of the folder to upload to. If None, uploads to root
        
    Returns:
        Optional[str]: File ID if upload successful, None otherwise
    """
    try:
        service = get_drive_service()
        file_metadata = {
            'name': os.path.basename(file_path),
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
