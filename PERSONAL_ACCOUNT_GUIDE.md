# Switching to Your Personal Google Drive Account

This guide will help you switch from using a service account to your personal Google Drive account. This solves the "storageQuotaExceeded" error because your personal account has more storage space than a service account.

## Why Switch to a Personal Account?

1. **More Storage**: Your personal Google Drive account likely has 15GB of free storage or more if you have a paid subscription.
2. **Avoid Quota Errors**: Service accounts have stricter usage limits that can cause "storageQuotaExceeded" errors.
3. **Better Management**: You can directly access and manage the uploaded files in your Google Drive.

## Setup Instructions

### 1. Create OAuth Credentials

1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Enable the Google Drive API for your project
4. Create OAuth credentials:
   - Navigate to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Select "Desktop app" as the application type
   - Give it a name (e.g., "HackMeet Drive Access")
   - Click "Create"
   - Download the credentials JSON file

### 2. Set Up Your Application

Run the setup script to configure your application to use your personal account:

```bash
python setup_oauth.py
```

The script will:
1. Prompt you to enter your Client ID and Client Secret
2. Create a client_secrets.json file
3. Update your .env file with the new configuration

### 3. First-Time Authentication

The first time you run the application after switching to a personal account, you'll need to authenticate:

1. The application will open a browser window
2. Sign in with your Google account
3. Grant permission for the application to access your Google Drive
4. The authentication token will be saved so you won't need to authenticate again

### 4. Create a Folder in Your Google Drive

1. Go to your [Google Drive](https://drive.google.com/)
2. Create a new folder where meeting recordings will be stored
3. Right-click the folder and select "Get link"
4. From the link (like https://drive.google.com/drive/folders/ABC123XYZ), copy the folder ID (ABC123XYZ)
5. Update your .env file with the new folder ID:

```
GOOGLE_DRIVE_FOLDER_ID="your_folder_id_here"
```

## Troubleshooting

### Authorization Failed

If you see "Authorization failed" messages:
1. Delete the `token.pickle` file in your project directory
2. Run the application again to re-authenticate

### Permissions Issues

If you see permission errors when uploading:
1. Make sure you approved all permissions during authentication
2. Check that the folder ID in your .env file is correct
3. Ensure your Google account has enough storage space

### Browser Authentication Doesn't Open

If the browser doesn't open automatically:
1. Look for a URL in the application logs
2. Copy and paste this URL into your browser manually
3. Complete the authentication process

## Managing Your Files

After switching to a personal account, all meeting recordings will be uploaded to the folder you specified in your Google Drive. You can:

1. Access these files directly through your Google Drive
2. Organize or delete files as needed
3. Share the folder with other users if required

This approach gives you full control over your meeting recordings while avoiding quota errors. 