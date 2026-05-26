# FlowLeads CRM — Kehityssuunnitelma

**Tuote:** Multi-tenant SaaS CRM AI-automaattisille liideille  
**Stack:** Python Flask · PostgreSQL · SQLAlchemy · Mailgun · n8n API-integraatio  
**Workflow:** Claude (suunnittelu) → ChatGPT (promptien rikastus) → Cursor (koodaus)

---

## Arkkitehtuurikuvaus

```
n8n workflow
    │
    │  POST /api/v1/leads  (API-avain)
    ▼
FlowLeads CRM API
    │
    ├── Lead vastaanotetaan
    ├── AI-rikastus (OpenAI / Anthropic)
    ├── Tallennus organisaation liideihin
    └── Pipeline-hallinta käyttöliittymässä
```

**Tenant-rakenne:** Jokainen asiakas = oma `Organization`. Kaikki liidit, käyttäjät ja asetukset scoped organisaation mukaan.

---

## Kehitysvaiheet

---

## VAIHE 1 — Pohja ja autentikointi
**Arvio:** 2–3 päivää  
**Tavoite:** Toimiva Flask-app, tietokanta, monikäyttäjätuki, superadmin 2FA:lla

### Cursor-prompt

```
Build a production-ready Flask CRM application foundation with the following requirements:

PROJECT STRUCTURE:
app/
  __init__.py          # App factory
  config.py            # Config from env vars, no hardcoded secrets
  extensions.py        # db, login_manager, mail, csrf, migrate

  auth/
    routes.py          # login, logout, register, password reset
    models.py          # User model
    forms.py           # LoginForm, RegisterForm, ResetPasswordForm
    services.py        # AuthService

  admin/
    routes.py          # Superadmin panel
    services.py

  users/
    models.py          # User, Organization, Role
    services.py

  core/
    security.py        # 2FA (TOTP), rate limiting
    permissions.py     # Role decorators: @require_role('superadmin')
    errors.py          # 400, 401, 403, 404, 429, 500 handlers
    audit.py           # AuditLog model + logging service

  templates/
    base.html
    auth/login.html, register.html, reset_password.html
    admin/dashboard.html

migrations/
.env.example
requirements.txt
run.py

DATABASE MODELS (PostgreSQL + SQLAlchemy):

Organization:
  id, name, slug, is_active, created_at

User:
  id, organization_id (FK), email, password_hash, role (enum: superadmin/admin/user/api_client)
  is_active, totp_secret, totp_enabled, failed_login_attempts, locked_until, created_at

AuditLog:
  id, user_id, organization_id, action, target_type, target_id,
  ip_address, user_agent, metadata (JSON), created_at

AUTHENTICATION:
- Email + password login with bcrypt hashing
- Flask-Login session management
- Password reset via email token (itsdangerous)
- Login attempt limiting: 5 attempts → 15 min lockout
- CSRF protection on all forms

2FA FOR SUPERADMIN (mandatory):
- TOTP-based (pyotp library)
- QR code generation for setup (qrcode library)
- Backup codes (10 one-time codes stored hashed)
- Superadmin cannot access any admin routes without active 2FA session
- Use decorator @require_2fa for all superadmin routes

ROLES & PERMISSIONS:
- superadmin: full system access + 2FA required
- admin: manage own organization
- user: standard CRM access
- api_client: API-only access

AUDIT LOGGING:
Log these events: login_success, login_failed, logout, password_changed,
2fa_enabled, user_created, user_deleted, role_changed

SECURITY:
- SESSION_COOKIE_SECURE=True in production
- SESSION_COOKIE_HTTPONLY=True
- SESSION_COOKIE_SAMESITE='Lax'
- All secrets from environment variables

ENV VARIABLES (.env.example):
FLASK_ENV=development
SECRET_KEY=
DATABASE_URL=postgresql://user:pass@localhost/flowleads
MAILGUN_API_KEY=
MAILGUN_DOMAIN=
MAILGUN_FROM_EMAIL=
MAILGUN_FROM_NAME=FlowLeads
BACKUP_DIR=./backups
BACKUP_RETENTION_DAYS=30
LOGIN_RATE_LIMIT=5/minute
API_RATE_LIMIT=100/hour

COMMANDS (flask CLI):
flask create-superadmin   # prompt for email + password
flask db upgrade

Include Dockerfile and docker-compose.yml with PostgreSQL service.
Write pytest tests for: user creation, login, failed login, superadmin 2FA requirement.
```

### ✅ Vaiheen 1 hyväksymiskriteerit
- [ ] `docker-compose up` käynnistää sovelluksen
- [ ] `flask db upgrade` luo taulut
- [ ] `flask create-superadmin` luo superadmin-käyttäjän
- [ ] Superadmin ei pääse admin-paneliin ilman 2FA:ta
- [ ] Kirjautuminen toimii, 5 väärää yritystä lukitsee
- [ ] Audit-loki kirjaa kirjautumistapahtumat
- [ ] Testit menevät läpi: `pytest tests/`

---

## VAIHE 2 — Lead-malli ja pipeline
**Arvio:** 2–3 päivää  
**Tavoite:** Liidien tietomalli, pipeline-vaiheet, perus CRUD, kanban-näkymä

### Cursor-prompt

```
Extend the FlowLeads CRM with a complete lead management system:

NEW MODELS (add to existing SQLAlchemy setup):

PipelineStage:
  id, organization_id (FK), name, order_index, color (#hex), is_default
  Default stages: New Lead, Contacted, Interested, Proposal Sent, Won, Lost

Lead:
  id, organization_id (FK), assigned_to (FK → User, nullable)
  
  # Contact info
  first_name, last_name, email, phone, company, title, website, linkedin_url
  
  # Pipeline
  stage_id (FK → PipelineStage), status (enum: active/won/lost/archived)
  
  # Lead source
  source (string: n8n, manual, import), source_ref (external ID from n8n)
  
  # AI enrichment
  ai_enriched (bool, default False), ai_enriched_at, ai_summary (text)
  ai_company_info (JSON), ai_contact_info (JSON)
  
  # Scoring
  score (int 0-100, nullable), score_reason (text)
  
  # Meta
  notes (text), tags (JSON array of strings)
  created_at, updated_at, last_contacted_at

Activity:
  id, lead_id (FK), user_id (FK), organization_id (FK)
  type (enum: note, email_sent, call, stage_changed, ai_enriched, created)
  content (text), metadata (JSON)
  created_at

ROUTES (blueprint: leads):
  GET  /leads                    # List view with filters + pagination
  GET  /leads/pipeline           # Kanban pipeline view
  GET  /leads/<id>               # Lead detail / profile
  POST /leads                    # Create manually
  PUT  /leads/<id>               # Update lead
  DELETE /leads/<id>             # Archive lead (soft delete)
  POST /leads/<id>/stage         # Move to stage
  POST /leads/<id>/note          # Add note
  GET  /leads/export             # CSV export

PIPELINE KANBAN VIEW (/leads/pipeline):
- Show leads grouped by stage as columns
- Each card shows: name, company, score badge, assigned user avatar, last activity
- Drag-and-drop stage change via JavaScript (use SortableJS CDN)
- Stage change triggers Activity log entry
- Filter by: assigned_to, source, score range, date range

LEAD LIST VIEW (/leads):
- Sortable table: name, company, stage, score, source, created_at
- Pagination (25 per page)
- Search by name/email/company
- Filter by stage, source, assigned_to
- Bulk actions: assign, change stage, export, archive

LEAD DETAIL VIEW (/leads/<id>):
- Full contact information card
- Activity timeline (newest first)
- AI enrichment status + summary
- Pipeline stage selector
- Quick note add form
- Email send button (Phase 5)
- Assigned user selector

SERVICES (leads/services.py):
- LeadService.create(data, organization_id)
- LeadService.update(lead_id, data)
- LeadService.move_stage(lead_id, stage_id, user_id)
- LeadService.get_pipeline_data(organization_id) → grouped by stage
- LeadService.search(organization_id, filters, page)
- LeadService.log_activity(lead_id, user_id, type, content)

PERMISSIONS:
- All lead queries must filter by organization_id (never leak cross-tenant)
- Only admin/superadmin can assign leads to other users
- Only admin/superadmin can delete/archive

Include database migration for new tables.
Write tests for: lead creation, stage movement, cross-tenant isolation.
```

### ✅ Vaiheen 2 hyväksymiskriteerit
- [ ] Kanban pipeline-näkymä toimii drag-and-dropilla
- [ ] Liidin luonti, muokkaus, arkistointi toimii
- [ ] Vaiheen muutos kirjataan activity-logiin
- [ ] Hakutoiminto ja pagination toimivat
- [ ] Cross-tenant eristys testattu (user A ei näe user B:n liidejä)
- [ ] CSV-vienti toimii

---

## VAIHE 3 — n8n API-integraatio
**Arvio:** 1–2 päivää  
**Tavoite:** n8n voi pushata liidejä REST API:lla, API-avainten hallinta

### Cursor-prompt

```
Add a complete REST API to FlowLeads CRM for n8n integration:

API STRUCTURE: All endpoints under /api/v1/

AUTHENTICATION:
- API key passed as: Authorization: Bearer <key>  OR  X-API-Key: <key>
- API keys stored hashed (SHA-256) in database
- Each key scoped to one organization
- Key usage logged per request

APIKey model:
  id, organization_id (FK), name, key_hash, key_prefix (first 8 chars, visible)
  is_active, last_used_at, expires_at (nullable), request_count
  created_by (FK → User), created_at

API ENDPOINTS:

Health & Auth:
  GET  /api/v1/health
       Response: {"success": true, "data": {"status": "ok", "version": "1.0.0"}}
  
  GET  /api/v1/me
       Response: {"success": true, "data": {"organization": {...}, "key_name": "..."}}

Leads (primary n8n endpoints):
  POST /api/v1/leads
       Create or update lead (upsert by email)
       Body: {
         "email": "john@example.com",          # required
         "first_name": "John",
         "last_name": "Doe",
         "company": "Acme Corp",
         "title": "CEO",
         "phone": "+358...",
         "website": "https://...",
         "linkedin_url": "https://linkedin.com/in/...",
         "source": "n8n",
         "source_ref": "n8n_item_id_123",      # n8n item ID for dedup
         "tags": ["saas", "b2b"],
         "metadata": {}                         # any extra fields from n8n
       }
       Logic:
         1. Find existing lead by email + organization_id
         2. If exists: update fields that are not null in payload, add activity "updated_via_api"
         3. If new: create lead, set stage to default "New Lead", add activity "created_via_api"
         4. Return lead object
       Response: {"success": true, "data": {"lead": {...}, "action": "created|updated"}}

  POST /api/v1/leads/bulk
       Accept array of up to 100 leads
       Process each with same upsert logic
       Response: {"success": true, "data": {"created": N, "updated": M, "errors": [...]}}

  GET  /api/v1/leads
       List leads (paginated)
       Query params: page, per_page, stage, source, created_after

  GET  /api/v1/leads/<id>
       Single lead detail

  PATCH /api/v1/leads/<id>
       Update lead fields

Pipeline:
  GET  /api/v1/pipeline/stages
       Return pipeline stages for the organization

RESPONSE FORMAT (all endpoints):
Success:
  {"success": true, "data": {...}, "error": null}
Error:
  {"success": false, "data": null, "error": {"code": "error_code", "message": "Human readable"}}

Error codes:
  invalid_api_key, expired_api_key, missing_api_key
  validation_error, not_found, rate_limit_exceeded, server_error

RATE LIMITING:
- 100 requests/hour per API key (configurable via API_RATE_LIMIT env)
- 429 response with Retry-After header when exceeded

INPUT VALIDATION:
- Email format validation
- Max field lengths enforced
- Unknown fields in metadata stored as-is (don't reject)

SUPERADMIN API KEY MANAGEMENT UI:
  GET  /admin/api-keys              # List all keys across organizations
  POST /admin/api-keys              # Generate new key
  DELETE /admin/api-keys/<id>       # Revoke key
  
  On create: show full key ONCE, never again (only prefix stored + hash)
  Audit log: api_key_created, api_key_revoked

ORGANIZATION API KEY MANAGEMENT UI:
  GET  /settings/api-keys           # List own org keys
  POST /settings/api-keys           # Generate (admin only)
  DELETE /settings/api-keys/<id>    # Revoke (admin only)

n8n INTEGRATION GUIDE (add to README):
  Endpoint: POST https://your-domain.com/api/v1/leads
  Headers:
    Authorization: Bearer YOUR_API_KEY
    Content-Type: application/json
  
  n8n HTTP Request node setup:
    Method: POST
    URL: https://your-domain.com/api/v1/leads
    Authentication: Generic Credential Type → Header Auth
    Header Name: Authorization
    Header Value: Bearer YOUR_API_KEY

Write tests for:
- Valid API key authentication
- Invalid/expired key rejection
- Lead creation via API
- Lead upsert (same email = update)
- Bulk lead import
- Rate limit enforcement
- Cross-tenant isolation (key from org A cannot see org B leads)
```

### ✅ Vaiheen 3 hyväksymiskriteerit
- [ ] `POST /api/v1/leads` luo liidin n8n:n lähettämillä tiedoilla
- [ ] Sama sähköposti = päivitys, ei duplikaatti
- [ ] Bulk-endpoint käsittelee 100 liidiä kerralla
- [ ] API-avain ei näy selväkielisenä tietokannassa
- [ ] Rate limiting toimii (429 oikeaan aikaan)
- [ ] Cross-tenant testattu
- [ ] n8n-ohje lisätty README:hen

---

## VAIHE 4 — AI-rikastus
**Arvio:** 2 päivää  
**Tavoite:** Liidin tullessa AI täydentää tiedot automaattisesti taustalla

### Cursor-prompt

```
Add automatic AI enrichment to FlowLeads CRM using OpenAI:

ENV VARIABLES (add to .env.example):
OPENAI_API_KEY=
AI_ENRICHMENT_ENABLED=true
AI_ENRICHMENT_MODEL=gpt-4o-mini
AI_AUTO_ENRICH_ON_CREATE=true

AI ENRICHMENT SERVICE (app/ai/services.py):

AIEnrichmentService.enrich_lead(lead_id):
  """
  Takes existing lead data and enriches it using OpenAI.
  Updates the lead record with enriched information.
  """
  
  Prompt template:
  ---
  You are a B2B sales intelligence assistant.
  Given this lead information, provide enriched analysis:
  
  Name: {first_name} {last_name}
  Company: {company}
  Title: {title}
  Website: {website}
  LinkedIn: {linkedin_url}
  
  Return a JSON object with:
  {
    "summary": "2-3 sentence prospect summary for a salesperson",
    "company_info": {
      "industry": "",
      "company_size_estimate": "",
      "business_model": "b2b|b2c|both",
      "likely_pain_points": ["", ""],
      "tech_stack_hints": [""]
    },
    "contact_info": {
      "seniority_level": "c-level|vp|director|manager|individual",
      "likely_decision_maker": true/false,
      "best_outreach_angle": ""
    },
    "lead_score": 0-100,
    "score_reason": "Brief explanation of score"
  }
  ---
  
  After enrichment:
  - Update lead: ai_enriched=True, ai_enriched_at=now, ai_summary, ai_company_info, ai_contact_info, score, score_reason
  - Add activity: type="ai_enriched", content="AI enrichment completed. Score: {score}"
  - Log any errors without crashing the lead creation flow

BACKGROUND PROCESSING:
Use Python threading or APScheduler for async enrichment (don't block the API response):
  - On lead creation via API: if AI_AUTO_ENRICH_ON_CREATE=true, queue enrichment in background thread
  - Enrichment queue: simple in-memory queue for MVP (upgrade to RQ/Celery later)
  - Max concurrent enrichments: 3
  - Retry failed enrichments up to 2 times with 60s delay

MANUAL RE-ENRICHMENT:
  POST /leads/<id>/enrich     # Trigger manual enrichment (admin/user)
  POST /api/v1/leads/<id>/enrich  # Via API

UI CHANGES:
Lead detail page:
  - Show AI enrichment badge: "AI Enriched ✓" with timestamp
  - Show AI summary in a highlighted box
  - Show lead score as colored badge: 0-40 red, 41-70 yellow, 71-100 green
  - Show company_info and contact_info in a collapsible "AI Insights" section
  - "Re-enrich" button for manual trigger
  - "Enriching..." spinner when in progress

Lead list/pipeline:
  - Score badge on each card/row
  - Filter by score range
  - Sort by score

ENRICHMENT STATUS TRACKING:
Add to Lead model:
  ai_enrichment_status: enum(pending, processing, completed, failed, disabled)
  ai_enrichment_error: text (last error message)

COST CONTROL:
  - Log token usage per enrichment in metadata
  - Skip enrichment if lead has no company AND no website AND no linkedin_url
  - Superadmin can see total AI API usage in admin panel

Write tests for:
- Enrichment triggered on lead creation
- Enrichment result saved to lead
- Failed enrichment doesn't crash lead creation
- Score badges shown correctly in UI
```

### ✅ Vaiheen 4 hyväksymiskriteerit
- [ ] Uusi liidi n8n:stä → AI rikastaa automaattisesti taustalla
- [ ] Pistemäärä (score) näkyy pipeline-kortissa
- [ ] Manuaalinen uudelleenrikastus toimii
- [ ] Rikastusvirhe ei kaada liidi-luontia
- [ ] AI-kustannusloki superadmin-paneelissa

---

## VAIHE 5 — Sähköposti CRM:stä
**Arvio:** 1–2 päivää  
**Tavoite:** Lähetä sähköposteja suoraan liidin profiilista, historia talteen

### Cursor-prompt

```
Add email sending capabilities to FlowLeads CRM using Mailgun:

EMAIL SERVICE (app/email/services.py):
EmailService.send_to_lead(lead_id, user_id, subject, body_html, body_text):
  - Send email via Mailgun API
  - From: organization's configured sender OR default MAILGUN_FROM_EMAIL
  - To: lead's email
  - Log as Activity: type="email_sent", content=subject, metadata={message_id, body_preview}
  - Store in EmailLog model
  - Return success/failure

EmailLog model:
  id, lead_id (FK), user_id (FK), organization_id (FK)
  subject, body_html, body_text
  mailgun_message_id, status (sent|failed|bounced|opened|clicked)
  sent_at, error_message

EMAIL TEMPLATES (customizable per organization):
EmailTemplate model:
  id, organization_id (FK nullable - null = system default)
  name, subject_template, body_html_template, body_text_template
  variables (JSON: list of variable names)
  created_by, created_at, updated_at

Default templates to seed:
  1. "Initial Outreach" - first contact email
  2. "Follow-up" - follow-up after no response
  3. "Demo Request" - invite to demo

Template variables: {{first_name}}, {{last_name}}, {{company}}, {{sender_name}}, {{ai_summary}}

ROUTES:

Email Compose:
  GET  /leads/<id>/email/compose                    # Compose UI
  POST /leads/<id>/email/send                       # Send email
  GET  /leads/<id>/email/history                    # Email history for lead

Email Templates:
  GET  /settings/email-templates                    # List templates
  POST /settings/email-templates                    # Create template  
  GET  /settings/email-templates/<id>/edit          # Edit template
  POST /settings/email-templates/<id>/edit          # Save template
  DELETE /settings/email-templates/<id>             # Delete (not system defaults)
  GET  /settings/email-templates/<id>/preview       # Preview with sample data

Superadmin:
  GET  /admin/email/logs                            # All sent emails
  POST /admin/email/test                            # Send test email to admin's address

UI - Email Compose (/leads/<id>/email/compose):
  - Template selector dropdown
  - Subject field (pre-filled from template)
  - Rich text editor (use Quill.js from CDN)
  - Preview panel showing rendered email with lead variables substituted
  - "Send" button
  - Show lead's email address prominently
  - Show AI summary for context while composing

UI - Email History:
  - Table: subject, sent_by, sent_at, status
  - Click to view full email content

MAILGUN WEBHOOKS (optional, for status tracking):
  POST /api/webhooks/mailgun
  - Verify webhook signature
  - Update EmailLog status on: delivered, bounced, opened, clicked
  - Add Activity when email opened/clicked

ORGANIZATION EMAIL SETTINGS:
  - Custom "From Name" per organization
  - Custom "From Email" per organization (must be verified Mailgun domain)
  - Mailgun API key can be per-organization OR use system default
  
Settings UI: /settings/email

Write tests for:
- Email sent and logged correctly
- Template variables substituted correctly
- Failed send logged with error
- Email history visible on lead profile
```

### ✅ Vaiheen 5 hyväksymiskriteerit
- [ ] Sähköpostin lähetys liidin profiilista toimii
- [ ] Mailgun-viesti lähtee oikeasti
- [ ] Sähköpostihistoria näkyy activity-timelinessa
- [ ] Muokattavat pohjat toimivat muuttujilla
- [ ] Superadmin näkee kaikki lähetetyt sähköpostit

---

## VAIHE 6 — Raportit ja analytiikka
**Arvio:** 2 päivää  
**Tavoite:** Dashboard, konversioanalytiikka, pipeline-arvo, aktiviteettihistoria

### Cursor-prompt

```
Add analytics and reporting to FlowLeads CRM:

DASHBOARD (/dashboard — main landing after login):

Key metrics cards (organization-scoped):
  - Total leads (this month vs last month, % change)
  - Leads by stage (counts per stage)
  - Conversion rate: (Won leads / Total closed leads) * 100
  - Average lead score
  - Emails sent this month
  - AI enrichments completed

Charts (use Chart.js from CDN):
  1. Line chart: New leads per day (last 30 days)
  2. Funnel chart / bar chart: Leads count per pipeline stage
  3. Pie chart: Lead sources (n8n, manual, import)
  4. Bar chart: Top 5 team members by leads contacted

Activity feed:
  - Recent 20 activities across all leads
  - Each item: lead name, action, user, time ago

REPORTS PAGE (/reports):

Report 1 — Pipeline Report:
  - Date range filter (this week / this month / custom)
  - Stage-by-stage breakdown: count, avg score, total (if deal value tracked)
  - Stage conversion rates: % moving from stage N to N+1
  - Avg time spent in each stage (days)
  - Won/Lost breakdown with reasons if noted

Report 2 — Lead Source Report:
  - Leads by source over time
  - Conversion rate per source
  - AI score distribution per source

Report 3 — Team Activity Report:
  - Per-user: leads assigned, notes added, emails sent, stage changes
  - Date range filter

Report 4 — AI Enrichment Report:
  - Enrichment success rate
  - Average score per enrichment batch
  - API token usage and cost estimate

ANALYTICS SERVICE (app/analytics/services.py):
  AnalyticsService.get_dashboard_stats(organization_id, period_days=30)
  AnalyticsService.get_pipeline_report(organization_id, start_date, end_date)
  AnalyticsService.get_source_report(organization_id, start_date, end_date)
  AnalyticsService.get_team_report(organization_id, start_date, end_date)
  AnalyticsService.get_ai_report(organization_id, start_date, end_date)

PERFORMANCE:
  - Add database indexes: lead.organization_id, lead.stage_id, lead.created_at, activity.lead_id, activity.created_at
  - Cache dashboard stats for 5 minutes (use Flask-Caching with simple cache)
  - Paginate all list queries

EXPORT:
  GET /reports/export?type=pipeline&format=csv&start=...&end=...
  GET /reports/export?type=team&format=csv

SUPERADMIN SYSTEM REPORT (/admin/reports):
  - Total organizations
  - Total leads across all orgs
  - API usage by organization
  - AI enrichment costs by organization
  - Monthly active organizations

Add database indexes migration.
Write tests for analytics service functions with sample data.
```

### ✅ Vaiheen 6 hyväksymiskriteerit
- [ ] Dashboard latautuu alle 2 sekunnissa
- [ ] Kaikki 4 raporttia toimivat date range -suodattimella
- [ ] CSV-vienti toimii
- [ ] Kaaviot renderöityvät oikein (Chart.js)
- [ ] Superadmin näkee koko järjestelmän tilastot

---

## VAIHE 7 — Tuotantovalmius
**Arvio:** 1–2 päivää  
**Tavoite:** Varmuuskopiot, tuotantokonfiguraatio, testikattavuus, README

### Cursor-prompt

```
Finalize FlowLeads CRM for production deployment:

BACKUP SYSTEM (app/backups/services.py):
BackupService.create_backup():
  1. Dump PostgreSQL: pg_dump → compressed .sql.gz
  2. Archive uploaded files directory
  3. Export email templates as JSON
  4. Export system settings (excluding secrets)
  5. Package into timestamped .tar.gz: backup_2025_01_15_143022.tar.gz
  6. Store in BACKUP_DIR
  7. Delete backups older than BACKUP_RETENTION_DAYS
  8. Send email notification to superadmin: success or failure

BackupService.restore_backup(backup_filename, confirmed_by_user_id, totp_code):
  1. Verify TOTP code for superadmin
  2. Show risk warning (handled in route)
  3. Create safety backup of current state
  4. Restore PostgreSQL from dump
  5. Restore files
  6. Log to audit: backup_restored
  7. Send confirmation email

SCHEDULED BACKUP:
Use APScheduler:
  - Daily at 02:00 UTC
  - Run BackupService.create_backup()
  - Initialize scheduler in app factory

BACKUP MANAGEMENT UI (/admin/backups):
  - List backups: filename, size, created_at, status
  - Download backup file
  - Restore button → confirmation modal with:
    * Risk warning text
    * Password confirmation field
    * 2FA code field
  - Manual "Create Backup Now" button

CLI COMMANDS:
  flask backup-create
  flask backup-restore <filename>
  flask rotate-api-key <key-id>
  flask send-test-email <email>
  flask create-superadmin

PRODUCTION CONFIGURATION:

Dockerfile (multi-stage):
  - Base: python:3.11-slim
  - Install: postgresql-client (for pg_dump), build deps
  - Copy app, install requirements
  - Expose 8000
  - CMD: gunicorn run:app --workers 4 --bind 0.0.0.0:8000

docker-compose.yml (production-like):
  services:
    app: (FlowLeads)
    db: postgres:15
    redis: redis:7 (for rate limiting + caching)
    scheduler: (same image, CMD: flask run-scheduler)
  
  volumes: postgres_data, backup_data, uploads_data

nginx.conf (included as example):
  - Proxy to gunicorn
  - SSL termination ready
  - Static file serving
  - Upload size limit: 10MB

COMPLETE TEST SUITE (tests/):
  test_auth.py:           login, logout, register, password reset, 2FA
  test_leads.py:          CRUD, pipeline stages, cross-tenant
  test_api.py:            all API endpoints, auth, rate limiting, bulk import
  test_email.py:          send, template rendering, logging
  test_ai.py:             enrichment, error handling
  test_backup.py:         create, restore flow, retention cleanup
  test_analytics.py:      dashboard stats, report data
  test_permissions.py:    role-based access, admin restrictions

Target: 80%+ coverage

README.md must include:
  1. What FlowLeads CRM is
  2. Quick start with Docker
  3. All environment variables explained
  4. Development setup (virtualenv)
  5. Database migrations
  6. Creating superadmin
  7. n8n integration setup (step by step)
  8. API reference (all endpoints)
  9. Email template variables
  10. Backup and restore procedure
  11. Running tests
  12. Production deployment checklist

Final security checklist:
  - No hardcoded secrets anywhere
  - All admin routes require login + role check
  - All API routes require valid API key
  - All queries scoped to organization
  - Input validation on all forms and API endpoints
  - Rate limiting active
  - CSRF on all forms
  - Audit log for all critical actions
```

### ✅ Vaiheen 7 hyväksymiskriteerit
- [ ] `docker-compose up` käynnistää koko stackin
- [ ] Päivittäinen varmuuskopio ajastuu automaattisesti
- [ ] Varmuuskopion palautus vaatii salasana + 2FA
- [ ] 80%+ testikattavuus
- [ ] README kertoo kaiken käyttöönotosta
- [ ] Ei yhtään kovakoodattua salaisuutta koodissa

---

## Kehitysworkflow

```
┌─────────────────────────────────────────────────────────┐
│  1. Claude (Cowork)                                     │
│     → Vaiheen tarkistus, seuraavan vaiheen suunnittelu  │
│     → Bugien analysointi, arkkitehtuuripäätökset        │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│  2. ChatGPT                                             │
│     → Cursor-promptin rikastus ja tarkentaminen         │
│     → Edge casien lisääminen                            │
│     → Testikattavuuden parantaminen                     │
└───────────────────┬─────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────┐
│  3. Cursor                                              │
│     → Vaiheen koodaus rikastetun promptin mukaan        │
│     → Testien ajo                                       │
│     → Tarkistuslistojen läpikäynti                      │
└─────────────────────────────────────────────────────────┘
```

## Yhteenveto

| Vaihe | Sisältö | Arvio |
|-------|---------|-------|
| 1 | Pohja, autentikointi, 2FA, audit-loki | 2–3 pv |
| 2 | Lead-malli, pipeline, kanban | 2–3 pv |
| 3 | n8n API-integraatio, API-avaimet | 1–2 pv |
| 4 | AI-rikastus, scoring | 2 pv |
| 5 | Sähköposti CRM:stä, Mailgun | 1–2 pv |
| 6 | Raportit, dashboard, analytiikka | 2 pv |
| 7 | Tuotantovalmius, backupit, testit | 1–2 pv |
| **Yht.** | **MVP tuotantoon** | **~2 viikkoa** |
