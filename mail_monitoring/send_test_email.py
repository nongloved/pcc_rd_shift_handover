#!/usr/bin/env python3
"""
Send a test email to the monitored mailbox folder.
Usage: python3 send_test_email.py [path/to/file.pdf]
"""
import imaplib
import smtplib
import ssl
import os
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email.mime.text import MIMEText
from email import encoders

SMTP_HOST       = os.getenv("SMTP_HOST", "outgoing.workd.go.th")
IMAP_HOST       = os.getenv("IMAP_HOST", "incoming.workd.go.th")
IMAP_PORT       = int(os.getenv("IMAP_PORT", "993"))
EMAIL_USER      = os.getenv("EMAIL_USER", "network@rd.go.th")
EMAIL_PASS      = os.getenv("EMAIL_PASS", "P@ssw0rds2")
TARGET_FOLDER   = os.getenv("IMAP_INBOX_FOLDER", "INBOX-/00_UIH_cc_support")

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else None
if not PDF_PATH:
    print("Usage: python3 send_test_email.py <path/to/file.pdf>")
    sys.exit(1)
if not os.path.isfile(PDF_PATH):
    print(f"File not found: {PDF_PATH}")
    sys.exit(1)

filename = os.path.basename(PDF_PATH)
subject  = f"Maintenance/RD - Test {os.path.splitext(filename)[0]}"

# --- Build email ---
msg = MIMEMultipart()
msg["From"]    = EMAIL_USER
msg["To"]      = EMAIL_USER
msg["Subject"] = subject
msg.attach(MIMEText(f"Test email for UIH mail monitor.\nAttachment: {filename}", "plain"))

with open(PDF_PATH, "rb") as f:
    part = MIMEBase("application", "octet-stream")
    part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)

# --- Send via SMTP ---
print(f"Sending: {subject}")
with smtplib.SMTP(SMTP_HOST, 587, timeout=10) as s:
    s.ehlo()
    s.starttls()
    s.ehlo()
    s.login(EMAIL_USER, EMAIL_PASS)
    s.send_message(msg)
print("  Sent via SMTP")

# --- Wait briefly then move from INBOX to monitored folder ---
import time
print(f"  Waiting 5s for delivery...")
time.sleep(5)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ctx) as imap:
    imap.login(EMAIL_USER, EMAIL_PASS)

    uid = None
    source_folder = None
    for folder in ["INBOX", "PM-Processed"]:
        imap.select(folder)
        _, data = imap.uid("search", None, f'SUBJECT "{subject}"')
        uids = data[0].split()
        if uids:
            uid = uids[-1].decode()
            source_folder = folder
            break

    if not uid:
        print("  Email not found in INBOX or PM-Processed — move it manually to:", TARGET_FOLDER)
        sys.exit(0)

    print(f"  Found in {source_folder} (UID {uid})")
    result, _ = imap.uid("COPY", uid, TARGET_FOLDER)
    if result == "OK":
        imap.uid("STORE", uid, "+FLAGS", "\\Deleted")
        imap.expunge()
        print(f"  Moved to {TARGET_FOLDER}")
    else:
        print(f"  COPY failed — move UID {uid} manually to {TARGET_FOLDER}")

print("Done. Monitor should pick it up within 30s.")
