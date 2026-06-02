# FlowLeads CRM — Pipeline, Dashboard & Kalenteri
## Tarkat UI-vaiheet referenssikuvien mukaan

**Flowly-värit (kaikissa käytetään näitä):**
```css
--sidebar:       #0B0F1A   /* Tumma sinimusta sidebar */
--blue-primary:  #1D6BF3   /* Flowly CTA-sininen */
--blue-light:    #38BDF8   /* Flowly korostussininen */
--bg:            #F4F6FB   /* Sisältöalueen tausta */
--card:          #FFFFFF
--text:          #0F1117
--text-muted:    #6B7280
--success:       #10B981
--warning:       #F59E0B
--danger:        #EF4444
```

---

## VAIHE UI-P1 — Pipeline (sininen kanban, referenssikuva 1)
**Referenssi:** Kuva jossa sininen pipeline — sarakeotsikoissa sininen palkki, kortit sinisiä, arvo näkyy joka kortissa
**Arvio:** 1–2 päivää

### Cursor-prompt

```
Redesign the FlowLeads CRM pipeline kanban view to match this exact reference style:
- Column headers: solid #1D6BF3 blue background bar with white text
- Lead cards: white background with subtle border, company name bold and prominent
- Each card shows VALUE prominently with € symbol
- Column header shows total € value directly under the stage name
- Action icons on cards (email icon, phone icon, warning icon)
- Some cards have colored left-border highlights (orange=warm lead, red=urgent, yellow=follow-up)
- Clean, data-dense layout — no wasted space
- All text in Finnish

File: app/templates/leads/pipeline.html + app/static/css/pipeline.css

DO NOT change any Python backend or LeadService logic.

=== COLUMN HEADER ===

HTML structure:
<div class="pipeline-col-header">
  <div class="pipeline-col-title-bar">
    <span class="pipeline-col-name">New Lead</span>
    <span class="pipeline-col-count">7</span>
  </div>
  <div class="pipeline-col-value">€18,500</div>
</div>

CSS:
.pipeline-col-header {
  background: #1D6BF3;
  border-radius: 8px 8px 0 0;
  overflow: hidden;
}
.pipeline-col-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
}
.pipeline-col-name {
  color: #FFFFFF;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.01em;
}
.pipeline-col-count {
  background: rgba(255,255,255,0.25);
  color: white;
  font-size: 11px;
  font-weight: 800;
  padding: 1px 8px;
  border-radius: 20px;
}
.pipeline-col-value {
  background: rgba(0,0,0,0.15);
  color: rgba(255,255,255,0.90);
  font-size: 13px;
  font-weight: 700;
  padding: 5px 14px;
  border-top: 1px solid rgba(255,255,255,0.15);
}

=== PIPELINE WRAPPER ===

.pipeline-wrapper {
  display: flex;
  gap: 10px;
  padding: 20px 28px 32px;
  overflow-x: auto;
  align-items: flex-start;
  min-height: calc(100vh - 120px);
}
.pipeline-column {
  min-width: 200px;
  max-width: 200px;
  flex-shrink: 0;
  background: #ECEEF5;
  border-radius: 10px;
  border: 1px solid #E4E7EF;
  overflow: hidden;
}
.pipeline-cards {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 60px;
}

=== LEAD CARD ===

HTML structure per card:
<div class="lead-card" data-lead-id="{{ lead.id }}" draggable="true">
  <div class="lead-card-accent" style="background: {{ accent_color }};"></div>
  <div class="lead-card-body">
    <div class="lead-card-company">{{ lead.company or lead.first_name }}</div>
    <div class="lead-card-contact">{{ lead.first_name }} {{ lead.last_name }}</div>
    <div class="lead-card-value-row">
      <span class="lead-card-value-label">Arvo</span>
      <span class="lead-card-value">€{{ lead.deal_value or '0' }}</span>
    </div>
    <div class="lead-card-actions-row">
      <a href="/leads/{{ lead.id }}/email/compose" class="lead-action-icon" title="Sähköposti">✉</a>
      {% if lead.score and lead.score < 40 %}
        <span class="lead-action-icon warning" title="Matala score">⚠</span>
      {% endif %}
      {% if lead.assigned_to %}
        <span class="lead-action-icon user" title="{{ lead.assigned_user.email }}">👤</span>
      {% endif %}
    </div>
  </div>
</div>

CSS:
.lead-card {
  background: #FFFFFF;
  border: 1px solid #E4E7EF;
  border-radius: 7px;
  display: flex;
  overflow: hidden;
  cursor: grab;
  transition: box-shadow 150ms ease, transform 150ms ease;
  position: relative;
}
.lead-card:hover {
  box-shadow: 0 4px 12px rgba(29,107,243,0.15);
  transform: translateY(-1px);
  border-color: #BFDBFE;
}
.lead-card.dragging {
  opacity: 0.5;
  box-shadow: 0 8px 24px rgba(29,107,243,0.25);
}

/* Left accent stripe — color per priority */
.lead-card-accent {
  width: 4px;
  flex-shrink: 0;
  background: #1D6BF3;  /* default blue */
}
/* Override colors:
   orange = #F59E0B (warm lead)
   red    = #EF4444 (urgent)
   green  = #10B981 (hot lead, score > 80)
   yellow = #FCD34D (follow up needed)
   grey   = #9CA3AF (cold)
*/
/* Set in Jinja based on lead.score or lead.tags */

.lead-card-body {
  padding: 9px 10px;
  flex: 1;
  min-width: 0;
}
.lead-card-company {
  font-size: 13px;
  font-weight: 700;
  color: #0F1117;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 2px;
}
.lead-card-contact {
  font-size: 11px;
  color: #6B7280;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 8px;
}
.lead-card-value-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 8px;
  padding-top: 6px;
  border-top: 1px solid #F0F2F8;
}
.lead-card-value-label {
  font-size: 9px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.lead-card-value {
  font-size: 13px;
  font-weight: 700;
  color: #0F1117;
}
.lead-card-actions-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.lead-action-icon {
  width: 24px;
  height: 24px;
  border-radius: 5px;
  background: #EBF2FF;
  color: #1D6BF3;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  text-decoration: none;
  cursor: pointer;
  border: 1px solid #BFDBFE;
  transition: background 120ms;
}
.lead-action-icon:hover { background: #1D6BF3; color: white; }
.lead-action-icon.warning { background: #FEF3C7; color: #D97706; border-color: #FDE68A; }
.lead-action-icon.user { background: #F3F4F6; color: #6B7280; border-color: #E5E7EB; }

=== ACCENT COLOR LOGIC (Jinja) ===

Determine left stripe color in template:
{% if lead.score >= 80 %}
  {% set accent = '#10B981' %}  {# green — korkea score #}
{% elif lead.score >= 60 %}
  {% set accent = '#1D6BF3' %}  {# blue — normaali #}
{% elif lead.score >= 40 %}
  {% set accent = '#F59E0B' %}  {# orange — seuraa #}
{% elif lead.score %}
  {% set accent = '#EF4444' %}  {# red — matala score #}
{% else %}
  {% set accent = '#9CA3AF' %}  {# grey — ei scorea #}
{% endif %}

=== ADD LEAD BUTTON AT COLUMN BOTTOM ===

<div class="pipeline-add-lead" onclick="openAddLeadModal('{{ stage.id }}')">
  + Lisää liidi
</div>

.pipeline-add-lead {
  margin: 6px 8px 8px;
  padding: 7px;
  border-radius: 6px;
  border: 1.5px dashed #BFDBFE;
  color: #1D6BF3;
  font-size: 12px;
  font-weight: 500;
  text-align: center;
  cursor: pointer;
  transition: all 120ms;
  background: transparent;
}
.pipeline-add-lead:hover {
  background: #EBF2FF;
  border-color: #1D6BF3;
}

=== PAGE HEADER ===

<div class="page-header">
  <div>
    <h1>Pipeline</h1>
    <p class="page-subtitle">{{ total_leads }} liidiä · €{{ total_value }} yhteensä</p>
  </div>
  <div style="display:flex; gap:8px; align-items:center;">
    <div class="search-input-wrapper">
      <input type="text" placeholder="Hae nimi, yritys..." class="pipeline-search">
    </div>
    <button class="btn btn-secondary btn-sm" onclick="toggleFilters()">Suodattimet</button>
    <a href="/leads/new" class="btn btn-primary btn-sm">+ Lisää liidi</a>
  </div>
</div>

=== DRAG AND DROP ===

Use SortableJS CDN:
<script src="https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js"></script>

Initialize:
document.querySelectorAll('.pipeline-cards').forEach(col => {
  new Sortable(col, {
    group: 'pipeline',
    animation: 150,
    ghostClass: 'lead-card-ghost',
    onEnd(evt) {
      if (evt.from !== evt.to) {
        fetch(`/leads/${evt.item.dataset.leadId}/stage`, {
          method: 'POST',
          headers: {'Content-Type':'application/json','X-CSRFToken': window.csrfToken},
          body: JSON.stringify({stage_id: evt.to.dataset.stageId})
        }).then(() => updateColumnTotals());
      }
    }
  });
});

.lead-card-ghost {
  opacity: 0.25;
  background: #EBF2FF !important;
  border: 2px dashed #1D6BF3 !important;
}

function updateColumnTotals() {
  // Recalculate each column's total value from card data-value attributes
  document.querySelectorAll('.pipeline-column').forEach(col => {
    const cards = col.querySelectorAll('.lead-card');
    let total = 0;
    cards.forEach(c => total += parseFloat(c.dataset.value || 0));
    col.querySelector('.pipeline-col-value').textContent =
      '€' + total.toLocaleString('fi-FI');
  });
}
```

### ✅ Hyväksymiskriteerit
- [ ] Sarakeotsikoissa sininen `#1D6BF3` taustapalkki
- [ ] Jokaisen sarakkeen alla näkyy € summa mustalla taustalla
- [ ] Kortit ovat valkoisia, vasen palkki kertoo score-värin
- [ ] Arvo näkyy jokaisessa kortissa "Arvo — €X"
- [ ] Pikakuvaikkeet (sähköposti, varoitus, käyttäjä) näkyvät
- [ ] Drag-and-drop toimii sarakkeiden välillä
- [ ] Sarakkeen arvo päivittyy dragin jälkeen

---

## VAIHE UI-D1 — Dashboard (Coupler.io-tyyli, referenssikuva 2)
**Referenssi:** Värilliset metric-kortit ylärivillä, iso viivakuvaaja, donut-kaaviot oikealla
**Arvio:** 2 päivää

### Cursor-prompt

```
Redesign the FlowLeads CRM dashboard to exactly match Coupler.io CRM dashboard style.
Reference image shows: 8 colored metric cards in 2 rows, large Won Deals line chart,
Deals Projection chart, Sales Pipeline donut, Deal Loss Reasons donut, and right filter panel.
Use Flowly brand colors throughout.

File: app/templates/dashboard/index.html + app/static/css/dashboard.css

=== METRIC CARDS — ROW 1 (4 cards, dark colored backgrounds) ===

Cards use SOLID DARK COLORED backgrounds like Coupler.io — not light/soft colors.

Card colors mapped to Flowly brand:
  Card 1 "Uudet liidit":        background #1D6BF3  (Flowly primary blue)
  Card 2 "Voitetut kaupat":     background #0EA5E9  (Flowly highlight blue)
  Card 3 "Pipeline-arvo":       background #7C3AED  (purple — premium feel)
  Card 4 "Avg. päiviä sulkuun": background #0F766E  (teal)

HTML per card:
<div class="metric-card" style="--card-color: #1D6BF3;">
  <div class="metric-card-inner">
    <div class="metric-label">UUDET LIIDIT</div>
    <div class="metric-value">{{ stats.new_leads }}</div>
    <div class="metric-delta {% if stats.lead_delta >= 0 %}up{% else %}down{% endif %}">
      {% if stats.lead_delta >= 0 %}▲{% else %}▼{% endif %}
      {{ stats.lead_delta|abs }}% vs. edellinen kk
    </div>
  </div>
</div>

CSS:
.metrics-grid-row1 {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
  margin-bottom: 12px;
}
.metric-card {
  background: var(--card-color);
  border-radius: 10px;
  padding: 20px;
  color: white;
  position: relative;
  overflow: hidden;
  min-height: 110px;
}
/* Subtle pattern overlay like Coupler.io */
.metric-card::after {
  content: '';
  position: absolute;
  top: -20px; right: -20px;
  width: 80px; height: 80px;
  border-radius: 50%;
  background: rgba(255,255,255,0.08);
}
.metric-label {
  font-size: 10px;
  font-weight: 700;
  color: rgba(255,255,255,0.70);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 10px;
}
.metric-value {
  font-size: 36px;
  font-weight: 800;
  color: white;
  line-height: 1;
  letter-spacing: -1px;
  margin-bottom: 10px;
}
.metric-delta {
  font-size: 12px;
  font-weight: 500;
  color: rgba(255,255,255,0.75);
}
.metric-delta.up   { color: #A7F3D0; }
.metric-delta.down { color: #FCA5A5; }

=== METRIC CARDS — ROW 2 (4 cards, same style different colors) ===

  Card 5 "Pipeline-arvo":       background #1E40AF  (dark blue)
  Card 6 "Avoimet kaupat":      background #065F46  (dark green)
  Card 7 "Painotettu arvo":     background #92400E  (dark amber)
  Card 8 "Avg. kaupan ikä":     background #1F2937  (dark charcoal)

=== MAIN CONTENT AREA — 2-column layout ===

.dashboard-main {
  display: grid;
  grid-template-columns: 1fr 340px;
  gap: 16px;
  margin-bottom: 16px;
}

LEFT COLUMN — Charts:

A. "Voitetut kaupat — viimeiset 12 kk" (large line chart)

<div class="card">
  <div class="card-header">
    <span class="card-title">Voitetut kaupat — viimeiset 12 kk</span>
    <div style="display:flex;gap:12px;align-items:center;">
      <span class="chart-legend"><span class="legend-dot" style="background:#1D6BF3"></span>Suljettu arvo</span>
      <span class="chart-legend"><span class="legend-dot" style="background:#38BDF8"></span>Voitetut kaupat</span>
    </div>
  </div>
  <div class="card-body" style="padding:0 20px 20px;">
    <div style="position:relative;height:220px;">
      <canvas id="wonDealsChart"></canvas>
    </div>
  </div>
</div>

Chart.js config (TWO y-axes like Coupler.io):
{
  type: 'line',
  data: {
    labels: monthLabels,
    datasets: [
      {
        label: 'Suljettu arvo (€)',
        data: closedValues,
        borderColor: '#1D6BF3',
        backgroundColor: 'rgba(29,107,243,0.06)',
        fill: true,
        tension: 0.4,
        pointRadius: 4,
        pointBackgroundColor: '#1D6BF3',
        yAxisID: 'y',
      },
      {
        label: 'Voitetut kaupat',
        data: wonCounts,
        borderColor: '#38BDF8',
        backgroundColor: 'transparent',
        tension: 0.4,
        pointRadius: 4,
        pointBackgroundColor: '#38BDF8',
        borderDash: [],
        yAxisID: 'y1',
      }
    ]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, border: { display: false }, ticks: { color: '#9CA3AF', font: { size: 11 } } },
      y: { grid: { color: '#F0F2F8' }, border: { display: false }, ticks: { color: '#9CA3AF', font: { size: 11 }, callback: v => '€'+v.toLocaleString() } },
      y1: { position: 'right', grid: { display: false }, border: { display: false }, ticks: { color: '#9CA3AF', font: { size: 11 } } }
    }
  }
}

B. "Myyntiennuste — seuraavat 12 kk" (second large chart below)

Same structure but:
- Two datasets: "Ennustettu arvo" (#7C3AED) + "Erääntyvät kaupat" (#1D6BF3 dashed)
- Y-axis shows € values
- X-axis shows future months

RIGHT COLUMN — Filter panel + donuts:

A. FILTER PANEL (like Coupler.io right sidebar):

<div class="card dashboard-filter-panel">
  <div class="card-body">
    <div class="filter-group">
      <label class="filter-label">Raportin aikaväli</label>
      <div class="date-range-row">
        <input type="date" class="form-input form-input-sm" value="{{ start_date }}">
        <span>—</span>
        <input type="date" class="form-input form-input-sm" value="{{ end_date }}">
      </div>
    </div>
    <div class="filter-group">
      <label class="filter-label">Vastuuhenkilö</label>
      <select class="form-input form-input-sm">
        <option>Kaikki</option>
        {% for user in org_users %}<option>{{ user.email }}</option>{% endfor %}
      </select>
    </div>
    <div class="filter-group">
      <label class="filter-label">Pipeline-vaihe</label>
      <select class="form-input form-input-sm">
        <option>Kaikki</option>
        {% for stage in stages %}<option>{{ stage.name }}</option>{% endfor %}
      </select>
    </div>
    <button class="btn btn-primary" style="width:100%;margin-top:8px;">Hae</button>
  </div>
</div>

.dashboard-filter-panel { position: sticky; top: 20px; }
.filter-group { margin-bottom: 16px; }
.filter-label { font-size: 11px; font-weight: 600; color: #6B7280; text-transform: uppercase; letter-spacing: 0.05em; display: block; margin-bottom: 6px; }
.date-range-row { display: flex; gap: 8px; align-items: center; }
.form-input-sm { padding: 6px 10px; font-size: 13px; }

B. "Myyntiputki" DONUT CHART:

<div class="card">
  <div class="card-header"><span class="card-title">Myyntiputki</span></div>
  <div class="card-body">
    <div style="position:relative;height:180px;">
      <canvas id="pipelineDonut"></canvas>
    </div>
    <div class="donut-legend">
      {% for stage in pipeline_stages %}
      <div class="donut-legend-item">
        <span class="legend-dot" style="background:{{ stage.color }}"></span>
        <span class="legend-name">{{ stage.name }}</span>
        <span class="legend-pct">{{ stage.percentage }}%</span>
      </div>
      {% endfor %}
    </div>
  </div>
</div>

Chart.js donut config:
{
  type: 'doughnut',
  data: {
    labels: stageNames,
    datasets: [{
      data: stagePercentages,
      backgroundColor: ['#1D6BF3','#38BDF8','#7C3AED','#F59E0B','#10B981','#EF4444'],
      borderWidth: 0,
      hoverOffset: 4,
    }]
  },
  options: {
    cutout: '68%',
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: false },
      tooltip: { callbacks: { label: ctx => ctx.label + ': ' + ctx.parsed + '%' } }
    }
  }
}

C. "Kaupan häviösyyt" DONUT CHART:

Same donut structure, data from lost leads:
  - Kiireettömyys, Hintaliian korkea, Parempi vaihtoehto, Feature puuttuu, Budjettirajoitus
  Colors: #EF4444, #F59E0B, #6B7280, #8B5CF6, #1D6BF3

.donut-legend { margin-top: 12px; }
.donut-legend-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid #F0F2F8; }
.donut-legend-item:last-child { border-bottom: none; }
.legend-dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
.legend-name { font-size: 12px; color: #374151; flex: 1; }
.legend-pct { font-size: 12px; font-weight: 700; color: #0F1117; }

=== PAGE HEADER ===

<div class="page-header">
  <div>
    <h1>Hei, {{ current_user.email.split('@')[0] }} 👋</h1>
    <p class="page-subtitle">Tässä on tänään — {{ current_date }}</p>
  </div>
  <div style="display:flex;gap:8px;align-items:center;">
    <span style="font-size:13px;color:#6B7280;">{{ org.name }}</span>
    <div class="btn-group">
      <button class="btn btn-secondary btn-sm {% if period==1 %}active{% endif %}" onclick="setPeriod(1)">Tänään</button>
      <button class="btn btn-secondary btn-sm {% if period==7 %}active{% endif %}" onclick="setPeriod(7)">7 pv</button>
      <button class="btn btn-primary btn-sm {% if period==30 %}active{% endif %}" onclick="setPeriod(30)">30 pv</button>
    </div>
    <a href="/reports" class="btn btn-secondary btn-sm">Raportit</a>
  </div>
</div>

.btn-group { display: flex; }
.btn-group .btn { border-radius: 0; border-right: none; }
.btn-group .btn:first-child { border-radius: 8px 0 0 8px; }
.btn-group .btn:last-child { border-radius: 0 8px 8px 0; border-right: 1px solid #E4E7EF; }
.btn-group .btn.active { background: #1D6BF3; color: white; border-color: #1D6BF3; }
```

### ✅ Hyväksymiskriteerit
- [ ] 8 metric-korttia kahdella rivillä, jokainen eri väri
- [ ] "Voitetut kaupat" viivakuvaaja kahdella Y-akselilla
- [ ] "Myyntiennuste" kuvaaja tulevaisuuteen
- [ ] Oikealla suodatinpaneeli (aikaväli, henkilö, vaihe)
- [ ] Myyntiputki-donut oikealla Flowly-väreillä
- [ ] Häviösyyt-donut oikealla

---

## VAIHE UI-K1 — Kalenteri (kuukausikalenteri, referenssikuva 3)
**Referenssi:** Täysi kuukausikalenteri, värilliset tapaamisblokit, oikea sivupaneeli, viikko/päivä-valinnat
**Arvio:** 2 päivää

### Cursor-prompt

```
Redesign the FlowLeads CRM calendar view to match a full monthly calendar with
colored event blocks, right detail panel, and month/week/day view toggles.
Reference: calendar screenshot with blue/green/orange colored event blocks on calendar grid.
Use Flowly brand colors.

File: app/templates/calendar/index.html + app/static/css/calendar.css

=== PAGE LAYOUT ===

.calendar-page {
  display: grid;
  grid-template-columns: 1fr 320px;
  gap: 0;
  height: calc(100vh - 80px);
  overflow: hidden;
}
.calendar-main { overflow-y: auto; padding: 20px 20px 20px 28px; }
.calendar-detail-panel {
  border-left: 1px solid #E4E7EF;
  background: #FFFFFF;
  overflow-y: auto;
  padding: 20px;
}

=== CALENDAR HEADER ===

<div class="calendar-header">
  <div style="display:flex;align-items:center;gap:12px;">
    <button class="cal-nav-btn" onclick="prevMonth()">‹</button>
    <h2 class="cal-month-title">{{ current_month }}</h2>
    <button class="cal-nav-btn" onclick="nextMonth()">›</button>
    <button class="btn btn-secondary btn-sm" onclick="goToday()">Tänään</button>
  </div>
  <div class="cal-view-toggle">
    <button class="cal-view-btn active" data-view="month">Kuukausi</button>
    <button class="cal-view-btn" data-view="week">Viikko</button>
    <button class="cal-view-btn" data-view="day">Päivä</button>
    <button class="cal-view-btn" data-view="schedule">Aikataulu</button>
  </div>
  <a href="/leads/meetings/schedule" class="btn btn-primary btn-sm">+ Tapaaminen</a>
</div>

.calendar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.cal-month-title { font-size: 20px; font-weight: 700; color: #0F1117; min-width: 200px; }
.cal-nav-btn {
  width: 32px; height: 32px;
  border-radius: 8px;
  border: 1px solid #E4E7EF;
  background: white;
  font-size: 18px;
  color: #374151;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all 120ms;
}
.cal-nav-btn:hover { background: #EBF2FF; color: #1D6BF3; border-color: #BFDBFE; }

.cal-view-toggle {
  display: flex;
  background: #F3F4F6;
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
}
.cal-view-btn {
  padding: 5px 14px;
  font-size: 12px;
  font-weight: 500;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: #6B7280;
  cursor: pointer;
  transition: all 120ms;
}
.cal-view-btn.active {
  background: #1D6BF3;
  color: white;
  box-shadow: 0 1px 3px rgba(29,107,243,0.3);
}

=== CALENDAR GRID (Month View) ===

<div class="cal-grid">
  <!-- Day name headers -->
  <div class="cal-grid-header">
    {% for day in ['Ma','Ti','Ke','To','Pe','La','Su'] %}
    <div class="cal-day-name">{{ day }}</div>
    {% endfor %}
  </div>
  <!-- Day cells -->
  <div class="cal-grid-body">
    {% for week in calendar_weeks %}
      {% for day in week %}
      <div class="cal-day-cell {% if day.is_today %}today{% endif %} {% if not day.in_month %}other-month{% endif %}"
           data-date="{{ day.date }}"
           onclick="selectDay('{{ day.date }}')">
        <div class="cal-day-number">{{ day.day }}</div>
        <div class="cal-day-events">
          {% for event in day.events[:3] %}
          <div class="cal-event-block {{ event.type }}"
               style="background:{{ event.color }};"
               onclick="showEventDetail({{ event.id }}, event)">
            {{ event.title[:20] }}{% if event.title|length > 20 %}...{% endif %}
          </div>
          {% endfor %}
          {% if day.events|length > 3 %}
          <div class="cal-more-events">+{{ day.events|length - 3 }} lisää</div>
          {% endif %}
        </div>
      </div>
      {% endfor %}
    {% endfor %}
  </div>
</div>

CSS:
.cal-grid-header {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  border-bottom: 2px solid #E4E7EF;
  margin-bottom: 0;
}
.cal-day-name {
  padding: 8px;
  text-align: center;
  font-size: 11px;
  font-weight: 700;
  color: #9CA3AF;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.cal-grid-body {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  border-left: 1px solid #E4E7EF;
  border-top: 1px solid #E4E7EF;
}
.cal-day-cell {
  min-height: 110px;
  border-right: 1px solid #E4E7EF;
  border-bottom: 1px solid #E4E7EF;
  padding: 6px;
  cursor: pointer;
  transition: background 120ms;
  vertical-align: top;
}
.cal-day-cell:hover { background: #F8F9FC; }
.cal-day-cell.today { background: #EBF2FF; }
.cal-day-cell.today .cal-day-number {
  background: #1D6BF3;
  color: white;
  border-radius: 50%;
  width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center;
  font-weight: 700;
}
.cal-day-cell.other-month { background: #FAFBFC; }
.cal-day-cell.other-month .cal-day-number { color: #D1D5DB; }

.cal-day-number {
  font-size: 12px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 4px;
  width: 24px; height: 24px;
  display: flex; align-items: center; justify-content: center;
}

.cal-event-block {
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 500;
  color: white;
  margin-bottom: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  cursor: pointer;
  transition: opacity 120ms;
}
.cal-event-block:hover { opacity: 0.85; }

/* Event type colors — Flowly palette */
.cal-event-block.meeting    { background: #1D6BF3; }  /* Tapaaminen — primary blue */
.cal-event-block.call       { background: #10B981; }  /* Puhelu — green */
.cal-event-block.followup   { background: #F59E0B; }  /* Follow-up — amber */
.cal-event-block.demo       { background: #7C3AED; }  /* Demo — purple */
.cal-event-block.task       { background: #38BDF8; }  /* Tehtävä — light blue */

.cal-more-events {
  font-size: 10px;
  color: #6B7280;
  font-weight: 500;
  padding: 1px 4px;
}

=== RIGHT DETAIL PANEL ===

HTML:
<div class="calendar-detail-panel">
  <!-- Filter row -->
  <div class="cal-panel-filters">
    <div class="filter-group">
      <label class="filter-label">Suodata</label>
      <select class="form-input form-input-sm" id="eventTypeFilter">
        <option>Kaikki tehtävät</option>
        <option>Tapaamiset</option>
        <option>Puhelut</option>
        <option>Follow-up</option>
      </select>
    </div>
    <div class="filter-group">
      <label class="filter-label">Kalenteri kuulle</label>
      <select class="form-input form-input-sm" id="userFilter">
        <option>{{ current_user.email }}</option>
        {% for user in org_users %}<option>{{ user.email }}</option>{% endfor %}
      </select>
    </div>
  </div>

  <!-- Selected event detail or today's events list -->
  <div id="eventDetail" class="cal-event-detail">
    <div class="cal-panel-section-title">Tänään</div>
    {% for event in todays_events %}
    <div class="cal-detail-item" onclick="showEventDetail({{ event.id }})">
      <div class="cal-detail-color-bar" style="background:{{ event.color }};"></div>
      <div class="cal-detail-content">
        <div class="cal-detail-title">{{ event.title }}</div>
        <div class="cal-detail-meta">{{ event.start_at.strftime('%H:%M') }} · {{ event.lead.company if event.lead else 'Ei liidiä' }}</div>
      </div>
    </div>
    {% else %}
    <div class="cal-empty">
      <p>Ei tapaamisia tänään</p>
      <p style="font-size:12px;color:#9CA3AF;">Kalenterisi on vapaa tälle päivälle.</p>
    </div>
    {% endfor %}
  </div>

  <!-- Event detail card (shown when event clicked) -->
  <div id="eventDetailCard" class="cal-event-detail-card" style="display:none;">
    <div class="cal-detail-card-header">
      <span class="cal-detail-card-title" id="detailTitle"></span>
      <button class="cal-detail-close" onclick="closeEventDetail()">✕</button>
    </div>
    <div class="cal-detail-fields">
      <div class="cal-detail-field">
        <label>Otsikko</label>
        <span id="detailTitleField"></span>
      </div>
      <div class="cal-detail-field">
        <label>Tiedot</label>
        <span id="detailDetails"></span>
      </div>
      <div class="cal-detail-field">
        <label>Aika</label>
        <span id="detailTime"></span>
      </div>
      <div class="cal-detail-field">
        <label>Koko päivä</label>
        <span id="detailAllDay"></span>
      </div>
      <div class="cal-detail-field">
        <label>Prioriteetti</label>
        <span id="detailPriority" class="badge"></span>
      </div>
    </div>
    <div style="margin-top:16px;">
      <button class="btn btn-primary" style="width:100%;" id="detailEditBtn">Muokkaa</button>
    </div>
  </div>
</div>

CSS for detail panel:
.cal-panel-filters { margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid #E4E7EF; }
.cal-panel-section-title { font-size: 12px; font-weight: 700; color: #6B7280; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 12px; }
.cal-detail-item {
  display: flex; gap: 10px;
  padding: 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 120ms;
  margin-bottom: 6px;
}
.cal-detail-item:hover { background: #F4F6FB; }
.cal-detail-color-bar { width: 4px; border-radius: 4px; flex-shrink: 0; }
.cal-detail-title { font-size: 13px; font-weight: 600; color: #0F1117; }
.cal-detail-meta { font-size: 11px; color: #9CA3AF; margin-top: 2px; }

.cal-event-detail-card {
  background: #F4F6FB;
  border-radius: 10px;
  border: 1px solid #E4E7EF;
  padding: 16px;
  margin-top: 16px;
}
.cal-detail-card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.cal-detail-card-title { font-size: 14px; font-weight: 700; color: #0F1117; }
.cal-detail-close { border: none; background: none; font-size: 16px; cursor: pointer; color: #9CA3AF; }
.cal-detail-field { margin-bottom: 12px; }
.cal-detail-field label { font-size: 11px; font-weight: 600; color: #9CA3AF; text-transform: uppercase; display: block; margin-bottom: 3px; }
.cal-detail-field span { font-size: 13px; color: #374151; }

.cal-empty { text-align: center; padding: 24px 0; color: #6B7280; font-size: 13px; }

=== JAVASCRIPT ===

// Show event detail in right panel when clicking event block
function showEventDetail(eventId, e) {
  if (e) e.stopPropagation();
  fetch(`/api/calendar/events/${eventId}`)
    .then(r => r.json())
    .then(data => {
      if (data.success) {
        const ev = data.data;
        document.getElementById('detailTitle').textContent = ev.title;
        document.getElementById('detailTitleField').textContent = ev.title;
        document.getElementById('detailDetails').textContent = ev.description || '—';
        document.getElementById('detailTime').textContent = ev.start_at + ' – ' + ev.end_at;
        document.getElementById('detailAllDay').textContent = ev.all_day ? 'Kyllä' : 'Ei';
        document.getElementById('detailEditBtn').onclick = () => window.location = `/calendar/events/${eventId}/edit`;
        document.getElementById('eventDetailCard').style.display = 'block';
        document.getElementById('eventDetail').style.display = 'none';
      }
    });
}
function closeEventDetail() {
  document.getElementById('eventDetailCard').style.display = 'none';
  document.getElementById('eventDetail').style.display = 'block';
}

// Add API endpoint if missing:
// GET /api/calendar/events/<id>
// Returns single CalendarEvent as JSON in standard {success, data, error} format
```

### ✅ Hyväksymiskriteerit
- [ ] Kuukausikalenteri näyttää 7 saraketta (ma–su)
- [ ] Tapaamisblokit näkyvät oikeilla väreillä päivissä
- [ ] Tänään-päivä korostuu sinisellä `#1D6BF3` numerolla
- [ ] Klikkaaminen avaa tapauksen tiedot oikeaan paneeliin
- [ ] Kuukausi/Viikko/Päivä/Aikataulu -togglet toimivat
- [ ] Oikeassa paneelissa tänään tapahtumat listattuna
- [ ] "Koko kuukausi vapaa" empty state näkyy selkeästi

---

## Yhteenveto

| Vaihe | Näkymä | Referenssi | Arvio |
|---|---|---|---|
| UI-P1 | Pipeline kanban | Sininen kanban-kuva | 1–2 pv |
| UI-D1 | Dashboard | Coupler.io CRM dashboard | 2 pv |
| UI-K1 | Kalenteri | Kuukausikalenteri + sivupaneeli | 2 pv |
| **Yht.** | | | **~5–6 pv** |

**Värimuistilista kaikille prompteille:**
```
Sidebar:         #0B0F1A
Pääsininen (CTA):#1D6BF3  ← käytetään nappeissa, aktiivinen nav, sarakeotsikoissa
Korostussininen: #38BDF8  ← käytetään toissijaisena värinä, linkeissä, chart-viivoissa
Sisältötausta:   #F4F6FB
Kortit:          #FFFFFF
Teksti:          #0F1117
Muted:           #6B7280
Menestys:        #10B981
Varoitus:        #F59E0B
Vaara:           #EF4444
```
