import os
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
    db     = os.getenv('MONGO_DB', 'noc_shift_handover')
    auth   = os.getenv('MONGO_AUTH_SOURCE', 'admin')
    if user and passwd:
        return f"mongodb://{quote_plus(user)}:{quote_plus(passwd)}@{host}:{port}/{db}?authSource={auth}"
    return f"mongodb://{host}:{port}/"


MONGO_URI = _build_mongo_uri()
client = MongoClient(MONGO_URI)
db = client[os.getenv('MONGO_DB', 'noc_shift_handover')]

collection = db['pm_mails']
# noc_tickets_collection: every tracked-status ticket (feeds "Ticket ค้าง ต้องดำเนินการต่อ").
# pm_tickets_today_collection: derived subset — Preventive Maintenance Plan tickets
# reported today (feeds "PM Tickets วันนี้") — materialized by helpdesk_monitoring
# each poll, so this API just reads it directly rather than filtering at request time.
# pm_tickets_history_collection: permanent archive of every Preventive Maintenance
# Plan ticket ever seen by the sync (not date-limited, never pruned).
noc_tickets_collection = db['noc_tickets']
pm_tickets_today_collection = db['ma_tickets_today']
pm_tickets_history_collection = db['ma_tickets']
employees_collection = db['helpdesk_users']

app = FastAPI()


def serialize(doc):
    doc['_id'] = str(doc['_id'])
    return doc


@app.get("/api/uih-mails")
def get_uih_mails():
    records = list(collection.find().sort("processed_at", -1))
    return [serialize(r) for r in records]


class UihMailUpdate(BaseModel):
    purpose: Optional[str] = None
    working_area: Optional[str] = None
    start_completion_time: Optional[str] = None
    downtime_per_circuit: Optional[str] = None
    status: Optional[str] = None
    done: Optional[bool] = None


@app.get("/api/uih-mails/daily")
def get_uih_mails_daily():
    today = datetime.now().date()
    records = list(collection.find().sort("processed_at", -1))
    daily = []
    for r in records:
        processed_at = r.get("processed_at")
        try:
            received_date = datetime.fromisoformat(processed_at).date() if processed_at else None
        except ValueError:
            received_date = None
        if received_date == today:
            daily.append(r)
    return [serialize(r) for r in daily]


@app.put("/api/uih-mails/{record_id}")
def update_uih_mail(record_id: str, body: UihMailUpdate):
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


@app.delete("/api/uih-mails/{record_id}")
def delete_uih_mail(record_id: str):
    result = collection.delete_one({"_id": ObjectId(record_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Record not found")
    return {"ok": True}


@app.get("/api/ma-tickets")
def get_ma_tickets():
    tickets = list(noc_tickets_collection.find().sort("ticket_id", -1))
    return [serialize(t) for t in tickets]


@app.get("/api/ma-pm-tickets")
def get_ma_pm_tickets():
    tickets = list(pm_tickets_today_collection.find().sort("ticket_id", -1))
    return [serialize(t) for t in tickets]


@app.get("/api/ma-tickets-history")
def get_ma_tickets_history():
    tickets = list(pm_tickets_history_collection.find().sort("ticket_id", -1))
    return [serialize(t) for t in tickets]


class MaTicketUpdate(BaseModel):
    done: Optional[bool] = None


@app.put("/api/ma-tickets/{ticket_id}")
def update_ma_ticket(ticket_id: str, body: MaTicketUpdate):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    result = noc_tickets_collection.update_one(
        {"_id": ObjectId(ticket_id)},
        {"$set": fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return {"ok": True}


class EmployeeVerifyRequest(BaseModel):
    username: str
    password: str
    email: str


@app.post("/api/verify-employee")
def verify_employee(body: EmployeeVerifyRequest):
    username = body.username.strip()
    password = body.password
    email = body.email.strip().lower()
    if not username or not password or not email:
        return {"verified": False}
    emp = employees_collection.find_one({"username": username})
    verified = bool(emp) \
        and emp.get("password", "") == password \
        and emp.get("email", "").strip().lower() == email
    if not verified:
        return {"verified": False}
    return {
        "verified": True,
        "first_name": emp.get("first_name", ""),
        "last_name": emp.get("last_name", ""),
    }
