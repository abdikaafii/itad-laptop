# API documentation

## Station (engine) API – `/api/v1/engine`

Auth: `Authorization: Bearer itad_…` (create under **Company & keys → API tokens**; shown once).
Rate limit: 300 requests/minute. Tokens belong to one company and can only do the three things below.

| Method | Path | Purpose |
|---|---|---|
| GET | `/ping` | Check the token. `{ok, company, server_time}` |
| GET | `/jobs/<job_ref>` | Job ticket for cross-checking: `{job_ref, title, status, customer, expected_assets, assets[]}`. 404 if unknown. |
| POST | `/reports` | Send the final signed report JSON (the file `laptop_window.py` v1.11 writes). 201 with `{report_id, asset_id, signature_status, …}`; 400 not JSON; 422 not a report; 401 bad token. |

Example:

```
python3 website/integrations/upload_report.py --server https://itad.example.com \
    --token itad_xxx --check-job report.json
```

A stored report is *not* automatically trusted: see signature statuses in SYSTEM_ARCHITECTURE.md.

## Browser JSON

| Path | Notes |
|---|---|
| `GET /api/v1/dashboard/stats` | Signed-in. Same filters as the dashboard (`days`, `technician`, `customer`). Scoped by role. |
| `GET /healthz` | Liveness. |

## Downloads (signed-in, role-scoped)

`/dashboard.csv`, `/reports/<type>.csv`, `/reports/<type>.pdf` with types monthly, customer_assets,
job_completion, failed_devices, technician_performance, erasure_certificates;
`/reports/asset/<id>.pdf`, `/reports/asset/<id>/hardware.pdf`, `/certificates/<id>/pdf`.

## Public

`GET /verify` (form), `GET /verify/<number>` – certificate check, rate limited, masked details.
