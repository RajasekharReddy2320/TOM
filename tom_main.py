import os
import base64
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from google import genai
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from slack_sdk import WebClient
from dotenv import load_dotenv

load_dotenv()

# --- CONFIG ---
SCOPES = ['https://www.googleapis.com/auth/gmail.modify']
slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Global tracking to prevent duplicates and old mail
hourly_mail_log = []
seen_messages = set()
# Capture script start time in milliseconds
SCRIPT_START_TIME = int(time.time() * 1000)

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
        creds = flow.run_local_server(port=0, open_browser=False)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)

# --- THE BRAIN ---
def analyze_realtime(content):
    prompt = f"""
    You are TOM, an AI assistant for Raja (BIT Mesra ECE student).
    Analyze this email: "{content}"

    CATEGORIES:
    - IMPORTANT: BIT Mesra, professors, teammates (Pranay, Yogi, Rahul, Sushma), projects (TraviLink, BITSAS, Lumicrop, BIT Maps).
    - UNIMPORTANT: Generic school news, receipts, automated alerts.
    - SPAM: Ads, sponsorships, marketing.

    REQUIRED OUTPUT:
    CATEGORY: [Name]
    SUMMARY: [20 words max]
    DRAFT: [Reply if IMPORTANT, else blank]
    """
    resp = gemini_client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
    return resp.text

def generate_hourly_summary(log):
    if not log:
        return "Nothing important from the past one hour."
    combined = "\n".join(log)
    prompt = f"Summarize Raja's emails from the last hour: \n{combined}"
    resp = gemini_client.models.generate_content(model="gemini-3-flash-preview", contents=prompt)
    return resp.text

def get_full_text(msg):
    try:
        if 'parts' in msg['payload']:
            for part in msg['payload']['parts']:
                if part['mimeType'] == 'text/plain':
                    return base64.urlsafe_b64decode(part['body']['data']).decode()
        return msg.get('snippet', '')
    except:
        return msg.get('snippet', '')

def create_draft(service, original_msg, body):
    try:
        headers = original_msg.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'] == 'Subject'), "No Subject")
        to_email = next((h['value'] for h in headers if h['name'] == 'From'), "")
        message = EmailMessage()
        message.set_content(body)
        message['To'] = to_email
        message['Subject'] = f"Re: {subject}"
        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
        service.users().drafts().create(userId="me", body={'message': {'raw': encoded, 'threadId': original_msg['threadId']}}).execute()
    except Exception as e:
        print(f"Draft Error: {e}")

# --- THE SCANNING LOGIC ---
def run_tom():
    global hourly_mail_log, seen_messages
    service = get_gmail_service()
    last_hour_check = datetime.now()
    
    print(f"✅ TOM is online. Strictly ignoring all emails before {datetime.fromtimestamp(SCRIPT_START_TIME/1000).strftime('%H:%M:%S')}")

    while True:
        now = datetime.now()
        
        try:
            # Fetch unread messages
            results = service.users().messages().list(userId='me', q='is:unread').execute()
            messages = results.get('messages', [])

            if messages:
                for m_info in messages:
                    m_id = m_info['id']
                    
                    # 1. Skip if already seen in this session
                    if m_id in seen_messages:
                        continue
                    
                    # Get full message metadata
                    msg = service.users().messages().get(userId='me', id=m_id).execute()
                    msg_time = int(msg['internalDate'])

                    # 2. STRICT CHECK: Ignore if the email arrived before the script was powered on
                    if msg_time <= SCRIPT_START_TIME:
                        # Mark as read so it doesn't appear in future 'is:unread' queries
                        service.users().messages().modify(userId='me', id=m_id, body={'removeLabelIds': ['UNREAD']}).execute()
                        seen_messages.add(m_id)
                        continue

                    # If it passed the checks, process it
                    content = get_full_text(msg)
                    analysis = analyze_realtime(content)

                    if "CATEGORY: IMPORTANT" in analysis:
                        summary = analysis.split("SUMMARY:")[1].split("DRAFT:")[0].strip()
                        draft_body = analysis.split("DRAFT:")[1].strip()
                        create_draft(service, msg, draft_body)
                        slack_client.chat_postMessage(
                            channel=os.getenv("SLACK_CHANNEL_ID"), 
                            text=f"🚨 *IMPORTANT MAIL*\n*Summary:* {summary}\n\n*Draft created in Gmail.*"
                        )
                        hourly_mail_log.append(f"IMPORTANT: {summary}")
                    
                    elif "CATEGORY: UNIMPORTANT" in analysis:
                        hourly_mail_log.append(f"Unimportant: {msg['snippet']}")

                    # Cleanup
                    service.users().messages().modify(userId='me', id=m_id, body={'removeLabelIds': ['UNREAD']}).execute()
                    seen_messages.add(m_id)

        except Exception as e:
            print(f"Error in Loop: {e}")

        # 3. HOURLY SUMMARY
        if now - last_hour_check >= timedelta(hours=1):
            summary_text = generate_hourly_summary(hourly_mail_log)
            slack_client.chat_postMessage(
                channel=os.getenv("SLACK_CHANNEL_ID"), 
                text=f"📊 *Hourly Briefing*\n{summary_text}"
            )
            hourly_mail_log = [] 
            last_hour_check = now
            # Optional: Clear seen_messages periodically to save memory
            if len(seen_messages) > 1000:
                seen_messages.clear()

        time.sleep(60) # Wait 1 minute

if __name__ == "__main__":
    run_tom()