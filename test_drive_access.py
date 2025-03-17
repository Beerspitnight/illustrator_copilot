from googleapiclient.discovery import build
from google.oauth2.service_account import Credentials
import os

# Load credentials from Heroku environment variable
import json
credentials_json = os.getenv("GOOGLE_DRIVE_CREDENTIALS")
if not credentials_json:
    print("❌ No credentials found. Check Heroku config.")
    exit()

credentials = Credentials.from_service_account_info(json.loads(credentials_json), scopes=["https://www.googleapis.com/auth/drive"])

# Build the Drive API client
service = build("drive", "v3", credentials=credentials)

# Folder ID of "ill-co-p2-learns"
FOLDER_ID = "1q8Rbo5N3mPweYlrf3rFFXxLGUbW95o-j"

# List files in the shared folder
results = service.files().list(q=f"'{FOLDER_ID}' in parents", fields="files(id, name)").execute()
files = results.get("files", [])

if files:
    print("✅ Service account can access the folder!")
    for file in files:
        print(f" - {file['name']} (ID: {file['id']})")
else:
    print("⚠️ The folder is empty or access is restricted.")
