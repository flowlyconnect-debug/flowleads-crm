# FlowLeads n8n – SearchJob pipeline

Pipeline:

```
SearchProfile (CRM)
  → SearchJob (pending)
  → Job Poller
  → Job Dispatcher
  → Lead Worker Universal (per region + page)
  → CRM leads + dedupe
  → PATCH job completed/failed
```

## Workflows in this folder

| File | Role |
|------|------|
| `workflows/flowleads-job-poller.json` | Polls `GET /api/v1/n8n/jobs`, marks running/completed/failed |
| `workflows/flowleads-job-dispatcher.json` | Expands regions, probes Oikotie page 1, builds page items, calls worker |
| `workflows/flowleads-lead-worker-universal.json` | One region + page → Oikotie → dedupe → `POST /api/v1/leads` |

Shared pagination logic: `lib/flowleads-dispatcher.js` (mirrors `app/search/oikotie_pagination.py`).

## n8n environment variables

Set in n8n (Settings → Variables or instance env):

| Variable | Example |
|----------|---------|
| `CRM_BASE_URL` | `https://flowleads-crm.onrender.com` |
| `N8N_MASTER_SECRET` | Same value as CRM `.env` `N8N_MASTER_SECRET` |

CRM also needs `APP_BASE_URL` so `crm_endpoint` in job payload is correct.

## Import order

1. Import **Lead Worker Universal** first.
2. Import **Job Dispatcher** → open `CALL – Lead Worker Universal` → select the worker workflow.
3. Import **Job Poller** → open `CALL – Job Dispatcher` → select the dispatcher workflow.
4. Activate Job Poller (dispatcher + worker stay callable sub-workflows).

## Fixing an existing n8n instance (instead of full import)

### Job Poller – `CALL – Lead Worker Universal` → replace with `CALL – Job Dispatcher`

**Before (bug):** hardcoded `job_id: 0`, `organization_id: 0`.

**After:** map fields from polled job item:

```
job_id          = {{ $('For each job').item.json.job_id }}
organization_id = {{ $('For each job').item.json.organization_id }}
profile_id      = {{ $('For each job').item.json.profile_id }}
remonttityyppi  = {{ $('For each job').item.json.remonttityyppi }}
regions         = {{ $('For each job').item.json.regions }}
source          = {{ $('For each job').item.json.source }}
crm_api_key     = {{ $('For each job').item.json.crm_api_key }}
crm_endpoint    = {{ $('For each job').item.json.crm_endpoint }}
max_pages       = 5
```

Remove direct Lead Worker call from poller.

### Job Dispatcher (new workflow)

Copy Code node bodies from `flowleads-job-dispatcher.json` or `lib/flowleads-dispatcher.js`.

Key behaviour:

- `max_pages` default **5** (safety cap for night tests)
- `offset = (page - 1) * 24`, `limit = 24`
- Total from `total` / `totalCount` / `count` / `data.total` / `meta.total`

### Lead Worker Universal

1. **POST lead to CRM** – use input fields, not hardcoded values:
   - URL: `{{ $json.crm_endpoint }}`
   - Authorization: `Bearer {{ $json.crm_api_key }}`
2. Accept one `current_region` + `page` + `offset` + `limit` per execution.
3. Return per page:
   - `leads_found`, `leads_sent`, `duplicates`, `failed`, `page`, `current_region`
4. Keep `source_ref` as `oikotie-<cardId>` for dedupe.

### Oikotie URL alignment

Dispatcher probe and Lead Worker fetch must use the **same URL builder**. Default in exports:

```
https://asunnot.oikotie.fi/api/5.0/cards?limit=24&offset=0&sortBy=published_desc&freeText=<remonttityyppi>&locations[]=<region>
```

If your production worker uses a different Oikotie endpoint, update both **Build Oikotie page 1 URL** (dispatcher) and **Build Oikotie URL** (worker) to match.

## Manual test (single profile)

1. CRM: Settings → Search profiles → create active profile with 1–2 regions.
2. Create test job: `POST /settings/search-profiles/<id>/create-test-job` (admin UI button).
3. Verify job: `GET /api/v1/n8n/jobs?status=pending` with `X-N8N-Secret`.
4. n8n: run **Job Poller** manually (Manual Trigger).
5. CRM: check job `completed`, new leads in pipeline, `search_dedupe` rows for `oikotie-*` refs.

## Night test (one profile)

1. Set profile `schedule_description = daily`, `is_active = true`, **one region** only.
2. Set `max_pages = 5` in dispatcher call (or profile test via manual poller run).
3. Ensure only one pending job exists for that profile.
4. Let Job Poller cron run (every 15 min in export) or trigger once before sleep.
5. Morning: verify `SearchJob.completed`, `leads_sent`, profile `total_leads_sent`, dedupe table.

## CRM tests

```bash
pytest tests/test_n8n_search.py tests/test_search_job_scheduler.py tests/test_oikotie_pagination.py -q
```
