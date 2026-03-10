from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

creds = Credentials.from_authorized_user_file('token.json')
if creds.expired and creds.refresh_token:
    creds.refresh(Request())

service = build('gmail', 'v1', credentials=creds)

result = service.users().watch(
    userId='me',
    body={
        'labelIds': ['INBOX'],
        'topicName': 'projects/gmail-classifier-489814/topics/gmail-push'
    }
).execute()

print("✅ Gmail watch active:", result)
