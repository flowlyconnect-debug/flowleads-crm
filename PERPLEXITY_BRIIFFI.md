# FlowLeads CRM — Perplexity-konteksti

Olen rakentamassa **FlowLeads CRM** -nimistä multi-tenant SaaS CRM -tuotetta. Myyn AI-automatisoitua liidigenerointi-palvelua, ja tämä CRM on osa tuotetta.

## Tuote lyhyesti

n8n-workflow etsii liidejä automaattisesti → lähettää ne CRM:ään API:n kautta → myyjä hallinnoi liidejä CRM:ssä → AI rikastaa liidit automaattisesti taustalla.

Live: `https://flowleads-crm.onrender.com`

## Tech stack

- **Backend:** Python 3 + Flask + Gunicorn
- **Tietokanta:** PostgreSQL + SQLAlchemy + Alembic
- **Autentikointi:** Flask-Login (UI) + API-avain SHA-256 (API) + TOTP 2FA superadminille
- **Sähköposti:** Mailgun API
- **AI-rikastus:** OpenAI API (background thread)
- **Taustatyöt:** APScheduler
- **Frontend:** Jinja2 + Chart.js + SortableJS
- **Väriteemat:** #0B0F1A (sidebar), #1D6BF3 (primary), #38BDF8 (highlight), #F4F6FB (bg)

## Rakenne

```
app/
  auth/         # kirjautuminen, 2FA, salasanan reset
  admin/        # superadmin-paneeli
  api/          # REST API v1
  leads/        # liidien hallinta, pipeline
  users/        # käyttäjät, organisaatiot
  email/        # Mailgun-palvelu, pohjat
  backups/      # pg_dump, palautus
  core/         # security, permissions, audit, errors
  dashboard/    # analytiikka, raportit
  tasks/        # tehtävät, muistutukset (V2)
  sequences/    # sähköpostisekvenssit (V2)
  automations/  # automaatiosäännöt (V2)
  gdpr/         # GDPR-työkalut (V2)
```

## Multi-tenant -malli

Jokainen asiakas = oma `Organization`. Kaikki data scopattu `organization_id`:llä. API-avain on aina sidottu yhteen organisaatioon.

## Keskeiset tietokantamallit

**Organization:** id, name, slug, is_active  
**User:** id, organization_id, email, password_hash, role (superadmin/admin/user/api_client), totp_secret  
**Lead:** id, organization_id, first_name, last_name, email, company, phone, source, pipeline_stage_id, score (0–100), ai_enrichment_status, ai_summary, tags (JSON), gdpr_consent  
**PipelineStage:** id, organization_id, name, order_index, color  
**APIKey:** id, organization_id, name, key_hash (SHA-256), is_active, expires_at  
**AuditLog:** id, user_id, action, target_type, target_id, ip_address, metadata (JSON)  
**Activity:** id, lead_id, type (email/call/note/meeting/stage_change), content  
**Task:** id, lead_id, user_id, title, due_date, priority, is_completed  
**EmailSequence / Step / Enrollment:** automaattiset follow-up sekvenssit  
**Automation / AutomationAction:** "jos liidi ei kontaktissa 14pv → luo tehtävä"  

## n8n → CRM API

```
POST /api/v1/leads
Authorization: Bearer <api_key>

{
  "email": "pakollinen@kentta.fi",
  "first_name": "Matti",
  "last_name": "Meikäläinen",
  "company": "Yritys Oy",
  "phone": "+358401234567",
  "source": "linkedin",
  "notes": "...",
  "tags": ["b2b", "saas"],
  "custom_fields": {}
}
```

Upsert-logiikka: sama email + org → päivitetään. Uusi email → luodaan, stage = "New Lead".

Muut endpointit: `GET /api/v1/health`, `GET /api/v1/me`, `POST /api/v1/leads/bulk`

## Valmiit ominaisuudet (V1, vaiheet 1–7)

Pipeline kanban (drag-drop), liidien CRUD, n8n API-integraatio, API-avainten hallinta, OpenAI AI-rikastus, Mailgun sähköposti + pohjat, Chart.js dashboardit, CSV-vienti, päivittäinen varmuuskopio, audit-loki, multi-tenant, roolit, superadmin 2FA, Docker.

## Kehitteillä (V2, vaiheet 8–12)

Tehtävät ja muistutukset, custom fields, segmentointi, sähköpostisekvenssit, automaatiosäännöt, GDPR-työkalut.

## Suunniteltu (V3, vaiheet 13–18)

Kalenterisynkronointi (Google/Outlook OAuth2), tarjousmoduuli, AI-ennusteet, webhook-integraatiot (Slack/Teams), embeddable web-lomakkeet, Stripe-laskutus (viimeisenä).

## Kehitysworkflow

Claude (suunnittelu) → ChatGPT (prompt-rikastus) → Cursor (koodaus)

## Tietoturva

SHA-256 API-avaimet, CSRF, rate limiting (5/min login, 100/h API), pakollinen 2FA superadminille, kaikki kyselyt org-scopattu, audit-loki kaikista kriittisistä toiminnoista, ei koskaan kovakoodattuja salaisuuksia.
