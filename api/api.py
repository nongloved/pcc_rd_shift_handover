import os
import re
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, HTTPException
from pymongo import MongoClient
from bson import ObjectId
from pydantic import BaseModel


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
    db     = os.getenv('MONGO_DB', 'uih_py_db')
    auth   = os.getenv('MONGO_AUTH_SOURCE', 'admin')
    if user and passwd:
        return f"mongodb://{quote_plus(user)}:{quote_plus(passwd)}@{host}:{port}/{db}?authSource={auth}"
    return f"mongodb://{host}:{port}/"


MONGO_URI = _build_mongo_uri()
client = MongoClient(MONGO_URI)
db = client[os.getenv('MONGO_DB', 'uih_py_db')]
collection = db['schedules']

helpdesk_db = client[os.getenv('HELPDESK_MONGO_DB', 'helpdesk_db')]
tickets_collection = helpdesk_db['tickets']

app = FastAPI()


def serialize(doc):
    doc['_id'] = str(doc['_id'])
    return doc


@app.get("/api/records")
def get_records():
    records = list(collection.find().sort("processed_at", -1))
    return [serialize(r) for r in records]


class RecordUpdate(BaseModel):
    purpose: Optional[str] = None
    working_area: Optional[str] = None
    start_completion_time: Optional[str] = None
    downtime_per_circuit: Optional[str] = None
    status: Optional[str] = None
    done: Optional[bool] = None


THT_RE = re.compile(r"(\d{2}):(\d{2})\s*THT\s*(\d{2})/(\d{2})/(\d{4})")


def parse_maintenance_window(start_completion_time):
    """Parse 'HH:MM THT dd/mm/yyyy - HH:MM THT dd/mm/yyyy' into (start, end) datetimes."""
    if not start_completion_time:
        return None
    matches = THT_RE.findall(start_completion_time)
    if len(matches) != 2:
        return None
    try:
        (h1, mi1, d1, mo1, y1), (h2, mi2, d2, mo2, y2) = matches
        start = datetime(int(y1), int(mo1), int(d1), int(h1), int(mi1))
        end = datetime(int(y2), int(mo2), int(d2), int(h2), int(mi2))
        return start, end
    except ValueError:
        return None


@app.get("/api/daily")
def get_daily_records():
    today = datetime.now().date()
    records = list(collection.find().sort("processed_at", -1))
    daily = []
    for r in records:
        window = parse_maintenance_window(r.get("start_completion_time"))
        if window and window[0].date() <= today <= window[1].date():
            daily.append(r)
    return [serialize(r) for r in daily]


@app.delete("/api/records/all")
def delete_all_records():
    result = collection.delete_many({})
    return {"deleted": result.deleted_count}


@app.put("/api/records/{record_id}")
def update_record(record_id: str, body: RecordUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = collection.update_one(
        {"_id": ObjectId(record_id)},
        {"$set": fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}


@app.delete("/api/records/{record_id}")
def delete_record(record_id: str):
    result = collection.delete_one({"_id": ObjectId(record_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}


@app.get("/api/tickets")
def get_tickets():
    tickets = list(tickets_collection.find().sort("ticket_id", -1))
    return [serialize(t) for t in tickets]


class TicketUpdate(BaseModel):
    done: Optional[bool] = None


@app.put("/api/tickets/{ticket_id}")
def update_ticket(ticket_id: str, body: TicketUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = tickets_collection.update_one(
        {"_id": ObjectId(ticket_id)},
        {"$set": fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ok": True}
