import os
import re
import time
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
import urllib3
from pymongo import MongoClient

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HELPDESK_HOST = os.getenv('HELPDESK_HOST', '10.1.18.2')
HELPDESK_USER = os.getenv('HELPDESK_USER', '')
HELPDESK_PASS = os.getenv('HELPDESK_PASS', '')
TICKETS_BASE_URL = f"https://{HELPDESK_HOST}/helpdesk/WebObjects/Helpdesk.woa/ra/Tickets"

# Only tickets in one of these statuses are synced — a whitelist instead of a
# blacklist, so any status we haven't explicitly vetted (Rejected, Pending Change,
# or anything WebHelpDesk adds later) is excluded by default rather than leaking
# through unnoticed. "Closed" is deliberately excluded despite being a valid status:
# it's a large, effectively-static historical archive (hundreds+ tickets going back
# years) that would make each poll take 1-2+ minutes for no operational benefit,
# since closed tickets never change — that's especially costly now that polls run
# every minute.
TRACKED_STATUSES = {"Open", "Assigned", "Pending Customer", "Pending Vendor", "Resolved"}

# Individual tickets manually confirmed stale/invalid despite an active-looking
# status — excluded one-off rather than blacklisting the whole status, since most
# tickets in that status are legitimate.
# 110631, 111598: "Pending Vendor" for 5 months with zero movement.
# 131377: "[TEST-LINEOA]" test ticket, note field has test@example.com — not a
#   real incident.
MANUALLY_EXCLUDED_TICKET_IDS = {110631, 111598, 131377}

POLL_INTERVAL_SEC = 60


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
MONGO_DB = os.getenv('MONGO_DB', 'noc_shift_handover')

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
# noc_tickets: every tracked-status ticket (feeds "Ticket ค้าง ต้องดำเนินการต่อ").
# ma_tickets_today: derived subset — Preventive Maintenance Plan tickets reported
# today (feeds "PM Tickets วันนี้") — materialized here each poll instead of filtered
# at request time, so the API layer can just read it directly. Mutually exclusive
# with noc_tickets.
# ma_tickets: permanent archive of every Preventive Maintenance Plan ticket ever seen
# by this sync (not date-limited) — accumulated only, never pruned. Can only cover
# tracked statuses (Closed is never fetched — see TRACKED_STATUSES above), so this is
# a running archive from when this collection was introduced onward, not a backfill
# of every PM-Plan ticket in WebHelpDesk's history.
tickets_collection = db['noc_tickets']
pm_tickets_today_collection = db['ma_tickets_today']
pm_tickets_history_collection = db['ma_tickets']


def _is_reported_today(report_date):
    if not report_date:
        return False
    try:
        dt = datetime.fromisoformat(str(report_date).replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(BANGKOK_TZ).date() == datetime.now(BANGKOK_TZ).date()


def _fetch_tickets_by_status(status):
    """Page through all tickets in a single status until an empty page. A single
    equality qualifier paginates reliably; a combined multi-status OR qualifier was
    observed to make WebHelpDesk's pagination stall/loop without converging, so each
    status is fetched as its own request instead of one big compound qualifier."""
    tickets = []
    page = 1
    qualifier = f"statustype.statusTypeName = '{status}'"
    while True:
        resp = requests.get(
            TICKETS_BASE_URL,
            params={
                "username": HELPDESK_USER, "password": HELPDESK_PASS,
                "style": "details", "page": page, "qualifier": qualifier
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


def fetch_group_tickets():
    """Fetch all tickets across every tracked status, org-wide (not scoped to this
    account's group), already full-detail via style=details."""
    tickets = []
    for status in TRACKED_STATUSES:
        batch = _fetch_tickets_by_status(status)
        print(f"  Fetched {len(batch)} ticket(s) with status '{status}'")
        tickets.extend(batch)
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


def is_tracked_status(raw):
    """Keep only tickets in a tracked status, minus any manually flagged as stale."""
    if raw.get('id') in MANUALLY_EXCLUDED_TICKET_IDS:
        return False
    status = (raw.get('statustype') or {}).get('statusTypeName') or ''
    return status in TRACKED_STATUSES


def _sync_ticket_collection(collection, label, tickets, prune=True):
    """Upsert `tickets` (already-extracted field dicts) into `collection` by
    ticket_id, preserving each document's locally-set Done flag. When `prune` is
    True (the default), also delete anything no longer present — used for
    noc_tickets/ma_tickets_today, which mirror *current* state. When False, the
    collection only ever grows — used for ma_tickets, a permanent archive."""
    new_count = 0
    updated_count = 0
    removed_count = 0
    seen_ids = []

    for data in tickets:
        ticket_id = data["ticket_id"]
        seen_ids.append(ticket_id)
        existing = collection.find_one({"ticket_id": ticket_id})

        if existing:
            fields = {k: v for k, v in data.items() if k != 'done'}  # preserve the locally-set Done flag
            if any(existing.get(k) != v for k, v in fields.items() if k != 'processed_at'):
                collection.update_one({"_id": existing["_id"]}, {"$set": fields})
                updated_count += 1
                print(f"  [{label}] Updated ticket {ticket_id}: {data['subject']}")
        else:
            collection.insert_one(data)
            new_count += 1
            print(f"  [{label}] Saved ticket {ticket_id}: {data['subject']}")

    if not prune:
        return new_count, updated_count, removed_count

    # Prune tickets that dropped out of this tracked set — nothing else ever removes
    # them, so without this they accumulate here forever. Guard against a fluke empty
    # response wiping the whole collection.
    if seen_ids:
        result = collection.delete_many({"ticket_id": {"$nin": seen_ids}})
        removed_count = result.deleted_count
        if removed_count:
            print(f"  [{label}] Removed {removed_count} ticket(s) no longer in the tracked set")
    else:
        print(f"  [{label}] Skipping prune: tracked set returned no tickets")

    return new_count, updated_count, removed_count


def _is_pm_today(ticket):
    return ticket.get("request_type") == "Preventive Maintenance Plan" and _is_reported_today(ticket.get("report_date"))


def poll_new_tickets():
    group_tickets = fetch_group_tickets()
    tracked = [extract_ticket_fields(raw) for raw in group_tickets if is_tracked_status(raw)]

    # Mutually exclusive: a PM-Plan-reported-today ticket lives in ma_tickets_today
    # only, not also in noc_tickets — otherwise it'd show up on both "Ticket ค้าง" and
    # "PM Tickets วันนี้" at once.
    pm_today = [t for t in tracked if _is_pm_today(t)]
    noc_only = [t for t in tracked if not _is_pm_today(t)]
    pm_all = [t for t in tracked if t.get("request_type") == "Preventive Maintenance Plan"]

    noc_result = _sync_ticket_collection(tickets_collection, "noc_tickets", noc_only)
    pm_result = _sync_ticket_collection(pm_tickets_today_collection, "ma_tickets_today", pm_today)
    history_result = _sync_ticket_collection(
        pm_tickets_history_collection, "ma_tickets", pm_all, prune=False
    )

    return noc_result, pm_result, history_result


def monitor_helpdesk():
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{ts}] Checking group ticket queue...")
    try:
        (noc_new, noc_updated, noc_removed), (pm_new, pm_updated, pm_removed), (hist_new, hist_updated, _) = poll_new_tickets()
        if noc_new or noc_updated or noc_removed or pm_new or pm_updated or pm_removed or hist_new or hist_updated:
            print(f"  Done checking — noc_tickets: {noc_new} new, {noc_updated} updated, {noc_removed} removed "
                  f"| ma_tickets_today: {pm_new} new, {pm_updated} updated, {pm_removed} removed "
                  f"| ma_tickets: {hist_new} new, {hist_updated} updated")
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
    print(f"Mongo DB: {MONGO_DB}")
    print("=" * 50)

    while True:
        monitor_helpdesk()
        time.sleep(POLL_INTERVAL_SEC)
