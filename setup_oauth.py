#!/usr/bin/env python
"""
Script to set up OAuth credentials for Google Drive.

This script guides you through the process of setting up OAuth credentials
for your personal Google Drive account, which will be used instead of a
service account to avoid storage quota limitations.
"""
import os
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def create_client_secrets():
    """Create a client_secrets.json file from user input."""
    print("\n=== Google Drive OAuth Setup ===")
    print("\nTo use your personal Google Drive account, you need to create OAuth credentials.")
    print("Follow these steps:")
    print("1. Go to https://console.cloud.google.com/apis/credentials")
    print("2. Create a new project or select an existing one")
    print("3. Click 'Create Credentials' and select 'OAuth client ID'")
    print("4. Select 'Desktop app' as the application type")
    print("5. Give it a name (e.g., 'HackMeet Drive Access')")
    print("6. Click 'Create'")
    print("7. You'll be shown your client ID and client secret\n")
    
    # Get the client ID and client secret from user input
    client_id = input("Enter your Client ID: ").strip()
    if not client_id:
        logger.error("Client ID cannot be empty.")
        return False
        
    client_secret = input("Enter your Client Secret: ").strip()
    if not client_secret:
        logger.error("Client Secret cannot be empty.")
        return False
    
    # Create the client_secrets.json file
    client_secrets = {
        "installed": {
            "client_id": client_id,
            "project_id": "hackmeet-bot",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_secret": client_secret,
            "redirect_uris": ["http://localhost"]
        }
    }
    
    # Write the client_secrets.json file
    try:
        with open('client_secrets.json', 'w') as f:
            json.dump(client_secrets, f, indent=2)
        logger.info("Successfully created client_secrets.json")
        return True
    except Exception as e:
        logger.error(f"Failed to create client_secrets.json: {e}")
        return False

def update_env_file():
    """Update the .env file with the OAuth credentials path."""
    env_path = Path('.env')
    
    if not env_path.exists():
        logger.error(".env file not found. Please create one.")
        return False
    
    try:
        with open(env_path, 'r') as f:
            env_lines = f.readlines()
        
        # Find the GOOGLE_DRIVE_CREDENTIALS line and replace it
        new_env_lines = []
        oauth_added = False
        
        for line in env_lines:
            if line.strip().startswith('GOOGLE_DRIVE_CREDENTIALS='):
                # Add a comment about the old service account
                new_env_lines.append(f"# Old service account: {line.strip()}\n")
                new_env_lines.append("# Path to OAuth client secrets file\n")
                new_env_lines.append("GOOGLE_OAUTH_CREDENTIALS=\"client_secrets.json\"\n")
                oauth_added = True
            else:
                new_env_lines.append(line)
        
        # If there was no GOOGLE_DRIVE_CREDENTIALS line, add the new one
        if not oauth_added:
            new_env_lines.append("\n# Path to OAuth client secrets file\n")
            new_env_lines.append("GOOGLE_OAUTH_CREDENTIALS=\"client_secrets.json\"\n")
        
        # Write the updated .env file
        with open(env_path, 'w') as f:
            f.writelines(new_env_lines)
        
        logger.info("Successfully updated .env file with OAuth credentials path")
        return True
    
    except Exception as e:
        logger.error(f"Failed to update .env file: {e}")
        return False

def main():
    """Main entry point for the script."""
    print("Google Drive Personal Account Setup")
    print("==================================")
    print("This script will help you set up your personal Google Drive account")
    print("to replace the service account and avoid storage quota limitations.")
    
    # Create the client_secrets.json file
    if not create_client_secrets():
        logger.error("Failed to create client_secrets.json. Exiting.")
        return False
    
    # Update the .env file
    if not update_env_file():
        logger.error("Failed to update .env file. You'll need to manually update it.")
        
    print("\nNext steps:")
    print("1. Run your application. You'll be prompted to authenticate in your browser.")
    print("2. After authentication, your credentials will be saved for future use.")
    print("3. Your application will now use your personal Google Drive account.")
    
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 