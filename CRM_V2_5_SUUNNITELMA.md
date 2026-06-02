# FlowLeads CRM — V2.5 Kehityssuunnitelma

**Prioriteetti:** Tuotteen käytettävyys, AI-hyöty ja ammattimainen rakenne  
**Edellytys:** V2 vaiheet 8–12 valmiina  
**Workflow:** Claude (suunnittelu) → ChatGPT (prompt-rikastus) → Cursor (koodaus)

---

## Vaiheistus

| Vaihe | Nimi | Kuvaus |
|---|---|---|
| V2.5-1 | Dashboard — päivittäinen työlista | "Tänään pitää tehdä nämä" |
| V2.5-2 | AI Playbook liidisivulle | Myyntisuunnitelma per liidi |
| V2.5-3 | Yritykset & Kontaktit -moduuli | Oikea asiakasrekisteri |
| V2.5-4 | Liidiasetukset → Hakuprofiili onboarding | Selkeä käyttöönottopolku |
| V2.5-5 | Tyhjät näkymät ohjaaviksi | Empty states onboardingiksi |
| V2.5-6 | Pipeline-kortteihin myyntilogiikka | Score, tehtävä, AI-suositus, häviösyy |
| V2.5-7 | UI-yhtenäistys ja visuaalinen hierarkia | Suomenkielisyys, sininen vain tärkeisiin |

---

---

## VAIHE V2.5-1 — Dashboard: päivittäinen työlista

```
Rebuild the FlowLeads dashboard to function as a daily sales command center.
The goal: when a salesperson opens the app, they immediately see what to do today — not just metrics.

---

## 1. NEW DASHBOARD SECTION — "Tänään" (Today's priorities)

Add a "Tänään" section to the dashboard ABOVE the existing metric cards.
This section has four priority cards in a 2x2 grid (or stacked on mobile):

### Card A — Kuumat liidit (Hot leads)
Query: leads WHERE organization_id = current AND score >= 70 AND pipeline_stage != 'Closed Won' AND pipeline_stage != 'Closed Lost' ORDER BY score DESC LIMIT 3
Display per lead:
- Lead name + company
- Score as a colored badge (≥80 = red "Kuuma", 60-79 = amber "Lämmin")
- Last contact: "Viimeisin kontakti X pv sitten" (days since last Activity)
- Quick action button: "Avaa" → links to lead detail page

### Card B — Myöhässä olevat follow-upit
Query: tasks WHERE organization_id = current AND is_completed = false AND due_date < NOW() ORDER BY due_date ASC LIMIT 5
Display per task:
- Lead name the task belongs to
- Task title
- How overdue: "X pv myöhässä" (red text)
- Quick action: "Merkitse tehdyksi" (AJAX complete) | "Avaa liidi"

### Card C — Uudet käsittelemättömät liidit
Query: leads WHERE organization_id = current AND pipeline_stage = first stage (New Lead) AND created_at > NOW() - interval '7 days' AND no Activity records exist for this lead ORDER BY created_at DESC LIMIT 5
Label: "Uusia liidejä — ei vielä käsitelty"
Display per lead: name, company, created_at relative ("2h sitten"), source
Quick action: "Aloita" → opens lead detail

### Card D — AI suosittelee toimintoa
Query: leads WHERE organization_id = current AND ai_recommendation IS NOT NULL AND pipeline_stage NOT IN ('Closed Won', 'Closed Lost') ORDER BY score DESC LIMIT 4
This uses the existing rule-based ai_recommendation property on Lead model.
Display per lead: lead name + company + ai_recommendation text (e.g. "Ota yhteyttä — 14 pv hiljaa")
Recommendation badge color: red for risk/overdue, blue for new/opportunity
Quick action: "Avaa" → lead detail

---

## 2. NEW ENDPOINT — /api/dashboard/today

GET /api/dashboard/today
Requires login. Scoped to current org.

Returns JSON:
{
  "hot_leads": [...],
  "overdue_tasks": [...],
  "unprocessed_leads": [...],
  "ai_recommendations": [...]
}

Each hot_lead object:
{ "id": 1, "name": "Matias Saarinen", "company": "Yritys Oy", "score": 85, "days_since_contact": 3, "url": "/leads/1" }

Each overdue_task object:
{ "id": 5, "lead_name": "...", "title": "...", "days_overdue": 2, "lead_url": "/leads/1" }

Each unprocessed_lead:
{ "id": 3, "name": "...", "company": "...", "created_at_relative": "2h sitten", "source": "linkedin" }

Each ai_recommendation:
{ "id": 7, "name": "...", "company": "...", "recommendation": "Ota yhteyttä — 14 pv hiljaa", "type": "risk" }

---

## 3. AI TYÖLISTA (below the 4 cards)

A single card titled "AI:n ehdottama järjestys tänään"
Subtitle: "Aloita näistä — järjestetty tärkeyden mukaan"

Logic (rule-based, no OpenAI API call):
Combine hot leads + overdue tasks + unprocessed leads into a unified ranked list.
Ranking rules:
1. Tasks overdue > 2 days: highest priority
2. Hot leads (score ≥ 80) with no contact in 7+ days
3. Unprocessed new leads < 24h old
4. Warm leads (score 60-79) with no contact in 14+ days
5. Any lead with ai_recommendation = "risk" type

For each item generate a human-readable suggestion in Finnish:
- Overdue task: "Tee tehtävä: [title] — [X] pv myöhässä. Liidi: [name]"
- Hot lead no contact: "Ota yhteyttä [name] ([company]) — kuuma liidi, ei kontaktia [X] pv"
- New unprocessed: "Käy läpi uusi liidi: [name] ([company]) — saapui [X] sitten"

Show max 5 items. Each has an "Avaa" link.

Endpoint: GET /api/dashboard/ai-worklist — returns ranked list as JSON array.

---

## 4. KEEP existing dashboard sections

Keep the existing metric cards, pipeline chart, activity stream below the new "Tänään" section.
The new section goes at the very top, before everything else.

---

## 5. TESTS

test_today_endpoint:
- Returns only current org's leads and tasks (cross-tenant isolation)
- Hot leads query only returns score >= 70
- Unprocessed leads only returns leads with no activities
- Overdue tasks only returns is_completed=False AND due_date < NOW()

test_ai_worklist_ranking:
- Overdue tasks appear before warm leads
- Items from other orgs never appear
```

---

---

## VAIHE V2.5-2 — AI Playbook liidisivulle

```
Add an "AI Playbook" section to the lead detail page. This is the single most important addition for sales effectiveness.

---

## 1. AI PLAYBOOK CARD on lead detail page

Add a prominent card to the lead detail page (app/templates/leads/detail.html or equivalent).
Position: below lead header info, above activity feed — make it unmissable.

Card title: "AI-myyntisuunnitelma"
Card style: white card with a subtle left border accent (#1D6BF3 blue, 3px)

Content sections inside the card:

### A — Miksi tämä liidi on relevantti?
Display: lead.ai_summary (already generated by OpenAI enrichment)
If ai_enrichment_status == 'pending': show skeleton loader + "AI analysoi liidiä..."
If ai_enrichment_status == 'failed': show "Analyysi ei onnistunut" with retry button (POST /leads/<id>/enrich)

### B — Suositeltu seuraava askel
Display: lead.ai_recommendation (rule-based property — already exists)
Show as a highlighted action pill: blue for new/opportunity, red for risk/overdue, amber for follow-up
Examples: "Soita tänään — ei kontaktia 14 pv", "Lähetä tarjous — korkea osuvuus"

### C — Voittotodennäköisyys
Display: lead.score as a progress bar + percentage text
Color: green ≥ 70, amber 40-69, red < 40
Label: "Voittotodennäköisyys [score]%"
Below bar: one sentence explanation (rule-based):
  - score ≥ 80: "Korkea osuvuus — toimi nopeasti"
  - score 60-79: "Hyvä liidi — seuraa aktiivisesti"
  - score 40-59: "Kohtalainen — tarvitsee lisää tietoa"
  - score < 40: "Heikko osuvuus — matala prioriteetti"

### D — Valmis sähköpostiviesti (action button)
Button: "✉ Kopioi sähköpostipohja"
On click: show a pre-filled email template in a textarea below the button.
Template content (generated from lead data — NO OpenAI API call, use template strings):

Subject: "Yhteistyömahdollisuus — [lead.company]"

Body:
"Hei [lead.first_name],

Olen [current_user.first_name] Flowly Solutionsista. Olemme erikoistuneet [org.industry tai 'AI-automaatioon'] ja autamme yrityksiä kuten [lead.company] [ai_recommendation based on lead data].

Voisiko teillä olla hetki jutella tällä viikolla?

Ystävällisin terveisin,
[current_user.first_name]"

Show "Kopioi" button to copy to clipboard.
Show "Lähetä sähköpostina" button → opens the existing send email modal pre-filled with this template.

### E — Valmis soittoavaus (action button)
Button: "📞 Näytä soittoavaus"
On click: reveal a talking points card below:

"Soittoavaus [lead.first_name]:lle:

1. Esittely: 'Hei, olen [current_user.first_name] Flowlylta — soitan koska [lead.company] nousi esiin hakuprofiilissamme.'
2. Avaus: '[AI summary lyhennettynä — max 1 lause]'
3. Kysymys: 'Onko [toimiala-spesifinen kysymys] teillä ajankohtainen?'
4. CTA: 'Voisiko varata 15 min Teams-palaverin tällä viikolla?'"

Show "Kopioi muistiinpanoihin" button → creates an Activity (type='note') with this content.

---

## 2. ENDPOINT — /api/leads/<id>/playbook

GET /api/leads/<id>/playbook
Requires login, lead must belong to current org.

Returns:
{
  "success": true,
  "data": {
    "ai_summary": "...",
    "ai_recommendation": "...",
    "recommendation_type": "risk|opportunity|followup",
    "score": 75,
    "score_label": "Hyvä liidi — seuraa aktiivisesti",
    "email_template": {
      "subject": "...",
      "body": "..."
    },
    "call_script": "..."
  }
}

Generate email_template and call_script server-side using Python string templates (not OpenAI).
Use lead fields: first_name, last_name, company, industry, ai_summary, ai_recommendation.

---

## 3. TESTS

test_playbook_endpoint:
- Returns 404 if lead belongs to different org (cross-tenant)
- Returns ai_summary from lead model
- Email template contains lead's first_name and company
- Call script contains current user's name

test_playbook_ui:
- "AI analysoi liidiä..." shown when ai_enrichment_status = 'pending'
- Score bar color correct for each range
- Copy button copies email template to clipboard
```

---

---

## VAIHE V2.5-3 — Yritykset & Kontaktit -moduuli

```
Add a Companies & Contacts module to FlowLeads. This transforms the CRM from a lead list into a proper customer registry.

---

## 1. NEW DATABASE MODELS

### Company model (app/companies/models.py):

class Company(db.Model):
    __tablename__ = 'companies'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)

    name = db.Column(db.String(200), nullable=False)
    industry = db.Column(db.String(100), nullable=True)
    website = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    region = db.Column(db.String(100), nullable=True)
    country = db.Column(db.String(100), default='Finland')
    revenue_range = db.Column(db.String(50), nullable=True)  # e.g. "100k-500k", "1M-5M"
    employee_count = db.Column(db.String(50), nullable=True)  # e.g. "10-50"
    type = db.Column(db.String(50), default='prospect')  # prospect, customer, partner, supplier
    notes = db.Column(db.Text, nullable=True)
    tags = db.Column(db.JSON, default=list)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    # Relationships
    organization = db.relationship('Organization', backref='companies')
    contacts = db.relationship('Contact', backref='company', lazy='dynamic')
    leads = db.relationship('Lead', backref='company_rel', lazy='dynamic', foreign_keys='Lead.company_id')

### Contact model (app/companies/models.py):

class Contact(db.Model):
    __tablename__ = 'contacts'

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organizations.id'), nullable=False)
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=True)

    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    title = db.Column(db.String(100), nullable=True)  # Toimitusjohtaja, Ostaja, etc.
    linkedin_url = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    tags = db.Column(db.JSON, default=list)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    organization = db.relationship('Organization', backref='contacts')
    leads = db.relationship('Lead', secondary='lead_contacts', backref='contacts')

### Lead updates — add foreign key to Company:
Add to existing Lead model:
    company_id = db.Column(db.Integer, db.ForeignKey('companies.id'), nullable=True)

### Association table — Lead ↔ Contact (many-to-many):
lead_contacts = db.Table('lead_contacts',
    db.Column('lead_id', db.Integer, db.ForeignKey('leads.id'), primary_key=True),
    db.Column('contact_id', db.Integer, db.ForeignKey('contacts.id'), primary_key=True)
)

Create Alembic migration for all new tables and the company_id column on leads.

---

## 2. COMPANY PAGES

### Companies list — GET /companies
Template: app/templates/companies/index.html
Sidebar link: "Yritykset" with icon ti-building

Table columns: Nimi | Toimiala | Tyyppi | Kontaktit | Liidit | Alue | Lisätty
- Tyyppi badge: "Asiakas" (green), "Prospekti" (blue), "Kumppani" (purple), "Toimittaja" (gray)
- Kontaktit: count of related contacts
- Liidit: count of related leads (open only — not Closed Lost)
- Click row → company detail page

Filter bar: by type, industry, region
Search: by company name

Empty state: "Ei yrityksiä vielä — luo ensimmäinen yritys tai liidit linkitetään automaattisesti kun niissä on yrityksen nimi"

Create button: "+ Uusi yritys" → opens inline form or modal

### Company detail — GET /companies/<id>
Template: app/templates/companies/detail.html

Layout:
- Header: company name, type badge, website link, industry, region
- Edit button (pencil)

3-column layout:
LEFT: Company info card (all fields, editable inline)
MIDDLE: Contacts list for this company
  - Show each contact: name, title, email, phone
  - "+ Lisää kontakti" button
RIGHT: Related leads list
  - Show open leads linked to this company
  - Score badge per lead
  - "+ Linkitä liidi" button

Activity feed at bottom (all activities across leads linked to this company)
Notes section (company-level notes, not lead-level)

---

## 3. CONTACT PAGES

### Contacts list — GET /contacts
Template: app/templates/companies/contacts.html
Sidebar link: "Kontaktit" with icon ti-users

Table columns: Nimi | Titteli | Yritys | Sähköposti | Puhelin | Liidit
- Yritys: clickable link to company
- Liidit: count of leads this contact is linked to

Search by name or email
Filter by company, title

Empty state: "Ei kontakteja vielä"

### Contact detail — simple drawer (not full page)
When clicking a contact row, open a right-side drawer (460px) showing:
- All contact info
- Linked company (clickable)
- Linked leads list
- Notes
- Edit form

---

## 4. LINK LEADS TO COMPANIES

### Auto-linking on lead creation:
In POST /api/v1/leads, after lead is created:
- If lead.company field is filled, search for existing Company WHERE name ILIKE lead.company AND organization_id = current
- If found: set lead.company_id = company.id
- If not found: create new Company(name=lead.company, organization_id=current, type='prospect') and set lead.company_id

### Manual linking in lead detail:
In lead detail page, show "Yritys" field with a link to the company if company_id is set.
If no company linked: show "Linkitä yritys" button → searchable dropdown of existing companies OR "Luo uusi yritys".

---

## 5. NAVIGATION

Add to sidebar under main nav section (between Liidit and Pipeline or after):
- "Yritykset" — ti-building icon — /companies — all roles
- "Kontaktit" — ti-users icon — /contacts — all roles

---

## 6. TESTS

test_company_org_scoping:
- Company belongs to Org A, Org B user cannot access it
- Lead auto-linking only creates companies for current org

test_company_auto_link:
- Lead with company="Yritys Oy" → finds existing company by name → links it
- Lead with new company name → creates new Company(type='prospect')
- Company name matching is case-insensitive

test_contact_lead_association:
- A contact can be linked to multiple leads
- Removing a lead does not delete the contact
- contact.leads returns only leads from current org

test_company_lead_count:
- Company detail shows correct count of open leads
- Closed Lost leads not counted
```

---

---

## VAIHE V2.5-4 — Liidiasetukset → Hakuprofiili onboarding

```
Transform the lead settings page into a clear onboarding flow called "Hakuprofiili".
This is the first thing a new customer does after signup — it should feel like a guided setup, not a settings page.

---

## 1. RENAME AND RESTRUCTURE

Change page title from "Liidiasetukset" to "Hakuprofiili"
Change subtitle to: "Määritä millaisia liidejä haet — AI löytää ne automaattisesti"

Change sidebar link label from "Liidiasetukset" to "Hakuprofiili"

---

## 2. ONBOARDING PROGRESS BAR (shown only if settings are incomplete)

If any of the 4 key fields are empty (industry, region, default_pipeline_stage_id, default_owner_id):
Show a progress bar at top of page:
"Profiilin täyttöaste: X/4 — Täydennä profiili saadaksesi parhaat tulokset"
Steps: [Toimiala] [Alue] [Vastuuhenkilö] [Pipeline-vaihe]
Each step shows a green checkmark if filled, gray circle if empty.

When all 4 are filled: hide the progress bar and show a green banner "Hakuprofiili valmis — liidit saapuvat automaattisesti"

---

## 3. FORM SECTIONS — Guided layout

Replace the current simple form with labeled sections:

### Askel 1 — Kohderyhmä
"Millaisia yrityksiä etsit?"

- Toimiala (text input with datalist suggestions):
  Suggestions: SaaS, Taloyhtiöt, Rakentaminen, Terveys, Kiinteistöt, Teollisuus, Kauppa, Koulutus, Muu
  Placeholder: "esim. Taloyhtiöt"

- Alue (text input with datalist):
  Suggestions: Koko Suomi, Uusimaa, Pirkanmaa, Varsinais-Suomi, Pohjois-Pohjanmaa, Keski-Suomi
  Placeholder: "esim. Uusimaa"

- Liidityypit (multi-checkbox, stored as JSON in default_tags):
  Options:
  [ ] B2B — yritysasiakkaat
  [ ] Päättäjät — toimitusjohtajat, ostajat
  [ ] Uudet yritykset — perustettu viim. 2v
  [ ] Kasvuyritykset — liikevaihto kasvaa
  [ ] Rekrytoivat yritykset

### Askel 2 — Reititys
"Minne ja kenelle liidit menevät?"

- Vastuuhenkilö (select dropdown — existing users)
- Pipeline-vaihe (select dropdown — existing stages)

### Askel 3 — Automaattiset tagit
"Lisätäänkö automaattisesti tageja?"

- Oletustagit (tag input — existing field)

---

## 4. PREVIEW PANEL (right side, sticky on desktop)

On desktop: 2-column layout — form on left, preview card on right
On mobile: preview below form

Preview card title: "Näin liidi näyttää"
Shows a mock lead card that updates in real time as user fills the form:

┌──────────────────────────────┐
│ 🏢 [Yrityksen nimi] (esimerkki)│
│ Toimiala: [toimiala]         │
│ Alue: [alue]                 │
│ Tagit: [tagit]               │
│ Omistaja: [omistaja]         │
│ Vaihe: [vaihe]               │
└──────────────────────────────┘

---

## 5. SAVE AND SUCCESS STATE

Save button: "Tallenna hakuprofiili"
After save:
- Show success state: green checkmark + "Hakuprofiili tallennettu — liidit saapuvat automaattisesti hakuprofiilisi perusteella"
- If this is the first save (total_lead_count == 0): show additional message "Ensimmäiset liidit saapuvat yleensä 24 tunnin kuluessa"

---

## 6. TESTS

test_onboarding_progress:
- Progress bar shows 0/4 for new org with empty settings
- Each filled field increments counter
- Progress bar hidden when all 4 filled

test_lead_type_tags:
- Selected liidityypit checkboxes stored as tags in default_tags
- Tags merged with existing tags, not replaced
```

---

---

## VAIHE V2.5-5 — Tyhjät näkymät ohjaaviksi

```
Replace all generic empty states with actionable, context-aware guidance.
Every empty state should act as micro-onboarding.

---

## PATTERN

Each empty state has:
1. An icon (Heroicon/Tabler, 48px, muted color)
2. A title (bold, what's missing)
3. One sentence body (what this feature does)
4. A primary CTA button (what to do now)
5. Optionally: a secondary link ("Lue lisää" or related action)

Use Jinja2 macro in app/templates/components/empty_state.html:

{% macro empty_state(icon, title, body, cta_text, cta_url, secondary_text=None, secondary_url=None) %}
<div class="empty-state">
  <i class="ti {{ icon }}" style="font-size:48px; color:var(--color-text-tertiary);"></i>
  <h3>{{ title }}</h3>
  <p>{{ body }}</p>
  <a href="{{ cta_url }}" class="btn btn-primary">{{ cta_text }}</a>
  {% if secondary_text %}
  <a href="{{ secondary_url }}" class="empty-state-secondary">{{ secondary_text }}</a>
  {% endif %}
</div>
{% endmacro %}

---

## EMPTY STATES PER PAGE

### Liidit (no leads yet)
Icon: ti-user-plus
Title: "Ei liidejä vielä"
Body: "Liidit saapuvat automaattisesti kun hakuprofiilisi on valmis."
CTA: "Avaa hakuprofiili" → /settings/leads
Secondary: "Lisää liidi manuaalisesti" → /leads/new

### Pipeline (no leads in stage)
Icon: ti-layout-kanban
Title: "Ei liidejä tässä vaiheessa"
Body: "Vedä liidejä tähän vaiheeseen tai muuta pipeline-vaiheiden järjestystä."
CTA: "Hallinnoi pipelinea" → /settings/pipeline
(No secondary)

### Tehtävät (no tasks today)
Icon: ti-circle-check
Title: "Ei tehtäviä tänään — olet ajan tasalla!"
Body: ""
CTA: "Lisää tehtävä" → /tasks/new
(Celebratory, no secondary needed)

### Kalenteri (no events)
Icon: ti-calendar-plus
Title: "Ei tapaamisia"
Body: "Haluatko luoda tapaamisen kuumimmalle liidille?"
CTA: "Luo tapaaminen" → /calendar/new
Secondary: "Yhdistä Google Kalenteri" → /settings/calendar

### Sähköpostisekvenssit (no sequences)
Icon: ti-mail-forward
Title: "Ei sähköpostisekvenssejä vielä"
Body: "Luo automaattinen 3 viestin sähköpostiketju uusille liideille."
CTA: "Luo sekvenssi" → /sequences/new
Secondary: "Katso esimerkki" → (opens example modal)

### Automaatiot (no automations)
Icon: ti-bolt
Title: "Ei automaatioita vielä"
Body: "Kun liidi saa korkean score-arvon → luo tehtävä myyjälle automaattisesti."
CTA: "Luo automaatio" → /automations/new

### Lomakkeet (no forms)
Icon: ti-forms
Title: "Ei verkkolomakkeita vielä"
Body: "Luo lomake, jonka voit upottaa verkkosivuillesi — liidit tulevat suoraan CRM:ään."
CTA: "Luo lomake" → /forms/new

### Yritykset (no companies)
Icon: ti-building
Title: "Ei yrityksiä vielä"
Body: "Yritykset luodaan automaattisesti liidien pohjalta tai voit lisätä ne käsin."
CTA: "Lisää yritys" → /companies/new

### Kontaktit (no contacts)
Icon: ti-users
Title: "Ei kontakteja vielä"
Body: "Tallenna päättäjiä ja yhteyshenkilöitä ilman että heistä tehdään liidiä."
CTA: "Lisää kontakti" → /contacts/new

### Raportit / Analytiikka (no data)
Icon: ti-chart-bar
Title: "Ei dataa vielä"
Body: "Raportit päivittyvät automaattisesti kun liidejä saapuu."
CTA: "Avaa hakuprofiili" → /settings/leads
(No secondary)

---

## CSS

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 48px 24px;
  text-align: center;
  gap: 12px;
}
.empty-state h3 { font-size: 16px; font-weight: 500; color: var(--color-text-primary); margin: 0; }
.empty-state p { font-size: 14px; color: var(--color-text-secondary); max-width: 320px; margin: 0; }
.empty-state-secondary { font-size: 13px; color: var(--color-text-secondary); text-decoration: underline; margin-top: 4px; }

---

## IMPLEMENTATION NOTES

- Replace every existing "Ei X vielä" plain text with the macro
- Do NOT change the empty state for tasks when there ARE tasks but none are overdue (that's not empty)
- Empty state on dashboard "Tehtäväsi tänään" card: use the tasks empty state above
```

---

---

## VAIHE V2.5-6 — Pipeline-kortteihin myyntilogiikka

```
Make pipeline cards smarter. Each card should tell the salesperson exactly what to do, not just show the lead name.

---

## 1. PIPELINE CARD REDESIGN

Update app/templates/pipeline/index.html kanban card template.

### Current card shows: lead name, company, score badge
### New card shows (in this layout):

┌─────────────────────────────────────┐
│ [colored left accent stripe]        │
│ [Company name] — [Lead name]        │ ← main title
│ [Score badge] [Stage days badge]    │ ← row 2
│ ─────────────────────────────────   │
│ [AI recommendation badge]           │ ← NEW
│ [Next task or "Ei tehtäviä"]        │ ← NEW
│ [Last contact: "X pv sitten"]       │ ← NEW
└─────────────────────────────────────┘

### Score badge:
- score ≥ 80: red pill "🔥 [score]"
- score 60-79: amber pill "↑ [score]"
- score < 40: gray pill "[score]"
- no score: hide badge

### Stage days badge (how long lead has been in current stage):
- < 3 days: no badge
- 3-7 days: gray "3 pv vaiheessa"
- > 7 days: amber "7+ pv — siirry eteenpäin"
- > 14 days: red "14+ pv — hälytys"

### AI recommendation badge (rule-based):
Show lead.ai_recommendation as a small colored text line:
- type=risk: red text with ti-alert-triangle icon
- type=opportunity: blue text with ti-star icon
- type=followup: amber text with ti-clock icon
Max 30 chars, truncate with ellipsis if longer.

### Next task:
Query: tasks WHERE lead_id = lead.id AND is_completed = false ORDER BY due_date ASC LIMIT 1
- If task found: show "📋 [task.title] — [due_date relative]"
  - If overdue: red text
  - If today: amber text
  - If future: gray text
- If no task: show nothing (don't show "Ei tehtäviä" — keep card clean)

### Last contact:
- days_since_last_activity: COUNT days since last Activity for this lead
- If 0: show nothing (contacted today)
- If 1-6: gray "Kontakti [X] pv sitten"
- If 7-13: amber "Kontakti [X] pv sitten"
- If 14+: red "Ei kontaktia [X] pv"

### Left accent stripe color:
- score ≥ 80: #EF4444 (red)
- score 60-79: #F59E0B (amber)
- score 40-59: #1D6BF3 (blue)
- score < 40: #E5E7EB (gray)
- no score: #E5E7EB (gray)

---

## 2. PERFORMANCE NOTE

Loading all this data for every card in the kanban could be slow with many leads.
Use SQLAlchemy eager loading:
- Load leads with joinedload for their latest task and latest activity
- Add a computed property or query that gets days_since_last_activity efficiently
- Cache pipeline data with a 30-second server-side cache per org if needed

---

## 3. LOST REASON — pakollinen häviösyy

When a lead is dragged to "Closed Lost" stage (or stage changed to Closed Lost via API):

### Frontend:
Intercept the drag-drop or stage-change action.
Before completing the move, show a small inline dropdown/modal:

"Miksi tämä liidi hävisi?"
Select options:
- Ei vastannut
- Väärä kohderyhmä
- Ei budjettia
- Ei ajankohtainen
- Kilpailija voitti
- Muu syy (text input)

"Vahvista" button → completes the stage change + saves reason
"Peruuta" button → cancels the stage change, lead stays in current stage

### Backend:
Add to Lead model:
    lost_reason = db.Column(db.String(100), nullable=True)
    lost_reason_note = db.Column(db.Text, nullable=True)  # for "Muu syy" text

Add to the stage-change API endpoint (PATCH /api/v1/leads/<id>/stage or existing update route):
- If new_stage is 'Closed Lost' and lost_reason is not provided: return 400 error
- Save lost_reason and lost_reason_note to lead
- Log to audit_log: action='lead_lost', metadata={'reason': lost_reason}
- Create Activity: type='stage_change', content='Liidi hävisi: [lost_reason]'

### Analytics:
Add a "Häviösyyt" card to the analytics/reports page:
- Bar chart (Chart.js) showing count per lost_reason
- Query: leads WHERE organization_id = current AND lost_reason IS NOT NULL GROUP BY lost_reason

---

## 4. TESTS

test_pipeline_card_data:
- days_since_last_activity calculated correctly
- Stage days calculated from updated_at of pipeline_stage change (or created_at as fallback)
- Cards only show current org's data

test_lost_reason_required:
- PATCH to Closed Lost without lost_reason returns 400
- PATCH to Closed Lost with lost_reason saves correctly
- Changing from Closed Lost to another stage does NOT require lost_reason

test_lost_reason_analytics:
- Analytics endpoint returns correct counts per reason
- Only counts current org's leads
```

---

---

## VAIHE V2.5-7 — UI-yhtenäistys ja visuaalinen hierarkia

```
Unify the visual language of FlowLeads. The goal: blue only for important actions, clear hierarchy, consistent Finnish, less empty space.

---

## 1. COLOR DISCIPLINE — Blue only for primary actions

Audit every use of #1D6BF3 (primary blue) and #38BDF8 (highlight blue) in the codebase.

Rules:
- #1D6BF3 ONLY for: primary CTA buttons, active nav item, links that trigger important actions
- #38BDF8 ONLY for: AI-related elements, score highlights, active states
- Metric cards on dashboard: use dark solid colors (#1D2B4F, #1A3A5C, #0F2744) — NOT gradient blues and purples
- Status badges: use semantic colors (green=success, amber=warning, red=danger, gray=neutral) — NOT blue for everything
- Info boxes: gray background (#F4F6FB) — NOT blue background
- Table row hover: light gray (#F4F6FB) — NOT blue tint
- Secondary buttons: white with gray border — NOT blue outline

Specifically FIX:
- Dashboard metric cards: remove purple and teal gradient backgrounds → use dark navy solid fills
- Pipeline column headers: keep #1D6BF3 (this is intentional brand blue)
- AI Pulse card dot: keep #38BDF8 (AI identity)
- All other colored backgrounds on info/secondary elements → change to gray

---

## 2. TYPOGRAPHY HIERARCHY

Ensure consistent text sizes across all pages:
- Page title (h1): 22px, weight 500
- Section title (h2): 18px, weight 500
- Card title (h3): 16px, weight 500
- Body text: 14px, weight 400
- Helper text / labels: 13px, color: var(--color-text-secondary)
- Micro text (badges, timestamps): 12px

Remove any inline font-size overrides that deviate from this scale.

---

## 3. FINNISH LANGUAGE AUDIT

Search ALL templates (app/templates/**/*.html) for English strings that should be Finnish.

Fix list (search and replace):
- "New Lead" → "Uusi liidi" (pipeline stage name — update in database seed/default data)
- "Contacted" → "Kontaktoitu"
- "Qualified" → "Kvalifioitu"
- "Proposal Sent" → "Tarjous lähetetty"
- "Closed Won" → "Voitettu"
- "Closed Lost" → "Hävitty"
- "Pipeline" (heading) → "Myyntiputki" or keep as "Pipeline" (both acceptable in Finnish B2B)
- "Dashboard" → "Kojelauta" or keep "Dashboard" (English acceptable)
- "Score" → keep as "Score" or "Pisteet" — be consistent across all pages
- "hot" tag → keep lowercase English (tags are technical)
- Button text: ALL buttons must be Finnish
- Form labels: ALL labels must be Finnish
- Error messages: ALL must be Finnish
- Toast messages: ALL must be Finnish
- Table column headers: ALL must be Finnish

Create a translation audit: scan all templates and list any remaining English user-facing strings.

---

## 4. WHITESPACE AND DENSITY

- Max content width: 1280px centered (already correct — verify it's applied everywhere)
- Cards: padding 20px (not less, not more) — verify consistency
- Table rows: height 48px — not 56px or 40px
- Section gaps: 24px between major sections, 16px between cards in a row
- Remove any section that is >50% empty space with no content
- Dashboard: if a card has 0 items and is not the "Today" section, collapse it to a smaller height

---

## 5. AI ELEMENT VISUAL IDENTITY

All AI-related UI elements should have a consistent look:
- Blue dot indicator: width 8px, height 8px, background #38BDF8, border-radius 50%
- AI badge: "AI" label with background rgba(56,189,248,0.1), color #38BDF8, border 1px solid rgba(56,189,248,0.3), border-radius 4px, font-size 11px
- AI cards/sections: left border 3px solid #38BDF8, background white
- AI recommendation text: always #38BDF8 for opportunity type, #EF4444 for risk type

Apply this consistently to:
- AI Pulse card on dashboard
- AI Playbook card on lead detail
- AI recommendation badges on pipeline cards
- ai_recommendation field wherever displayed

---

## 6. TESTS

test_ui_consistency (manual checklist for Cursor to verify):
- No purple or teal gradient backgrounds remain on metric cards
- All button labels are in Finnish
- All form labels are in Finnish
- All pipeline stage names are in Finnish (in default seed data)
- AI elements use #38BDF8 consistently
- No inline font-size below 12px
```

---

## Tiedostot

| Tiedosto | Muutos |
|---|---|
| `app/templates/dashboard/index.html` | V2.5-1: Tänään-osio + työlista |
| `app/dashboard/routes.py` | V2.5-1: /api/dashboard/today + /ai-worklist |
| `app/templates/leads/detail.html` | V2.5-2: AI Playbook -kortti |
| `app/leads/routes.py` | V2.5-2: /api/leads/<id>/playbook |
| `app/companies/models.py` | V2.5-3: Company + Contact mallit |
| `app/companies/routes.py` | V2.5-3: Yritykset + Kontaktit sivut |
| `app/templates/companies/` | V2.5-3: Yritykset, kontaktit, detail-sivut |
| `app/templates/settings/lead_settings.html` | V2.5-4: Hakuprofiili onboarding |
| `app/templates/components/empty_state.html` | V2.5-5: Empty state macro |
| `app/templates/**/*.html` | V2.5-5: Kaikki empty statet päivitetty |
| `app/templates/pipeline/index.html` | V2.5-6: Kortit, häviösyy-modal |
| `app/leads/models.py` | V2.5-6: lost_reason + lost_reason_note |
| `app/static/css/design-system.css` | V2.5-7: Värit, typografia, hierarkia |
| `migrations/` | V2.5-3 + V2.5-6 uudet migraatiot |
| `tests/` | Kaikki uudet testit per vaihe |
