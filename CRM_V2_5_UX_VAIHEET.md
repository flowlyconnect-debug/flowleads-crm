# FlowLeads CRM — V2.5 UX-vaiheet

**Pohja:** ChatGPT-analyysi + Claude-arvio  
**Järjestys:** Nopein vaikutus ensin → backend-muutokset myöhemmin  
**Workflow:** ChatGPT (rikastus) → Cursor (koodaus)

---

## UX-A: Sidebar-ryhmittely

```
Restructure the FlowLeads sidebar navigation into logical groups.
No backend changes needed — this is a pure template change.

---

## CURRENT SIDEBAR (flat list):
Kojelauta, Myyntiputki, Liidit, Yritykset, Kontaktit, Tehtävät, Kalenteri,
Sähköposti, Sekvenssit, Tarjoukset, Lomakkeet, Automaatiot, Raportit, Asetukset

## NEW SIDEBAR (grouped):

### Group 1 — Myynti
- Kojelauta (ti-home)
- Myyntiputki (ti-layout-kanban)
- Liidit (ti-user-plus)
- Yritykset (ti-building)
- Kontaktit (ti-users)

### Group 2 — Toiminta
- Tehtävät (ti-circle-check)
- Kalenteri (ti-calendar)
- Sähköposti (ti-mail)
- Sekvenssit (ti-mail-forward)

### Group 3 — Kasvu
- Lomakkeet (ti-forms)
- Automaatiot (ti-bolt)
- Tarjoukset → DISABLED (V3 placeholder)
  Style: opacity 0.4, cursor not-allowed, no href
  Tooltip on hover: "Tulossa pian"

### Group 4 — Hallinta
- Raportit (ti-chart-bar)
- Ennuste → DISABLED (V3 placeholder)
  Style: opacity 0.4, cursor not-allowed, no href
  Tooltip on hover: "Tulossa pian"
- Asetukset (ti-settings)
- Hakuprofiili (ti-settings-automation)

---

## IMPLEMENTATION

In app/templates/base.html (or sidebar partial), add group headers between nav items.

Group header style:
<span class="nav-group-label">Myynti</span>

CSS:
.nav-group-label {
  display: block;
  font-size: 11px;
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(255,255,255,0.35);
  padding: 16px 16px 6px;
  margin-top: 4px;
}

Disabled nav item style:
.nav-item-disabled {
  opacity: 0.4;
  cursor: not-allowed;
  pointer-events: none;
  position: relative;
}
.nav-item-disabled::after {
  content: "Tulossa pian";
  position: absolute;
  left: calc(100% + 8px);
  top: 50%;
  transform: translateY(-50%);
  background: rgba(0,0,0,0.8);
  color: white;
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 4px;
  white-space: nowrap;
  display: none;
}
.nav-item-disabled:hover::after {
  display: block;
  pointer-events: none;
}

The first group ("Myynti") has NO group label — it's the default, no need to label it.
Labels start from "Toiminta".

---

## ACTIVE STATE

Keep existing active state logic (blue background on active item).
Active item must still work — groups are visual only, no routing changes.
```

---

---

## UX-B: AI-drawer — toiminnallinen

```
Rebuild the AI assistant drawer (the floating button bottom-right) to show actionable alerts instead of generic suggestions.

---

## CURRENT PROBLEM
Drawer opens but shows nothing urgent → user closes it immediately. Wasted opportunity.

## NEW BEHAVIOR
Drawer shows: "X asiaa vaatii huomiota" with real data and action buttons per item.

---

## 1. DRAWER HEADER

Title: "FlowLeads AI"
Subtitle: "[N] asiaa vaatii huomiota" — N = total alert count from API
If N = 0: subtitle = "Kaikki kunnossa — ei kiireellisiä toimia"
           Show a green checkmark icon below + "Palaa myöhemmin"

---

## 2. ALERT ITEMS (loaded from API on drawer open)

Endpoint: GET /api/ai/alerts — requires login, scoped to current org

Returns array of alerts, max 5, prioritized:
[
  {
    "type": "new_lead",
    "lead_id": 3,
    "lead_name": "Matias Saarinen",
    "company": "Revealuxury",
    "message": "Uusi liidi saapunut — ei vielä käsitelty",
    "actions": ["view_lead", "create_task"]
  },
  {
    "type": "overdue_task",
    "task_id": 7,
    "lead_id": 2,
    "lead_name": "Pekka Virtanen",
    "message": "Follow-up myöhässä 2 pv",
    "actions": ["view_lead", "complete_task"]
  },
  {
    "type": "hot_lead_no_contact",
    "lead_id": 5,
    "lead_name": "Anna Mäkinen",
    "company": "Talo Oy",
    "score": 84,
    "days_no_contact": 9,
    "message": "Kuuma liidi — ei kontaktia 9 pv",
    "actions": ["view_lead", "send_email"]
  }
]

## Backend logic for /api/ai/alerts:
Priority order:
1. Overdue tasks (due_date < NOW, is_completed=False) — type: overdue_task
2. New leads unprocessed (created < 24h, no activities, stage = first stage) — type: new_lead
3. Hot leads no contact (score >= 70, no activity in 7+ days) — type: hot_lead_no_contact
4. Leads with ai_recommendation = risk type — type: ai_risk
Max 5 total. Scoped to organization_id.

---

## 3. ALERT ITEM UI (per item in drawer)

Each alert = a card with:
- Left color bar: red for overdue, blue for new_lead, amber for hot_lead_no_contact
- Icon: ti-alert-triangle (overdue/risk), ti-user-plus (new), ti-flame (hot)
- Message text (bold, 14px)
- Sub-text: "Syy: [why this matters]"
- Action buttons row (small, 12px):

Action button mapping:
- "view_lead" → "Avaa liidi →" (link to /leads/<id>)
- "create_task" → "Luo tehtävä" (AJAX: POST /tasks with lead_id prefilled, show success inline)
- "complete_task" → "Merkitse tehdyksi" (AJAX: PATCH /tasks/<id>/complete, remove item from list with fade animation)
- "send_email" → "Kirjoita sähköposti" (link to /leads/<id>?action=email which opens email compose)

---

## 4. DRAWER FOOTER

At bottom of drawer (always visible):
"Näytä kaikki →" link → /leads?filter=ai_priority (leads sorted by urgency)

---

## 5. FAB BADGE

The floating AI button (bottom-right) shows a red badge with alert count:
- If alerts > 0: red circle badge top-right of button with count number
- If alerts = 0: no badge
- Badge updates on page load (fetch /api/ai/alerts count on DOM ready)
- Badge CSS: position absolute, top -6px, right -6px, width 18px, height 18px,
  background #EF4444, color white, font-size 11px, border-radius 50%,
  display flex, align-items center, justify-content center

---

## 6. EMPTY STATE IN DRAWER

When no alerts:
- Icon: ti-circle-check (32px, green)
- Title: "Kaikki kunnossa"
- Body: "Ei kiireellisiä toimia tällä hetkellä. Palaa myöhemmin."
- No action buttons

---

## 7. LOADING STATE

When drawer opens and data is loading:
Show 3 skeleton cards (gray animated placeholder blocks, same height as alert cards)
Replace with real data when fetch completes.

---

## TESTS

test_ai_alerts_endpoint:
- Returns only current org's alerts
- Overdue tasks appear before hot leads
- Max 5 items returned
- Empty array returned when no alerts (not null)
- complete_task action removes task from alerts on next fetch
```

---

---

## UX-C: Dashboard — 3-rivi-rakenne

```
Rebuild the dashboard layout into 3 clear rows. Replace the current multiple-card layout.

---

## ROW 1 — Metrikkortti-rivi (4 cards)

Remove existing metric cards and replace with exactly these 4, in this order:
Left to right: Uudet liidit | Kuumat liidit | Tehtävät tänään | Pipeline-arvo

Card 1 — Uudet liidit:
- Value: COUNT leads WHERE organization_id=current AND created_at > NOW()-7d
- Label: "Uudet liidit (7 pv)"
- Trend: vs previous 7d period ("+X% vs edellinen vko" or "-X%")
- Background: dark navy #0F1E35

Card 2 — Kuumat liidit:
- Value: COUNT leads WHERE score >= 70 AND pipeline_stage NOT IN (Closed Won, Closed Lost)
- Label: "Kuumat liidit"
- Sub: "Score ≥ 70"
- Background: dark navy #0F1E35

Card 3 — Tehtävät tänään:
- Value: COUNT tasks WHERE is_completed=False AND due_date::date = TODAY
- Label: "Tehtävät tänään"
- If overdue tasks exist: show amber sub-text "X myöhässä"
- Background: dark navy #0F1E35

Card 4 — Pipeline-arvo:
- Value: SUM leads.deal_value WHERE pipeline_stage NOT IN (Closed Won, Closed Lost)
- Formatted: "€X,XXX" (or "Ei dataa" if no deal values set)
- Label: "Pipeline-arvo"
- Background: dark navy #0F1E35

All 4 cards same dark navy background. White text. No gradients, no purple, no teal.
Metric value: 28px, weight 500, white
Label: 13px, color rgba(255,255,255,0.6)
Trend/sub: 12px, color rgba(255,255,255,0.5)

---

## ROW 2 — AI-työlista (full width)

Card title: "AI:n ehdottama järjestys tänään"
Subtitle: "Aloita näistä"

Content: ordered list of 5 max actionable items.
Each item has:
- Number badge (1, 2, 3...)
- Action text in Finnish: "Soita [name] — [reason]"
- Sub-text reason (muted, 12px): the "why" behind the recommendation
- "Avaa →" link to the lead

Examples:
"1. Soita Matias Saariselle (Revealuxury)"
   sub: "Uusi liidi, ei kontaktia, score 84"
"2. Lähetä follow-up Pekka Virtaselle"
   sub: "Follow-up tehtävä myöhässä 2 pv"
"3. Käy läpi Anna Mäkinen"
   sub: "Ei kontaktia 9 pv, score 71"

This list is fetched from GET /api/dashboard/ai-worklist (build this endpoint).
Endpoint logic: combine overdue tasks + new unprocessed leads + hot leads no contact.
Rank by: (1) overdue > 2d, (2) hot + no contact > 7d, (3) new < 24h, (4) warm no contact > 14d.
For each item generate Finnish action text server-side using string templates.

Show skeleton loader while fetching.
If empty: "Ei kiireellisiä toimia tänään — hyvää työtä!" (green checkmark)

---

## ROW 3 — AI Pulse + Live aktiviteetti (2 columns, 1:1)

Left: AI Pulse card (keep existing — shows ai_recommendation items)
Right: Live aktiviteetti (keep existing — shows activity stream with 30s refresh)

These two cards remain exactly as they are. Just ensure they sit side by side in a 2-column grid.

---

## REMOVE from dashboard:
- Old separate "Tehtäväsi tänään" card
- Old separate "AI Pulse" card if it was standalone — merge into row 3 as described
- Any card that duplicates what's now in the AI-työlista row
- Workflow context bar at bottom (replace with row 1 metrics instead)

---

## LAYOUT CSS

.dashboard-row-1 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
.dashboard-row-2 { margin-bottom: 20px; }
.dashboard-row-3 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

@media (max-width: 768px) {
  .dashboard-row-1 { grid-template-columns: repeat(2, 1fr); }
  .dashboard-row-3 { grid-template-columns: 1fr; }
}

---

## TESTS

test_dashboard_metrics:
- Uudet liidit count is correct for 7-day window
- Kuumat liidit only counts score >= 70 and not closed
- Tehtävät tänään counts only today's tasks
- All metrics scoped to current org

test_ai_worklist:
- Returns max 5 items
- Items from other orgs never appear
- Empty list returns [] not null
```

---

---

## UX-D: Liidit-sivu — viimeisin aktiviteetti -sarake

```
Add "Viimeisin aktiviteetti" column to the leads list table. Simple but high value for prioritization.

---

## 1. NEW COLUMN

Add column "Viimeisin aktiviteetti" to leads list table (app/templates/leads/index.html).
Position: after "Score" column, before "Lisätty" column.

Column header: "VIIMEISIN AKTIVITEETTI"
Sortable: yes — clicking sorts by last_activity_at DESC/ASC

---

## 2. COLUMN DATA

For each lead row, show the most recent Activity for that lead.
Display format:
- If activity today: "Sähköposti tänään" / "Soitettu tänään" (activity type in Finnish)
- If activity 1-6 days ago: "[type] [X] pv sitten" — gray text
- If activity 7-13 days ago: "[type] [X] pv sitten" — amber text
- If activity 14+ days ago: "Ei kontaktia [X] pv" — red text
- If no activities ever: "Ei kontaktia" — muted gray

Activity type display mapping:
- email → "Sähköposti"
- call → "Soitettu"
- note → "Muistiinpano"
- meeting → "Tapaaminen"
- stage_change → "Vaihe muuttunut"
- ai_score → "AI pisteytys"

---

## 3. BACKEND

In the leads list query (app/leads/routes.py or services.py):
Use a subquery or joinedload to get the latest Activity per lead efficiently.

Recommended approach (SQLAlchemy):
latest_activity = db.session.query(
    Activity.lead_id,
    db.func.max(Activity.created_at).label('last_at'),
).filter(
    Activity.organization_id == current_org.id
).group_by(Activity.lead_id).subquery()

Join to leads query:
leads = Lead.query.filter_by(organization_id=current_org.id)\
    .outerjoin(latest_activity, Lead.id == latest_activity.c.lead_id)\
    .add_columns(latest_activity.c.last_at)\
    .order_by(...)

Pass last_at per lead to template.
In template: compute days_since = (now - last_at).days and apply color logic.

---

## 4. SORTING

Allow sorting by last_activity_at:
GET /leads?sort=last_activity&order=asc (no contact first) or desc (most recent first)
Default sort: keep existing (created_at desc)

Add sort indicator (↑↓) to column header when active.

---

## 5. FILTER SHORTCUT

Add a quick filter chip above the table:
"Ei kontaktia 7+ pv" — when clicked, filters to leads with last_activity_at < NOW()-7d OR no activities at all.

---

## TESTS

test_last_activity_column:
- Lead with no activities shows "Ei kontaktia"
- Lead with activity today shows "tänään" text
- Lead with activity 15 days ago shows red text
- Sorting by last_activity works correctly
- Cross-tenant: only current org's activities counted
```

---

---

## UX-E: Pipeline-kortit — myyntilogiikka

```
Update pipeline kanban cards to show sales-relevant context on every card.

---

## UPDATED CARD LAYOUT

Each pipeline card shows (top to bottom):

LINE 1: [Company name] — bold, 14px
LINE 2: [Lead first name last name] — muted, 13px
LINE 3: [Score badge] [Stage-days badge]
LINE 4 (if exists): [AI recommendation] — small colored text
LINE 5 (if exists): [Next task] — small text with due date color
LINE 6: [Last contact] — small muted text

---

## Score badge:
- score >= 80: background #FEE2E2, color #991B1B, text "🔥 [score]"
- score 60-79: background #FEF3C7, color #92400E, text "[score]"
- score < 40: background #F3F4F6, color #6B7280, text "[score]"
- No score: hide badge entirely

## Stage-days badge (days in current stage):
Add field to Lead: stage_changed_at (DateTime, updated whenever pipeline_stage_id changes)
If stage_changed_at is null, fall back to lead.created_at.
days_in_stage = (NOW - stage_changed_at).days

- < 3 days: no badge shown
- 3-7 days: gray pill "[N] pv vaiheessa"
- 8-14 days: amber pill "[N] pv vaiheessa"
- > 14 days: red pill "[N] pv — siirrä eteenpäin"

## AI recommendation (from lead.ai_recommendation property):
- Show as one line, max 32 chars, truncated
- Color: red for risk type, blue for opportunity, amber for followup
- Icons: ti-alert-triangle (risk), ti-star (opportunity), ti-clock (followup)
- If no recommendation: hide this line entirely (do not show empty space)

## Next task:
Query most recent incomplete task for this lead (preloaded with the kanban query).
- If overdue: "[title]" in red, "(myöhässä)" suffix
- If due today: "[title]" in amber, "(tänään)"
- If due future: "[title]" in gray, "([relative date])"
- If no task: hide this line entirely

## Last contact:
From days_since_last_activity (preloaded):
- 0 days: hide (contacted today — all good)
- 1-6 days: gray "Kontakti [N] pv sitten"
- 7-13 days: amber "Kontakti [N] pv sitten"
- 14+ days: red "Ei kontaktia [N] pv"

## Left accent stripe color:
3px left border color on each card:
- score >= 80: #EF4444
- score 60-79: #F59E0B
- score 40-59: #1D6BF3
- score < 40 or no score: #E5E7EB

---

## PERFORMANCE

The kanban loads all leads for the org — avoid N+1 queries.
Use SQLAlchemy joinedload or a single efficient query that gets:
- Latest activity per lead (last_activity_at)
- Next incomplete task per lead (title + due_date)
- Lead fields including ai_recommendation property

Add a computed property to Lead model:
@property
def days_in_current_stage(self):
    ref = self.stage_changed_at or self.created_at
    if not ref:
        return 0
    return (datetime.utcnow() - ref).days

---

## LOST REASON MODAL

When lead is dragged into "Closed Lost" (or "Hävitty") stage:
1. Intercept drop (SortableJS onEnd event)
2. Before calling the stage-change API, show an inline mini-modal:

Modal HTML (rendered inline in the page, shown/hidden with JS):
<div id="lost-reason-modal">
  <p>Miksi tämä liidi hävisi?</p>
  <select id="lost-reason-select">
    <option value="">Valitse syy...</option>
    <option value="no_response">Ei vastannut</option>
    <option value="wrong_target">Väärä kohderyhmä</option>
    <option value="no_budget">Ei budjettia</option>
    <option value="not_timely">Ei ajankohtainen</option>
    <option value="competitor">Kilpailija voitti</option>
    <option value="other">Muu syy</option>
  </select>
  <input type="text" id="lost-reason-note" placeholder="Lisätietoja..." style="display:none">
  <button id="lost-confirm">Vahvista</button>
  <button id="lost-cancel">Peruuta</button>
</div>

JS logic:
- Show "Lisätietoja" text input only when "Muu syy" selected
- "Vahvista" calls PATCH /leads/<id>/stage with { stage_id: X, lost_reason: Y, lost_reason_note: Z }
- "Peruuta" reverts the card to its original column (SortableJS cancel)
- Require a selection before confirming (show error if empty)

Backend: save lost_reason + lost_reason_note on Lead model (add these fields + migration).
Log to audit_log: action='lead_lost', metadata: { reason, note }
Create Activity: type='stage_change', content='Hävitty: [lost_reason_label]'

---

## TESTS

test_card_data_loading:
- Pipeline query uses no N+1 queries (check query count)
- days_in_current_stage updates when stage changes
- stage_changed_at is set when lead moves to new stage

test_lost_reason:
- Cannot move to Closed Lost without selecting a reason (frontend + backend validation)
- Backend returns 400 if lost_reason missing when stage = Closed Lost
- lost_reason saved correctly to Lead model
```

---

---

## UX-F: Tehtävät-sivu — ryhmitelty rakenne

```
Restructure the tasks page into three clear sections. Remove duplicate empty cards.

---

## NEW PAGE STRUCTURE

Page title: "Tehtävät"
Right side: "+ Uusi tehtävä" button

### Section 1 — Myöhässä
Show only if there are overdue tasks (due_date < TODAY AND is_completed = False).
Section header: "Myöhässä" with red badge showing count.
Header background: light red tint (#FEF2F2), left border 3px solid #EF4444.

Each task row:
- Checkbox (complete on click, AJAX)
- Task title
- Lead name (link to lead)
- Due date in red: "Myöhässä [N] pv"
- Action: "Avaa liidi →"

### Section 2 — Tänään
Header: "Tänään" with count badge (gray or blue).
Query: due_date = TODAY AND is_completed = False.

Each task row:
- Checkbox
- Task title
- Lead name (link)
- Due time if set, else "Tänään"
- Priority badge (if high priority: red dot)

Empty state for this section: "Ei tehtäviä tänään — olet ajan tasalla!" (green check icon, no card wrapper)

### Section 3 — Tämä viikko
Header: "Tämä viikko" — shows Mon-Sun of current week.
Query: due_date between TOMORROW and END_OF_WEEK AND is_completed = False.

Each task row:
- Checkbox
- Task title
- Lead name
- Due day: "To 5.6." etc.

Empty state: "Ei tulevia tehtäviä tällä viikolla"

### Section 4 — Myöhemmin (collapsed by default)
Header: "Myöhemmin ▾" — click to expand.
Query: due_date > END_OF_WEEK AND is_completed = False.
Collapsed by default — not shown until user clicks.

---

## TASK CREATION (inline at top)

Keep existing "+ Uusi tehtävä" button.
On click: show inline form at top of page (not modal):
- Title (text, required)
- Liidi (searchable dropdown, optional)
- Eräpäivä (date picker, defaults to today)
- Prioriteetti (Low/Normal/High toggle)
- Save: "Lisää tehtävä" | Cancel: "Peruuta"

After save: new task appears in correct section without page reload (AJAX + DOM insert).

---

## COMPLETED TASKS

Add a "Näytä tehdyt tehtävät" toggle link at bottom of page.
When clicked: shows last 20 completed tasks in a muted list below the active sections.
These are NOT shown by default.

---

## REMOVE

Remove any duplicate empty state card that currently shows when tasks list is empty.
Remove any section that shows a second "create task" form if one already exists.
There should be exactly ONE task creation form on the page.

---

## TESTS

test_task_sections:
- Overdue section only appears when overdue tasks exist
- Tänään section shows tasks with due_date = today
- Tämä viikko shows tomorrow through end of week
- Completed tasks hidden by default
- Checkbox complete updates is_completed via AJAX
- Cross-tenant: only current org's tasks shown
```

---

---

## UX-G: Kalenteri + Yritykset-parannukset

```
Two improvements in one pass: calendar unified view and companies table enhancements.

---

## PART 1 — KALENTERI: Unified view

### Current problem:
Two identical views (Tänään / Tämä viikko) showing the same empty message.

### New structure:

#### View toggle (top of calendar page):
Two tabs: [Päivä] [Viikko]
Default: Viikko tab active.

#### Päivä-tab:
Shows today's events as a time-based list (not grid).
Each event:
- Time (e.g. "14:00–15:00")
- Title
- Lead name (if linked)
- Location or "Google Meet" badge

Empty state: "Ei tapaamisia tänään. Haluatko luoda tapaamisen kuumimmalle liidille?"
CTA button: "Luo tapaaminen" → /calendar/new

#### Viikko-tab:
7-column grid (Mon–Sun).
Each day column shows:
- Day label: "Ma 2.6." with circle highlight if today
- Events as colored blocks (same as existing implementation)

Empty week: show empty state only if current week has zero events.
Empty state: "Ei tapaamisia tällä viikolla"
CTA: "Luo tapaaminen"

#### Tulevat tapaamiset (right sidebar panel, always visible):
Title: "Tulevat tapaamiset"
List: next 5 events after now, sorted by start_time ASC.
Each: date + time, title, lead name.
Empty: "Ei tulevia tapaamisia"

---

## PART 2 — YRITYKSET-SIVU: Table improvements

### Quick filter chips (above table, no separate filter panel):

[Kaikki] [Asiakkaat] [Prospektit] [Kumppanit] [Toimittajat]

Active chip: blue background, white text.
Inactive: gray border, gray text.
Filter is applied client-side if < 200 companies, server-side (query param) if > 200.

### Table columns (exact order):

Yritys | Toimiala | Kaupunki | Kontaktit | Liidit | Omistaja | Luotu | Tyyppi-badge

- Yritys: company name as clickable link to /companies/<id>
- Toimiala: plain text
- Kaupunki: plain text (from city field)
- Kontaktit: count of contacts — "2 kontaktia" or "—"
- Liidit: count of open leads — "3 liidiä" or "—" (closed lost not counted)
- Omistaja: name of assigned user or "—"
- Luotu: relative date "3 pv sitten"
- Tyyppi-badge: right-aligned pill
  - Asiakas: green
  - Prospekti: blue
  - Kumppani: purple
  - Toimittaja: gray

### Row hover:
On hover show row-actions (opacity 0 → 1):
- Edit icon (ti-pencil)
- Delete icon (ti-trash) with confirm

### Search:
Search bar above table: searches by company name (client-side filtering for small sets).

### Empty state:
"Ei yrityksiä vielä"
Body: "Yritykset luodaan automaattisesti liidien pohjalta tai voit lisätä ne käsin."
CTA: "+ Lisää yritys"

---

## TESTS

test_calendar_tabs:
- Päivä tab shows only today's events
- Viikko tab shows Mon-Sun of current week
- Tulevat tapaamiset shows next 5 events
- All events scoped to current org

test_companies_filter:
- Asiakkaat filter shows only type='customer'
- Liidit count excludes Closed Lost leads
- Search filters by name correctly
- Cross-tenant: Org A cannot see Org B's companies
```

---

## Yhteenveto muutoksista

| Tiedosto | Vaihe |
|---|---|
| `app/templates/base.html` (sidebar) | UX-A |
| `app/templates/ai/` tai `base.html` (drawer) | UX-B |
| `app/api/routes.py` (/api/ai/alerts) | UX-B |
| `app/templates/dashboard/index.html` | UX-C |
| `app/dashboard/routes.py` (/api/dashboard/ai-worklist) | UX-C |
| `app/templates/leads/index.html` | UX-D |
| `app/leads/routes.py` (viimeisin aktiviteetti query) | UX-D |
| `app/templates/pipeline/index.html` | UX-E |
| `app/leads/models.py` (stage_changed_at, lost_reason) | UX-E |
| `migrations/` | UX-E |
| `app/templates/tasks/index.html` | UX-F |
| `app/tasks/routes.py` | UX-F |
| `app/templates/calendar/index.html` | UX-G |
| `app/templates/companies/index.html` | UX-G |
