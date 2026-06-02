# FlowLeads CRM — Projektiyhteenveto uudelle Claude-sessiolle

**Päivitetty:** 2026-05-28  
**Tarkoitus:** Konteksti uudelle Claude-projektille (n8n-agentin kehitys)

---

## Mikä tämä projekti on

**FlowLeads CRM** on multi-tenant SaaS CRM, joka on rakennettu AI-automaattiselle liidigeneroinnille. Matias myy palvelua, jossa n8n-workflow etsii liidejä automaattisesti → liidit tulevat CRM:ään → myyjä hallinnoi niitä CRM:ssä.

**Live-osoite:** `https://flowleads-crm.onrender.com`  
**Projektin kansio:** `C:\Users\matso\Documents\flow-leads`

---

## Tekninen stack

| Osa | Teknologia |
|---|---|
| Backend | Python 3 + Flask + Gunicorn |
| Tietokanta | PostgreSQL + SQLAlchemy ORM |
| Migraatiot | Alembic / Flask-Migrate |
| Autentikointi | Flask-Login (UI) + API-avain SHA-256 (API) |
| 2FA | TOTP (pyotp) — pakollinen superadminille |
| Sähköposti | Mailgun API |
| Taustatyöt | APScheduler |
| Frontend | Jinja2 templating + Chart.js + SortableJS |
| AI-rikastus | OpenAI API (background thread) |
| Salaus | Fernet (OAuth-tokenit) |
| Reverse proxy | Nginx |
| Palvelinhallinta | systemd |
| Kehitystyökalut | Claude (suunnittelu) → ChatGPT (prompt-rikastus) → Cursor (koodaus) |

---

## Arkkitehtuuri

```
n8n workflow
    │
    │  POST /api/v1/leads  (API-avain headerissa: Authorization: Bearer <key>)
    ▼
FlowLeads CRM API
    │
    ├── Lead vastaanotetaan (upsert — sama email = päivitetään)
    ├── AI-rikastus taustalla (OpenAI)
    ├── Tallennus org-scopella
    └── Pipeline-hallinta UI:ssa
```

**Multi-tenant:** Jokainen asiakas = oma `Organization`. Kaikki data scopattu `organization_id`:llä. Ei koskaan luota pelkkään frontend-rajaukseen.

---

## n8n → CRM API-integraatio (TÄRKEÄÄ)

### Endpoint
```
POST https://flowleads-crm.onrender.com/api/v1/leads
```

### Header
```
Authorization: Bearer <api_key>
```
tai
```
X-API-Key: <api_key>
```

### Request body (JSON)
```json
{
  "first_name": "Matti",
  "last_name": "Meikäläinen",
  "email": "matti@yritys.fi",
  "company": "Yritys Oy",
  "phone": "+358401234567",
  "source": "linkedin",
  "notes": "Kiinnostunut AI-automaatiosta",
  "tags": ["b2b", "saas"],
  "custom_fields": {
    "linkedin_url": "https://linkedin.com/in/matti",
    "company_size": "10-50"
  }
}
```

### Upsert-logiikka
- Sama `email` + sama `organization_id` → päivitetään olemassa oleva liidi
- Uusi email → luodaan uusi liidi, asetetaan `pipeline_stage` = "New Lead"

### Response
```json
{
  "success": true,
  "data": {
    "lead_id": 123,
    "action": "created",   // tai "updated"
    "ai_enrichment": "pending"
  },
  "error": null
}
```

### Virheissä
```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "validation_error",
    "message": "email is required"
  }
}
```

---

## API-avainten hallinta

- API-avaimet luodaan CRM:n superadmin-paneelista
- Tallennetaan tietokantaan **SHA-256 hashattuna** (ei koskaan selväkielisenä)
- Voidaan nimetä, poistaa käytöstä, asettaa vanhenemaan
- Rajattu aina yhteen organisaatioon
- Käyttö lokitetaan audit-lokiin

---

## Tietokantamallit (keskeiset)

### Organization
```
id, name, slug, is_active, created_at
```

### User
```
id, organization_id, email, password_hash, role (superadmin/admin/user/api_client),
is_active, totp_secret, totp_enabled, created_at
```

### Lead
```
id, organization_id, first_name, last_name, email, company, phone,
source, pipeline_stage_id, score (0-100), ai_enrichment_status,
ai_summary, tags (JSON array), notes, created_at, updated_at,
gdpr_consent, gdpr_consent_date, is_anonymized
```

### PipelineStage
```
id, organization_id, name, order_index, color
```
Default-stagit: New Lead → Contacted → Qualified → Proposal Sent → Closed Won / Closed Lost

### APIKey
```
id, organization_id, name, key_hash (SHA-256), is_active, expires_at,
last_used_at, created_by, created_at
```

### AuditLog
```
id, organization_id, user_id, action, target_type, target_id,
ip_address, user_agent, metadata (JSON), created_at
```

### Activity
```
id, organization_id, lead_id, user_id, type (email/call/note/meeting/stage_change),
content, created_at
```

### Task (V2)
```
id, organization_id, lead_id, user_id, title, due_date, priority,
is_completed, completed_at, reminder_sent
```

### EmailSequence / EmailSequenceStep / EmailSequenceEnrollment (V2)
- Automaattiset follow-up sekvenssit liideille
- Trigger: lead luotu, stage muuttui, ei kontaktia X päivään

### Automation / AutomationAction / AutomationLog (V2)
- "Jos lead ei ole kontaktissa 14 päivään → luo tehtävä myyjälle"

### Segment (V2)
- Tallennetut suodattimet JSON-logiikalla (AND/OR operaattorit)

### CustomFieldDefinition / CustomFieldValue (V2)
- Org-kohtaiset custom-kentät liideille

---

## Valmiit ominaisuudet (MVP Vaiheet 1–7)

| Ominaisuus | Status |
|---|---|
| Flask-rakenne + moduulit | ✅ Valmis |
| PostgreSQL + migraatiot | ✅ Valmis |
| Multi-tenant (Organization) | ✅ Valmis |
| Käyttäjät + roolit | ✅ Valmis |
| Superadmin 2FA (TOTP) | ✅ Valmis |
| Kirjautuminen + rate limiting | ✅ Valmis |
| Pipeline Kanban (SortableJS drag-drop) | ✅ Valmis |
| Liidien hallinta (CRUD) | ✅ Valmis |
| n8n API-integraatio (/api/v1/leads) | ✅ Valmis |
| API-avainten hallinta | ✅ Valmis |
| OpenAI AI-rikastus (background) | ✅ Valmis |
| Mailgun sähköpostipalvelu | ✅ Valmis |
| Muokattavat sähköpostipohjat | ✅ Valmis |
| Raportointi + Chart.js dashboardit | ✅ Valmis |
| CSV-vienti | ✅ Valmis |
| Päivittäinen varmuuskopio (APScheduler) | ✅ Valmis |
| Audit-loki | ✅ Valmis |
| Docker + docker-compose | ✅ Valmis |

---

## V2 ominaisuudet (Vaiheet 8–12) — kehitteillä

| Ominaisuus | Vaihe |
|---|---|
| Tehtävät ja muistutukset | Vaihe 8 |
| Custom fields + Segmentointi | Vaihe 9 |
| Sähköpostisekvenssit | Vaihe 10 |
| Automaatiosäännöt + notifikaatiot | Vaihe 11 |
| GDPR-työkalut + datan vienti | Vaihe 12 |

---

## V3 ominaisuudet (Vaiheet 13–18) — suunniteltu, ei koodattu

| Ominaisuus | Vaihe |
|---|---|
| Google/Outlook kalenterisynkronointi (OAuth2) | Vaihe 13 |
| Tarjous- ja sopimusmoduuli | Vaihe 14 |
| AI-ennusteet (close probability, revenue forecast) | Vaihe 15 |
| Webhook-integraatiot (Slack, Teams) | Vaihe 16 |
| Web-lomakkeet (embeddable widget) | Vaihe 17 |
| Stripe-laskutus (trial + feature gates) | Vaihe 18 ← VIIMEINEN |

---

## Brändäys ja UI

**Flowly Solutions -värit:**
```css
--color-sidebar-bg:    #0B0F1A;   /* tumma sidebar */
--color-primary:       #1D6BF3;   /* sininen primary */
--color-highlight:     #38BDF8;   /* vaalea sininen highlight */
--color-content-bg:    #F4F6FB;   /* sisältöalue tausta */
```

**UI-tyylit:**
- Sidebar navigaatio (ei top nav)
- Solid dark metric-kortit dashboardilla
- Pipeline: sininen column header + valkoinen kortti vasemmalla väriaksenttiviivalla
- Chart.js kaikki visualisoinnit
- Heroicons SVG-ikonit
- Inter-fontti (Google Fonts)
- Command Palette (Cmd+K)
- Drawer-pattern (slide-in paneelit, ei full-page navigaatio)

---

## Tiedostot projektikansiossa

| Tiedosto | Sisältö |
|---|---|
| `CRM_KEHITYSSUUNNITELMA.md` | MVP Vaiheet 1–7, täydet Cursor-promptit |
| `CRM_V2_ARVIO_JA_SUUNNITELMA.md` | V2 Vaiheet 8–12 + feature-arvio |
| `CRM_V3_SUUNNITELMA.md` | V3 Vaiheet 13–18 |
| `CRM_UI_REDESIGN.md` | UI/UX redesign vaiheet 1–5 |
| `CRM_UI_PIPELINE_DASHBOARD_KALENTERI.md` | Pipeline/Dashboard/Kalenteri-näkymät tarkasti |
| `CRM_UX_VAIHEET.md` | UX-parannukset vaiheet 1–5 (viimeisin) |
| `CHATGPT_BRIIFFI.md` | ChatGPT:n rooli prompt-rikastajana |
| `PROJEKTI_YHTEENVETO.md` | Tämä tiedosto |

---

## Kehitysworkflow

```
Claude (suunnittelu + vaiheet)
    ↓
ChatGPT (rikastaa Cursor-promptit: lisää edge caset, validoinnit, testiskenaariot)
    ↓
Cursor (koodaa)
```

**ChatGPT ei saa:** muuttaa tech stackia, lisätä epäscoupattuja featureja, muuttaa mallien rakennetta, kirjoittaa oikeaa koodia.

---

## Tietoturvaperiaatteet

- Ei koskaan kovakoodattuja salaisuuksia — kaikki `.env`:stä
- API-avaimet aina SHA-256 hashattuna kannassa
- Kaikki kyselyt scopattu `organization_id`:llä
- CSRF-suojaus kaikissa lomakkeissa
- Rate limiting: kirjautuminen 5/min, API 100/h
- Superadmin-toiminnot vaativat 2FA-vahvistuksen
- Audit-lokiin kirjataan kaikki kriittiset toiminnot
- SQL-injektio estetty ORM:n kautta
- Session cookie: Secure + HttpOnly + SameSite=Lax tuotannossa

---

## n8n-agentin kehittämistä varten — avaintiedot

Jos rakennat n8n-agenttia joka syöttää liidejä tähän CRM:ään:

1. **API-endpoint:** `POST /api/v1/leads`
2. **Auth:** `Authorization: Bearer <api_key>` (avain luodaan CRM superadmin-paneelista)
3. **Pakollinen kenttä:** `email`
4. **Upsert:** sama email = päivitetään automaattisesti
5. **Org-scoping:** API-avain on aina sidottu yhteen organisaatioon — avain määrittää kenelle liidi menee
6. **AI-rikastus:** tapahtuu automaattisesti CRM:ssä vastaanoton jälkeen, n8n:n ei tarvitse tehdä mitään
7. **Bulk import:** `POST /api/v1/leads/bulk` (array of leads, max 100 kerralla)
8. **Status check:** `GET /api/v1/health` — palauttaa `{"success": true, "data": {"status": "ok"}}`
9. **Oma data:** `GET /api/v1/me` — palauttaa API-avaimen tiedot ja organisaation

**Suositellut source-arvot n8n:stä:**
`linkedin`, `website`, `referral`, `cold_email`, `apollo`, `hunter`, `manual`, `n8n`
