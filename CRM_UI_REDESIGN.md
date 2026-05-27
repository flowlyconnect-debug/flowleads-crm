# FlowLeads CRM — UI Redesign Suunnitelma

**Tavoite:** Ammattimainen, erottuva CRM joka näyttää paremmalta kuin Pipedrive  
**Inspiraatio:** Pipedrive (pipeline), Coupler.io (dashboard-kortit), Octoboard (analytiikka)  
**Väritema:** flowlysolutions.net — värit poimittu suoraan sivulta

---

## Flowly Brand Colors — poimittu suoraan sivulta

Nämä värit on analysoitu flowlysolutions.net-sivun kuvakaappauksista:

```
Tausta:      Erittäin tumma sinimusta (hero + body)
Pääsininen:  Kirkas sininen CTA-nappi "Varaa ilmainen konsultaatio"
Aksentti:    Vaaleansininen/syaani korostusteksti ("Flowly", "Säästä Aikaa")
Kortit:      Tumma siniharmaa, hieman vaaleampi kuin tausta
Teksti:      Valkoinen otsikoille, vaaleanharmaa body-tekstille
Ikonilaatikot: Pääsininen pyöristetty neliö
```

```css
/* ✅ FLOWLY BRAND COLORS — poimittu flowlysolutions.net */

/* SIDEBAR & BACKGROUNDS */
--color-sidebar:        #0B0F1A;   /* Hero-tausta — erittäin tumma sinimusta */
--color-sidebar-light:  #111827;   /* Sidebar hover / card bg */
--color-sidebar-hover:  #1A2235;   /* Aktiivinen nav-item tausta */

/* CONTENT AREA — vaalea puoli sisällölle */
--color-bg:             #F4F6FB;   /* Pääsisältöalueen tausta */
--color-bg-secondary:   #ECEEF5;   /* Toinen tausta (pipeline-sarake) */
--color-card:           #FFFFFF;   /* Kortit */
--color-card-hover:     #FAFBFF;   /* Kortti hover */

/* FLOWLY PRIMARY BLUE — CTA-nappi */
--color-accent:         #1D6BF3;   /* "Varaa ilmainen konsultaatio" -nappi */
--color-accent-hover:   #1558D6;   /* Nappi hover */
--color-accent-soft:    #EBF2FF;   /* Sininen tausta badge/highlight */
--color-accent-border:  #BFDBFE;   /* Sininen reunaviiva */

/* FLOWLY HIGHLIGHT BLUE — korostusteksti */
--color-highlight:      #38BDF8;   /* "Flowly", "Säästä Aikaa" -teksti */
--color-highlight-dark: #0EA5E9;   /* Tummempi highlight */

/* BORDERS */
--color-border:         #E4E7EF;   /* Vaalea sisältöalue */
--color-border-dark:    #1E2D47;   /* Tumma sidebar-puoli */
--color-border-light:   #F0F2F8;

/* TEXT */
--color-text-primary:   #0F1117;   /* Otsikot sisältöalueella */
--color-text-secondary: #5C6170;   /* Body-teksti */
--color-text-muted:     #9CA3AF;
--color-text-inverse:   #FFFFFF;   /* Teksti tummalla taustalla */
--color-text-highlight: #38BDF8;   /* Sininen korostusteksti (Flowly-tyyli) */

/* STATUS — pysyvät */
--color-success:        #10B981;
--color-success-soft:   #D1FAE5;
--color-warning:        #F59E0B;
--color-warning-soft:   #FEF3C7;
--color-danger:         #EF4444;
--color-danger-soft:    #FEE2E2;
--color-info:           #38BDF8;   /* Sama kuin Flowly highlight */
--color-info-soft:      #E0F7FF;

/* PIPELINE STAGES */
--stage-new:            #1D6BF3;   /* Flowly blue — Uusi liidi */
--stage-new-soft:       #EBF2FF;
--stage-contacted:      #38BDF8;   /* Flowly highlight — Kontaktoitu */
--stage-contacted-soft: #E0F7FF;
--stage-interested:     #F59E0B;   /* Kiinnostunut */
--stage-interested-soft:#FEF3C7;
--stage-proposal:       #8B5CF6;   /* Tarjous */
--stage-proposal-soft:  #EDE9FE;
--stage-won:            #10B981;   /* Voitettu */
--stage-won-soft:       #D1FAE5;
--stage-lost:           #6B7280;   /* Hävitty */
--stage-lost-soft:      #F3F4F6;
```

---

## Mitä referenssikuvista otetaan mukaan

### Pipedrive (kuva 3)
✅ Tumma sidebar navigaatiolla (ei ylänav)
✅ Pipeline-sarakkeet arvoilla: "$34,900 · 5 deals"
✅ Kortit joissa yritysnimi, arvo, assignee avatar
✅ Värikoodatut stage-badget
✅ Tiivistetty, nopea layout

### Coupler.io CRM dashboard (kuva 2)
✅ Värilliset metric-kortit (Total sales, Win rate, Avg days to close)
✅ "Won deals" viivakuvaaja
✅ "Deals projection" ennuste
✅ "Deal loss reasons" donut-kaavio
✅ Suodatinpaneeli oikealla

### Octoboard (kuva 1)
✅ Data-raskaat kortit numeroilla
✅ Mini sparkline -kaaviot korteissa
✅ Tiivis grid-layout

### Suomalainen CRM (kuva 4)
✅ Suomenkieliset vaiheet: Liidi, Validoitu, Tarjous, Voitettu
✅ Sarakkeen arvo näkyy ylhäällä (€154,050)
✅ Arvonmuutos viime kauteen (punaisella/vihreällä)
✅ Selkeät kortit yritysnimi + €-arvo

---

## Design System — komponentit

### Typografia
```
Font-family: 'Inter', system-ui, -apple-system, sans-serif
(Lisää <link> Google Fontsista base.html:ään)

font-size-xs:   11px  (badge-teksti, timestamp)
font-size-sm:   13px  (taulukkorivit, selite)
font-size-base: 14px  (body)
font-size-md:   15px  (card-otsikko)
font-size-lg:   18px  (section otsikko)
font-size-xl:   24px  (metric-numero)
font-size-2xl:  32px  (hero-numero dashboardilla)

font-weight-normal:  400
font-weight-medium:  500
font-weight-semibold: 600
font-weight-bold:    700
```

### Spacing & Border Radius
```
border-radius-sm:  6px   (badge, input)
border-radius-md:  10px  (kortti)
border-radius-lg:  14px  (modal, suuri kortti)
border-radius-xl:  20px  (hero-kortti)

shadow-sm:  0 1px 3px rgba(0,0,0,0.06)
shadow-md:  0 4px 12px rgba(0,0,0,0.08)
shadow-lg:  0 8px 24px rgba(0,0,0,0.12)
shadow-accent: 0 4px 14px rgba(99,102,241,0.25)  /* aksenttivarjo */
```

---

## VAIHE UI-1 — Design System + Layout-pohja
**Arvio:** 2 päivää  
**Tavoite:** Kaikki CSS-muuttujat, uusi sidebar, base-template, typografia

### Cursor-prompt

```
Completely redesign the FlowLeads CRM visual layer. Do NOT change any Python/Flask
backend logic — only modify HTML templates, CSS, and minimal JavaScript.

STEP 1: CREATE DESIGN SYSTEM

Create: app/static/css/design-system.css

Content:

:root {
  /* === FLOWLY BRAND COLORS — poimittu flowlysolutions.net === */

  /* Sidebar — tumma sinimusta kuten Flowly hero-tausta */
  --color-sidebar:        #0B0F1A;
  --color-sidebar-light:  #111827;
  --color-sidebar-hover:  #1A2235;

  /* Flowly primary blue — sama kuin CTA-nappi sivulla */
  --color-accent:         #1D6BF3;
  --color-accent-hover:   #1558D6;
  --color-accent-soft:    #EBF2FF;
  --color-accent-border:  #BFDBFE;

  /* Flowly highlight — korostusteksti kuten "Säästä Aikaa" */
  --color-highlight:      #38BDF8;
  --color-highlight-dark: #0EA5E9;

  /* === BACKGROUNDS === */
  --color-bg:             #F4F6FB;
  --color-bg-secondary:   #ECEEF5;
  --color-card:           #FFFFFF;
  --color-card-hover:     #FAFBFF;

  /* === BORDERS === */
  --color-border:         #E4E7EF;
  --color-border-light:   #F0F2F8;

  /* === TEXT === */
  --color-text-primary:   #0F1117;
  --color-text-secondary: #5C6170;
  --color-text-muted:     #9CA3AF;
  --color-text-inverse:   #FFFFFF;
  --color-text-accent:    #6366F1;

  /* === STATUS === */
  --color-success:        #10B981;
  --color-success-soft:   #D1FAE5;
  --color-warning:        #F59E0B;
  --color-warning-soft:   #FEF3C7;
  --color-danger:         #EF4444;
  --color-danger-soft:    #FEE2E2;
  --color-info:           #3B82F6;
  --color-info-soft:      #DBEAFE;

  /* === PIPELINE STAGES === */
  --stage-new:            #8B5CF6;
  --stage-new-soft:       #EDE9FE;
  --stage-contacted:      #3B82F6;
  --stage-contacted-soft: #DBEAFE;
  --stage-interested:     #F59E0B;
  --stage-interested-soft:#FEF3C7;
  --stage-proposal:       #EC4899;
  --stage-proposal-soft:  #FCE7F3;
  --stage-won:            #10B981;
  --stage-won-soft:       #D1FAE5;
  --stage-lost:           #6B7280;
  --stage-lost-soft:      #F3F4F6;

  /* === TYPOGRAPHY === */
  --font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  --font-size-xs:   11px;
  --font-size-sm:   13px;
  --font-size-base: 14px;
  --font-size-md:   15px;
  --font-size-lg:   18px;
  --font-size-xl:   24px;
  --font-size-2xl:  32px;
  --font-size-3xl:  40px;

  /* === SPACING === */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;

  /* === RADIUS === */
  --radius-sm:  6px;
  --radius-md:  10px;
  --radius-lg:  14px;
  --radius-xl:  20px;
  --radius-full: 9999px;

  /* === SHADOWS === */
  --shadow-xs:  0 1px 2px rgba(0,0,0,0.05);
  --shadow-sm:  0 1px 4px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
  --shadow-md:  0 4px 12px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04);
  --shadow-lg:  0 8px 24px rgba(0,0,0,0.10), 0 4px 8px rgba(0,0,0,0.06);
  --shadow-accent: 0 4px 14px rgba(99,102,241,0.30);

  /* === TRANSITIONS === */
  --transition-fast: 120ms ease;
  --transition-base: 200ms ease;
  --transition-slow: 350ms ease;

  /* === SIDEBAR === */
  --sidebar-width: 240px;
  --sidebar-width-collapsed: 64px;
  --topbar-height: 0px;  /* NO top nav */
}


STEP 2: REMOVE TOP NAVIGATION ENTIRELY

In app/templates/base.html:
- DELETE the entire <nav> or <header> topbar element
- The sidebar IS the only navigation
- Page title and breadcrumb move to the page content area (top of main content)

STEP 3: BUILD THE SIDEBAR (app/templates/components/sidebar.html)

Structure:
┌─────────────────────────────┐
│  [Logo]  FlowLeads          │  ← 56px height, logo + brand name
├─────────────────────────────┤
│  [search icon]  Hae...      │  ← Quick search input (opens modal on click)
├─────────────────────────────┤
│  PÄÄVALIKKO                 │  ← Section label (uppercase, muted, 11px)
│  [icon] Dashboard           │
│  [icon] Pipeline            │
│  [icon] Liidit              │
│  [icon] Tehtävät    [3]     │  ← Badge for overdue count
│  [icon] Kalenteri           │
│  [icon] Sähköposti          │
├─────────────────────────────┤
│  SEGMENTIT                  │
│  [dot] Korkea potentiaali   │  ← Pinned segments with color dot
│  [dot] B2B SaaS             │
│  + Uusi segmentti           │
├─────────────────────────────┤
│  ANALYTIIKKA                │
│  [icon] Raportit            │
│  [icon] Ennuste             │
│  [icon] Automaatiot         │
├─────────────────────────────┤
│  ASETUKSET                  │
│  [icon] Asetukset           │
│  [icon] Integraatiot        │
│  (superadmin only):         │
│  [icon] Hallintapaneeli     │
├─────────────────────────────┤
│                             │
│  [avatar] Etunimi S.    ▾  │  ← User menu at bottom
│  Pro-plan badge             │
└─────────────────────────────┘

CSS for sidebar:
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  position: fixed;
  left: 0; top: 0;
  background: var(--color-sidebar);   /* #0B0F1A — Flowly dark */
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  overflow-x: hidden;
  z-index: 100;
  border-right: 1px solid rgba(255,255,255,0.05);
  /* Hienovarainen grid-pattern kuten Flowly hero */
  background-image: linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
  background-size: 32px 32px;
}

.sidebar-logo {
  padding: 20px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  border-bottom: 1px solid rgba(255,255,255,0.06);
}
.sidebar-logo img { width: 28px; height: 28px; }
.sidebar-logo span {
  font-size: 16px;
  font-weight: 700;
  color: white;
  letter-spacing: -0.3px;
}

.sidebar-search {
  margin: 12px;
  padding: 8px 12px;
  background: rgba(255,255,255,0.07);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: var(--radius-md);
  display: flex; align-items: center; gap: 8px;
  cursor: pointer;
  transition: background var(--transition-fast);
}
.sidebar-search:hover { background: rgba(255,255,255,0.11); }
.sidebar-search span { color: rgba(255,255,255,0.45); font-size: 13px; }

.sidebar-section-label {
  padding: 16px 16px 6px;
  font-size: 10px;
  font-weight: 600;
  color: rgba(255,255,255,0.30);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.sidebar-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px;
  margin: 1px 8px;
  border-radius: var(--radius-md);
  color: rgba(255,255,255,0.65);
  font-size: 14px;
  font-weight: 500;
  text-decoration: none;
  transition: all var(--transition-fast);
  cursor: pointer;
  position: relative;
}
.sidebar-item:hover {
  background: rgba(255,255,255,0.08);
  color: rgba(255,255,255,0.90);
}
.sidebar-item.active {
  background: var(--color-accent);
  color: white;
  box-shadow: var(--shadow-accent);
}
.sidebar-item.active svg { opacity: 1; }
.sidebar-item svg { width: 17px; height: 17px; opacity: 0.7; flex-shrink: 0; }

.sidebar-badge {
  margin-left: auto;
  background: var(--color-danger);
  color: white;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: var(--radius-full);
  min-width: 18px;
  text-align: center;
}

.sidebar-user {
  margin-top: auto;
  padding: 12px;
  border-top: 1px solid rgba(255,255,255,0.06);
}
.sidebar-user-inner {
  display: flex; align-items: center; gap: 10px;
  padding: 10px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
}
.sidebar-user-inner:hover { background: rgba(255,255,255,0.08); }
.sidebar-user-avatar {
  width: 32px; height: 32px;
  border-radius: var(--radius-full);
  background: var(--color-accent);
  color: white;
  font-size: 13px;
  font-weight: 600;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.sidebar-user-name { font-size: 13px; font-weight: 500; color: rgba(255,255,255,0.80); }
.sidebar-user-plan { font-size: 11px; color: rgba(255,255,255,0.35); }

Icons: Use Heroicons (MIT license) via CDN or inline SVG.
Add to base.html: <script src="https://unpkg.com/heroicons@2.1.1/dist/heroicons.min.js"></script>
OR use inline SVG heroicons for each item.


STEP 4: MAIN CONTENT AREA

.app-layout {
  display: flex;
  min-height: 100vh;
  background: var(--color-bg);
}

.app-main {
  margin-left: var(--sidebar-width);
  flex: 1;
  min-width: 0;
}

.page-header {
  padding: 24px 28px 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}
.page-header h1 {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text-primary);
  letter-spacing: -0.4px;
}
.page-header .page-subtitle {
  font-size: 13px;
  color: var(--color-text-muted);
  margin-top: 2px;
}

.page-content { padding: 0 28px 32px; }


STEP 5: BASE CARD COMPONENT

.card {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}
.card:hover { box-shadow: var(--shadow-md); }
.card-header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border-light);
  display: flex; align-items: center; justify-content: space-between;
}
.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.card-body { padding: 20px; }


STEP 6: BUTTON SYSTEM

.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 8px 16px;
  font-size: 14px; font-weight: 500;
  border-radius: var(--radius-md);
  border: none; cursor: pointer;
  transition: all var(--transition-fast);
  text-decoration: none;
  white-space: nowrap;
}
.btn-primary {
  background: var(--color-accent);
  color: white;
  box-shadow: 0 1px 3px rgba(99,102,241,0.3);
}
.btn-primary:hover {
  background: var(--color-accent-hover);
  box-shadow: var(--shadow-accent);
  transform: translateY(-1px);
}
.btn-secondary {
  background: white;
  color: var(--color-text-primary);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-xs);
}
.btn-secondary:hover { background: var(--color-bg); border-color: #D1D5DB; }
.btn-danger { background: var(--color-danger); color: white; }
.btn-sm { padding: 5px 11px; font-size: 12px; }
.btn-lg { padding: 11px 22px; font-size: 15px; }
.btn svg { width: 16px; height: 16px; }


STEP 7: BADGE / STATUS COMPONENTS

.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px;
  font-size: 11px; font-weight: 600;
  border-radius: var(--radius-full);
  white-space: nowrap;
  letter-spacing: 0.02em;
}
.badge-success  { background: var(--color-success-soft); color: #065F46; }
.badge-warning  { background: var(--color-warning-soft); color: #92400E; }
.badge-danger   { background: var(--color-danger-soft);  color: #991B1B; }
.badge-info     { background: var(--color-info-soft);    color: #1E40AF; }
.badge-accent   { background: var(--color-accent-soft);  color: #3730A3; }
.badge-neutral  { background: #F3F4F6; color: #374151; }


STEP 8: UPDATE base.html

Replace existing base.html with:
- Remove <nav>/<header> topbar completely
- Add <link href="/static/css/design-system.css" rel="stylesheet">
- Add Google Fonts: Inter 400,500,600,700
- Add Heroicons CDN
- Add Chart.js CDN (already in use)
- Wrap body in <div class="app-layout">
- Include sidebar component: {% include 'components/sidebar.html' %}
- Wrap all content blocks in <main class="app-main">
- Add flash message area inside .app-main (not in topbar)

Write a comprehensive app/static/css/main.css that:
- Imports design-system.css first
- Adds all component styles above
- Adds utility classes: .text-success, .text-danger, .mt-4, .flex, .gap-2, etc.
- Adds responsive breakpoint: @media (max-width: 1024px) { sidebar collapses }

DO NOT modify any Python routes, models, or services.
DO test that all existing template extends work correctly.
```

### ✅ Vaiheen UI-1 hyväksymiskriteerit
- [ ] Kaikki sivut renderöityvät ilman topbaria
- [ ] Sidebar näkyy kaikilla sivuilla kiinteästi
- [ ] Aktiivinen navigaatiokohde korostuu
- [ ] Värimuuttujat löytyvät design-system.css:stä
- [ ] Inter-fontti latautuu
- [ ] Responsiivisuus toimii (sidebar piiloutuu < 1024px)

---

## VAIHE UI-2 — Dashboard redesign
**Arvio:** 2 päivää  
**Tavoite:** Coupler.io + Octoboard -tyylinen dashboard, kaikki tärkeä yhdellä silmäyksellä

### Cursor-prompt

```
Redesign the FlowLeads CRM dashboard (/dashboard) using the new design system.
Reference: Coupler.io CRM dashboard and Octoboard screenshots.
DO NOT change the backend AnalyticsService — only redesign the template and add CSS.

LAYOUT: app/templates/dashboard/index.html

Structure (top to bottom):

1. PAGE HEADER
   <div class="page-header">
     <div>
       <h1>Hei, {user.first_name} 👋</h1>
       <p class="page-subtitle">Tässä on tänään — {current_date}</p>
     </div>
     <div style="display:flex;gap:8px">
       <button class="btn btn-secondary btn-sm">Tänään</button>
       <button class="btn btn-secondary btn-sm">7 pv</button>
       <button class="btn btn-secondary btn-sm active">30 pv</button>
     </div>
   </div>

2. METRIC CARDS ROW — 4 cards in a grid (2+2 on smaller screens)

Card design (Coupler.io style — colored left border + icon):

.metric-card {
  background: var(--color-card);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  box-shadow: var(--shadow-sm);
  padding: 20px;
  display: flex; flex-direction: column; gap: 12px;
  position: relative;
  overflow: hidden;
}
.metric-card::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 4px;
  border-radius: 4px 0 0 4px;
}
.metric-card.accent::before   { background: var(--color-accent); }
.metric-card.success::before  { background: var(--color-success); }
.metric-card.warning::before  { background: var(--color-warning); }
.metric-card.info::before     { background: var(--color-info); }

.metric-card-header { display: flex; align-items: center; justify-content: space-between; }
.metric-card-label  { font-size: 12px; font-weight: 500; color: var(--color-text-secondary); text-transform: uppercase; letter-spacing: 0.05em; }
.metric-card-icon   { width: 36px; height: 36px; border-radius: var(--radius-md); display: flex; align-items: center; justify-content: center; }
.metric-card-icon.accent  { background: var(--color-accent-soft); color: var(--color-accent); }
.metric-card-icon.success { background: var(--color-success-soft); color: var(--color-success); }
.metric-card-icon.warning { background: var(--color-warning-soft); color: var(--color-warning); }
.metric-card-icon.info    { background: var(--color-info-soft);    color: var(--color-info); }

.metric-card-value  { font-size: 32px; font-weight: 700; color: var(--color-text-primary); letter-spacing: -0.8px; line-height: 1; }
.metric-card-delta  { font-size: 12px; font-weight: 500; display: flex; align-items: center; gap: 4px; }
.metric-card-delta.up   { color: var(--color-success); }
.metric-card-delta.down { color: var(--color-danger); }

Cards:
Card 1 (accent):   "Uudet liidit"        → stats.new_leads_count + "▲ 23% viime kuusta"
Card 2 (success):  "Voitetut kaupat"      → stats.won_deals_count + win rate %
Card 3 (warning):  "Pipeline-arvo"        → stats.pipeline_value formatted as €45,200
Card 4 (info):     "Avg. päiviä sulkuun"  → stats.avg_days_to_close

3. MAIN CONTENT GRID — 2 columns (65% / 35%)

LEFT COLUMN:

A. "Liidit viimeinen 30 pv" — Line chart (Chart.js)
   - Area chart with gradient fill
   - X-axis: dates, Y-axis: count
   - Two lines: new leads (accent color) + contacted (success color)
   - Gradient fill under lines: rgba(99,102,241,0.12)
   - Grid lines: very light (#F0F2F8)
   - No border box, floating chart

Chart.js config for area chart:
{
  type: 'line',
  data: {
    datasets: [{
      label: 'Uudet liidit',
      borderColor: '#6366F1',
      backgroundColor: 'rgba(99,102,241,0.08)',
      fill: true,
      tension: 0.4,
      pointRadius: 0,
      pointHoverRadius: 5,
    }]
  },
  options: {
    plugins: { legend: { display: true, position: 'top' } },
    scales: {
      x: { grid: { display: false }, border: { display: false } },
      y: { grid: { color: '#F0F2F8' }, border: { display: false } }
    }
  }
}

B. "Pipeline funnel" — Horizontal bar chart showing leads per stage
   Each bar colored by stage color variable

RIGHT COLUMN:

A. "Tehtäväsi tänään" card
   - Count badge (overdue highlighted red)
   - List of 5 next tasks: checkbox + title + lead name + due time
   - "Näytä kaikki" link
   - Empty state: "Ei tehtäviä tänään 🎉"

B. "Liidit lähteittäin" — Donut chart (Chart.js)
   - n8n (accent), Manual (info), Webform (success)
   - Total in center

C. "Viimeinen aktiviteetti" — Feed
   Timeline items (icon + text + time ago):
   [lead icon] Uusi liidi: Acme Corp — 5 min sitten
   [email icon] Sähköposti lähetetty: John Doe — 1h sitten
   [task icon] Tehtävä valmis: Soitto — 2h sitten
   - Alternating left border colors by type
   - Max 8 items, "Näytä lisää" link

4. BOTTOM ROW — 3 columns

A. "Myyntiennuste" card (if V3 done)
   - Large number: "€45,200 odotettu"
   - Small: "seuraavat 30 päivää"
   - Progress bar: worst case → best case range

B. "Top liidit" — Mini table
   - Top 5 leads by score × value
   - Score badge + company + stage badge

C. "Sähköpostit tällä viikolla" — Mini stat
   - Sent: 34, Open rate: 42%, Reply rate: 8%
   - Mini bar chart

GENERAL CSS for dashboard grid:
.dashboard-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.dashboard-grid-4 { grid-template-columns: repeat(4, 1fr); }
.dashboard-grid-65 { grid-template-columns: 65fr 35fr; }
.dashboard-grid-3 { grid-template-columns: repeat(3, 1fr); }

All chart containers need: position: relative; height: 240px;
Ensure Chart.js responsive: true, maintainAspectRatio: false.
```

### ✅ Vaiheen UI-2 hyväksymiskriteerit
- [ ] 4 metric-korttia näkyvät yhtenäisellä tyylillä
- [ ] Viivakuvaaja liideistä renderöityy gradientilla
- [ ] Päivän tehtävät näkyvät selkeästi
- [ ] Donut-kaavio lähteistä näyttää oikeat datat
- [ ] Aktiviteettifeed toimii
- [ ] Kaikki kortit responsiivisia

---

## VAIHE UI-3 — Pipeline redesign
**Arvio:** 2 päivää  
**Tavoite:** Pipedrive-tasoinen kanban — sarake-arvot, drag-drop, professionaalit kortit

### Cursor-prompt

```
Redesign the lead pipeline (kanban) view to match Pipedrive quality.
File: app/templates/leads/pipeline.html
DO NOT change backend routes or LeadService.

PIPELINE HEADER:
┌────────────────────────────────────────────────────────────────┐
│  Pipeline                    [Hae...]  [Suodattimet ▾]  [+ Lisää liidi] │
│  13 liidiä · €154,050 yhteensä · Muokattu äsken              │
└────────────────────────────────────────────────────────────────┘

COLUMN HEADER (each stage):
┌─────────────────────────────┐
│ [●] Uusi liidi        [5]   │  ← Stage dot color + name + card count badge
│ €81,000                     │  ← Total value in column
│ ▼ -€12,050 viime kuusta     │  ← Delta (red if down, green if up)
└─────────────────────────────┘

CSS:
.pipeline-wrapper {
  display: flex;
  gap: 12px;
  padding: 0 28px 32px;
  overflow-x: auto;
  min-height: calc(100vh - 140px);
  align-items: flex-start;
}

.pipeline-column {
  min-width: 272px;
  max-width: 272px;
  flex-shrink: 0;
  background: var(--color-bg-secondary);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  overflow: hidden;
}

.pipeline-column-header {
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border);
  position: sticky;
  top: 0;
  background: var(--color-bg-secondary);
  z-index: 1;
}

.pipeline-column-title {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 6px;
}
.stage-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.pipeline-column-title span { font-size: 13px; font-weight: 600; color: var(--color-text-primary); }
.pipeline-count {
  margin-left: auto;
  background: var(--color-border);
  color: var(--color-text-secondary);
  font-size: 11px; font-weight: 700;
  padding: 1px 7px;
  border-radius: var(--radius-full);
}

.pipeline-column-value { font-size: 18px; font-weight: 700; color: var(--color-text-primary); }
.pipeline-column-delta { font-size: 12px; font-weight: 500; margin-top: 2px; }
.pipeline-column-delta.positive { color: var(--color-success); }
.pipeline-column-delta.negative { color: var(--color-danger); }

.pipeline-cards {
  padding: 10px;
  min-height: 80px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

LEAD CARD design:

.lead-card {
  background: var(--color-card);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  padding: 14px;
  cursor: grab;
  transition: all var(--transition-fast);
  box-shadow: var(--shadow-xs);
  position: relative;
}
.lead-card:hover {
  box-shadow: var(--shadow-md);
  border-color: var(--color-accent-border);
  transform: translateY(-1px);
}
.lead-card.dragging {
  opacity: 0.6;
  box-shadow: var(--shadow-lg);
  transform: rotate(1.5deg);
}

.lead-card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }
.lead-card-name { font-size: 13px; font-weight: 600; color: var(--color-text-primary); line-height: 1.3; }
.lead-card-company { font-size: 12px; color: var(--color-text-secondary); margin-top: 1px; }
.lead-card-value { font-size: 13px; font-weight: 700; color: var(--color-text-primary); white-space: nowrap; }

.lead-card-middle { margin-bottom: 10px; }
.lead-card-source {
  font-size: 11px;
  color: var(--color-text-muted);
  display: flex; align-items: center; gap: 4px;
}

.lead-card-footer {
  display: flex; align-items: center; justify-content: space-between;
  padding-top: 10px;
  border-top: 1px solid var(--color-border-light);
}

.lead-score-badge {
  font-size: 11px; font-weight: 700;
  padding: 2px 7px;
  border-radius: var(--radius-full);
}
/* Color by score range */
.score-high   { background: var(--color-success-soft); color: #065F46; }
.score-medium { background: var(--color-warning-soft); color: #92400E; }
.score-low    { background: var(--color-danger-soft);  color: #991B1B; }

.lead-card-avatar {
  width: 22px; height: 22px;
  border-radius: var(--radius-full);
  background: var(--color-accent);
  color: white;
  font-size: 10px; font-weight: 600;
  display: flex; align-items: center; justify-content: center;
}

.lead-card-date { font-size: 11px; color: var(--color-text-muted); }

.lead-card-actions {
  position: absolute;
  top: 10px; right: 10px;
  opacity: 0;
  transition: opacity var(--transition-fast);
  display: flex; gap: 4px;
}
.lead-card:hover .lead-card-actions { opacity: 1; }
.lead-card-action-btn {
  width: 24px; height: 24px;
  border-radius: var(--radius-sm);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  display: flex; align-items: center; justify-content: center;
  cursor: pointer;
  color: var(--color-text-muted);
}
.lead-card-action-btn:hover { background: var(--color-accent-soft); color: var(--color-accent); }

AI ENRICHMENT INDICATOR on card:
- If ai_enriched: small sparkle icon (✦) next to score, tooltip "AI-rikastettu"
- If ai_enrichment_status == 'processing': small spinner

DRAG AND DROP:
Use SortableJS (CDN: https://cdnjs.cloudflare.com/ajax/libs/Sortable/1.15.0/Sortable.min.js)

JavaScript:
document.querySelectorAll('.pipeline-cards').forEach(column => {
  Sortable.create(column, {
    group: 'pipeline',
    animation: 150,
    ghostClass: 'lead-card-ghost',
    dragClass: 'lead-card-dragging',
    onEnd: function(evt) {
      const leadId = evt.item.dataset.leadId;
      const newStageId = evt.to.dataset.stageId;
      if (evt.from !== evt.to) {
        fetch(`/leads/${leadId}/stage`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json', 'X-CSRFToken': window.csrfToken},
          body: JSON.stringify({stage_id: newStageId})
        });
        // Update column value totals after move
        updateColumnStats();
      }
    }
  });
});

.lead-card-ghost { opacity: 0.3; background: var(--color-accent-soft); border: 2px dashed var(--color-accent); }

ADD LEAD inline button at bottom of each column:
.pipeline-add-btn {
  margin: 8px 10px 10px;
  padding: 8px;
  border-radius: var(--radius-md);
  border: 1.5px dashed var(--color-border);
  color: var(--color-text-muted);
  font-size: 13px;
  text-align: center;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.pipeline-add-btn:hover { border-color: var(--color-accent); color: var(--color-accent); background: var(--color-accent-soft); }
```

### ✅ Vaiheen UI-3 hyväksymiskriteerit
- [ ] Pipeline näyttää sarakearvot (€ + count)
- [ ] Drag-and-drop toimii ja tallentaa vaiheen
- [ ] Kortit näyttävät: nimi, yritys, arvo, score-badge, assignee
- [ ] Hover näyttää pikakuvaikkeet (muokkaa, avaa)
- [ ] Vaakasuora scrollaus toimii monta saraketta
- [ ] Aktiivinen liidi korostuu

---

## VAIHE UI-4 — Liidilista ja profiili
**Arvio:** 1–2 päivää  
**Tavoite:** Nopea taulukkonäkymä + ammattilainen profiilinäkymä (360-näkymä)

### Cursor-prompt

```
Redesign lead list and lead detail views using new design system.

LEAD LIST VIEW (app/templates/leads/index.html):

TABLE HEADER BAR:
┌─────────────────────────────────────────────────────────────────┐
│ Liidit                    [🔍 Hae...] [Suodattimet ▾] [+ Liidi] │
│ 247 liidiä · 12 uutta tänään                                    │
│ [Kaikki] [n8n] [Manuaalinen] [Lomake]  (source filter tabs)    │
└─────────────────────────────────────────────────────────────────┘

TABLE:
Columns: [checkbox] | Kontakti | Yritys | Vaihe | Score | Lähde | Luotu | Toiminnot

.leads-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.leads-table thead th {
  padding: 10px 14px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--color-border);
  white-space: nowrap;
  cursor: pointer;
  user-select: none;
}
.leads-table thead th:hover { color: var(--color-text-primary); }
.leads-table thead th.sorted { color: var(--color-accent); }

.leads-table tbody tr {
  border-bottom: 1px solid var(--color-border-light);
  transition: background var(--transition-fast);
}
.leads-table tbody tr:hover { background: var(--color-card-hover); }

.leads-table td { padding: 12px 14px; vertical-align: middle; }

.lead-contact-cell { display: flex; align-items: center; gap: 10px; }
.lead-contact-avatar {
  width: 32px; height: 32px;
  border-radius: var(--radius-full);
  background: linear-gradient(135deg, var(--color-accent), #818CF8);
  color: white; font-size: 12px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
}
.lead-contact-name { font-weight: 600; color: var(--color-text-primary); }
.lead-contact-email { font-size: 12px; color: var(--color-text-muted); }

LEAD DETAIL VIEW (app/templates/leads/detail.html):

Layout: Full-width with sticky sidebar
┌──────────────────────────────────┬───────────────────────┐
│  MAIN (65%)                      │  SIDEBAR (35%)        │
│  ┌──────────────────────────┐    │  ┌─────────────────┐  │
│  │ Contact Header Card      │    │  │ Stage selector  │  │
│  │ [Avatar] John Doe        │    │  │ Assign to       │  │
│  │ CEO · Acme Corp          │    │  │ Score gauge     │  │
│  │ 📧 john@acme.com         │    │  │ Deal value      │  │
│  │ 📞 +358 50 123 4567      │    │  │ Tags            │  │
│  │ 🔗 LinkedIn              │    │  │ Source badge    │  │
│  │ [AI Badge ✦ 82/100]      │    │  │ Created date    │  │
│  └──────────────────────────┘    │  └─────────────────┘  │
│                                  │                       │
│  [Aktiviteetti|Tehtävät|Sähköposti|Sekvenssit]           │
│  Tab content area                │  AI Insights card     │
└──────────────────────────────────┴───────────────────────┘

CONTACT HEADER CARD:
.lead-header-card {
  background: linear-gradient(135deg, var(--color-primary) 0%, #2D3154 100%);
  border-radius: var(--radius-lg);
  padding: 24px;
  color: white;
  display: flex; align-items: center; gap: 20px;
  margin-bottom: 20px;
}
.lead-header-avatar {
  width: 64px; height: 64px;
  border-radius: var(--radius-xl);
  background: var(--color-accent);
  color: white;
  font-size: 24px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  border: 3px solid rgba(255,255,255,0.2);
}
.lead-header-name { font-size: 22px; font-weight: 700; margin-bottom: 4px; }
.lead-header-title { font-size: 14px; color: rgba(255,255,255,0.65); }
.lead-header-badges { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }

CONTACT INFO grid (2 columns, icon + value):
.contact-info-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
  padding: 16px 0;
}
.contact-info-item { display: flex; align-items: center; gap: 8px; }
.contact-info-icon { color: var(--color-text-muted); width: 16px; }
.contact-info-value { font-size: 13px; color: var(--color-text-primary); }

ACTIVITY FEED:
.activity-feed { display: flex; flex-direction: column; gap: 0; }
.activity-item {
  display: flex; gap: 12px;
  padding: 14px 0;
  border-bottom: 1px solid var(--color-border-light);
  position: relative;
}
.activity-icon {
  width: 32px; height: 32px;
  border-radius: var(--radius-full);
  display: flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  font-size: 14px;
}
.activity-icon.note    { background: var(--color-accent-soft); color: var(--color-accent); }
.activity-icon.email   { background: var(--color-info-soft);   color: var(--color-info); }
.activity-icon.stage   { background: var(--color-success-soft);color: var(--color-success); }
.activity-icon.ai      { background: #F5F3FF; color: #7C3AED; }
.activity-icon.task    { background: var(--color-warning-soft);color: var(--color-warning); }

.activity-content { flex: 1; }
.activity-title { font-size: 13px; font-weight: 500; color: var(--color-text-primary); }
.activity-body  { font-size: 13px; color: var(--color-text-secondary); margin-top: 4px; line-height: 1.5; }
.activity-time  { font-size: 11px; color: var(--color-text-muted); margin-top: 4px; }

AI INSIGHTS sidebar card:
.ai-insights-card { background: linear-gradient(135deg, #F5F3FF, #EEF2FF); border: 1px solid var(--color-accent-border); }
.ai-score-gauge { /* Visual 0-100 progress arc or bar */ }
.ai-signal-list { list-style: none; }
.ai-signal-positive::before { content: "▲ "; color: var(--color-success); font-size: 10px; }
.ai-signal-risk::before { content: "▼ "; color: var(--color-danger); font-size: 10px; }

TABS:
.tab-nav {
  display: flex; gap: 0;
  border-bottom: 2px solid var(--color-border);
  margin-bottom: 20px;
}
.tab-item {
  padding: 10px 18px;
  font-size: 13px; font-weight: 500;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  margin-bottom: -2px;
  transition: all var(--transition-fast);
}
.tab-item:hover { color: var(--color-text-primary); }
.tab-item.active { color: var(--color-accent); border-bottom-color: var(--color-accent); font-weight: 600; }
```

### ✅ Vaiheen UI-4 hyväksymiskriteerit
- [ ] Liidilista näyttää avataret, vaihe-badget, score-badget
- [ ] Liidin profiili avautuu gradient-otsikkokortilla
- [ ] Välilehdet (Aktiviteetti/Tehtävät/Sähköposti) toimivat
- [ ] AI-insights näkyy sivupalkissa
- [ ] Kaikki kentät editoitavissa (inline tai modal)

---

## VAIHE UI-5 — Raportit, lomakkeet ja loput näkymät
**Arvio:** 2 päivää  
**Tavoite:** Kaikki jäljellä olevat näkymät päivitetään design-systeemillä

### Cursor-prompt

```
Apply the FlowLeads design system to all remaining views.
DO NOT change backend logic. Only update templates and CSS.

1. REPORTS PAGE (/reports)
   - Tab navigation: Pipeline | Lähteet | Tiimi | AI-rikastus | Ennuste
   - Each report: card with Chart.js chart + data table below
   - Date range picker: styled with design-system inputs
   - Export button: btn btn-secondary with download icon

2. TASKS PAGE (/tasks)
   - Three-column layout: Today | This Week | Overdue (overdue column has red header)
   - Each task card: checkbox (large, accented), priority dot, title, lead pill, due time
   - Completed tasks: strikethrough, faded
   - Quick-add form at top: input + type selector + date + "Lisää" btn

3. EMAIL COMPOSE (/leads/<id>/email/compose)
   - Full-width modal or page
   - "From / To" header row styled as email client
   - Subject input: large, borderless bottom-border only
   - Quill editor: custom toolbar with brand colors
   - Template selector: grid of template cards (hover to preview)

4. SETTINGS PAGES (/settings/*)
   - Left sub-navigation (within settings page, not sidebar)
   - Section: Profiili | Organisaatio | API-avaimet | Sähköposti | Webhook | Tietosuoja | Laskutus
   - Clean form layout: label above input, helper text below
   - Save button: sticky at bottom of form section

5. FORMS & INPUTS global styles:
   .form-group { margin-bottom: 20px; }
   .form-label { font-size: 13px; font-weight: 500; color: var(--color-text-primary); margin-bottom: 6px; display: block; }
   .form-helper { font-size: 12px; color: var(--color-text-muted); margin-top: 4px; }
   .form-input {
     width: 100%; padding: 9px 13px;
     font-size: 14px; color: var(--color-text-primary);
     background: white;
     border: 1.5px solid var(--color-border);
     border-radius: var(--radius-md);
     transition: border-color var(--transition-fast), box-shadow var(--transition-fast);
     outline: none;
   }
   .form-input:focus {
     border-color: var(--color-accent);
     box-shadow: 0 0 0 3px rgba(99,102,241,0.12);
   }
   .form-input.error { border-color: var(--color-danger); }
   .form-error-text { font-size: 12px; color: var(--color-danger); margin-top: 4px; }
   .form-select { /* same as input */ appearance: none; background-image: url("chevron-down svg"); }

6. MODAL component:
   .modal-overlay {
     position: fixed; inset: 0; z-index: 500;
     background: rgba(0,0,0,0.45);
     backdrop-filter: blur(4px);
     display: flex; align-items: center; justify-content: center;
   }
   .modal {
     background: white;
     border-radius: var(--radius-xl);
     box-shadow: 0 24px 64px rgba(0,0,0,0.18);
     width: 90%; max-width: 540px;
     max-height: 90vh; overflow-y: auto;
   }
   .modal-header {
     padding: 22px 24px 18px;
     border-bottom: 1px solid var(--color-border);
     display: flex; align-items: center; justify-content: space-between;
   }
   .modal-title { font-size: 16px; font-weight: 700; }
   .modal-close { ... }
   .modal-body { padding: 24px; }
   .modal-footer { padding: 18px 24px; border-top: 1px solid var(--color-border); display: flex; justify-content: flex-end; gap: 10px; }

7. EMPTY STATES for all list views:
   .empty-state {
     text-align: center; padding: 60px 20px;
     color: var(--color-text-muted);
   }
   .empty-state-icon { font-size: 48px; margin-bottom: 16px; opacity: 0.4; }
   .empty-state-title { font-size: 16px; font-weight: 600; color: var(--color-text-secondary); margin-bottom: 8px; }
   .empty-state-text { font-size: 14px; margin-bottom: 20px; }
   
   Pipeline empty: "Ei liidejä tässä vaiheessa. Vedä liidi tänne tai lisää uusi."
   Tasks empty: "Ei tehtäviä tänään 🎉 Olet ajan tasalla!"
   Leads empty: "Ei liidejä vielä. Yhdistä n8n tai lisää ensimmäinen liidi."

8. NOTIFICATION BELL (in sidebar bottom area or main content header):
   .notif-bell { position: relative; cursor: pointer; }
   .notif-count {
     position: absolute; top: -4px; right: -4px;
     background: var(--color-danger);
     color: white; font-size: 9px; font-weight: 800;
     min-width: 16px; height: 16px;
     border-radius: var(--radius-full);
     display: flex; align-items: center; justify-content: center;
   }
   .notif-dropdown { ... positioned below bell, max-height 400px, overflow-y scroll }

9. LOADING STATES:
   .skeleton {
     background: linear-gradient(90deg, #F0F2F8 25%, #E4E7EF 50%, #F0F2F8 75%);
     background-size: 200% 100%;
     animation: skeleton-loading 1.5s infinite;
     border-radius: var(--radius-sm);
   }
   @keyframes skeleton-loading { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
   
   Use skeleton divs as placeholder while data loads via AJAX.

10. PRINT / EXPORT button style:
    Add to report pages: .btn-export with printer/download icon
```

### ✅ Vaiheen UI-5 hyväksymiskriteerit
- [ ] Kaikki sivut käyttävät yhtenäistä design-systeemiä
- [ ] Lomakekentät näyttävät focus-tilan (sininen reunus)
- [ ] Modalit käyttävät blur-taustaa
- [ ] Tyhjät tilat (empty states) ovat selkeitä
- [ ] Skeleton-latausanimaatio toimii
- [ ] Kaikki näkymät responsiivisia

---

## UI-yhteenveto

| Vaihe | Sisältö | Arvio |
|---|---|---|
| UI-1 | Design system CSS + sidebar + base layout | 2 pv |
| UI-2 | Dashboard redesign (Coupler.io-tyyli) | 2 pv |
| UI-3 | Pipeline redesign (Pipedrive-tyyli) | 2 pv |
| UI-4 | Liidilista + profiili | 1–2 pv |
| UI-5 | Kaikki muut näkymät | 2 pv |
| **Yht.** | **Visuaalinen kokonaisuudistus** | **~9–10 pv** |

---

## Mitä CRM:n EI pidä näyttää

Lähetä Cursorille myös nämä rajoitukset:

```
VISUAL DON'TS — do NOT do these:

❌ No Bootstrap default styles (blue buttons, gray navbar)
❌ No table-heavy layouts for everything — use cards
❌ No uppercase everything — only section labels in uppercase
❌ No rounded-pill buttons everywhere — use radius-md (10px)
❌ No dark mode toggle (not needed for MVP)
❌ No gradient text everywhere — only sparingly for hero elements
❌ No Comic Sans, Arial, or default system fonts — Inter only
❌ No red/green color blocks — use soft background + colored text
❌ No centered layout — left-aligned data-dense professional layout
❌ No animation on every element — only hover transitions (120-200ms)
❌ No fixed 100vh on scroll areas — let content flow
❌ No inline style="" attributes — everything in CSS classes
❌ No !important everywhere — structure CSS properly
```

---

## ChatGPT-päivitys UI-vaiheille

Lisää ChatGPT briiffiin ennen UI-promptien rikastusta:

```
UI-REDESIGN LISÄKONTEKSTI:
- Kaikki UI-vaiheet ovat VAIN template/CSS -muutoksia. EI Python-muutoksia.
- Design system on app/static/css/design-system.css — kaikki värit CSS-muuttujina
- Fonttiperhe: Inter (Google Fonts)
- Ikonit: Heroicons inline SVG tai CDN
- Kaaviot: Chart.js (jo asennettu)
- Drag-and-drop: SortableJS CDN
- Värimuuttujat alkavat --color-* ja ne tulee käyttää kaikkialla, ei hardcoded hex
- Sidebar on kiinteä 240px vasen paneeli, ei topbaria lainkaan
- Kaikki kortit käyttävät .card -luokkaa, kaikki painikkeet .btn -luokkaa
```
