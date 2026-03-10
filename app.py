import os
import json
import tempfile
import base64
import requests
# NEW
from groq import Groq

from flask import Flask, request
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import traceback

# ---- CONFIG ----
NTFY_TOPIC     = os.environ.get("NTFY_TOPIC", "https://ntfy.sh/john-college-mail")
# ----------------

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
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
    prompt = f"""You are an email classifier for a college student in India.

From: {sender}
Subject: {subject}
Body preview: {body}

Your job is to decide if this email truly needs the student's IMMEDIATE attention.

Mark IMPORTANT: yes ONLY for:
- Exam schedules, timetable changes, hall ticket releases
- Attendance warnings or shortage notices
- Fee payment deadlines or dues
- Assignment or project submission deadlines
- Academic results or grade releases
- Mess menu changes or hostel notices
- Urgent college administration notices
- Course registration or add/drop deadlines
- Scholarship deadlines or disbursements
- Online course deadlines (NPTEL, Coursera etc.)
- Calendar or schedule changes affecting academics

Mark IMPORTANT: no for EVERYTHING else including:
- Internship or placement opportunities
- Hackathons or coding competitions
- Events, fests, cultural programs
- Webinars or guest lectures
- Research publications or paper calls
- Club or society announcements
- Newsletters or college magazines
- Job postings or referrals
- Sports events or tryouts
- Volunteer opportunities

Be strict. When in doubt, mark as NOT important.

Reply ONLY in this exact format, nothing else:
IMPORTANT: yes/no
CATEGORY: Exam / Attendance / Fee / Deadline / Mess / Admin / Course / Scholarship / Other
REASON: one sentence max"""

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

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
@app.route('/webhook', methods=['POST'])
def gmail_webhook():
    print("🔥 Webhook hit!", flush=True)
    try:
        envelope = request.get_json()
        if not envelope or 'message' not in envelope:
            return 'bad request', 400

        # Decode the Pub/Sub message to get historyId
        import base64 as b64
        data = json.loads(b64.b64decode(envelope['message']['data']).decode())
        history_id = data.get('historyId')
        print(f"📨 HistoryId: {history_id}", flush=True)

        service = get_gmail_service()
        print("✅ Gmail service created", flush=True)

        # Get only NEW messages using history
        try:
            history = service.users().history().list(
                userId='me',
                startHistoryId=history_id,
                historyTypes=['messageAdded'],
                labelId='INBOX'
            ).execute()
        except Exception:
            return 'ok', 200

        messages_added = []
        for record in history.get('history', []):
            for msg in record.get('messagesAdded', []):
                messages_added.append(msg['message']['id'])

        if not messages_added:
            print("📭 No new messages", flush=True)
            return 'ok', 200

        for msg_id in messages_added:
            subject, sender, body, labels = get_email_content(service, msg_id)

            if 'UNREAD' not in labels:
                continue

            print(f"\n📧 New email: {subject} | From: {sender}", flush=True)
            classification = classify_email(subject, sender, body)
            print(f"🤖 Groq says:\n{classification}", flush=True)
            parsed = parse_classification(classification)

            if parsed["important"]:
                send_notification(subject, sender, parsed["category"], parsed["reason"])
            else:
                service.users().messages().modify(
                    userId='me',
                    id=msg_id,
                    body={'removeLabelIds': ['UNREAD']}
                ).execute()
                print(f"✅ Silently marked read: [{parsed['category']}] {subject}", flush=True)

    except Exception as e:
        print(f"❌ Error: {e}", flush=True)
        print(traceback.format_exc(), flush=True)

    return 'ok', 200

@app.route('/')
def home():
    return "Email filter is running ✅"

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
