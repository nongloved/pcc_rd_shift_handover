import os
import re
import time
import warnings
from datetime import datetime

import requests
import urllib3
from pymongo import MongoClient

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HELPDESK_HOST = os.getenv('HELPDESK_HOST', '10.1.18.2')
HELPDESK_USER = os.getenv('HELPDESK_USER', '')
HELPDESK_PASS = os.getenv('HELPDESK_PASS', '')
TICKETS_BASE_URL = f"https://{HELPDESK_HOST}/helpdesk/WebObjects/Helpdesk.woa/ra/Tickets"

# Statuses that mean the ticket is no longer open. WebHelpDesk tracks "Closed" and
# "Resolved" as separate terminal statuses — a qualifier excluding only one of them
# still lets the other flood in from every department, not just this team's queue.
CLOSED_STATUSES = {"Closed", "Resolved"}
OPEN_QUALIFIER = " and ".join(
    f"statustype.statusTypeName != '{s}'" for s in CLOSED_STATUSES
)

POLL_INTERVAL_SEC = 15 * 60


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
    auth   = os.getenv('MONGO_AUTH_SOURCE', 'admin')
    if user and passwd:
        return f"mongodb://{quote_plus(user)}:{quote_plus(passwd)}@{host}:{port}/?authSource={auth}"
    return f"mongodb://{host}:{port}/"


MONGO_URI = _build_mongo_uri()
HELPDESK_MONGO_DB = os.getenv('HELPDESK_MONGO_DB', 'helpdesk_db')

client = MongoClient(MONGO_URI)
db = client[HELPDESK_MONGO_DB]
tickets_collection = db['tickets']


def fetch_group_tickets():
    """Page through all open tickets org-wide (not scoped to this account's group),
    already full-detail via style=details, until an empty page."""
    tickets = []
    page = 1
    while True:
        resp = requests.get(
            TICKETS_BASE_URL,
            params={
                "username": HELPDESK_USER, "password": HELPDESK_PASS,
                "style": "details", "page": page, "qualifier": OPEN_QUALIFIER
            },
            verify=False,
            timeout=15
        )
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        tickets.extend(batch)
        page += 1
    return tickets


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
    problemtype = raw.get('problemtype') or {}
    statustype = raw.get('statustype') or {}
    prioritytype = raw.get('prioritytype') or {}
    return {
        "ticket_id": raw.get('id'),
        "circuit_id": extract_circuit_id(detail),
        "subject": raw.get('subject'),
        "request_type": problemtype.get('detailDisplayName'),
        "status": statustype.get('statusTypeName'),
        "priority": prioritytype.get('priorityTypeName'),
        "location": location.get('locationName'),
        "room": raw.get('room'),
        "note": notes[0].get('mobileNoteText') if notes else None,
        "due_date": raw.get('displayDueDate'),
        "detail": detail,
        "report_date": raw.get('reportDateUtc'),
        "last_updated": raw.get('lastUpdated'),
        "done": False,
        "processed_at": datetime.now().isoformat()
    }


def is_not_resolved(raw):
    """Exclude tickets whose status is terminal (Closed/Resolved) — everything else is allowed through."""
    status = (raw.get('statustype') or {}).get('statusTypeName') or ''
    return status not in CLOSED_STATUSES


def poll_new_tickets():
    group_tickets = fetch_group_tickets()
    new_count = 0
    updated_count = 0
    removed_count = 0
    seen_ids = []

    for raw in group_tickets:
        ticket_id = raw.get('id')

        if not is_not_resolved(raw):
            continue

        seen_ids.append(ticket_id)
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

    # Prune tickets that dropped out of the live queue (resolved/closed/reassigned) —
    # nothing else ever removes them, so without this they accumulate here forever.
    # Guard against a fluke empty response wiping the whole collection.
    if seen_ids:
        result = tickets_collection.delete_many({"ticket_id": {"$nin": seen_ids}})
        removed_count = result.deleted_count
        if removed_count:
            print(f"  Removed {removed_count} ticket(s) no longer in the live queue")
    else:
        print("  Skipping prune: live queue returned no tickets")

    return new_count, updated_count, removed_count


def monitor_helpdesk():
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] Checking group ticket queue...")
    try:
        new_count, updated_count, removed_count = poll_new_tickets()
        if new_count or updated_count or removed_count:
            print(f"  Done checking — {new_count} new, {updated_count} updated, {removed_count} removed")
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
