import os
import io
import time
import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
import re
import json
import pdfplumber
from pymongo import MongoClient
from datetime import datetime, timedelta

IMAP_HOST = os.getenv('IMAP_HOST', 'incoming.workd.go.th')
IMAP_PORT = int(os.getenv('IMAP_PORT', '993'))
EMAIL_USER = os.getenv('EMAIL_USER', '')
EMAIL_PASS = os.getenv('EMAIL_PASS', '')
PROCESSED_FOLDER = os.getenv('PROCESSED_FOLDER', './pdf_processed')
IMAP_PROCESSED_FOLDER = os.getenv('IMAP_PROCESSED_FOLDER', 'PM-Processed')
IMAP_INBOX_FOLDERS = [f.strip() for f in os.getenv('IMAP_INBOX_FOLDER', 'INBOX-/00_UIH_cc_support').split(',')]


def _build_mongo_uri():
    from urllib.parse import quote_plus

    if os.getenv('USE_REMOTE_MONGO', 'false').lower() == 'true':
        user        = os.getenv('REMOTE_MONGO_USER', '')
        passwd      = os.getenv('REMOTE_MONGO_PASS', '')
        hosts       = os.getenv('REMOTE_MONGO_HOSTS', '')
        replica_set = os.getenv('REMOTE_MONGO_REPLICA_SET', '')
        auth        = os.getenv('REMOTE_MONGO_AUTH_SOURCE', 'admin')
        params = f"authSource={auth}"
        if replica_set:
            params += f"&replicaSet={replica_set}"
        return f"mongodb://{quote_plus(user)}:{quote_plus(passwd)}@{hosts}/?{params}"

    user   = os.getenv('MONGO_USER', '')
    passwd = os.getenv('MONGO_PASS', '')
    host   = os.getenv('MONGO_HOST', 'localhost')
    port   = os.getenv('MONGO_PORT', '27018')
    db     = os.getenv('MONGO_DB', 'noc_shift_handover')
    auth   = os.getenv('MONGO_AUTH_SOURCE', 'admin')
    if user and passwd:
        return f"mongodb://{quote_plus(user)}:{quote_plus(passwd)}@{host}:{port}/{db}?authSource={auth}"
    return f"mongodb://{host}:{port}/"


MONGO_URI = _build_mongo_uri()

client = MongoClient(MONGO_URI)
db = client[os.getenv('MONGO_DB', 'noc_shift_handover')]
collection = db['pm_mails']


def decode_subject(raw_subject):
    parts = decode_header(raw_subject or '')
    result = ''
    for part, charset in parts:
        if isinstance(part, bytes):
            try:
                result += part.decode(charset or 'utf-8', errors='replace')
            except (LookupError, TypeError):
                result += part.decode('utf-8', errors='replace')
        else:
            result += part
    return result


def imap_quote(folder_name):
    """IMAP mailbox names containing spaces (or other specials) must be quoted —
    imaplib does not do this automatically."""
    escaped = folder_name.replace('\\', '\\\\').replace('"', '\\"')
    return f'"{escaped}"'


def ensure_imap_folder(imap, folder_name):
    status, _ = imap.select(imap_quote(folder_name))
    if status != 'OK':
        imap.create(imap_quote(folder_name))
        print(f"  Created IMAP folder: {folder_name}")


def extract_field(text, *patterns):
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return "N/A"


def extract_pdf_to_json(pdf_bytes, filename, mail_received_at=None):
    import pytesseract
    from pdf2image import convert_from_bytes

    pages_text = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            pages_text.append(text if text else "")

    full_text = "\n".join(pages_text)

    purpose = extract_field(
        full_text,
        r"Purpose\s*:\s*(.+)",
        r"วัตถุประสงค์\s*:\s*(.+)"
    )

    working_area = extract_field(
        full_text,
        r"Working Area\s*:\s*(.+)",
        r"พื้นที่ดำเนินการ\s*:\s*(.+)",
        r"AIS site\s*\n?\s*(AIS site)"
    )
    if working_area in ("N/A", ""):
        wa_match = re.search(r"Working Area\s*\n\s*(.+)", full_text)
        if wa_match:
            working_area = wa_match.group(1).strip()

    start_completion = extract_field(
        full_text,
        r"Start\s*[-–]\s*completion time/date\s*:\s*(.+)",
        r"Start\s*[-–]\s*completion\s*time/date\s*\n\s*:\s*(.+)",
        r"00:00 THT\s+(\d{2}/\d{2}/\d{4}\s*-\s*\d{2}:\d{2} THT \d{2}/\d{2}/\d{4})"
    )
    if start_completion == "N/A":
        tht_match = re.search(
            r"(\d{2}:\d{2}\s+THT\s+\d{2}/\d{2}/\d{4}\s*[-–]\s*\d{2}:\d{2}\s+THT\s+\d{2}/\d{2}/\d{4})",
            full_text
        )
        if tht_match:
            start_completion = tht_match.group(1).strip()

    downtime = extract_field(
        full_text,
        r"Downtime per circuit\s*:\s*(.+)",
        r"ระยะเวลากระทบวงจร\s*:\s*(.+)",
        r"(\d{2}:\d{2}\s*hrs\.)"
    )

    images = convert_from_bytes(pdf_bytes, dpi=200)

    circuits = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            if page_idx >= len(images):
                continue
            img = images[page_idx]
            img_w, img_h = img.size
            sx = img_w / page.width
            sy = img_h / page.height

            for table in page.find_tables():
                rows_text = table.extract()
                for row_idx, (row_data, row_obj) in enumerate(zip(rows_text, table.rows)):
                    if not row_data or len(row_data) < 4:
                        continue
                    row_no = (row_data[0] or '').strip()
                    sid    = (row_data[1] or '').strip()
                    cid    = (row_data[2] or '').strip()
                    if not (row_no.isdigit() and re.match(r'^\d{5,}$', sid) and re.match(r'^\d{5,}$', cid)):
                        continue

                    name = ''
                    if len(row_obj.cells) > 3 and row_obj.cells[3]:
                        x0, top, x1, bottom = row_obj.cells[3]
                        crop = img.crop((
                            max(0, int(x0 * sx) - 4),
                            max(0, int(top * sy) - 4),
                            min(img_w, int(x1 * sx) + 4),
                            min(img_h, int(bottom * sy) + 4)
                        ))
                        name = pytesseract.image_to_string(
                            crop, lang='tha+eng', config='--psm 7'
                        ).strip()
                    if not name:
                        name = (row_data[3] or '').strip()

                    circuits.append({"sid": sid, "cid": cid, "circuit_name": name})

    return {
        "filename": filename,
        "purpose": purpose,
        "working_area": working_area,
        "start_completion_time": start_completion,
        "downtime_per_circuit": downtime,
        "affected_circuits": circuits,
        "status": "Pending Calendar Sync",
        "done": False,
        "mail_received_at": mail_received_at,
        "processed_at": datetime.now().isoformat()
    }


def process_folder(imap, folder):

    status, _ = imap.select(imap_quote(folder))
    if status != 'OK':
        print(f"  [WARN] Cannot select folder: {folder}")
        return

    # Search last 7 days to catch emails that arrived out of UID order
    since_date = (datetime.now() - timedelta(days=7)).strftime("%d-%b-%Y")
    status, data = imap.uid('search', None, f'SINCE {since_date} SUBJECT "Maintenance/RD"')
    if status != 'OK' or not data[0].strip():
        return

    all_uids = [int(uid) for uid in data[0].split()]
    if not all_uids:
        return

    print(f"  [{folder}] Found {len(all_uids)} email(s) in last 7 days")

    for uid in all_uids:
        _, msg_data = imap.uid('fetch', str(uid), '(INTERNALDATE RFC822)')
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_subject(msg.get('Subject', ''))
        print(f"\n  Email: {subject}")

        if 'Maintenance/RD' not in subject:
            print("  skip: subject does not contain 'Maintenance/RD'")
            continue

        # INTERNALDATE is stamped by the mail server itself when the message arrived —
        # unlike the Date: header, it can't be set/skewed by the sending client.
        mail_received_at = None
        internaldate_match = re.search(rb'INTERNALDATE "([^"]+)"', msg_data[0][0])
        if internaldate_match:
            try:
                mail_received_at = parsedate_to_datetime(internaldate_match.group(1).decode()).isoformat()
            except (TypeError, ValueError):
                mail_received_at = None

        for part in msg.walk():
            attachment_name = part.get_filename()
            is_pdf = (
                part.get_content_type() == 'application/pdf' or
                (attachment_name and attachment_name.lower().endswith('.pdf'))
            )
            if not is_pdf:
                continue

            attachment_name = attachment_name or f"attachment_{uid}.pdf"
            pdf_bytes = part.get_payload(decode=True)
            local_path = os.path.join(PROCESSED_FOLDER, attachment_name)

            # Dedup via the PDF already existing on disk (in the pdf_processed volume) —
            # deliberately NOT MongoDB-based, so deleting DB records/collections (e.g. via
            # Compass) can never cause an email to be treated as new and reprocessed.
            if os.path.exists(local_path):
                print(f"  skip: already processed: {attachment_name}")
                continue

            try:
                json_data = extract_pdf_to_json(pdf_bytes, attachment_name, mail_received_at)

                print(f"  Extract OK!")
                print(f"     Purpose       : {json_data['purpose']}")
                print(f"     Working Area  : {json_data['working_area']}")
                print(f"     Time          : {json_data['start_completion_time']}")
                print(f"     Downtime      : {json_data['downtime_per_circuit']}")
                print(f"     Circuits      : {len(json_data['affected_circuits'])} circuits")

                print(json.dumps(json_data, ensure_ascii=False, indent=2))

                collection.insert_one(json_data)
                print(f"  Saved to MongoDB")

                with open(local_path, 'wb') as f:
                    f.write(pdf_bytes)
                print(f"  Saved PDF: {local_path}")

            except Exception as e:
                import traceback
                print(f"  Error on file {attachment_name}: {e}")
                traceback.print_exc()

        if folder != IMAP_PROCESSED_FOLDER:
            imap.uid('copy', str(uid), imap_quote(IMAP_PROCESSED_FOLDER))


def monitor_email():
    count = collection.count_documents({})
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking new emails... (DB: {count} records)")

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        imap.login(EMAIL_USER, EMAIL_PASS)
        ensure_imap_folder(imap, IMAP_PROCESSED_FOLDER)

        for folder in IMAP_INBOX_FOLDERS:
            process_folder(imap, folder)

        print("  Done checking")

        imap.logout()

    except Exception as e:
        import traceback
        print(f"  IMAP Error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    print("=" * 50)
    print("Starting UIH Mail Monitor (Python)")
    print("=" * 50)
    os.makedirs(PROCESSED_FOLDER, exist_ok=True)

    print(f"Monitoring folders: {', '.join(IMAP_INBOX_FOLDERS)}")
    print("Dedup via pdf_processed/ file existence (independent of MongoDB) — scanning last 7 days each poll")
    print("=" * 50)

    while True:
        monitor_email()
        time.sleep(30)
