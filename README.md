# PCC RD Shift Handover

Watches the RD mailbox for maintenance notices and polls the Helpdesk ticket system, serving both
through the NOC shift handover dashboard.

API paths are namespaced by source: `uih-*` for mail-derived maintenance records (Mongo db
`uih_pm_mails`), `ma-*` for Helpdesk tickets (Mongo db `ma_tickets`).

- **mail_monitoring/** — watches the RD mailbox for "Maintenance/RD" emails and OCR-extracts
  schedule PDFs (Thai+English) into MongoDB (`uih_pm_mails.pm_mails`).
- **helpdesk_monitoring/** — polls the WebHelpDesk ticket API and syncs open tickets into
  MongoDB (`ma_tickets.tickets`). No exposed port, writes only.
- **api/** — FastAPI service exposing:
  - `GET /api/uih-mails`, `GET /api/uih-mails/daily` (today's maintenance, filtered by whether
    the actual maintenance window overlaps today — not by when the email arrived), and
    `PUT`/`DELETE /api/uih-mails/{id}`.
  - `GET /api/ma-tickets` (all Helpdesk tickets), `GET /api/ma-pm-tickets` (Preventive
    Maintenance Plan tickets reported today), and `PUT /api/ma-tickets/{id}`.
  - `POST /api/verify-employee`.

  Internal only, no host port.
- **shift_handover/** — the main page. Express app serving the NOC shift handover dashboard at
  `/` and the full searchable/exportable records page at `/records`, proxying `/api/uih-*` and
  `/api/ma-*` to `api/` directly under the same path names.

## Run

```
cp .env.example .env   # fill in real credentials
docker compose up -d --build
```

| Service             | Port            | Purpose                                          |
|---------------------|-----------------|---------------------------------------------------|
| mongodb             | 27018           | `uih_pm_mails` + `ma_tickets` storage              |
| mail_monitoring     | (none)          | Background poller, writes to `uih_pm_mails` only   |
| helpdesk_monitoring | (none)          | Background poller, writes to `ma_tickets` only     |
| api                 | 8000 (internal) | FastAPI `uih-mails` / `ma-tickets` API             |
| shift_handover      | 4102            | Main dashboard (`/`) + all records (`/records`)    |
