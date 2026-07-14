# PCC RD Shift Handover

Watches the RD mailbox for maintenance notices and polls the Helpdesk ticket system, serving both
through the NOC shift handover dashboard.

All data lives in a single MongoDB database, `noc_shift_handover` (on the shared remote
replica set — `mongodb-primary`/`mongodb-secondary`, replica set `rs0`), with 4 collections:

| Collection        | Written by          | Read by (API)                          | Content                                                    |
|--------------------|---------------------|-----------------------------------------|--------------------------------------------------------------|
| `pm_mails`         | mail_monitoring     | `GET /api/uih-mails*`                    | Mail-derived maintenance records ("ใบแจ้งงาน RD วันนี้")   |
| `noc_tickets`       | helpdesk_monitoring | `GET/PUT /api/ma-tickets*`               | Every tracked-status Helpdesk ticket ("Ticket ค้าง ต้องดำเนินการต่อ") |
| `ma_tickets`        | helpdesk_monitoring | `GET /api/ma-pm-tickets`                 | Derived subset of `noc_tickets` — Preventive Maintenance Plan tickets reported today ("PM Tickets วันนี้"), materialized each poll rather than filtered at request time |
| `helpdesk_users`    | (manual/external)   | `POST /api/verify-employee`              | Employee credentials for the shift handover ack modal      |

Note the API path names (`ma-tickets` / `ma-pm-tickets`) predate this DB layout and don't map
1:1 onto the collection names — `/api/ma-tickets` reads `noc_tickets`, `/api/ma-pm-tickets` reads
`ma_tickets`. Worth knowing before assuming a path name tells you the backing collection.

- **mail_monitoring/** — watches the RD mailbox for "Maintenance/RD" emails and OCR-extracts
  schedule PDFs (Thai+English) into MongoDB (`noc_shift_handover.pm_mails`).
- **helpdesk_monitoring/** — polls the WebHelpDesk ticket API. Each poll: syncs every
  tracked-status ticket into `noc_shift_handover.noc_tickets`, then derives and syncs the
  PM-Plan-reported-today subset into `noc_shift_handover.ma_tickets`. No exposed port, writes only.
- **api/** — FastAPI service exposing:
  - `GET /api/uih-mails`, `GET /api/uih-mails/daily` (today's maintenance, filtered by whether
    the actual maintenance window overlaps today — not by when the email arrived), and
    `PUT`/`DELETE /api/uih-mails/{id}`.
  - `GET /api/ma-tickets` (all tracked Helpdesk tickets), `GET /api/ma-pm-tickets` (Preventive
    Maintenance Plan tickets reported today, pre-filtered), and `PUT /api/ma-tickets/{id}`.
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

| Service             | Port            | Purpose                                                |
|----------------------|-----------------|-----------------------------------------------------------|
| mail_monitoring      | (none)          | Background poller, writes `noc_shift_handover.pm_mails`    |
| helpdesk_monitoring  | (none)          | Background poller, writes `noc_shift_handover.noc_tickets` + `.ma_tickets` |
| api                  | 8000 (internal) | FastAPI `uih-mails` / `ma-tickets` API                     |
| shift_handover       | 4102            | Main dashboard (`/`) + all records (`/records`)            |
