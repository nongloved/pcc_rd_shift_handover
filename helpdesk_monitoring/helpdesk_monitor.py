import os
import re
import time
import warnings
from datetime import datetime, timezone, timedelta

import requests
import urllib3
from pymongo import MongoClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HELPDESK_HOST = os.getenv('HELPDESK_HOST', '10.1.18.2')
HELPDESK_USER = os.getenv('HELPDESK_USER', '')
HELPDESK_PASS = os.getenv('HELPDESK_PASS', '')
TICKETS_BASE_URL = f"https://{HELPDESK_HOST}/helpdesk/WebObjects/Helpdesk.woa/ra/Tickets/"
TICKET_URL = TICKETS_BASE_URL + "{ticket_id}/"

POLL_INTERVAL_SEC = 30 * 60


def _build_mongo_uri():
    from urllib.parse import quote_plus
    user   = os.getenv('MONGO_USER', '')
    passwd = os.getenv('MONGO_PASS', '')
    host   = os.getenv('MONGO_HOST', 'localhost')
    port   = os.getenv('MONGO_PORT', '27018')
    auth   = os.getenv('MONGO_AUTH_SOURCE', 'admin')
    if user and passwd:
        return f"mongodb://{quote_plus(user)}:{quote_plus(passwd)}@{host}:{port}/?authSource={auth}"
    return f"mongodb://{host}:{port}/"


MONGO_URI = _build_mongo_uri()
HELPDESK_MONGO_DB = os.getenv('HELPDESK_MONGO_DB', 'helpdesk_db')

client = MongoClient(MONGO_URI)
db = client[HELPDESK_MONGO_DB]
tickets_collection = db['tickets']


TH_TZ = timezone(timedelta(hours=7))


def parse_utc(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_today_th(*timestamps):
    """True if any of the given UTC timestamp strings falls on today's date in Thailand time."""
    today_th = datetime.now(TH_TZ).date()
    for ts in timestamps:
        dt = parse_utc(ts)
        if dt and dt.astimezone(TH_TZ).date() == today_th:
            return True
    return False


def fetch_group_tickets():
    resp = requests.get(
        TICKETS_BASE_URL,
        params={"username": HELPDESK_USER, "password": HELPDESK_PASS, "list": "group"},
        verify=False,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def fetch_ticket(ticket_id):
    url = TICKET_URL.format(ticket_id=ticket_id)
    resp = requests.get(
        url,
        params={"username": HELPDESK_USER, "password": HELPDESK_PASS, "style": "details"},
        verify=False,
        timeout=15
    )
    if resp.status_code in (404, 400):
        # 400 covers "deleted or no permission" — treated the same as not-found
        return None
    resp.raise_for_status()
    return resp.json()


CIRCUIT_ID_RE = re.compile(r"Circuit ID(?:\s+(Main|Backup))?\s*:\s*(\S+)", re.IGNORECASE)


def extract_circuit_id(detail):
    if not detail:
        return None
    matches = CIRCUIT_ID_RE.findall(detail)
    if not matches:
        return None
    for label, value in matches:
        if label.lower() == "main":
            return value
    return matches[0][1]


def extract_ticket_fields(raw):
    notes = raw.get('notes') or []
    location = raw.get('location') or {}
    detail = raw.get('detail')
    return {
        "ticket_id": raw.get('id'),
        "circuit_id": extract_circuit_id(detail),
        "subject": raw.get('subject'),
        "location": location.get('locationName'),
        "room": raw.get('room'),
        "note": notes[0].get('mobileNoteText') if notes else None,
        "due_date": raw.get('displayDueDate'),
        "detail": detail,
        "done": False,
        "processed_at": datetime.now().isoformat()
    }


def poll_new_tickets():
    group_tickets = fetch_group_tickets()
    new_count = 0
    updated_count = 0
    skipped_count = 0

    # Pre-filter using the summary's lastUpdated to avoid a detail fetch for old,
    # untouched tickets — opening a ticket also bumps lastUpdated, so this alone
    # covers "opened today OR updated today" in practice.
    todays_summaries = [s for s in group_tickets if is_today_th(s.get('lastUpdated'))]
    skipped_count = len(group_tickets) - len(todays_summaries)

    for summary in todays_summaries:
        ticket_id = summary.get('id')

        raw = fetch_ticket(ticket_id)
        if raw is None:
            continue

        # Authoritative check against the full record's opened/updated dates.
        if not is_today_th(raw.get('reportDateUtc'), raw.get('lastUpdated')):
            continue

        data = extract_ticket_fields(raw)
        existing = tickets_collection.find_one({"ticket_id": ticket_id})

        if existing:
            data.pop('done', None)  # preserve the locally-set Done flag
            if any(existing.get(k) != v for k, v in data.items() if k != 'processed_at'):
                tickets_collection.update_one({"_id": existing["_id"]}, {"$set": data})
                updated_count += 1
                print(f"  Updated ticket {ticket_id}: {data['subject']}")
        else:
            tickets_collection.insert_one(data)
            new_count += 1
            print(f"  Saved ticket {ticket_id}: {data['subject']}")

    print(f"  ({skipped_count} not from today, skipped)")
    return new_count, updated_count


def monitor_helpdesk():
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] Checking group ticket queue...")
    try:
        new_count, updated_count = poll_new_tickets()
        if new_count or updated_count:
            print(f"  Done checking — {new_count} new, {updated_count} updated")
        else:
            print("  No changes")
    except Exception as e:
        import traceback
        print(f"  Helpdesk API error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    print("=" * 50)
    print("Starting Helpdesk Ticket Monitor")
    print("=" * 50)
    print(f"Helpdesk host: {HELPDESK_HOST}")
    print(f"Mongo DB: {HELPDESK_MONGO_DB}")
    print("=" * 50)

    while True:
        monitor_helpdesk()
        time.sleep(POLL_INTERVAL_SEC)
