# FlowLeads — Dashboardin toteutussuunnitelma (Mission Control)

> Status: **suunnitelma, ei toteutettu.** Ei vielä koodi-, CSS- eikä template-muutoksia.
> Pohja: `CRM_UI_DASHBOARD_BLUEPRINT_MISSION_CONTROL.md` (hyväksytty rakenne).
> Päivätty: 2026-06-16.

## Lähtötilanne koodissa (todennettu)

Dashboard on jo täysin client-data-vetoinen. Tämä on tärkein havainto: **rakenne ja tyyli muuttuvat vain front-endissä, dataa ja reittejä ei tarvitse koskea.**

- Reitti: `app/analytics/routes.py` → `dashboard()` (rivi ~705) renderöi `templates/analytics/dashboard.html`.
- `templates/analytics/dashboard.html` = ohut kuori: `extends base.html`, lataa `css/dashboard.css`, `{% include "dashboard/index.html" %}`, lataa `js/dashboard.js`.
- `templates/dashboard/index.html` = varsinainen markup (komento-otsikko, KPI-kortit, AI-worklist, AI-pulssi, live-aktiviteetti).
- `static/js/dashboard.js` hakee datan JSON-endpointeista ja täyttää DOM:in: `/api/dashboard/metrics`, `/api/dashboard/ai-worklist`, `/api/dashboard/pipeline-distribution`, `/api/dashboard/activity-stream`.
- Tokenit/värit: `static/css/design-system.css` (lukittu — ei muutoksia).

## 1. Mitä tiedostoja muutetaan

**Muutetaan (vain front-end):**

| Tiedosto | Rooli muutoksessa |
| --- | --- |
| `app/templates/dashboard/index.html` | Markupin uudelleenjärjestely blueprintin vyöhykkeiksi. Pääkohde. |
| `app/static/css/dashboard.css` | Uudet grid-vyöhykkeet ja paneelityylit. Vain olemassa olevia tokeneita käyttäen. |
| `app/static/js/dashboard.js` | Telemetria-virran renderöinti olemassa olevasta datasta; paneelien uudet sijainnit/otsikot. |

**EI kosketa (kriittistä):**

- `app/analytics/routes.py` ja kaikki `/api/dashboard/*`-endpointit — datasopimus pysyy identtisenä.
- `app/analytics/services.py`, mallit, migraatiot.
- `static/css/design-system.css` — värit ja tokenit lukittu.
- `templates/base.html`, `components/sidebar.html`.
- `templates/analytics/dashboard.html` — vain jos pitäisi lisätä uusi CSS-linkki; lähtökohtaisesti ennallaan.

## 2. Mitä komponentteja luodaan tai muokataan

| Komponentti | Toimenpide | Huom |
| --- | --- | --- |
| Telemetria-virta (ticker) | **Luodaan uusi** markup + CSS + JS-render | Rakennetaan olemassa olevasta `activity_feed`-datasta ja hälytysluvuista — **ei uutta dataa eikä endpointtia**. |
| KPI-band | **Muokataan** (`.dashboard-row-1`, `.metric-card-dark`) | Samat 4 mittaria, instrumentti-band, tabulaariset luvut. ID:t säilyvät. |
| AI-monitori (sankari) | **Muokataan + nostetaan** (`.ai-worklist-card`) | Pääsarakkeeseen, ranking + syy-rivi. ID `aiWorklistBody` säilyy. |
| Putken pulssi | **Muokataan + siirretään** oikeaan sarakkeeseen | Käyttää jo haettua `/api/dashboard/pipeline-distribution`-dataa. |
| AI-pulssi | **Muokataan + siirretään** oikeaan sarakkeeseen | Vain tulkitut signaalit. ID `aiPulseFeed` säilyy. |
| Live-aktiviteetti | **Siirretään alas** full-width | ID `activityStream` säilyy. |
| Hälytysrivi (`.alert-strip`) | **Yhdistetään** telemetria-virtaan | Vanha alert-strip-markup korvautuu tickerillä. |
| Vanhat `.dashboard-row-2/3`-gridit | **Korvataan** uudella vyöhyke-gridillä | Dead CSS poistetaan vasta lopuksi. |

## 3. Mikä tehdään ensin (järjestys)

0. **Baseline:** uusi git-haara + kuvakaappaus nykyisestä dashboardista (normaali käyttäjä + superadmin) vertailupohjaksi.
1. **CSS-runko additiivisesti:** uudet vyöhyke- ja grid-luokat `dashboard.css`:ään poistamatta vanhoja. Mikään ei vielä rikkoudu.
2. **Markupin vyöhykkeet:** `dashboard/index.html` järjestetään blueprintin mukaan (ylä-ticker, otsikko, KPI-band, pää 2fr/1fr, ala-loki) — **säilyttäen kaikki JS:n lukemat DOM-id:t ja `data-*`-attribuutit**.
3. **Telemetria-virta:** markup + CSS + `dashboard.js`-render olemassa olevasta datasta.
4. **KPI-band + AI-monitori-sankari:** uudelleentyylitys, tabulaariset luvut, ranking-rivit.
5. **Putken pulssi + AI-pulssi + live-aktiviteetti:** siirto ja tyylitys uusiin paikkoihin.
6. **Viimeistely:** liike < 300 ms ease-out, hover-tilat, ja vasta nyt **kuolleen CSS:n poisto**.

## 4. Miten varmistetaan ettei backend, reitit tai multi-tenant rikkoudu

- **Nolla .py-tiedostoa muutetaan** → reitit, palvelut ja `/api/dashboard/*`-datasopimus pysyvät identtisinä. Multi-tenant-skooppaus (`organization_id`, `get_accessible_organizations`, kyselyiden `filter_by(organization_id=...)`) on kokonaan backendissä, eikä siihen kosketa.
- **Säilytetään kaikki DOM-id:t ja data-attribuutit** joita `dashboard.js` käyttää: `#metricNewLeads`, `#metricHotLeads`, `#metricTasksToday`, `#metricPipelineValue`, `#aiWorklistBody`, `#aiPulseFeed`, `#activityStream`, `#dashboard-command-center` (+ `data-activity-feed`, `data-org-query`), `#csrf-token`, `#dashboard-current-date`. Näiden uudelleennimeäminen rikkoisi JS:n — sitä ei tehdä.
- **Säilytetään kaikki `url_for(...)`-linkit ja `**org_q` / `organization_id`-parametrit**, jotta superadminin org-skooppaus pysyy. Pidetään `{% if org_picker %}`-haara ja `{% if current_user.is_superadmin() %}`-ehdot ennallaan.
- **Verifiointi joka vaiheen jälkeen:**
  - `pytest` (olemassa oleva testisuite) — varmistaa ettei reitit/logiikka rikkoutuneet.
  - Lataa dashboard (a) normaalina käyttäjänä ja (b) superadminina `?organization_id=...` — varmista että 4 mittaria, worklist, pulssi ja aktiviteetti täyttyvät (JS-endpointit ennallaan).
  - Selaimen konsoli: ei JS-virheitä (puuttuva id paljastuisi heti).
  - Cross-tenant-tarkistus: data pysyy organisaatiokohtaisena (backend skooppaa — ei muutu).
  - Kuvakaappaus-vertailu baselineen.
- **Korkean panoksen verifiointi:** ennen mergeä erillinen tarkistus (esim. subagent) joka lukee diffin ja vahvistaa, ettei yhtään `.py`-tiedostoa, endpointtia, id:tä tai org-skooppausta ole muuttunut.

## 5. Miten muutos tehdään pienissä vaiheissa

- **Yksi commit per yllä oleva askel (1–6)**, jokainen itsenäisesti katsottavissa ja toimiva.
- **Additiivinen järjestys:** uusi CSS ensin → markup vaihto → kuollut CSS poistetaan vasta lopuksi. Dashboard pysyy toimivana joka commitin jälkeen.
- **Markup ja tyyli erikseen:** ensin rakenne (vyöhykkeet + id:t paikallaan), sitten visuaalinen tyyli — näin regressio on helppo paikantaa.
- **Smoke-testi joka askeleen jälkeen:** sivu latautuu, konsoli puhdas, 4 mittaria täyttyvät, worklist/pulssi/aktiviteetti renderöityvät.
- **Helppo perääntyä:** koska kyse on yhdestä include-templatesta + yhdestä CSS:stä + yhdestä JS:stä, minkä tahansa askeleen voi perua ilman vaikutusta muihin näkymiin.

## Reunaehdot (yhä voimassa)

Ei värimuutoksia, ei uusia päävärejä, ei uusia JS-kirjastoja, ei backend-logiikan muutoksia, ei neon/sci-fi-tyyliä, ei liian sekavaa. Yksi näkymä (dashboard) kerrallaan. **Ei toteutusta ennen erillistä hyväksyntää.**
