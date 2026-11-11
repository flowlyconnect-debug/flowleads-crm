# FlowLeads CRM – notes for Claude

Multi-tenant Flask CRM (Python 3.11, SQLAlchemy, Alembic, APScheduler, Gunicorn on Render,
Postgres). UI and docs are in Finnish; the user writes in Finnish.

## Lead acquisition = shared Oikotie listing catalogue (read this first)

Full design: `docs/ARKKITEHTUURI_KOHDEKATALOGI.md`. In short:

- n8n crawls **all** Oikotie listings into one shared catalogue (`app/listings/`), analyses each
  listing **once** with AI into renovation types, and enriches contacts only for listings some
  profile matched.
- A `SearchProfile` (`app/search/models.py`) is just a **filter** (regions + renovation type(s) +
  `min_confidence`, optional cities / timing / build year / building type, with `daily_lead_limit`).
  Matches (`ListingMatch`) become leads in that org via `materialize_pending_matches` (scheduler job
  `listing_matching`, every 10 min). Unknown listing values never exclude a listing.
- A catalogue lead is **one housing company's renovation**, not a property manager: leads are never
  merged by email (`match_by_email=False`); another apartment of the same taloyhtiö joins the existing
  lead (`ListingMatch.status == "merged"`).
- Onboarding (`app/admin/onboarding_services.py`, `invite_services.py`) creates an **active** profile and
  nothing else per customer: no API key, no SearchJob. Turning a profile on must go through
  `activate_profile()` so it is rematched. Billing is manual for now.
- **Oikotie blocks n8n Cloud since 11/2026** (DataDome 403 + captcha, the old Lead Worker too). The Crawler stops
  at the first block; never add bot-protection workarounds. Listings can come from any other source through
  `app/listings/import_services.py`: CSV upload on `/admin/catalogue` or `POST /api/v1/n8n/listings/import`.
  Rows with a renovation type become leads at once; text-only rows go to the Analyzer (`fetch_detail: false`).
- n8n ↔ CRM API: `/api/v1/n8n/listings/*` (`app/listings/routes.py`), auth header `X-N8N-Secret`.
- n8n workflows: `n8n/workflows/flowleads-{crawler,analyzer,enricher}.json`. They are **generated**:
  edit `n8n/tools/build_catalogue_workflows.py` and run
  `python n8n/tools/build_catalogue_workflows.py`. Don't hand-edit the JSON. Oikotie/Finder/Asiakastieto
  nodes come from the production export `n8n/source/lead-worker-export.json` via
  `n8n/tools/build_workflows.py::build_worker()` (with its bug fixes).
- Renovation types: `app/search/constants.py::REMONTTITYYPIT` is the single source of truth (the
  Analyzer receives them from `/analysis/claim`). Regions: `FINNISH_REGIONS` (18 maakuntaa) and
  `app/listings/services.py::REGION_SLUGS`.

**Legacy, do not extend:** the per-profile SearchJob pipeline (`app/search/services.py`,
`/api/v1/n8n/jobs`, job tokens, heartbeats). Its scheduler jobs are off (`SEARCH_JOBS_ENABLED=false`)
and there is no n8n workflow for it any more. Profile fields `schedule_description` and
`crm_api_key` are unused.

## Working on this repo

- Tests: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (≈6 min, all must pass).
  Catalogue tests: `tests/test_listings_catalogue.py`, `tests/test_catalogue_filters_and_onboarding.py`. n8n workflow e2e (needs Node.js):
  `tests/test_n8n_catalogue_e2e.py`, which runs the generated workflows with the mini n8n emulator in
  `tests/n8n_sim/` against a live local CRM. Re-run it after regenerating workflows.
- Migrations: Alembic in `migrations/versions/`. Check with
  `DATABASE_URL=sqlite:///<tmp>.db flask --app run.py db upgrade` (and `downgrade`). Use
  `batch_alter_table` for drops (SQLite).
- Oikotie returns 403 to non-n8n machines, so scraping can't be verified locally. Use the mocks.
- The n8n Cloud instance has many unrelated workflows. **Never modify existing n8n workflows**;
  the user imports the generated JSON files themselves.
- Another Claude session may be editing UI files in this folder at the same time. Stage only your own
  changes (check `git diff` before committing).
