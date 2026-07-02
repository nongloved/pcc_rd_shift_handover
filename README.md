# PCC RD Shift Handover

Mail-monitoring-only stack: watches the RD mailbox for maintenance notices and serves them
through the NOC shift handover dashboard.

- **mail_monitoring/** — watches the RD mailbox for "Maintenance/RD" emails and OCR-extracts
  schedule PDFs (Thai+English) into MongoDB (`schedules` collection).
- **api/** — FastAPI service exposing `/api/records`, `/api/daily` (today's maintenance, filtered
  by whether the actual maintenance window overlaps today — not by when the email arrived), and
  CRUD (`PUT`/`DELETE`) on records. Internal only, no host port.
- **shift_handover/** — the main page. Express app serving the NOC shift handover dashboard at
  `/` and the full searchable/exportable records page at `/records`, proxying `/api/uih-*` to
  `api/` directly.

No helpdesk ticket monitoring in this stack — the shift-handover pages never called `/api/tickets`
(the ticket board is separate local/mock data), so it was left out.

## Run

```
cp .env.example .env   # fill in real credentials
docker compose up -d --build
```

| Service          | Port            | Purpose                                      |
|-------------------|-----------------|-----------------------------------------------|
| mongodb           | 27018           | Schedules storage                              |
| api               | 8000 (internal) | FastAPI records/daily API                      |
| shift_handover    | 3002            | Main dashboard (`/`) + all records (`/records`) |

`mail_monitoring` runs in the background with no exposed port — it only writes to MongoDB.
