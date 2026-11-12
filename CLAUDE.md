# FlowLeads CRM – notes for Claude

Multi-tenant Flask CRM (Python 3.11, SQLAlchemy, Alembic, APScheduler, Gunicorn on Render,
Postgres). UI and docs are in Finnish; the user writes in Finnish.

## Lead acquisition = shared Oikotie listing catalogue (read this first)

Full design: `docs/ARKKITEHTUURI_KOHDEKATALOGI.md`. In short:

- n8n crawls **all** Oikotie listings into one shared catalogue (`app/listings/`), analyses each
  listing **once** with AI into renovation types, and enriches contacts only for listings some
  profile matched.
- A `SearchProfile` (`app/search/models.py`) is just a **filter** (regions + renovation type(s) +
  `min_confidence`, optional cities / timing / build year / building type). Unknown listing values
  never exclude a listing.
- Matches (`ListingMatch`, status `pending`) do **not** become leads on their own. They wait on the
  customer's **Kohteet** page (`/targets`: `app/listings/target_routes.py`, `target_services.py`), where a
  user moves them to leads (`transfer_targets`) or hides them (`dismissed`). The available count is shown
  on the dashboard, in the sidebar and on `/admin/customers`. The scheduler job `listing_matching` (every
  10 min) only rematches profiles and joins same-taloyhtiö matches to existing leads. The old automatic
  conversion (`materialize_pending_matches`, `daily_lead_limit`) runs only with
  `LISTING_AUTO_CREATE_LEADS=true` (off by default; the older catalogue tests turn it on with the
  `auto_create_leads` fixture).
- A catalogue lead is **one housing company's renovation**, not a property manager: `Lead.company` is the
  taloyhtiö, the manager goes to `Lead.title`; leads are never merged by email (`match_by_email=False`); another apartment of the same taloyhtiö joins the existing
  lead (`ListingMatch.status == "merged"`).
- Onboarding (`app/admin/onboarding_services.py`, `invite_services.py`) creates an **active** profile and
  nothing else per customer: no API key, no SearchJob. It asks only the renovation types (urakat); regions
  default to "Koko Suomi" and the customer narrows the list with the Kohteet filters. Turning a profile on must go through
  `activate_profile()` so it is rematched. Billing is manual for now.
- **Oikotie blocks n8n Cloud since 11/2026** (DataDome 403 + captcha, the old Lead Worker too). The Crawler stops
  at the first block; never add bot-protection workarounds. Listings can come from any other source through
  `app/listings/import_services.py`: CSV upload on `/admin/catalogue` or `POST /api/v1/n8n/listings/import`.
  Rows with a renovation type are matched at once (targets on Kohteet); text-only rows go to the Analyzer (`fetch_detail: false`).
- **Reserve + "Hae kohteita"** (`TARGET_FETCH_ENABLED`, on by default, off in tests): a new match has
  `released_at = NULL` and is invisible to the customer until they press the button on Kohteet
  (`fetch_targets()`), which releases a batch of varying size once per hour per organization. The page shows
  a 10-15 s progress animation; the batch gets a `released_at` that far in the future. `count_targets()` and
  everything built on it count released targets only; `count_reserve()` is the rest.
- Kohteet has a filter bar (renovation, region, city, building type, contact info, sort) and a daily
  digest of new targets (scheduler job `target_digest`, `send_target_digests()`, per-organization switch
  `OrganizationSettings.targets_digest_enabled`). Transfers can set the lead owner.
- Read models over what an organization already sees (`app/listings/insight_services.py`,
  `insight_routes.py`, never the reserve): **Isännöitsijät** (`/managers`) and the **taloyhtiökortti**
  (`/housing-companies/<listing_id>`, opened from a target name, a manager page or a lead; no nav item).
  **Saved searches** on Kohteet (`SavedTargetSearch`, per user) are notified by `notify_saved_searches()` in the
  `listing_matching` job. Tests: `tests/test_insights.py`.
- **One housing company = one target** (`app/listings/housing.py`): `Listing.company_key` (normalised name + city;
  Y-tunnus only for nameless rows) groups listings on Kohteet, on the taloyhtiökortti (signal timeline) and in
  transfers/hiding/fetching. Each target has a score 0-100 (`ListingMatch.score`, default sort "Parhaat ensin").
  Board chair / maintenance company / property-manager offices: `app/listings/contacts.py`. Map: `/targets/map`
  (`app/listings/geo.py`, vendored Leaflet). Plain-language search: `app/listings/nl_search.py`.
- Other customer modules: CRM export to HubSpot/Pipedrive (`app/integrations/`), monthly report
  (`app/analytics/monthly.py`), plans/trial/monthly contract and public `/hinnasto` (`app/billing/`, prices in
  `plans.py`; an ended trial blocks fetching and transferring targets only). Superadmin `/admin/system` and
  `flask system-check` show email/backup/key status. Backups fall back to a Python dump (`app/backups/logical.py`)
  when pg_dump is older than the server. Typed task times are Finnish time, stored UTC (`tasks/services.local_to_utc`).
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

## Production rollout handoff

When the user says **"Jatka FlowLeadsin tuotantoon vientiä"**, read `docs/TUOTANTOON_VIENTI_TILANNE.md`
(state of the Render deployment, what is done, what is next) and continue from its "Seuraavaksi" list.
Keep that file up to date as items are finished.

## Working on this repo

- Tests: `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (≈6 min, all must pass).
  Catalogue tests: `tests/test_listings_catalogue.py`, `tests/test_catalogue_filters_and_onboarding.py`, `tests/test_targets.py` (Kohteet). n8n workflow e2e (needs Node.js):
  `tests/test_n8n_catalogue_e2e.py`, which runs the generated workflows with the mini n8n emulator in
  `tests/n8n_sim/` against a live local CRM. Re-run it after regenerating workflows.
- Front-end libraries (Chart.js, Sortable, Quill, Inter) are vendored in `app/static/vendor/`; do not add
  CDN links. CI (`.github/workflows/tests.yml`) runs migrations and the suite on every PR and on main.
- UI messages are Finnish: services raise English messages (shared with the JSON API) and
  `app/core/messages_fi.py` translates them when flashed. Timestamps are shown with the `helsinki` filter.
- Migrations: Alembic in `migrations/versions/`. Check with
  `DATABASE_URL=sqlite:///<tmp>.db flask --app run.py db upgrade` (and `downgrade`). Use
  `batch_alter_table` for drops (SQLite).
- Oikotie returns 403 to non-n8n machines, so scraping can't be verified locally. Use the mocks.
- The n8n Cloud instance has many unrelated workflows. **Never modify existing n8n workflows**;
  the user imports the generated JSON files themselves.
- Another Claude session may be editing UI files in this folder at the same time. Stage only your own
  changes (check `git diff` before committing).
