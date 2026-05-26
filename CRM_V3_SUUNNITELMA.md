# FlowLeads CRM — V3 Kehityssuunnitelma

**Edellytys:** MVP (Vaiheet 1–7) ja V2 (Vaiheet 8–12) on valmis ja tuotannossa.  
**Tavoite:** Kasvuvaihe — integraatiot, tarjoukset, ennustava AI, laskutus.  
**Periaate:** Jokainen V3-vaihe voidaan tehdä itsenäisesti ilman muita V3-vaiheita.

---

## Mitä V3 sisältää

| Vaihe | Sisältö | Prioriteetti | Arvio |
|---|---|---|---|
| 13 | Kalenteriintegraatio (Google + Outlook) | Korkea | 3 pv |
| 14 | Tarjoukset ja sopimukset | Korkea | 3 pv |
| 15 | Ennustava analytiikka (AI close probability) | Keski | 2 pv |
| 16 | Webhook-ilmoitukset (Slack, Teams, custom) | Keski | 2 pv |
| 17 | Verkkosivulomakeintegraatio | Korkea | 2 pv |
| 18 | Laskutusintegraatio (Stripe — SaaS billing) | Korkea | 3 pv |

---

## VAIHE 13 — Kalenteriintegraatio
**Arvio:** 3 päivää  
**Tavoite:** Tapaamiset synkronoituvat Google Calendarin / Outlookin kanssa. Myyjä näkee päivän kalenterin CRM:ssä.

### Cursor-prompt

```
Add calendar integration (Google Calendar and Microsoft Outlook) to FlowLeads CRM:

SUPPORTED PROVIDERS: google, microsoft

NEW MODELS:

CalendarConnection:
  id, user_id (FK), organization_id (FK)
  provider (enum: google, microsoft)
  access_token (encrypted), refresh_token (encrypted), token_expires_at
  calendar_id (string, selected calendar)
  sync_enabled (bool, default True)
  last_synced_at, created_at

  Use Fernet encryption (cryptography library) for tokens.
  NEVER store tokens in plain text.
  Encryption key from env: CALENDAR_ENCRYPTION_KEY

CalendarEvent:
  id, user_id (FK), lead_id (FK nullable), organization_id (FK)
  external_event_id (string, provider's event ID)
  provider (string)
  title, description (text nullable)
  start_at, end_at, location (string nullable)
  meeting_url (string nullable)  # Google Meet / Teams link
  attendees (JSON array of emails)
  is_synced (bool)
  created_at, updated_at

OAUTH2 FLOW:

Google Calendar:
  Scopes: https://www.googleapis.com/auth/calendar.events
  ENV: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI
  Library: google-auth, google-auth-oauthlib, google-api-python-client

Microsoft (Outlook/Teams):
  Scopes: Calendars.ReadWrite, OnlineMeetings.ReadWrite
  ENV: MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_REDIRECT_URI
  Library: msal

ROUTES:

OAuth connection:
  GET  /settings/calendar                       # Calendar settings page
  GET  /settings/calendar/connect/google        # Start Google OAuth2 flow
  GET  /settings/calendar/callback/google       # Google OAuth2 callback
  GET  /settings/calendar/connect/microsoft     # Start Microsoft OAuth2 flow
  GET  /settings/calendar/callback/microsoft    # Microsoft OAuth2 callback
  POST /settings/calendar/disconnect            # Revoke and delete connection
  GET  /settings/calendar/test                  # Test connection, list calendars

Events:
  GET  /calendar                                # My calendar view
  POST /leads/<id>/meetings/schedule            # Schedule meeting linked to lead
  GET  /leads/<id>/meetings                     # Meetings for this lead
  DELETE /calendar/events/<id>                  # Cancel event (also cancels in provider)

CALENDAR SERVICE (app/calendar/services.py):

CalendarService.create_event(user_id, lead_id, title, start_at, end_at, description, attendees):
  1. Create CalendarEvent in DB
  2. Push to provider (Google or Microsoft based on user's connection)
  3. If provider supports it, create video meeting link (Google Meet / Teams)
  4. Send calendar invites to attendees (provider handles this)
  5. Log activity: meeting_scheduled

CalendarService.sync_upcoming(user_id):
  # Pull events from provider for next 7 days
  # Match to leads by attendee email
  # Update CalendarEvent records
  # Run hourly by APScheduler for connected users

CalendarService.refresh_token_if_needed(connection):
  # Check token expiry, refresh if < 5 min remaining
  # Called before every API call to provider

CalendarService.cancel_event(event_id, user_id):
  # Delete from provider calendar
  # Mark CalendarEvent as cancelled
  # Log activity: meeting_cancelled

UI:

Schedule Meeting modal (from lead detail):
  - Title (default: "Tapaaminen — {lead.company}")
  - Date + time picker for start
  - Duration selector: 15/30/45/60/90 min
  - Description (pre-fill with lead summary)
  - Attendees: lead email pre-filled + can add more
  - Video meeting toggle (creates Google Meet / Teams link)
  - Location field (alternative to video)
  - Shows provider icon (Google/Microsoft) based on user's connection

Lead detail — Meetings tab:
  - Upcoming meetings: title, date, attendees, meeting link button
  - Past meetings (collapsed)
  - "Aikatauluta tapaaminen" button

My Calendar (/calendar):
  - Week view of upcoming events (simple table, not a full calendar widget)
  - Each event shows: title, time, lead name (linked), meeting URL button
  - "Tänään" section at top
  - Events synced from provider shown alongside CRM-created events

Dashboard widget:
  - "Tulevat tapaamiset" — next 3 meetings

ENV VARIABLES (add to .env.example):
  CALENDAR_ENCRYPTION_KEY=
  GOOGLE_CLIENT_ID=
  GOOGLE_CLIENT_SECRET=
  GOOGLE_REDIRECT_URI=
  MICROSOFT_CLIENT_ID=
  MICROSOFT_CLIENT_SECRET=
  MICROSOFT_REDIRECT_URI=

Write tests for:
  - Token encryption/decryption
  - Event creation in DB
  - Token refresh logic
  - Event linked to lead and activity logged
  - Disconnection deletes tokens
```

### ✅ Vaiheen 13 hyväksymiskriteerit
- [ ] Google OAuth2 -yhteys toimii (token tallennetaan enkryptoituna)
- [ ] Tapaaminen luodaan liidin profiilista → näkyy Google Calendarissa
- [ ] Google Meet -linkki generoidaan automaattisesti
- [ ] Tulevat tapaamiset näkyvät dashboardilla
- [ ] Tokenin automaattinen uusiminen toimii
- [ ] Yhteyden katkaisu poistaa tokenin tietokannasta

---

## VAIHE 14 — Tarjoukset ja sopimukset
**Arvio:** 3 päivää  
**Tavoite:** Luo, lähetä ja seuraa tarjouksia suoraan CRM:stä. Tiedä milloin asiakas avaa tarjouksen.

### Cursor-prompt

```
Add proposal and contract management to FlowLeads CRM:

NEW MODELS:

Proposal:
  id, organization_id (FK), lead_id (FK), created_by (FK → User)
  title, reference_number (auto-generated: FLW-2025-001)
  status (enum: draft, sent, viewed, accepted, declined, expired)
  valid_until (date)
  currency (string, default EUR)
  subtotal, discount_percent, discount_amount, tax_percent, total
  notes (text nullable)
  sent_at, viewed_at, accepted_at, declined_at
  view_token (string, unique — for public proposal URL)
  created_at, updated_at

ProposalLineItem:
  id, proposal_id (FK), order_index (int)
  description (string), quantity (decimal), unit_price (decimal)
  discount_percent (decimal, default 0), total (decimal)

ProposalTemplate:
  id, organization_id (FK), name
  default_valid_days (int, default 30)
  default_notes (text), default_tax_percent (decimal)
  header_html (text)   # company header/logo area
  footer_html (text)   # terms, contact info
  created_by, created_at

PROPOSAL SERVICE (app/proposals/services.py):

ProposalService.create(lead_id, data, user_id, organization_id) → Proposal
ProposalService.calculate_totals(proposal) → updates subtotal, discount, tax, total
ProposalService.send(proposal_id, user_id):
  - Set status=sent, sent_at=now
  - Generate view_token (secrets.token_urlsafe(32))
  - Send email to lead with proposal link
  - Log activity: proposal_sent

ProposalService.record_view(view_token):
  - Find proposal by view_token
  - If first view: set status=viewed, viewed_at=now
  - Trigger automation: email_opened equivalent → AutomationEngine.trigger('proposal_viewed')
  - Log activity: proposal_viewed
  - Return proposal data for public view

ProposalService.accept(view_token, signature_name):
  - Set status=accepted, accepted_at=now
  - Store signature_name
  - Move lead stage to "Won" (if configured in org settings)
  - Notify assigned user via Notification + email
  - Log audit: proposal_accepted

ProposalService.decline(view_token, reason):
  - Set status=declined
  - Notify assigned user
  - Log activity: proposal_declined

ROUTES:

CRM routes:
  GET  /proposals                           # List all proposals (org)
  POST /proposals                           # Create proposal
  GET  /proposals/<id>                      # Edit proposal
  PUT  /proposals/<id>                      # Save proposal
  DELETE /proposals/<id>                    # Delete draft only
  POST /proposals/<id>/send                 # Send to lead
  GET  /leads/<id>/proposals                # Proposals for this lead
  POST /proposals/<id>/duplicate            # Clone proposal

Public routes (no auth required):
  GET  /p/<view_token>                      # Public proposal view
  POST /p/<view_token>/accept               # Accept proposal
  POST /p/<view_token>/decline              # Decline with reason

Settings:
  GET/POST /settings/proposals              # Proposal template settings

UI:

Proposal Editor (/proposals/<id>):
  - Two-column layout: edit form LEFT, live preview RIGHT
  - Company header (from template)
  - Line items table: description, qty, unit price, discount%, total
    * Add row button
    * Drag to reorder
    * Row totals calculate automatically
  - Subtotal, discount, tax, TOTAL shown live
  - Valid until date picker
  - Notes field
  - "Preview" button → full-page preview
  - "Lähetä tarjous" button → confirm modal with lead's email shown

Public Proposal Page (/p/<view_token>):
  - Clean, professional layout (no CRM navigation)
  - Company logo/header from template
  - Proposal reference number + date + valid until
  - Line items table (read-only)
  - Total summary
  - Notes
  - TWO buttons: "Hyväksy tarjous" | "Hylkää"
  - Accept modal: "Allekirjoita nimesi" text field + confirmation
  - Decline modal: reason text field (optional)
  - Footer with company contact info

Proposal List (/proposals):
  - Table: reference, lead, title, total, status badge, sent date, valid until
  - Status badges with colors: draft(grey), sent(blue), viewed(yellow), accepted(green), declined(red), expired(orange)
  - Filter by status
  - Expiry alerts: highlight proposals valid_until within 3 days

Lead detail:
  - "Tarjoukset" tab
  - Create proposal button
  - List of proposals with status
  - Total value of accepted proposals shown

Dashboard:
  - "Avoimet tarjoukset" card: count + total value
  - "Hyväksytty tällä kuulla" card: count + total value
  - Recent proposal activity in feed

AUTOMATION TRIGGERS (add to AutomationEngine):
  proposal_viewed    # Lead opened the proposal
  proposal_accepted  # Lead accepted
  proposal_declined  # Lead declined

REFERENCE NUMBER GENERATION:
  Format: FLW-{YEAR}-{SEQUENCE}
  Sequence per organization, resets each year.
  Store in organization settings: proposal_sequence_{year}

Write tests for:
  - Proposal creation and total calculation
  - Send creates view_token
  - Public view records view event
  - Accept changes status and notifies user
  - Expired proposals (valid_until in past) flagged correctly
  - view_token is unguessable (length check)
  - Unauthenticated access to /p/<token> works
  - Cross-tenant: view_token from org A not usable by org B
```

### ✅ Vaiheen 14 hyväksymiskriteerit
- [ ] Tarjous luodaan rivinimikkeillä, summat laskevat automaattisesti
- [ ] Tarjouksen lähetys luo uniikin julkisen linkin
- [ ] Julkinen sivu toimii ilman kirjautumista
- [ ] Ensimmäinen avaus → status "viewed" + myyjälle ilmoitus
- [ ] Hyväksyminen → status "accepted" + liidi siirtyy "Won"-vaiheeseen
- [ ] Automaatiotriggerit laukeavat oikein (proposal_viewed, proposal_accepted)

---

## VAIHE 15 — Ennustava analytiikka
**Arvio:** 2 päivää  
**Tavoite:** AI arvioi jokaisen kaupan todennäköisyyden, ennustaa kuukauden myynnin

### Cursor-prompt

```
Add predictive analytics and deal probability scoring to FlowLeads CRM:

CONCEPT:
Use OpenAI to analyze lead signals and predict close probability.
Train on closed deals (won/lost) in the organization's own history.
Show probability on pipeline cards and forecast total expected revenue.

NEW FIELDS (add to existing models):

Lead (add):
  close_probability (decimal 0.0-1.0, nullable)
  probability_updated_at (datetime nullable)
  expected_value (decimal nullable)  # if org uses deal values
  deal_value (decimal nullable)      # manually set deal value

PredictionLog:
  id, lead_id (FK), organization_id (FK)
  probability (decimal), signals (JSON)
  model_version (string), created_at

PREDICTION SERVICE (app/analytics/prediction.py):

PredictionService.predict_lead(lead_id) → probability (float 0.0-1.0):
  """
  Uses OpenAI to score close probability based on lead signals.
  """
  
  Signals collected:
  - Lead score (from AI enrichment)
  - Current pipeline stage + days in stage
  - Number of activities (notes, calls, emails)
  - Email open/click rate
  - Days since created
  - Has proposal (and proposal status)
  - Has had meeting
  - Response rate (emails sent vs replied)
  - Tags (industry, size)
  - Company size estimate (from AI enrichment)
  - Days since last contact
  
  OpenAI prompt:
  ---
  You are a B2B sales analyst. Based on these signals, estimate the probability
  that this lead will become a paying customer within 90 days.
  
  Lead signals:
  {json.dumps(signals, indent=2)}
  
  Historical context for this organization:
  - Average conversion rate: {org_stats.conversion_rate}%
  - Average days to close: {org_stats.avg_days_to_close}
  - This lead has been in pipeline: {days_in_pipeline} days
  
  Return ONLY a JSON object:
  {
    "probability": 0.73,
    "key_positive_signals": ["has had meeting", "email opened 3 times"],
    "key_risk_signals": ["no proposal sent yet", "no contact in 7 days"],
    "recommendation": "Send proposal this week — lead shows strong buying signals"
  }
  ---

PredictionService.predict_batch(organization_id):
  # Run prediction for all active leads in org
  # Called by APScheduler weekly (Sunday night)
  # Also callable manually from admin

PredictionService.calculate_forecast(organization_id, period_days=30):
  """
  Revenue forecast = sum(lead.deal_value * lead.close_probability) 
  for all active leads expected to close within period_days
  """
  Returns: {
    "expected_revenue": 45000,
    "best_case": 78000,       # sum of deal values where probability > 0.5
    "worst_case": 12000,       # sum of deal values where probability > 0.8
    "leads_count": 23,
    "by_stage": {...}
  }

PredictionService.get_historical_accuracy(organization_id):
  # Compare past predictions to actual outcomes
  # "Our predictions were X% accurate last quarter"

ROUTES:
  POST /leads/<id>/predict              # Refresh prediction for one lead (admin)
  POST /admin/predictions/run-batch     # Trigger batch prediction (superadmin)
  GET  /reports/forecast                # Revenue forecast page

UI CHANGES:

Pipeline kanban cards (update):
  - Show probability badge: e.g. "73%" with color: <30% red, 30-60% yellow, >60% green
  - Show deal value if set
  - Sort column by probability (button)

Lead detail page:
  - New "AI-ennuste" section:
    * Close probability gauge (visual: 0-100% bar)
    * "Positiiviset signaalit" list
    * "Riskisignaalit" list
    * "Suositus" text from AI
    * Deal value field (editable)
    * Expected value = deal_value × probability
    * Last updated timestamp + "Päivitä nyt" button

Revenue Forecast page (/reports/forecast):
  - Header: "Odotettu myynti seuraavat 30 päivää: €45,000"
  - Three scenarios:
    * Todennäköinen: €45,000 (probability-weighted)
    * Optimistinen: €78,000 (all >50% close)
    * Konservatiivinen: €12,000 (only >80% close)
  - Bar chart: Expected revenue by pipeline stage
  - Table: Active deals sorted by probability × value (highest first)
    Columns: Lead, Company, Stage, Probability, Deal Value, Expected, Last Contact
  - Date range: next 30/60/90 days toggle

Dashboard:
  - "Myyntiennuste" card: expected revenue this month
  - Add "Korkean potentiaalin liidit" widget: top 5 by probability × value

DEAL VALUE WORKFLOW:
  - Add "Kaupan arvo" field to lead edit form (optional, decimal, currency label)
  - Org setting: default_currency (EUR/USD/SEK etc.)
  - Lead list: add optional "Arvo" column
  - Pipeline: show total value per stage in column header

Write tests for:
  - Signals collected correctly from lead data
  - Probability stored and timestamped
  - Forecast calculation (weighted sum)
  - Best/worst case scenarios
  - Batch prediction runs without crashing on leads with minimal data
```

### ✅ Vaiheen 15 hyväksymiskriteerit
- [ ] Todennäköisyys näkyy pipeline-kortissa värikoodattuna
- [ ] Lead-profiilissa näkyy positiiviset ja riskisignaalit
- [ ] Ennustesivu näyttää kolme skenaariota (tod./opt./kons.)
- [ ] Viikoittainen batchajo päivittää kaikki todennäköisyydet
- [ ] Kaupan arvo × todennäköisyys = odotettu arvo laskee oikein

---

## VAIHE 16 — Webhook-ilmoitukset (Slack, Teams, custom)
**Arvio:** 2 päivää  
**Tavoite:** Tärkeät CRM-tapahtumat lähettävät ilmoituksen Slackiin, Teamsiin tai mihin tahansa webhookiin

### Cursor-prompt

```
Add outbound webhook notifications to FlowLeads CRM, with built-in Slack and Teams support:

NEW MODELS:

WebhookEndpoint:
  id, organization_id (FK), name
  provider (enum: slack, teams, custom)
  url (string, encrypted at rest)
  secret (string, encrypted — used to sign outgoing payloads)
  is_active (bool)
  events (JSON array of event names to send)
  created_by (FK → User), created_at
  last_triggered_at, success_count, failure_count

WebhookDelivery:
  id, endpoint_id (FK), organization_id (FK)
  event_type (string), payload (JSON)
  response_status (int nullable), response_body (text nullable)
  delivered_at, duration_ms (int)
  status (enum: pending, delivered, failed)
  retry_count (int, default 0)

SUPPORTED EVENTS:
  lead.created               # New lead in CRM
  lead.stage_changed         # Lead moved to different stage
  lead.score_updated         # AI score changed significantly (>10 points)
  lead.assigned              # Lead assigned to user
  task.overdue               # Task is overdue
  proposal.viewed            # Lead opened proposal
  proposal.accepted          # Lead accepted proposal
  proposal.declined          # Lead declined proposal
  sequence.completed         # Lead completed email sequence
  lead.high_score            # Score crossed 80 for the first time

WEBHOOK SERVICE (app/webhooks/services.py):

WebhookService.dispatch(event_type, payload, organization_id):
  # Called after events happen throughout the codebase
  # Find active endpoints for org that subscribe to this event
  # Build formatted payload
  # Send via HTTP POST (timeout 10s)
  # Log to WebhookDelivery
  # Retry once after 5 min if failed (APScheduler)

WebhookService.format_payload(event_type, raw_payload, provider):
  # For Slack: format as Slack Block Kit message
  # For Teams: format as Teams Adaptive Card
  # For custom: standard JSON envelope

SLACK PAYLOAD FORMAT:
{
  "blocks": [
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Uusi liidi: Acme Corp* 🎯\n*Kontakti:* John Doe, CEO\n*Score:* 82/100\n*Lähde:* n8n"
      }
    },
    {
      "type": "actions",
      "elements": [{
        "type": "button",
        "text": {"type": "plain_text", "text": "Avaa CRM:ssä"},
        "url": "https://app.flowleads.fi/leads/123"
      }]
    }
  ]
}

TEAMS PAYLOAD FORMAT:
Adaptive Card with lead info + "Avaa CRM:ssä" button.

CUSTOM PAYLOAD FORMAT:
{
  "event": "lead.created",
  "timestamp": "2025-01-15T14:30:00Z",
  "organization_id": "...",
  "data": {
    "lead": {lead object},
    "triggered_by": {user object or "system"}
  }
}

PAYLOAD SIGNING (custom endpoints):
  HMAC-SHA256 signature in header: X-FlowLeads-Signature: sha256={hex_digest}
  Signature = HMAC-SHA256(secret, raw_body)
  Receivers can verify authenticity.

ROUTES:
  GET  /settings/webhooks                   # List webhook endpoints
  POST /settings/webhooks                   # Add endpoint
  GET  /settings/webhooks/<id>/edit         # Edit endpoint
  PUT  /settings/webhooks/<id>              # Save changes
  DELETE /settings/webhooks/<id>            # Delete
  POST /settings/webhooks/<id>/test         # Send test payload
  GET  /settings/webhooks/<id>/deliveries   # Delivery log

UI:

Webhook Settings (/settings/webhooks):
  - List of endpoints: name, provider icon, events count, status, last triggered
  - Add endpoint modal:
    * Name
    * Provider: Slack / Teams / Custom (changes form)
    * For Slack: "Slack Incoming Webhook URL" field + link to Slack docs
    * For Teams: "Teams Incoming Webhook URL" field + link to Teams docs
    * For Custom: URL + secret field
    * Events checkboxes (grouped by category)
    * Test button → sends sample payload immediately

  Slack setup guide shown inline:
    "1. Go to Slack → Apps → Incoming Webhooks
     2. Add to channel, copy URL
     3. Paste URL here"

Delivery Log (/settings/webhooks/<id>/deliveries):
  - Table: event, status (green/red), response code, timestamp, duration
  - Expand row to see full payload sent and response received
  - "Retry" button for failed deliveries

ENV:
  WEBHOOK_ENCRYPTION_KEY=   # For encrypting stored URLs and secrets

Write tests for:
  - Webhook delivery on lead.created event
  - Slack payload format correct
  - Signature generation and verification
  - Failed delivery logged
  - Test endpoint sends sample payload
  - Inactive endpoint does not receive events
  - Cross-tenant: org A webhook not triggered by org B events
```

### ✅ Vaiheen 16 hyväksymiskriteerit
- [ ] Slack-webhook lähettää ilmoituksen kun uusi liidi tulee n8n:stä
- [ ] Tarjouksen hyväksyminen → Slack-ilmoitus myyjälle
- [ ] Testipainike lähettää oikean muotoisen testipayloadin
- [ ] Toimitushistoria näyttää HTTP-statuskoodin ja vastauksen
- [ ] Epäonnistunut toimitus yritetään uudelleen kerran
- [ ] Webhook-URL tallennetaan enkryptoituna

---

## VAIHE 17 — Verkkosivulomakeintegraatio
**Arvio:** 2 päivää  
**Tavoite:** Asiakkaan verkkosivulta tuleva lomake luo liidin suoraan CRM:ään ilman n8n:ää

### Cursor-prompt

```
Add website form integration to FlowLeads CRM — embed forms that capture leads directly:

CONCEPT:
Organizations get an embeddable JavaScript widget that renders a contact form.
Submissions create leads directly in FlowLeads via a public API endpoint.
No login or API key required for submissions — uses form_token instead.

NEW MODELS:

WebForm:
  id, organization_id (FK), name
  form_token (string, unique — public identifier for form)
  title, description (shown above form)
  submit_button_text (default: "Lähetä")
  success_message (default: "Kiitos! Otamme yhteyttä pian.")
  fields (JSON — ordered list of field configs)
  default_stage_id (FK → PipelineStage, nullable)
  default_assigned_to (FK → User, nullable)
  auto_enroll_sequence_id (FK → EmailSequence, nullable)
  notify_users (JSON array of user_ids to notify on submission)
  is_active (bool)
  submission_count (int)
  created_by, created_at

WebFormSubmission:
  id, form_id (FK), organization_id (FK), lead_id (FK nullable)
  raw_data (JSON), ip_address, user_agent
  status (enum: processed, duplicate, spam, failed)
  created_at

FIELD CONFIG (in WebForm.fields JSON):
[
  {"key": "first_name", "label": "Etunimi", "type": "text", "required": true},
  {"key": "last_name", "label": "Sukunimi", "type": "text", "required": true},
  {"key": "email", "label": "Sähköposti", "type": "email", "required": true},
  {"key": "company", "label": "Yritys", "type": "text", "required": false},
  {"key": "phone", "label": "Puhelin", "type": "tel", "required": false},
  {"key": "message", "label": "Viesti", "type": "textarea", "required": false},
  {"key": "custom_budget", "label": "Budjetti", "type": "select",
   "options": ["<5k", "5-20k", ">20k"], "required": false}
]
Supported field types: text, email, tel, number, textarea, select, checkbox

PUBLIC SUBMISSION ENDPOINT:
  POST /api/public/forms/<form_token>/submit
  
  No authentication required.
  Rate limit: 10 submissions per IP per hour.
  CORS: Allow all origins (form is embedded on customer sites).
  
  Logic:
  1. Validate form_token → find active WebForm
  2. Validate required fields
  3. Spam check: same email + same form within 5 minutes → reject as duplicate
  4. Create/upsert Lead (same as API upsert logic)
  5. Set source="webform", source_ref=form.name
  6. Apply default_stage, default_assigned_to from form config
  7. Auto-enroll in sequence if configured
  8. Trigger automations: AutomationEngine.trigger('lead_created')
  9. Send notification to notify_users
  10. Log WebFormSubmission
  11. Return: {"success": true, "message": form.success_message}
  
  On error: {"success": false, "error": {"code": "...", "message": "..."}}
  NEVER reveal internal errors to public endpoint.

EMBED WIDGET (static JS file served by Flask):
  GET /static/forms/embed.js

  The embed.js script:
  - Self-contained, no jQuery or external dependencies
  - Renders form inside target div
  - Submits via fetch() to POST /api/public/forms/<token>/submit
  - Shows loading state during submit
  - Replaces form with success_message on success
  - Shows inline field errors on validation failure
  - Respects field order from fields config
  - Lightweight (~8KB minified)

Website embedding instructions (shown in CRM):
  Option 1 — Script tag:
  <div id="flowleads-form"></div>
  <script src="https://app.flowleads.fi/static/forms/embed.js"
          data-form-token="abc123"
          data-target="#flowleads-form"></script>
  
  Option 2 — iFrame:
  <iframe src="https://app.flowleads.fi/forms/abc123/embed"
          width="100%" height="500" frameborder="0"></iframe>

  iFrame route:
  GET /forms/<form_token>/embed   # Standalone HTML page for iframe use

ROUTES:
  GET  /forms                              # List forms
  POST /forms                              # Create form
  GET  /forms/<id>/edit                    # Edit form
  PUT  /forms/<id>                         # Save form
  DELETE /forms/<id>                       # Delete (soft — preserve submissions)
  GET  /forms/<id>/submissions             # View submissions
  GET  /forms/<id>/embed-code             # Show copy-paste embed code
  GET  /forms/<form_token>/embed           # Public iframe page (no auth)

UI:

Form Builder (/forms/<id>/edit):
  - Form name + settings at top
  - Field list with drag-to-reorder
  - Add field button: choose type, set label, required toggle, options (for select)
  - Live preview panel on right side
  - Settings tab:
    * Default stage
    * Assign to user
    * Auto-enroll sequence
    * Notify users
    * Success message
  - "Embed-koodi" tab: shows ready-to-paste HTML snippets

Form Submissions (/forms/<id>/submissions):
  - Table: submission time, name, email, company, IP, status
  - Click row → see all submitted fields
  - Link to created lead

Dashboard widget:
  - "Lomakelähetykset tänään" count

Write tests for:
  - Form submission creates lead
  - Duplicate submission (same email < 5 min) rejected
  - Required field missing → validation error
  - Rate limit enforced per IP
  - Inactive form returns 404
  - Auto-enrollment in sequence triggered
  - Notification sent to configured users
  - embed.js served as static file
```

### ✅ Vaiheen 17 hyväksymiskriteerit
- [ ] Lomake luodaan CRM:ssä, embed-koodi kopioitavissa
- [ ] Lomakkeen täyttö verkkosivulla luo liidin CRM:ään
- [ ] Duplikaattien esto toimii (sama sähköposti 5 min sisällä)
- [ ] Rate limiting toimii per IP (10/h)
- [ ] Automaattinen sekvenssiinkirjautuminen toimii
- [ ] iFrame-versio toimii itsenäisesti

---

## VAIHE 18 — Laskutusintegraatio (Stripe SaaS billing)
**Arvio:** 3 päivää  
**Tavoite:** FlowLeads CRM myy itse itseään — asiakkaat tilaavat ja maksavat suoraan. Superadmin hallinnoi tilauksia.

### Cursor-prompt

```
Add Stripe billing to FlowLeads CRM to monetize the SaaS product itself:

CONCEPT:
FlowLeads CRM is now a paid SaaS. Organizations subscribe via Stripe.
Superadmin manages plans. Organizations see their billing in settings.

PRICING PLANS (seeded data):
  Starter:    €29/mo  — 1 user, 500 leads, basic features
  Growth:     €79/mo  — 5 users, 5000 leads, sequences + automations
  Pro:        €199/mo — unlimited users, unlimited leads, all features
  Enterprise: Custom  — contact sales

NEW MODELS:

Plan:
  id, name, stripe_price_id (monthly), stripe_price_id_yearly (nullable)
  price_monthly, price_yearly
  max_users (int, -1 = unlimited), max_leads (int, -1 = unlimited)
  features (JSON: list of feature flags)
  is_active (bool), is_public (bool)

Subscription:
  id, organization_id (FK, unique)
  plan_id (FK), stripe_subscription_id, stripe_customer_id
  status (enum: trialing, active, past_due, cancelled, paused)
  trial_ends_at, current_period_start, current_period_end
  cancelled_at, cancel_at_period_end (bool)
  created_at, updated_at

STRIPE INTEGRATION:
  ENV: STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, STRIPE_WEBHOOK_SECRET
  Library: stripe (official Python SDK)

BILLING SERVICE (app/billing/services.py):

BillingService.create_checkout_session(organization_id, plan_id, interval):
  # Create Stripe Checkout session
  # On success → redirect to /billing/success
  # On cancel → redirect to /billing/cancel

BillingService.create_customer_portal_session(organization_id):
  # Stripe Customer Portal for managing payment method, invoices, cancellation

BillingService.handle_webhook(event):
  # Process Stripe webhook events:
  # customer.subscription.created → activate subscription
  # customer.subscription.updated → update plan/status
  # customer.subscription.deleted → cancel/downgrade
  # invoice.payment_failed → set status=past_due, notify admin
  # invoice.payment_succeeded → extend period, clear past_due
  # customer.subscription.trial_will_end → send reminder 3 days before

BillingService.check_limit(organization_id, resource_type):
  # resource_type: 'users', 'leads'
  # Returns True if within plan limits, False if exceeded
  # Called before creating users or leads

FEATURE FLAGS:
  Check plan features before allowing access:
  - sequences_enabled (Growth+)
  - automations_enabled (Growth+)
  - ai_enrichment_enabled (Starter gets 50/mo, Growth 500/mo, Pro unlimited)
  - proposals_enabled (Growth+)
  - custom_fields_enabled (Growth+)
  - webhooks_enabled (Pro+)
  - api_access_enabled (Growth+)

  FeatureGate.require(organization_id, feature_name) → raises FeatureNotAvailable if locked
  Use @require_feature('sequences_enabled') decorator on routes.

ROUTES:

Public:
  GET  /pricing                         # Public pricing page
  POST /billing/checkout/<plan_id>      # Start Stripe checkout

Authenticated:
  GET  /billing                         # Billing dashboard
  GET  /billing/success                 # Post-checkout success page
  POST /billing/portal                  # Redirect to Stripe Customer Portal
  GET  /billing/invoices                # Invoice history (from Stripe)

Webhook:
  POST /api/webhooks/stripe             # Stripe webhook endpoint (no auth, verified by signature)

Superadmin:
  GET  /admin/billing                   # All org subscriptions
  GET  /admin/billing/<org_id>          # Org billing detail
  POST /admin/billing/<org_id>/grant    # Manually grant plan (for enterprise/demo)
  GET  /admin/plans                     # Manage plans
  POST /admin/plans                     # Create plan (links to Stripe price)

TRIAL SYSTEM:
  New organizations get 14-day trial of Growth plan automatically.
  After trial: downgrade to Starter OR prompt to subscribe.
  Trial banner shown in UI: "Kokeilujakso päättyy X päivän kuluttua. Tilaa nyt."

LIMIT ENFORCEMENT:
  When limit reached:
  - Lead creation: HTTP 402 with {"error": {"code": "lead_limit_reached", "message": "..."}}
  - API response includes: upgrade_url
  - UI shows upgrade prompt instead of error

  Grace period: 7 days over limit before hard block (to avoid interrupting customers mid-month)

BILLING UI (/billing):
  - Current plan name + price
  - Usage: X/500 leads, X/5 users (progress bars)
  - Next billing date + amount
  - "Vaihda suunnitelma" → Stripe Checkout
  - "Hallinnoi maksutietoja" → Stripe Customer Portal
  - Invoice history table: date, amount, status, PDF link (from Stripe)

PRICING PAGE (/pricing):
  - Clean 3-column pricing table
  - Monthly/yearly toggle (yearly = 2 months free)
  - Feature comparison
  - CTA buttons → checkout
  - "Aloita ilmainen kokeilu" for new orgs

Write tests for:
  - Stripe webhook signature verification
  - Subscription created → organization activated
  - Payment failed → status past_due
  - Feature gate blocks access on wrong plan
  - Lead limit enforced on correct count
  - Trial expiry transitions
```

### ✅ Vaiheen 18 hyväksymiskriteerit
- [ ] Uusi organisaatio saa 14 pv kokeilun automaattisesti
- [ ] Stripe Checkout -maksu toimii testiympäristössä
- [ ] Webhook päivittää tilauksen tilan (created, failed, cancelled)
- [ ] Feature gate estää sekvenssit Starter-planilla
- [ ] Liidilimiitin ylittäminen palauttaa 402 + upgrade_url
- [ ] Stripe Customer Portal toimii (laskut, maksukortti)
- [ ] Superadmin näkee kaikkien organisaatioiden tilaukset

---

## V3 Yhteenveto

```
Vaihe 13: Kalenteriintegraatio         Google + Outlook + tapaamisten linkitys liideihin
Vaihe 14: Tarjoukset ja sopimukset     Luo, lähetä, seuraa tarjouksia — asiakas hyväksyy selaimessa
Vaihe 15: Ennustava analytiikka        AI ennustaa kaupan todennäköisyyden + myyntiennuste
Vaihe 16: Webhook-ilmoitukset          Slack/Teams/custom — CRM-tapahtumat ulkoisiin järjestelmiin
Vaihe 17: Verkkosivulomake             Embedattava lomake → liidi suoraan CRM:ään
Vaihe 18: Stripe-laskutus              Oma SaaS-tilauslogiikka, trial, feature gates

Yhteensä: ~15 päivää
```

---

## Koko roadmap yhteenvetona

```
DONE  MVP   Vaiheet 1–7   Pohja, pipeline, AI, n8n, sähköposti, raportointi
DONE  V2    Vaiheet 8–12  Tehtävät, omat kentät, sekvenssit, automaatiot, GDPR

NOW   V3    Vaihe 13      Kalenteriintegraatio
            Vaihe 14      Tarjoukset ja sopimukset       ← SUOSITELTAVA SEURAAVA
            Vaihe 15      Ennustava analytiikka
            Vaihe 16      Webhook-ilmoitukset
            Vaihe 17      Verkkosivulomake               ← SUOSITELTAVA SEURAAVA
            Vaihe 18      Stripe-laskutus                ← SUOSITELTAVA SEURAAVA
```

**Suositeltu järjestys jos teet vain osan:**  
Tee ensin **Vaihe 18** (Stripe) — se tekee tuotteesta myyntikelpoisen. Sitten **Vaihe 17** (lomake) — uudet liidit ilman n8n:ää. Sitten **Vaihe 14** (tarjoukset) — suljet kaupat CRM:ssä.
