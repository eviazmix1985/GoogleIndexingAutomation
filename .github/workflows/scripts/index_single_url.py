import os
from google.oauth2 import service_account
from googleapiclient.discovery import build

TARGET_URL = os.environ["TARGET_URL"]
CREDS_FILE = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]

credentials = service_account.Credentials.from_service_account_file(
    CREDS_FILE,
    scopes=["https://www.googleapis.com/auth/indexing"]
)

service = build("indexing", "v3", credentials=credentials)

body = {
    "url": TARGET_URL,
    "type": "URL_UPDATED"
}

result = service.urlNotifications().publish(body=body).execute()
print("Submitted:", TARGET_URL)
print(result)
