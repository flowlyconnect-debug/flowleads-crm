# FlowLeads CRM — V2 Feature-arvio ja kehityssuunnitelma

**Perustuu:** Matias Flowlyn feature-ehdotuksiin  
**Konteksti:** MVP (Vaiheet 1–7) on jo suunniteltu. Tämä asiakirja kattaa mitä tehdään sen jälkeen, missä järjestyksessä, ja mitä jätetään tekemättä.

---

## Ensin: mikä on jo tehty MVP:ssä

Ennen kuin arvioidaan uusia ehdotuksia, on hyvä tietää mitä Vaiheet 1–7 jo sisältävät:

| Ominaisuus | MVP-status |
|---|---|
| Pipeline kanban-näkymällä | ✅ Tehty |
| Liidien hallinta (CRUD) | ✅ Tehty |
| n8n API-integraatio | ✅ Tehty |
| AI-rikastus + scoring | ✅ Tehty |
| Sähköposti CRM:stä | ✅ Tehty |
| Raportointi ja dashboard | ✅ Tehty |
| Roolipohjaiset oikeudet | ✅ Tehty |
| Audit-loki | ✅ Tehty |
| Varmuuskopiot | ✅ Tehty |
| Multi-tenant | ✅ Tehty |
| API-avainten hallinta | ✅ Tehty |
| Tagit | ✅ Tehty |

---

## Feature-arvio: tee / älä tee / myöhemmin

### ✅ TEE — Lisätään V2:een (selkeä arvo, ei liian iso)

| Ehdotus | Perustelu |
|---|---|
| **Tehtävät ja muistutukset** | Myyjä luo tehtävän liidille "Soita pe klo 14" — ilman tätä CRM on vain lista, ei työkalu. Kriittinen käyttöönotolle. |
| **Omat kentät (custom fields)** | Asiakkaat myyvät eri asioita — ilman custom fieldejä he eivät voi mukauttaa CRM:ää prosesseihinsa. Suuri syy ostaa tai hylätä SaaS-CRM. |
| **Segmentointi** | Listanäkymään tallennetut suodattimet ("Kaikki B2B SaaS -liidit joiden score > 70"). Tämä on helppo rakentaa aiemman hakutoiminnon päälle. |
| **GDPR-työkalut** | Pakollinen EU-markkinoille. Suostumuksen hallinta, oikeus tietojen poistoon, tietojen vientimahdollisuus. Ilman tätä tuote ei ole myyntikelpoinen isommille asiakkaille. |
| **Datan vienti (Export)** | Asiakkaat eivät osta SaaS-palvelua jos he eivät luota saavansa datansa ulos. CSV/Excel vienti kaikista näkymistä. |
| **Sähköpostisekvenssit (nurturing)** | Automaattinen "Lähetä follow-up 3 päivän päästä jos ei vastausta" — tämä on se, minkä takia liidit ostetaan. Ydinominaisuus tuotteen arvolupaukselle. |
| **Automaattiset muistutukset** | "Liidi ei ole ollut kontaktissa 14 päivään" → tehtävä myyjälle. Estää liidien unohtumisen. |

---

### ⏳ MYÖHEMMIN — Rakennetaan V3:een tai kun asiakkaat pyytävät

| Ehdotus | Perustelu |
|---|---|
| **Kalenteriintegraatio** | Google Calendar / Outlook-synkronointi on arvokasta, mutta monimutkainen OAuth2-integraatio. Tehdään kun asiakkaat pyytävät. |
| **Tarjousten ja sopimusten hallinta** | Hyvä lisä, mutta täysin oma moduuli. Voi lisätä myöhemmin — älä suunnittele sitä varten nyt mitään. |
| **Mobiilikäyttö (optimoitu)** | Responsiivinen web toimii. Natiivisovellus on vuoden projekti. Riittää kun UI on mobiiliystävällinen. |
| **Ennustava analytiikka** | AI-scoring on jo tehty. Todellinen ennustava malli vaatii historiadataa — lisätään kun asiakkailla on 6+ kk dataa. |
| **Monikielisyys** | Vasta kun on asiakkaita muista maista. Arkkitehtuuri ei estä lisäämistä myöhemmin. |
| **Webhook-ilmoitukset ulos** | "Tarjous avattu → ilmoitus Slackiin" — hyvä integraatio, mutta tehdään kun API on vakaa. |
| **Verkkosivulomakeintegraatio** | Lomake → liidi CRM:ään. Arvokas, mutta n8n tekee tämän jo — ei tarvita natiivitoteutusta heti. |

---

### ❌ ÄLÄ TEE — Ei kuulu tähän tuotteeseen

| Ehdotus | Perustelu |
|---|---|
| **ERP-integraatio** | ERP-integraatiot (SAP, Netvisor, Procountor) ovat täysin eri maailma. Jokainen on kuukausien projekti, jokainen asiakas käyttää eri ERP:iä. Tämä on konsulttibisnestä, ei SaaS-ominaisuus. **Tarjoa n8n-integraatioita sen sijaan.** |
| **Asiakaspalvelun tiketit** | Tikettijärjestelmä (kuten Zendesk) on kokonaan eri tuote eri ostajalle. Sekoittaa tuotteen fokuksen. Jos asiakas haluaa tiketit, integroi olemassa olevaan työkaluun. |
| **Markkinointiautomaatio (täysi)** | Mailchimp/HubSpot-tasoinen markkinointiautomaatio on vuosien työ. Sähköpostisekvenssit riittävät — älä rakenna kampanjahallintatyökalua. |
| **360-asiakasnäkymä (support + myynti + laskutus)** | Kuulostaa hyvältä paperilla, mutta vaatii ERP- ja tiketti-integraatiot. Lopputulos on monimutkainen ja hidas. Pidä CRM myyntikeskeisenä. |
| **Oma natiivimobiilisovellus** | iOS/Android-applikaatio on erillinen projekti, erillinen tiimi, erillinen ylläpito. Responsiivinen web on riittävä MVP:lle ja V2:lle. |
| **Tiedonsiirtopalvelu vanhoista järjestelmistä** | "Import vanhoista järjestelmistä ilman isoa käsityötä" — kuulostaa yksinkertaiselta, mutta jokainen lähdejärjestelmä on erilainen. Tarjoa CSV-import ja anna asiakkaan hoitaa muunnos. |

---

## V2 Kehitysvaiheet

### Prioriteettijärjestys

```
Vaihe 8  → Tehtävät ja muistutukset          (myyjä käyttää päivittäin)
Vaihe 9  → Omat kentät + segmentointi        (asiakkaat mukauttavat)
Vaihe 10 → Sähköpostisekvenssit              (tuotteen ydinarvolupaus)
Vaihe 11 → Automaatiomoottori               (työnkulut ja hälytykset)
Vaihe 12 → GDPR + datan hallinta            (myynnin edellytys EU:ssa)
```

---

## VAIHE 8 — Tehtävät ja muistutukset
**Arvio:** 2 päivää  
**Tavoite:** Myyjä voi luoda tehtäviä liideille, saada muistutuksia, nähdä päivän työlistansa

### Cursor-prompt

```
Add a task and reminder system to FlowLeads CRM:

NEW MODEL:

Task:
  id, organization_id (FK), lead_id (FK nullable), assigned_to (FK → User)
  created_by (FK → User)
  title (string, max 200), description (text, nullable)
  type (enum: call, email, follow_up, meeting, other)
  status (enum: pending, in_progress, completed, cancelled)
  due_date (datetime), completed_at (datetime nullable)
  priority (enum: low, normal, high, urgent)
  reminder_at (datetime nullable)
  created_at, updated_at

ROUTES (blueprint: tasks):
  GET  /tasks                       # My task list (assigned to me)
  GET  /tasks/today                 # Tasks due today
  GET  /tasks/overdue               # Overdue tasks
  POST /tasks                       # Create task (standalone)
  POST /leads/<id>/tasks            # Create task linked to lead
  GET  /leads/<id>/tasks            # Tasks for a lead
  PATCH /tasks/<id>                 # Update task (status, due_date, etc.)
  POST /tasks/<id>/complete         # Mark complete
  DELETE /tasks/<id>                # Cancel task

TASK SERVICE (app/tasks/services.py):
  TaskService.create(data, user_id, organization_id)
  TaskService.complete(task_id, user_id)
  TaskService.get_due_today(user_id, organization_id)
  TaskService.get_overdue(organization_id)
  TaskService.send_reminders()  # Called by scheduler

REMINDER SYSTEM:
  APScheduler job runs every 15 minutes:
  - Find tasks where reminder_at <= now AND status=pending AND reminder_sent=False
  - Send email reminder to assigned_to user via Mailgun
  - Mark reminder_sent=True
  - Add field to Task model: reminder_sent (bool, default False)

  Email subject: "Muistutus: {task.title} – liidi {lead.company}"
  Add email template: task_reminder

AUTO-TASK CREATION RULES (configurable per organization):
  Settings table entries:
  - auto_task_on_new_lead: bool (default True) → create "Ota yhteyttä" task assigned to org admin
  - auto_task_no_contact_days: int (default 14) → create task if lead not contacted in N days
  - auto_task_stage_change: bool → create follow-up task when lead moves to "Proposal Sent"

  TaskService.create_auto_tasks(lead, trigger_type)

UI CHANGES:

Navigation: Add "Tehtävät" to main nav with badge showing overdue count

My Tasks page (/tasks):
  - Tabs: Today | This Week | All | Overdue
  - Each task row: checkbox, priority icon, title, lead name (linked), due date, type badge
  - Click checkbox → mark complete (AJAX, no page reload)
  - Overdue tasks highlighted in red
  - Quick-add task form at top
  - Filter by: type, priority, assigned_to (admin only)

Lead Detail page updates:
  - Tasks tab alongside Activity tab
  - Task list for this lead
  - "Add Task" button opens modal:
    * Title (required)
    * Type dropdown
    * Due date + time picker
    * Priority
    * Reminder toggle + time
    * Assign to (admin can assign to others)
  - Completed tasks shown collapsed/greyed

Dashboard updates:
  - Add "Tehtäväsi tänään" card: count of tasks due today
  - Add "Myöhässä" card: count of overdue tasks (red)
  - Recent tasks widget

API endpoint for n8n:
  POST /api/v1/leads/<id>/tasks    # n8n can create tasks on leads

Add Activity log entry when:
  - Task created (type: task_created)
  - Task completed (type: task_completed)
  - Task overdue reminder sent (type: task_reminder_sent)

Write tests for:
  - Task creation linked to lead
  - Task completion and activity logging
  - Reminder scheduling and sending
  - Auto-task creation on new lead
  - Overdue task detection
  - Cross-tenant isolation
```

### ✅ Vaiheen 8 hyväksymiskriteerit
- [ ] Myyjä näkee päivittäiset tehtävänsä etusivulla
- [ ] Tehtävän luonti liidin profiilista toimii
- [ ] Muistutussähköposti lähtee oikeaan aikaan
- [ ] Automaattinen tehtävä luodaan uudelle liidille
- [ ] 14 päivän inaktiivisuus → tehtävä myyjälle
- [ ] Yliaikainen tehtävä näkyy punaisena

---

## VAIHE 9 — Omat kentät ja segmentointi
**Arvio:** 2–3 päivää  
**Tavoite:** Asiakkaat voivat lisätä omia kenttiä liideille ja tallentaa hakusuodattimia segmenteiksi

### Cursor-prompt

```
Add custom fields and lead segmentation to FlowLeads CRM:

PART 1: CUSTOM FIELDS

NEW MODELS:

CustomFieldDefinition:
  id, organization_id (FK)
  entity_type (enum: lead, contact, company)  # only lead for MVP
  name (string), label (string), field_type (enum: text, number, date, boolean, select, multiselect, url)
  options (JSON array, for select/multiselect)
  is_required (bool, default False)
  is_searchable (bool, default True)
  order_index (int)
  created_at

CustomFieldValue:
  id, organization_id (FK), entity_id (int), entity_type (string)
  field_definition_id (FK), value_text, value_number, value_date, value_boolean, value_json
  created_at, updated_at

CUSTOM FIELD LOGIC:
  CustomFieldService.get_fields(organization_id, entity_type)
  CustomFieldService.get_values(entity_id, entity_type, organization_id)
  CustomFieldService.set_value(entity_id, entity_type, field_id, value, organization_id)
  CustomFieldService.validate_value(field_definition, raw_value) → returns typed value or raises

Validation rules by type:
  text: max 1000 chars
  number: must be numeric, optional min/max from field options
  date: ISO 8601 format
  boolean: true/false
  select: value must be in options list
  multiselect: array, all values must be in options list
  url: must be valid URL format

ADMIN UI for custom fields (/settings/custom-fields):
  - List current custom fields with drag-to-reorder
  - Add new field: label, type, required toggle, searchable toggle
  - For select/multiselect: add/remove options
  - Delete field (with warning: "This will delete all stored values for this field")
  - Preview how field looks on lead form

Lead Form updates:
  - Render custom fields after standard fields in lead create/edit
  - Field types render appropriate input: text→input, number→number input,
    date→date picker, boolean→toggle, select→dropdown, multiselect→multi-select
  - Required fields validated before save
  - Custom field values saved via CustomFieldService on lead create/update

Lead Detail page:
  - Show custom fields in a "Custom Fields" section
  - Inline edit: click value to edit, click away to save (AJAX)

API support:
  POST /api/v1/leads
  - Accept "custom_fields": {"field_name": value, ...} in payload
  - n8n can set custom field values when pushing leads
  GET /api/v1/leads/<id>
  - Include custom_fields object in response

---

PART 2: SEGMENTATION

NEW MODEL:

Segment:
  id, organization_id (FK), name (string), description (text nullable)
  created_by (FK → User)
  filters (JSON)  # stored filter state
  is_pinned (bool)  # show in sidebar
  lead_count_cache (int)  # updated periodically
  created_at, updated_at

FILTER SYSTEM:
Segments are saved versions of the lead list filters.

Filter JSON structure:
{
  "logic": "AND",  # AND / OR
  "conditions": [
    {"field": "score", "operator": "gte", "value": 70},
    {"field": "stage.name", "operator": "eq", "value": "Interested"},
    {"field": "source", "operator": "in", "value": ["n8n", "manual"]},
    {"field": "custom.industry", "operator": "eq", "value": "SaaS"},
    {"field": "last_contacted_at", "operator": "lt", "value": "{{now-14d}}"}
  ]
}

Supported operators by type:
  text:     eq, neq, contains, not_contains, is_empty, is_not_empty
  number:   eq, neq, gt, gte, lt, lte, is_empty
  date:     eq, before, after, between, is_empty, relative ({{now-7d}})
  boolean:  is_true, is_false
  select:   eq, neq, in, not_in
  stage:    eq, neq, in

SegmentService.apply_filters(organization_id, filters) → SQLAlchemy query
SegmentService.get_lead_count(segment_id) → int
SegmentService.save(name, filters, user_id, organization_id) → Segment
SegmentService.refresh_counts(organization_id) → update all count caches

ROUTES:
  GET  /segments                     # List segments
  POST /segments                     # Save current filters as segment
  GET  /segments/<id>                # View segment (= lead list with filters applied)
  PUT  /segments/<id>                # Update segment filters
  DELETE /segments/<id>              # Delete segment
  POST /segments/<id>/pin            # Pin to sidebar

UI CHANGES:

Lead List (/leads):
  - Filter panel becomes persistent sidebar (collapsible)
  - Filters: stage, source, score range, assigned_to, tags, custom fields, date ranges
  - "Save as Segment" button when filters are active
  - Segment name input → saves to Segment model

Navigation sidebar:
  - "Segmentit" section below pipeline
  - Lists pinned segments with lead count badge
  - "Kaikki segmentit" link

Segment detail (/segments/<id>):
  - Same as lead list but pre-filtered
  - Shows segment name + description
  - "Edit filters" button
  - Lead count
  - Bulk actions: assign, change stage, export, add tag

API:
  GET /api/v1/segments               # List segments
  GET /api/v1/segments/<id>/leads    # Get leads in segment (paginated)

Write tests for:
  - Custom field creation, validation by type
  - Custom field value storage and retrieval
  - Custom field values in API response
  - Segment filter logic (AND/OR, each operator)
  - Segment lead count
  - Cross-tenant isolation for both
```

### ✅ Vaiheen 9 hyväksymiskriteerit
- [ ] Admin voi luoda text/number/date/select-tyyppisiä kenttiä
- [ ] Custom kentät näkyvät liidilomakkeessa ja profiilissa
- [ ] n8n voi lähettää custom kentän arvoja API:lla
- [ ] Suodatin-logiikka toimii AND/OR -yhdistelmillä
- [ ] Segmentti tallentuu ja näyttää oikean liidimäärän
- [ ] Pinnit segmentit näkyvät sivupalkissa

---

## VAIHE 10 — Sähköpostisekvenssit (liidi-nurturing)
**Arvio:** 3 päivää  
**Tavoite:** Automaattinen viestisarja uudelle liidille — oikea viesti oikeaan aikaan ilman manuaalista työtä

### Cursor-prompt

```
Add email sequence (drip campaign) functionality to FlowLeads CRM:

NEW MODELS:

EmailSequence:
  id, organization_id (FK), name, description
  is_active (bool), trigger_type (enum: manual, on_lead_created, on_stage_change, on_segment_match)
  trigger_config (JSON)  # e.g. {"stage_id": 5} or {"segment_id": 3}
  created_by (FK → User), created_at

EmailSequenceStep:
  id, sequence_id (FK), order_index (int)
  delay_days (int, 0 = immediate), delay_hours (int, default 0)
  subject_template (string), body_html_template (text), body_text_template (text)
  condition (JSON nullable)  # skip this step if condition met, e.g. {"if_replied": true}
  created_at

EmailSequenceEnrollment:
  id, sequence_id (FK), lead_id (FK), organization_id (FK)
  enrolled_by (FK → User, nullable — null if auto-enrolled)
  status (enum: active, completed, cancelled, unsubscribed)
  current_step_index (int, default 0)
  next_send_at (datetime)
  enrolled_at, completed_at, cancelled_at

EmailSequenceSent:
  id, enrollment_id (FK), step_id (FK), lead_id (FK)
  email_log_id (FK → EmailLog)
  sent_at, opened_at (nullable), clicked_at (nullable)

SEQUENCE ENGINE (app/sequences/services.py):

SequenceService.enroll_lead(lead_id, sequence_id, enrolled_by=None):
  - Check lead not already active in this sequence
  - Create enrollment, calculate next_send_at for step 0
  - Log activity: sequence_enrolled

SequenceService.process_due_steps():
  # Called by APScheduler every 10 minutes
  - Find enrollments where next_send_at <= now AND status=active
  - For each enrollment:
    * Get current step
    * Check step conditions (if any)
    * Render template with lead variables
    * Send via EmailService.send_to_lead()
    * Create EmailSequenceSent record
    * Advance to next step OR mark completed
    * Calculate next_send_at for next step
    * Log activity: sequence_email_sent

SequenceService.unenroll(enrollment_id, reason):
  - Set status to cancelled/unsubscribed
  - Log activity: sequence_unenrolled

SequenceService.handle_reply(lead_id):
  # Called when Mailgun webhook detects reply (or manual trigger)
  # Cancel active enrollments where stop_on_reply=True

UNSUBSCRIBE:
  - Every sequence email includes unsubscribe footer link
  - Link: /unsubscribe?token=<signed_token>
  - Token contains: lead_id, sequence_id (signed with SECRET_KEY)
  - On click: unenroll from sequence, set lead.unsubscribed=True
  - Unsubscribed leads never receive sequence emails

TRIGGER TYPES:
  on_lead_created:  auto-enroll when new lead created (via API or manually)
  on_stage_change:  auto-enroll when lead moves to specific stage
  on_segment_match: auto-enroll when lead enters segment
  manual:           user manually enrolls lead from lead detail page

ROUTES:
  GET  /sequences                         # List sequences
  POST /sequences                         # Create sequence
  GET  /sequences/<id>                    # View sequence + stats
  PUT  /sequences/<id>                    # Edit sequence settings
  DELETE /sequences/<id>                  # Delete (only if no active enrollments)

  POST /sequences/<id>/steps              # Add step
  PUT  /sequences/<id>/steps/<step_id>    # Edit step
  DELETE /sequences/<id>/steps/<step_id> # Remove step
  POST /sequences/<id>/steps/reorder     # Reorder steps

  POST /leads/<id>/sequences/enroll       # Manually enroll lead
  POST /leads/<id>/sequences/unenroll     # Unenroll from sequence
  GET  /leads/<id>/sequences              # Active sequences for lead

UI:

Sequence Builder (/sequences/<id>):
  - Visual step builder: timeline with steps
  - Each step card shows: step number, delay, subject preview
  - Add step button between/after steps
  - Step editor modal:
    * Delay: "Wait X days and Y hours after previous step"
    * Subject (with variable support: {{first_name}}, {{company}} etc.)
    * Rich text body (Quill.js)
    * Text version
    * Condition: "Skip if lead has replied" toggle
  - Preview: select a test lead → renders email with real data
  - Activate/deactivate toggle

Sequence Stats (/sequences/<id>/stats):
  - Total enrolled, active, completed, unsubscribed
  - Per-step: sent count, open rate, click rate
  - Lead list of active enrollments

Lead Detail updates:
  - "Sekvenssit" tab: shows active sequences + step progress
  - "Lisää sekvenssiin" button → dropdown of available sequences
  - Shows next scheduled email with date/time

API (for n8n):
  POST /api/v1/leads/<id>/sequences/enroll  # n8n can trigger enrollment

Write tests for:
  - Sequence step scheduling (correct delay calculation)
  - Email sent and next step scheduled
  - Sequence completes after last step
  - Unsubscribe link works and stops emails
  - Auto-enrollment trigger on lead created
  - Unsubscribed lead receives no emails
  - Cross-tenant isolation
```

### ✅ Vaiheen 10 hyväksymiskriteerit
- [ ] Voi rakentaa 3-vaiheisen viestisekvenssin UI:ssa
- [ ] Ensimmäinen viesti lähtee automaattisesti uudelle liidille
- [ ] Seuraava viesti lähetetään oikeana päivänä (delay)
- [ ] Unsubscribe-linkki toimii ja pysäyttää kaikki viestit
- [ ] Sekvenssin avaus- ja klikkausprosentit näkyvät

---

## VAIHE 11 — Automaatiomoottori
**Arvio:** 3 päivää  
**Tavoite:** Visuaalinen työnkulkueditori — "kun X tapahtuu, tee Y"

### Cursor-prompt

```
Add a workflow automation engine to FlowLeads CRM:

CONCEPT: Simple trigger → action automations (not a full workflow builder like n8n).
Target: 80% of common automation needs with 20% of complexity.

NEW MODELS:

Automation:
  id, organization_id (FK), name, description
  is_active (bool), trigger_type (string), trigger_config (JSON)
  created_by (FK → User), run_count (int), last_run_at
  created_at

AutomationAction:
  id, automation_id (FK), order_index (int)
  action_type (string), action_config (JSON)

AutomationLog:
  id, automation_id (FK), lead_id (FK nullable), organization_id (FK)
  trigger_data (JSON), result (enum: success, failed, skipped)
  error_message (text nullable), created_at

SUPPORTED TRIGGERS:
  lead_created              # Any new lead
  lead_stage_changed        # Lead moves to specific stage
  lead_score_changed        # Score crosses threshold (e.g. score > 80)
  lead_no_activity          # No activity for N days
  lead_tag_added            # Specific tag added to lead
  task_overdue              # Task overdue by N hours
  email_opened              # Lead opens an email
  sequence_completed        # Lead completes a sequence

SUPPORTED ACTIONS:
  create_task               # Create task on lead, assign to user/owner
  send_email                # Send email using template
  enroll_in_sequence        # Add lead to email sequence
  change_stage              # Move lead to stage
  assign_lead               # Assign lead to user
  add_tag                   # Add tag to lead
  remove_tag                # Remove tag from lead
  send_webhook              # POST to external URL (for n8n, Slack, etc.)
  notify_user               # In-app notification to specific user

AUTOMATION ENGINE (app/automations/services.py):

AutomationEngine.trigger(event_type, payload, organization_id):
  # Called from various places in the codebase when events happen
  # payload = {"lead_id": ..., "old_stage": ..., "new_stage": ..., etc.}
  
  1. Find active automations for organization with matching trigger_type
  2. Evaluate trigger conditions from trigger_config
  3. For each matching automation, execute actions in order
  4. Log result to AutomationLog
  5. Never crash the original operation if automation fails

AutomationEngine.execute_action(action, lead, context):
  # Dispatch to action-specific handler
  # Each action handler returns success/failure

ADD TRIGGER CALLS throughout codebase:
  After lead created:        AutomationEngine.trigger('lead_created', ...)
  After stage change:        AutomationEngine.trigger('lead_stage_changed', ...)
  After score update:        AutomationEngine.trigger('lead_score_changed', ...)
  After tag added:           AutomationEngine.trigger('lead_tag_added', ...)
  In scheduler (daily):      AutomationEngine.trigger('lead_no_activity', ...)
  After task marked overdue: AutomationEngine.trigger('task_overdue', ...)

CONDITION EVALUATION:
trigger_config example for lead_stage_changed:
{
  "to_stage_id": 5,           # only trigger when moving to this stage
  "min_score": 60,            # only if lead score >= 60
  "source": ["n8n"]           # only for leads from n8n
}

trigger_config example for lead_no_activity:
{
  "days": 14,
  "stages": [2, 3, 4]  # only in these stages
}

WEBHOOK ACTION:
  action_config: {
    "url": "https://...",
    "method": "POST",
    "headers": {"X-Secret": "..."},
    "body_template": "{\"lead_id\": \"{{lead.id}}\", \"company\": \"{{lead.company}}\"}"
  }
  - Timeout: 10 seconds
  - No retry in MVP (log failure)
  - NEVER store webhook secrets in plain text — use encrypted field

IN-APP NOTIFICATIONS:

Notification model:
  id, user_id (FK), organization_id (FK)
  type (string), title, message, link (url to relevant page)
  is_read (bool), created_at

Routes:
  GET  /api/notifications           # Get unread count + recent 10
  POST /api/notifications/<id>/read # Mark read
  POST /api/notifications/read-all  # Mark all read

UI:
  - Bell icon in nav with unread count badge
  - Dropdown showing recent notifications
  - Click → navigate to relevant lead/task

AUTOMATION UI (/automations):
  - List of automations: name, trigger, action count, is_active, last_run
  - Create automation → simple form:
    * Name
    * Trigger selector (dropdown with descriptions)
    * Trigger conditions (dynamic fields based on trigger type)
    * Actions list (add/remove/reorder)
    * Each action: type dropdown + config fields
  - Toggle active/inactive
  - View run log: recent 50 executions with status

BUILT-IN ALERT AUTOMATIONS (seeded defaults):
  1. "Passiivinen liidi" — lead_no_activity 14d → create_task "Ota yhteyttä"
  2. "Korkean potentiaalin liidi" — lead_score_changed score>80 → notify_user + assign_lead
  3. "Tarjous lähetetty" — lead_stage_changed to "Proposal Sent" → enroll_in_sequence "Follow-up"

Write tests for:
  - Trigger fires on correct event
  - Conditions evaluated correctly
  - Action executes (task created, email sent, stage changed)
  - Failed action logged without crashing original operation
  - Automation disabled → does not fire
  - Cross-tenant: org A automation does not fire for org B leads
```

### ✅ Vaiheen 11 hyväksymiskriteerit
- [ ] Voi luoda "liidi luotu → lähetä terveysviesti" -automaation UI:ssa
- [ ] Trigger ei kaada lead-luontia vaikka automaatio epäonnistuu
- [ ] Webhook-toiminto lähettää POST-pyynnön ulkoiseen URL:iin
- [ ] Ilmoitukset näkyvät kellokuvakkeessa
- [ ] Automaatioloki näyttää jokaisen suorituksen tuloksen

---

## VAIHE 12 — GDPR ja datan hallinta
**Arvio:** 1–2 päivää  
**Tavoite:** EU-yhteensopivuus, datan vienti, oikeus tulla unohdetuksi

### Cursor-prompt

```
Add GDPR compliance and data management to FlowLeads CRM:

GDPR REQUIREMENTS:

1. CONSENT TRACKING
Add to Lead model:
  gdpr_consent (bool, default False)
  gdpr_consent_at (datetime nullable)
  gdpr_consent_source (string: "api", "form", "manual", nullable)
  gdpr_legal_basis (enum: consent, legitimate_interest, contract, nullable)
  marketing_opt_in (bool, default False)
  unsubscribed (bool, default False)
  unsubscribed_at (datetime nullable)

API: n8n can set gdpr_consent and gdpr_legal_basis when creating leads.
UI: Visible + editable on lead detail page.

Sequence emails: Only send to leads where unsubscribed=False.
Email sends: Log gdpr_legal_basis in EmailLog.

2. RIGHT TO BE FORGOTTEN (data deletion)

GDPRService.anonymize_lead(lead_id, requested_by_user_id):
  """
  Anonymizes lead data while preserving analytics integrity.
  Does NOT hard-delete to maintain referential integrity.
  """
  - Replace personal data with anonymized values:
    * email → "anonymized_{id}@deleted.invalid"
    * first_name → "Anonymoitu"
    * last_name → "Henkilö"
    * phone → None
    * linkedin_url → None
    * ai_summary → None
    * notes → "[Tiedot poistettu GDPR-pyynnöstä]"
  - Set: is_anonymized=True, anonymized_at=now
  - Cancel all active sequence enrollments
  - Delete custom field values
  - Log to AuditLog: gdpr_anonymization_requested
  - Send confirmation email to requesting user

Add to Lead model: is_anonymized (bool, default False), anonymized_at

ROUTE:
  POST /leads/<id>/gdpr/anonymize   # Admin only, requires password confirmation

3. DATA EXPORT (Right to Access)

DataExportService.export_lead(lead_id) → JSON:
  - All lead fields
  - All custom field values
  - All activities
  - All emails sent
  - All task history
  - Sequence enrollment history
  - Audit log entries for this lead

DataExportService.export_organization(organization_id) → ZIP:
  - leads.csv
  - activities.csv
  - emails_sent.csv
  - tasks.csv
  - custom_fields.csv

ROUTES:
  GET  /leads/<id>/export           # Export single lead as JSON
  GET  /settings/export             # Export all org data (admin only)
  POST /settings/export/request     # Queue export, email download link when ready

The org export runs as background job (APScheduler), sends email with download link.
Download link expires after 48 hours.

4. PRIVACY SETTINGS (/settings/privacy):

Organization-level settings (stored in Settings table):
  - gdpr_default_legal_basis (consent/legitimate_interest)
  - gdpr_retention_days (default 730 = 2 years)
  - gdpr_auto_anonymize_inactive (bool) — anonymize leads inactive for retention_days
  - privacy_policy_url (string) — used in email footers
  - data_controller_name (string) — shown in export
  - data_controller_email (string)

5. DATA RETENTION AUTOMATION:
APScheduler monthly job:
  - Find leads where:
    * last activity > gdpr_retention_days ago
    * gdpr_auto_anonymize_inactive = True
    * is_anonymized = False
  - Anonymize each lead
  - Send report to superadmin: "X leads anonymized this month"

6. CONSENT AUDIT:
Extend AuditLog to track:
  - gdpr_consent_given (who set consent, when, what source)
  - gdpr_consent_withdrawn
  - gdpr_anonymization_requested
  - gdpr_data_exported

7. UI ADDITIONS:

Lead list:
  - GDPR status column (toggleable): shows consent icon, opt-in status
  - Filter: show only consented leads, show only opted-in

Lead detail:
  - "Tietosuoja" section:
    * Consent status + date + source
    * Legal basis
    * Marketing opt-in toggle
    * Unsubscribe status
    * "Anonymisoi tiedot" button (admin only, requires confirmation + reason)
    * "Vie tiedot (JSON)" link

Superadmin panel:
  - GDPR dashboard: counts of consented, opted-in, anonymized, unsubscribed
  - Data retention settings
  - Bulk anonymization tool

Write tests for:
  - Lead anonymization replaces all personal data
  - Anonymized lead still in DB (referential integrity)
  - Unsubscribed lead skipped in sequence sends
  - Data export contains all lead-related records
  - Retention automation anonymizes correct leads
  - GDPR events logged in audit log
```

### ✅ Vaiheen 12 hyväksymiskriteerit
- [ ] Suostumustieto tallennetaan ja näkyy liidillä
- [ ] Anonymisointi poistaa kaikki henkilötiedot
- [ ] Anonymisoitu liidi säilyy tietokannassa (viittaukset ehjänä)
- [ ] Organisaation kaikkien liidien vienti ZIP-muodossa toimii
- [ ] Automaattinen tiedonpoistoajo toimii säilytysajan mukaan
- [ ] Unsubscribed-liidi ei saa sekvenssisähköposteja

---

## Yhteenveto: koko tuotteen roadmap

```
MVP (Vaiheet 1–7) — ~2 viikkoa
├── Pohja, autentikointi, 2FA
├── Pipeline, kanban
├── n8n API-integraatio
├── AI-rikastus + scoring
├── Sähköposti CRM:stä
├── Raportointi
└── Tuotantovalmius

V2 (Vaiheet 8–12) — ~3 viikkoa
├── Tehtävät ja muistutukset      (myyjä käyttää päivittäin)
├── Omat kentät + segmentointi    (räätälöinti per asiakas)
├── Sähköpostisekvenssit          (liidi-nurturing)
├── Automaatiomoottori            (työnkulut + hälytykset)
└── GDPR + datan hallinta         (EU-myyntikelpoinen)

V3 (myöhemmin, kun asiakkaat pyytävät)
├── Kalenteriintegraatio (Google/Outlook)
├── Tarjous- ja sopimusten hallinta
├── Ennustava analytiikka (close probability)
├── Webhook-ilmoitukset (Slack, Teams)
└── Verkkosivulomakeintegraatio

EI TEHDÄ (väärä suunta)
├── ERP-integraatiot
├── Asiakaspalvelun tikettijärjestelmä
├── Natiivi mobiilisovellus
├── Täysi markkinointiautomaatio
└── 360-asiakasnäkymä (support + myynti + laskutus)
```

---

## ChatGPT-päivitys

Lisää tämä ChatGPT:n briiffiin ennen V2-vaiheiden rikastusta:

```
UUDET MALLIT V2:ssa (lisätty aiempien päälle):
- Task: tehtävät ja muistutukset, linkitetty Lead-malliin
- CustomFieldDefinition + CustomFieldValue: org-kohtaiset kentät
- Segment: tallennetut suodattimet
- EmailSequence + EmailSequenceStep + EmailSequenceEnrollment: viestisarjat
- Automation + AutomationAction + AutomationLog: automaatiomoottori
- Notification: sisäiset ilmoitukset
- GDPR-kentät Lead-mallissa: gdpr_consent, is_anonymized jne.

TÄRKEÄÄ: Kaikki uudet mallit ovat organization_id-scoped.
Kaikki uudet APScheduler-jobsit rekisteröidään app factoryssa.
AutomationEngine.trigger() kutsutaan olemassa olevista routeista — EI muuteta route-logiikkaa muuten.
```
