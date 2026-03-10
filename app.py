import os
import json
import tempfile
import base64
import requests
# NEW
from google import genai
from flask import Flask, request
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import traceback

# ---- CONFIG ----
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_KEY_HERE")
NTFY_TOPIC     = os.environ.get("NTFY_TOPIC", "https://ntfy.sh/john-college-mail")
# ----------------

client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

def get_gmail_service():
    token_data = os.environ.get("TOKEN_JSON")

    if token_data:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(token_data)
            tmp_path = f.name
        creds = Credentials.from_authorized_user_file(tmp_path)
        os.unlink(tmp_path)
    else:
        creds = Credentials.from_authorized_user_file('token.json')

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build('gmail', 'v1', credentials=creds)

def get_email_content(service, msg_id):
    msg = service.users().messages().get(
        userId='me', id=msg_id, format='full'
    ).execute()

    headers = {h['name']: h['value'] for h in msg['payload']['headers']}
    subject = headers.get('Subject', 'No Subject')
    sender  = headers.get('From', 'Unknown')

    body = ""
    payload = msg['payload']

    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain' and 'data' in part.get('body', {}):
                body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')[:600]
                break
    elif 'body' in payload and 'data' in payload['body']:
        body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')[:600]

    return subject, sender, body, msg.get('labelIds', [])

def classify_email(subject, sender, body):
    prompt = f"""You are an email classifier for a college student.
Decide if this email needs their immediate attention.

From: {sender}
Subject: {subject}
Body preview: {body}

Classify it. Reply ONLY in this exact format, nothing else:
IMPORTANT: yes/no
CATEGORY: Academic / Deadline / Admin / Event / Newsletter / Spam / Social
REASON: one sentence max"""

    response = client.models.generate_content(
        model="gemini-1.5-flash",
        contents=prompt
    )
    return response.text.strip()

def parse_classification(text):
    result = {"important": False, "category": "General", "reason": ""}
    for line in text.split('\n'):
        if line.startswith("IMPORTANT:"):
            result["important"] = "yes" in line.lower()
        elif line.startswith("CATEGORY:"):
            result["category"] = line.split(":", 1)[1].strip()
        elif line.startswith("REASON:"):
            result["reason"] = line.split(":", 1)[1].strip()
    return result

def send_notification(subject, sender, category, reason):
    sender_name = sender.split('<')[0].strip() or sender
    requests.post(
        NTFY_TOPIC,
        data=subject.encode('utf-8'),
        headers={
            "Title": f"{category} | {sender_name[:35]}",
            "Priority": "high",
            "Tags": "email,bell"
        }
    )
    print(f"🔔 Notified: {subject}")

@app.route('/webhook', methods=['POST'])
def gmail_webhook():
    print("🔥 Webhook hit!", flush=True)
    try:
        envelope = request.get_json()
        print(f"📨 Envelope received: {envelope}", flush=True)
        service = get_gmail_service()
        print("✅ Gmail service created", flush=True)
        if not envelope or 'message' not in envelope:
            return 'bad request', 400

        service = get_gmail_service()

        results = service.users().messages().list(
            userId='me', q='is:unread', maxResults=3
        ).execute()

        messages = results.get('messages', [])
        if not messages:
            return 'ok', 200

        for msg_meta in messages:
            msg_id = msg_meta['id']
            subject, sender, body, labels = get_email_content(service, msg_id)

            if 'UNREAD' not in labels:
                continue

            print(f"\n📧 New email: {subject} | From: {sender}")

            classification = classify_email(subject, sender, body)
            print(f"🤖 Gemini says:\n{classification}")

            parsed = parse_classification(classification)

            if parsed["important"]:
                send_notification(subject, sender, parsed["category"], parsed["reason"])
            else:
                service.users().messages().modify(
                    userId='me',
                    id=msg_id,
                    body={'removeLabelIds': ['UNREAD']}
                ).execute()
                print(f"✅ Silently marked read: [{parsed['category']}] {subject}")

    except Exception as e:
        print(f"❌ Error: {e}")
        print(traceback.format_exc())

    return 'ok', 200

@app.route('/')
def home():
    return "Email filter is running ✅"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
