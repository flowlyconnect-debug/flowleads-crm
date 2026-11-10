# ChatGPT-prompti: FlowLeads n8n-pipelinen korjaus (kopioi tästä alaspäin)

---

Olet kokenut n8n-asiantuntija. Autat minua korjaamaan FlowLeads CRM:n n8n-pipelinen Hostingerin self-hosted n8n:ssä. Käy muutokset läpi kanssani **yksi vaihe kerrallaan** — älä anna kaikkia muutoksia kerralla. Odota että kuittaan vaiheen tehdyksi ennen seuraavaa. Jos jokin ei täsmää kuvaukseen (node puuttuu, nimi eri, virheilmoitus), pyydä minulta screenshot tai tarkennus ennen kuin arvaat.

## Konteksti

Kolme workflowta:

1. **FlowLeads – Job Poller** (aktiivinen, schedule 15 min): hakee pending-jobit CRM:stä, PATCHaa running, kutsuu Dispatcheria, PATCHaa completed/failed.
2. **FlowLeads – Job Dispatcher** (sub-workflow): laajentaa `regions[]` → alueet, rakentaa sivut 1–5, kutsuu Lead Workeria per sivu, kokoaa totalit.
3. **FlowLeads – Lead Worker (Enriched)** (sub-workflow): hakee Oikotie-liidit yhden sivun osalta (page/offset), rikastaa AI:lla + Finder/Asiakastieto-fallbackilla, dedupettaa CRM:ää vasten ja POSTaa uudet liidit.

Kiinteät arvot (ympäristö EI salli `$env`-käyttöä — kaikki kovakoodattu tai payloadista):
- CRM base URL: `https://flowleads-crm.onrender.com`
- Dedupe check: `POST https://flowleads-crm.onrender.com/api/v1/n8n/dedupe/check` (header `X-N8N-Secret: <sama secret kuin Renderissä>`)
- Dedupe mark: `POST https://flowleads-crm.onrender.com/api/v1/n8n/dedupe/mark` (sama header)
- Lead POST: `{{ $json.crm_endpoint }}`, auth `Bearer {{ $json.crm_api_key }}` (tulee jobin payloadista)
- Missään ei saa käyttää `$env.CRM_BASE_URL` tai `$env.N8N_MASTER_SECRET`.

## Tunnetut juurisyyt (jo diagnosoitu, älä kyseenalaista ilman uutta evidenssiä)

1. Dispatcherin page-loopissa on vanha probe-ketju (`Build Oikotie page 1 URL → GET Oikotie page 1 → Build page items`), joka hakee aina offset 0:n ja romahduttaa sivutuksen yhteen sivuun. Worker hakee Oikotien itse → ketju poistetaan.
2. Workerin trigger on jsonExample-moodissa (vain maakunta/page/pageLabel) eikä saa jobin kenttiä (organization_id, crm_api_key, crm_endpoint...) → vaihdetaan Accept all data.
3. Workerin `HTTP – Dedupe Mark (CRM)` -noden URL-kentässä on vahingossa secret-merkkijono eikä dedupe/mark-URL.
4. Workerin loppupään Code-nodet ovat "Run once for all items" -moodissa → vain ensimmäinen liidi käsitellään.
5. `HTTP – Fetch Listing Detail Page` on kytketty sekä loopin feedbackiin että suoraan AI-ketjuun → tuplakäsittely.
6. Skipped/failed-haarat eivät palauta summary-itemiä, ja Dispatcherin Aggregate lukee vain Worker-CALLin viimeisen ajon.

## Työjärjestys

**VAIHE 1: Worker → VAIHE 2: Dispatcher → VAIHE 3: Poller-tarkistus → VAIHE 4: testi.** Publish jokaisen workflow'n jälkeen järjestyksessä Worker → Dispatcher → Poller.

---

### VAIHE 1 — FlowLeads – Lead Worker (Enriched)

**1a.** Node `TRIGGER – Start from Parent Workflow` → Input data mode: **Accept all data** (poista jsonExample).

**1b.** Node `HTTP – Dedupe Mark (CRM)` → URL-kenttään:
```
https://flowleads-crm.onrender.com/api/v1/n8n/dedupe/mark
```
(Headerit ja body ennallaan.)

**1c.** Poista kytkentä `HTTP – Fetch Listing Detail Page` → `PARSE – Extract Raw Listing Content`. Säilytä `HTTP – Fetch Listing Detail Page` → `LOOP – Process Listings` ja `LOOP – Process Listings` (done-haara) → `PARSE – Extract Raw Listing Content`.

**1d.** Vaihda Mode: **Run Once for Each Item** näihin nodeihin ja päivitä koodit:

`TRANSFORM – Build FlowLeads Payload`: pidä koodi muuten, mutta lopun `return [{ json: {...} }];` → `return { json: {...} };`

`CODE – Mark New Flag`: lopun `return [{ json: { ...ctx, is_new: isNew } }];` → `return { json: { ...ctx, is_new: isNew } };`

`CODE – Page Result (sent)` — koko koodi:
```javascript
const c = $('CODE – Mark New Flag').item.json;
let post = {};
try { post = $('HTTP – POST Lead to FlowLeads').item.json || {}; } catch (e) {}
const failed = !!(post.error || post.errors || post.success === false);
return { json: {
  source_ref: c.source_ref,
  page: c.page,
  current_region: c.current_region,
  result: failed ? 'failed' : 'sent',
  failed,
  error: failed ? JSON.stringify(post).slice(0, 300) : ''
}};
```

`CODE – Page Result (dup)` — koko koodi:
```javascript
const c = $json;
return { json: {
  source_ref: c.source_ref,
  page: c.page,
  current_region: c.current_region,
  result: 'duplicate'
}};
```

**1e.** Uusi Code-node `CODE – Page Result (skipped)` (Mode: Run Once for Each Item):
```javascript
const j = $json || {};
return { json: {
  source_ref: j.source_ref || ('oikotie-' + (j.ID || '')),
  result: 'skipped'
}};
```
Kytke siihen: `IF – Lead Is Relevant` **false-haara** ja `IF – Lead Has Minimum Required Data` **false-haara**.

**1f.** Uusi Code-node `CODE – Worker Result` (Mode: Run Once for All Items, Settings → Always Output Data: ON). Kytke sisään: `CODE – Page Result (sent)`, `CODE – Page Result (dup)`, `CODE – Page Result (skipped)`. Ei kytkentöjä ulos. Koodi:
```javascript
const safeAll = (name) => { try { return $(name).all().map(i => i.json || {}); } catch (e) { return []; } };

const sentRows = safeAll('CODE – Page Result (sent)');
const dupRows = safeAll('CODE – Page Result (dup)');
const skipRows = safeAll('CODE – Page Result (skipped)');

let job = {};
try { job = $('TRIGGER – Start from Parent Workflow').first().json || {}; } catch (e) {}

const failedRows = sentRows.filter(r => r.failed);
const okSent = sentRows.length - failedRows.length;

return [{ json: {
  job_id: job.job_id ?? null,
  organization_id: job.organization_id ?? null,
  profile_id: job.profile_id ?? null,
  page: Number(job.page || 1),
  current_region: job.current_region || job.maakunta || '',
  leads_found: sentRows.length + dupRows.length + skipRows.length,
  leads_sent: okSent,
  duplicates: dupRows.length,
  skipped: skipRows.length,
  status: failedRows.length > 0 ? 'failed' : 'ok',
  error: failedRows.length > 0 ? String(failedRows[0].error || 'POST failed') : ''
}}];
```

**1g.** Node `TRANSFORM – Build Iteration Payload` — lisää koodin alkuun tyhjän sivun suoja:
```javascript
const valid = items.filter(it => it.json && (it.json.ID || it.json.URL));
if (!valid.length) return [];
```
ja käytä loopissa `valid`-muuttujaa `items`-muuttujan sijaan.

**1h.** Save + **Publish Worker.** Kuittaa minulle, niin siirrytään Dispatcheriin.

---

### VAIHE 2 — FlowLeads – Job Dispatcher

**2a.** Poista nämä kolme nodea kokonaan: `Build Oikotie page 1 URL`, `GET Oikotie page 1`, `Build page items`.

**2b.** Poista vanha `CALL – Lead Worker Universal` (siinä on rikkinäinen pageLabel-expression ja vanha input-skeema, joka kummittelee). Luo uusi **Execute Workflow** -node:
- Nimi: `CALL – Lead Worker`
- Workflow: FlowLeads – Lead Worker (Enriched)
- Mode: **Run once for each item**
- Input-mappauskenttiä ei pitäisi näkyä (Workerin trigger on nyt Accept all data). Jos näkyy maakunta/page/pageLabel-kenttiä, Worker ei ole publishattuna oikein — palaa vaiheeseen 1h.
- Settings: **Always Output Data: ON** ja **On Error: Continue (using regular output)**.

**2c.** Kytkennät (tarkka rakenne):
```
For each region [loop-haara]  → Build pages → For each page
For each page  [loop-haara]   → CALL – Lead Worker → Next page → For each page (input)
For each page  [done-haara]   → Next region → For each region (input)
For each region [done-haara]  → Aggregate totals
```
`For each page` ja `For each region`: Batch Size 1 (oletus). `Next page` -noden koodi pysyy: `return $input.all();`

**2d.** Korvaa `Aggregate totals` -noden koodi kokonaan:
```javascript
let rows = $input.all().map(i => i.json || {});

if (!rows.filter(r => Object.keys(r).length).length) {
  try { rows = $('CALL – Lead Worker').all().map(i => i.json || {}); } catch (e) { rows = []; }
}

const trigger = $('Execute Workflow Trigger').first().json;

const byKey = new Map();
let n = 0;
for (const row of rows) {
  if (!Object.keys(row).length) continue;
  const key = (row.current_region !== undefined || row.page !== undefined)
    ? `${row.current_region || ''}|${row.page || ''}`
    : `nokey-${n++}`;
  byKey.set(key, row);
}

const totals = {
  job_id: trigger.job_id,
  organization_id: trigger.organization_id,
  profile_id: trigger.profile_id,
  status: 'completed',
  leads_found: 0,
  leads_sent: 0,
  duplicates: 0,
  skipped: 0,
  failed_pages: 0,
  worker_error: '',
  error: ''
};

for (const row of byKey.values()) {
  totals.leads_found += Number(row.leads_found || 0);
  totals.leads_sent  += Number(row.leads_sent  || 0);
  totals.duplicates  += Number(row.duplicates  || 0);
  totals.skipped     += Number(row.skipped     || 0);
  if (row.status === 'failed' || row.failed || row.page_failed) totals.failed_pages += 1;
  if (row.worker_error || row.error) totals.worker_error = String(row.worker_error || row.error);
}

if (totals.worker_error || totals.failed_pages > 0) {
  totals.status = 'failed';
  totals.error = totals.worker_error || `Failed pages: ${totals.failed_pages}`;
  totals.worker_error = totals.error;
}

return [{ json: totals }];
```

**2e.** Save + **Publish Dispatcher.** Kuittaa.

---

### VAIHE 3 — FlowLeads – Job Poller (vain tarkistus)

- Varmista PATCH-nodejen URL: `{{ $('Config').first().json.crm_base_url }}/api/v1/n8n/jobs/{{ $json.job_id }}` (kaikki kolme PATCHia).
- Varmista `Build dispatcher payload` on `PATCH job running` → `CALL – Job Dispatcher` -välissä.
- Jos `CALL – Job Dispatcher` -nodessa näkyy vanhoja input-kenttiä, luo se uusiksi (Execute Workflow, mode: Run once for each item, Always Output Data ei pakollinen tässä).
- Ei `$env`-viittauksia missään.
- Save + **Publish Poller** (Active: ON — ainoana kolmesta).

---

### VAIHE 4 — Testaus (yksi job)

1. CRM: aja selaimen consolessa (kirjautuneena, `ENABLE_TEST_JOBS=true`):
   - `POST /admin/dev/reset-running-test-jobs` → odotettu `{"reset": N}`
   - `POST /admin/dev/create-test-jobs` → odotettu `{"created": N, "skipped": M}`
   (CSRF: `document.querySelector('meta[name="csrf-token"]')?.content` headereihin `X-CSRFToken` ja `X-CSRF-Token`.)
2. n8n: avaa **Poller** → Manual Trigger → Execute workflow. Älä aja Dispatcheria tai Workeria suoraan.
3. Tarkista:
   - Dispatcher-execution: `For each page` 5 iteraatiota per alue, `CALL – Lead Worker` 5 ajoa.
   - Worker-executionit: `HTTP – Fetch Oikotie Listings API` offset-parametri 0 / 24 / 48 / 72 / 96.
   - `CODE – Worker Result` palauttaa joka ajosta yhden summary-itemin (myös duplicate/skip-tapauksissa).
   - `Aggregate totals`: leads_found/sent/duplicates/skipped täsmäävät.
   - CRM: job status `completed`, uudet liidit näkyvät (source_ref `oikotie-...`).
   - Aja sama job toistamiseen → `duplicates` kasvaa, `leads_sent` 0, ei uusia liidejä.

## Yleisimmät virheet ja mitä ne tarkoittavat

- **"No fields - node executed, but no items were sent on this branch"** Next page -nodessa → CALL-noden Always Output Data ei ole päällä, tai kytkentä menee väärästä haarasta.
- **"Node X hasn't been executed"** Code-nodessa → `$('X')`-viittaus nodeen jota ei ajettu; koodissa pitää olla try/catch (yllä olevissa on).
- **"The value in the JSON Body field is not valid JSON"** dedupe-nodeissa → `organization_id` puuttuu itemistä → Workerin trigger ei ole Accept all data -tilassa tai Dispatcherin CALL-node mappaa kenttiä vanhalla skeemalla → luo CALL uusiksi.
- **Worker-inputissa näkyy maakunta/page/pageLabel-kenttiä pakollisina** → vanha jsonExample-skeema voimassa; publishaa Worker uudelleen ja avaa Dispatcher uudestaan.
- **duplicates ei kasva toisella ajolla** → Dedupe Mark URL väärin (vaihe 1b tekemättä).
- **leads_sent aina 0 tai 1 vaikka osumia enemmän** → loppupään Code-nodet edelleen "Run once for all items" -moodissa (vaihe 1d).
- **"URLSearchParams is not defined"** → n8n Code-node ei tue sitä; rakenna query string manuaalisesti (ei pitäisi enää esiintyä, probe-ketju poistettiin).
- **Sivut käydään mutta kaikilla sama data** → probe-ketju jäänyt page-looppiin (vaihe 2a tekemättä).

Aloita kysymällä minulta: "Onko kaikki kolme workflowta auki ja oletko valmis aloittamaan Vaiheesta 1 (Worker)?"
