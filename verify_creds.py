import json
import os

# Get credentials from Heroku environment variables
credentials = os.getenv("GOOGLE_DRIVE_CREDENTIALS")

if credentials:
    try:
        creds_dict = json.loads(credentials)
        print("✅ Credentials loaded successfully!")
        print(f"Service Account Email: {creds_dict.get('client_email')}")
    except json.JSONDecodeError:
        print("❌ Invalid JSON format in credentials.")
else:
    print("❌ GOOGLE_DRIVE_CREDENTIALS not found.")
