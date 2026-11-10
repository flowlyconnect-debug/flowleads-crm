> **VANHENTUNUT (2026-11-10):** korvattu v2-pipelinella, ks. [README.md](README.md). Säilytetty historian vuoksi.

# FlowLeads n8n-pipeline — korjaussuunnitelma (2026-07-02)

Perustuu kolmen workflow-exportin analyysiin: Job Poller (`LNe2Z4KLA1eGAOCH`), Job Dispatcher (`UosPC00MN1s2uo4Q`), Lead Worker Enriched (`JOpQpAHw5adGoX4j`).

---

## OSA 1 — Nykytilan yhteenveto

### Mikä toimii jo (älä koske)

- **Poller kokonaisuudessaan**: schedule → Config → GET pending jobs → Split jobs → For each job → PATCH running → Build dispatcher payload → CALL Dispatcher → Worker error? → PATCH completed/failed → Next job. URL:t oikein, ei `$env`-viittauksia, Build dispatcher payload säilyttää jobin kentät PATCHin jälkeen.
- **Dispatcherin runko**: Expand regions, For each region -haarat (done → Aggregate totals, loop → Build pages) ja For each page -haarat (done → Next region, loop → sivun käsittely) ovat oikein kytketyt.
- **Build pages** (Dispatcher): tuottaa oikein 5 page-itemiä (page/offset/limit/pageLabel) ja säilyttää kaikki jobin kentät.
- **Workerin Oikotie-haku ja rikastus**: bootstrap + cookie + `api/search` offsetilla, listausten normalisointi, AI-luokittelu, isännöitsijärikastus, Finder/Asiakastieto-fallback — koko keskiosa toimii, älä refaktoroi.
- **Dedupe check** -kutsu ja `is_new`-logiikka toimivat, kun `organization_id` on saatavilla.
- **CRM-puoli**: `POST /admin/dev/create-test-jobs` JA `POST /admin/dev/reset-running-test-jobs` ovat molemmat jo olemassa (login + admin/superadmin + 2FA + `ENABLE_TEST_JOBS`). Reset-endpointtia ei tarvitse tehdä — korjasin vain duplikaattifunktion `app/search/job_scheduler.py`:stä.
- Missään workflow'ssa **ei ole `$env`-viittauksia** (varmistettu grepillä kaikista kolmesta exportista).

### Mikä on rikki — todennäköiset juurisyyt

**Juurisyy 1 — Dispatcherin page-loopissa on vanha "probe"-ketju.**
For each page -loopin sisällä on `Build Oikotie page 1 URL → GET Oikotie page 1 → Build page items`. Tämä ketju:

1. Hakee **aina offset 0** (koodissa kovakoodattu `offset: '0'`, `page: 1`) — jokainen loop-kierros hakee saman ensimmäisen sivun.
2. `Build page items` laskee sivumäärän uudelleen vastauksen `total`-kentästä; jos kenttää ei löydy odotetussa muodossa, se palauttaa vain `[{page: 1}]` → käytännössä yksi sivu.
3. Worker hakee Oikotie-datan **itse** oman page/offset-inputin perusteella — koko probe-ketju on redundantti ja rikkoo sivutuksen.

**Juurisyy 2 — Worker ei saa jobin kenttiä.**
Workerin trigger on `jsonExample`-moodissa skeemalla `{maakunta, page, pageLabel}`, ja Dispatcherin CALL-node mappaa vain nämä kolme kenttää. `job_id`, `organization_id`, `profile_id`, `crm_api_key`, `crm_endpoint`, `remonttityyppi` eivät koskaan saavu Workerille → dedupe/POST-payloadit jäävät vajaiksi. Lisäksi CALL-noden `pageLabel`-expression on korruptoitunut (rivinvaihdot söivät `||`-operaattorit): `{{ $json.pageLabel \n\n String($json.page \n\n 1) }}`.

**Juurisyy 3 — Workerin Dedupe Mark -URL on rikki.**
`HTTP – Dedupe Mark (CRM)` -noden URL-kenttään on liimattu **secret-merkkijono**, ei `https://flowleads-crm.onrender.com/api/v1/n8n/dedupe/mark`. `neverError: true` piilottaa virheen → mark ei koskaan onnistu → samat liidit käsitellään joka ajossa uudestaan.

**Juurisyy 4 — Workerin loppupää käsittelee vain ensimmäisen liidin.**
`TRANSFORM – Build FlowLeads Payload`, `CODE – Mark New Flag` ja molemmat `CODE – Page Result` -nodet ovat oletusmoodissa "Run once for all items" ja käyttävät `$json`-viittausta → vain aallon ensimmäinen liidi käsitellään, loput putoavat pois.

**Juurisyy 5 — Workerissa on tuplakytkentä.**
`HTTP – Fetch Listing Detail Page` on kytketty sekä takaisin `LOOP – Process Listings` -inputtiin (oikein) että suoraan `PARSE – Extract Raw Listing Content` -ketjuun. Lisäksi LOOPin done-haara menee samaan PARSE-nodeen. Sama listaus voi kulkea AI-ketjun läpi kahdesti (toinen kerta näkyy duplikaattina).

**Juurisyy 6 — summary-item puuttuu useilta haaroilta.**
`IF – Lead Is Relevant` (false) ja `IF – Lead Has Minimum Required Data` (false) pudottavat itemit tyhjään — ei summarya. POST-virhe lasketaan silti "sent". `Aggregate totals` lukee `$('CALL – Lead Worker Universal').all()`, joka loopin sisällä palauttaa **vain viimeisen ajon** itemit → totalit väärin.

Huom: `alwaysOutputData: true` on jo päällä CALL-nodessa, joten "Next page ei saa itemiä" -pysähdys on jo osittain lievennetty — mutta oikea korjaus on Worker-summary + probe-ketjun poisto.

### Missä workflow'ssa ongelma on

- **Poller: kunnossa.** Ei pakollisia muutoksia (yksi valinnainen tarkennus, ks. Vaihe 3).
- **Dispatcher: 2 muutosta** — probe-ketjun poisto page-loopista + CALL-noden uudelleenluonti + Aggregate totals -koodin päivitys.
- **Worker: eniten korjattavaa** — trigger passthrough'ksi, Dedupe Mark URL, node-moodit, tuplakytkennän poisto, summary-nodet.

---

## OSA 2 — Toteutussuunnitelma vaiheittain

Järjestys on tärkeä: **Worker ensin, sitten Dispatcher, sitten Poller-testi.** Publishaa jokaisen vaiheen jälkeen.

### Vaihe 1 — Korjaa Worker (FlowLeads – Lead Worker (Enriched))

**1.1 Trigger vastaanottamaan kaikki data**

- Node: `TRIGGER – Start from Parent Workflow`
- Asetus: Input data mode → **Accept all data** (passthrough), poista jsonExample-skeema.
- `CODE – Normalize Job Input` on jo olemassa ja mappaa `current_region → maakunta` + säilyttää jobin kentät — ei muutoksia siihen.

**1.2 Korjaa Dedupe Mark URL**

- Node: `HTTP – Dedupe Mark (CRM)`
- URL-kenttä nyt: secret-merkkijono (virhe). Vaihda:
  ```
  https://flowleads-crm.onrender.com/api/v1/n8n/dedupe/mark
  ```
- Headerit ja JSON body ovat jo oikein — älä koske niihin.

**1.3 Poista tuplakytkentä**

- Poista yhteys: `HTTP – Fetch Listing Detail Page` → `PARSE – Extract Raw Listing Content` (suora haara).
- Säilytä: `HTTP – Fetch Listing Detail Page` → `LOOP – Process Listings` (loopin feedback) ja `LOOP – Process Listings` (done) → `PARSE – Extract Raw Listing Content`.
- Lopputulos: loop hakee detaljisivut yksitellen (rate limit säilyy), done-haara vie **kaikki** kerätyt sivut kerralla AI-ketjuun. Yksi AI-ajo per listaus.

**1.4 Vaihda loppupään nodet per-item-moodiin**

Kaikkiin neljään: node → Settings/parametrit → Mode: **Run Once for Each Item**.

- `TRANSFORM – Build FlowLeads Payload` — muuta return-muoto: `return { json: {...} };` (ei array). Muu koodi ennallaan (`const item = $json;` toimii per-item-moodissa oikein).
- `CODE – Mark New Flag` — sama: `return { json: { ...ctx, is_new: isNew } };`
- `CODE – Page Result (sent)` — korvaa koodi (lisää POST-virheen tarkistus):
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
- `CODE – Page Result (dup)` — korvaa koodi:
  ```javascript
  const c = $json;
  return { json: {
    source_ref: c.source_ref,
    page: c.page,
    current_region: c.current_region,
    result: 'duplicate'
  }};
  ```

**1.5 Lisää CODE – Page Result (skipped)** (uusi Code-node, Mode: Run Once for Each Item)

```javascript
const j = $json || {};
return { json: {
  source_ref: j.source_ref || ('oikotie-' + (j.ID || '')),
  result: 'skipped'
}};
```

Kytkennät:
- `IF – Lead Is Relevant` **false-haara** → `CODE – Page Result (skipped)`
- `IF – Lead Has Minimum Required Data` **false-haara** → `CODE – Page Result (skipped)`

**1.6 Lisää CODE – Worker Result** (uusi Code-node, workflown viimeinen node)

- Mode: Run Once for All Items. Settings → **Always Output Data: ON**.
- Kytkennät sisään: `CODE – Page Result (sent)` → tämä, `CODE – Page Result (dup)` → tämä, `CODE – Page Result (skipped)` → tämä. Ei yhteyksiä ulos.
- Koodi (idempotentti — laskee aina samat totalit node-viittauksista, vaikka node ajettaisiin useasti):

```javascript
const safeAll = (name) => { try { return $(name).all().map(i => i.json || {}); } catch (e) { return []; } };

const sentRows = safeAll('CODE – Page Result (sent)');
const dupRows = safeAll('CODE – Page Result (dup)');
const skipRows = safeAll('CODE – Page Result (skipped)');

let job = {};
try { job = $('TRIGGER – Start from Parent Workflow').first().json || {}; } catch (e) {}

const failedRows = sentRows.filter(r => r.failed);
const okSent = sentRows.length - failedRows.length;

const summary = {
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
};

return [{ json: summary }];
```

**1.7 Tyhjän sivun suoja**

- Node: `TRANSFORM – Build Iteration Payload` — lisää koodin alkuun:
  ```javascript
  const valid = items.filter(it => it.json && (it.json.ID || it.json.URL));
  if (!valid.length) return [];
  ```
  ja muuta loopin lähde `items` → `valid` (eli `return valid.map(item => {...})`).
- Tyhjällä sivulla Worker ei palauta mitään → Dispatcherin CALL-noden Always Output Data tuottaa tyhjän itemin → loop jatkuu, Aggregate laskee 0.

**Odotettu output per Worker-ajo** (yksi item, aina):
```json
{ "job_id": 16, "organization_id": 1, "profile_id": 2, "page": 3,
  "current_region": "Lappi", "leads_found": 24, "leads_sent": 2,
  "duplicates": 21, "skipped": 1, "status": "ok", "error": "" }
```

**Publishaa Worker nyt** (Save + Publish). Trigger-muutos ei näy parent-workflow'lle ennen publishia.

### Vaihe 2 — Korjaa Dispatcherin page-loop

**2.1 Poista probe-ketju** (kolme nodea kokonaan, ei vain kytkentöjä — vanhat skeemat eivät saa jäädä kummittelemaan):

- `Build Oikotie page 1 URL`
- `GET Oikotie page 1`
- `Build page items`

**2.2 Poista vanha `CALL – Lead Worker Universal` ja luo uusi Execute Workflow -node** (vanhassa on korruptoitunut pageLabel-expression ja vanha input-skeema):

- Nimi: `CALL – Lead Worker` (uusi nimi tahallaan — jos säilytät vanhan nimen, muista että Aggregate-fallback-koodi viittaa nimeen)
- Workflow: FlowLeads – Lead Worker (Enriched)
- Mode: **Run once for each item**
- Koska Workerin trigger on nyt "Accept all data", input-mappauskenttiä ei pitäisi näkyä — item menee läpi sellaisenaan (mukana job_id, organization_id, crm_api_key, crm_endpoint, current_region, page, offset, limit, remonttityyppi).
- Settings: **Always Output Data: ON**, **On Error: Continue (using regular output)** — kaatuva Worker-ajo ei saa tappaa koko jobia.

**2.3 Kytkennät (lopullinen rakenne):**

```
Execute Workflow Trigger → Expand regions → For each region
For each region [loop]  → Build pages → For each page
For each page  [loop]   → CALL – Lead Worker → Next page → For each page
For each page  [done]   → Next region → For each region
For each region [done]  → Aggregate totals
```

`Next page` -noden koodi `return $input.all();` säilyy ennallaan.

**2.4 Korvaa `Aggregate totals` -koodi.** Region-loopin done-output kerää kaikki loopin läpi palautetut itemit (= kaikkien sivujen Worker-summaryt kaikilta alueilta), joten lue `$input.all()` eikä `$('CALL ...').all()` (joka palauttaa loopissa vain viimeisen ajon):

```javascript
let rows = $input.all().map(i => i.json || {});

// Fallback: jos done-outputissa ei jostain syystä ole rivejä
if (!rows.filter(r => Object.keys(r).length).length) {
  try { rows = $('CALL – Lead Worker').all().map(i => i.json || {}); } catch (e) { rows = []; }
}

const trigger = $('Execute Workflow Trigger').first().json;

// Suoja: jos sama region+page esiintyy useasti, pidä viimeisin
const byKey = new Map();
let n = 0;
for (const row of rows) {
  if (!Object.keys(row).length) continue; // Always Output Data -tyhjät itemit
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
  totals.worker_error = totals.error; // Pollerin "Worker error?" -IF lukee tätä kenttää
}

return [{ json: totals }];
```

Huom: viimeinen `worker_error`-peilaus on tärkeä — Pollerin `Worker error?` -IF tarkistaa vain `worker_error`-kentän, ja ilman peilausta pelkät failed_pages-virheet PATCHattaisiin "completed".

**Publishaa Dispatcher.**

### Vaihe 3 — Poller (vain tarkistus)

Ei pakollisia muutoksia. Tarkista:

1. `CALL – Job Dispatcher` välittää jobin kentät (Dispatcherin trigger on passthrough — jos noden UI:ssa näkyy vanhoja input-kenttiä, luo node uusiksi samalla tavalla kuin Vaihe 2.2).
2. PATCH-nodejen URL:t: `{{ $('Config').first().json.crm_base_url }}/api/v1/n8n/jobs/{{ $json.job_id }}` — kunnossa exportissa.
3. Valinnainen parannus: `Worker error?` -IF:iin toinen ehto (OR): `{{ $json.status }}` equals `failed`. Ei pakollinen, koska Aggregate peilaa nyt errorin worker_error-kenttään.
4. Huom: `PATCH job running`- ja `GET pending jobs` -nodeissa secret on kovakoodattu tekstinä, muissa se luetaan Configista. Toimii, mutta yhtenäistä myöhemmin (ks. parannusehdotukset).

**Publishaa Poller viimeisenä.**

### Vaihe 4 — Testaus

1. **Nollaa jumittuneet jobit** (selaimen console, kirjautuneena admin/superadmin + 2FA, Renderissä `ENABLE_TEST_JOBS=true`):
   ```javascript
   const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
   fetch('/admin/dev/reset-running-test-jobs', { method: 'POST', credentials: 'same-origin',
     headers: {'Content-Type':'application/json','X-CSRFToken':csrf,'X-CSRF-Token':csrf}, body: '{}' })
     .then(async r => console.log(r.status, await r.text()));
   ```
   Odotettu: `{"reset": N}`. Aja sitten sama `/admin/dev/create-test-jobs` → `{"created": N, "skipped": M}`.
2. **Aja Poller käsin**: avaa Poller n8n:ssä → Manual Trigger → Execute workflow. Älä aja Dispatcheria/Workeria suoraan käsin (ne saavat datansa parentilta).
3. **Mistä näet että sivut 1–5 käytiin**: Dispatcherin executionissa `For each page` näyttää 5 iteraatiota per alue, ja `CALL – Lead Worker` 5 ajoa. n8n → Executions → Lead Worker: 5 suoritusta, joissa `HTTP – Fetch Oikotie Listings API` -noden query-parametri `offset` on 0, 24, 48, 72, 96.
4. **Mistä näet että liidi meni CRM:ään**: Worker-executionissa `HTTP – POST Lead to FlowLeads` palauttaa onnistumisvastauksen, ja liidi näkyy CRM:n Leads-listassa (source_ref `oikotie-...`).
5. **Mistä näet että dedupe toimii**: aja sama job kahdesti — toisella ajolla `IF – Lead Is New` menee false-haaraan ja lopputotaleissa `duplicates` kasvaa, `leads_sent` = 0. Jos duplicates EI kasva toisella ajolla, Dedupe Mark -URL on edelleen väärä.
6. **Mistä näet että Poller sai payloadin**: `PATCH job completed` -noden request body sisältää oikeat `leads_found`/`leads_sent`, ja CRM:ssä jobin status on `completed`.
7. **Tarkista myös**: SearchJobin `regions`-arvot vastaavat täsmälleen Workerin region-mapin avaimia (esim. `Lappi`, `Pohjois-Pohjanmaa` — isot alkukirjaimet ja väliviivat).
8. Jos `PARSE – Extract Raw Listing Content` heittää pairing-virheen ("Can't get data for expression") done-haaran kautta ajettaessa: kerro, niin katsotaan vaihtoehtoinen tapa kuljettaa listauskonteksti fetch-itemin mukana. Älä arvaa korjausta itse.

### Vaihe 5 — Publish ja aktivointi

| Järjestys | Workflow | Publish | Active-kytkin |
|---|---|---|---|
| 1 | Lead Worker (Enriched) | kyllä | **ei tarvita** (sub-workflow) |
| 2 | Job Dispatcher | kyllä | **ei tarvita** (sub-workflow) |
| 3 | Job Poller | kyllä | **ON** (schedule 15 min) |

Sub-workflowt toimivat Execute Workflow -kutsulla ilman Active-tilaa. Jos trigger-input-muutos ei näy parent-nodessa, avaa parent-workflow uudestaan (refresh) publishauksen jälkeen.

---

## Ehdotukset myöhemmäksi (EI toteuteta ilman erillistä lupaa)

1. **Secretin keskittäminen ja rotaatio** — secret on nyt kovakoodattuna useissa nodeissa ja päätyi myös workflow-exporteihin. Keskitä yhteen Config-nodeen per workflow ja vaihda arvo Renderiin + nodeihin samalla kertaa.
2. **Retry/backoff Oikotie-hakuihin** — HTTP-nodeihin Retry On Fail (esim. 3 × 5 s). Perustelu: Oikotie ja Render kylmäkäynnistyvät ajoittain.
3. **Rate limit / sleep sivujen välissä** — pieni Wait-node page-looppiin (Oikotie-blokkiriskin pienennys).
4. **Early stop tyhjällä sivulla** — jos Worker palauttaa `leads_found: 0`, keskeytä alueen loput sivut (säästää aikaa ja kutsuja).
5. **`max_pages` per SearchProfile** — nyt oletus 5; profiilikohtainen arvo antaisi asiakaskohtaisen hallinnan.
6. **Sivukohtainen tracking** — `page_results`-taulu tai SearchJobin metadata-kenttä (page, region, found, sent, duplicates, error) → debug helpottuu radikaalisti.
7. **Parempi virheloki SearchJobiin** — nyt vain yksi error-string; lista virheistä per sivu olisi arvokkaampi.
8. **Dedupe myös emaililla** — nyt dedupe on source_ref-pohjainen; sama isännöitsijä eri kohteesta tulee uutena liidinä. Harkitse (source_ref OR email) -logiikkaa CRM:n dedupe-endpointissa.
9. **Admin-UI-nappi testijobeille** — console-fetchin tilalle napit "Luo testijobit" / "Resetoi running-jobit" admin-sivulle (endpointit ovat jo olemassa).
10. **Oikotie-alueiden mapping CRM:ään** — Workerin region-map on kovakoodattu; siirto CRM:n asetuksiin tai jobin payloadiin poistaisi rikkoutumisriskin.
11. **Proxy/header-strategia** — jos Oikotie alkaa blokata, keskitetty user-agent/cookie-käsittely tai proxy.
12. **Observability** — dev-only endpoint tai dashboard-kortti: viimeisimmät jobit, sivutotalit, virheet, n8n-executionien linkit.
13. **Worker summary -skeeman versiointi** — lisää summaryyn `schema_version: 1`, niin Aggregate voi tulevaisuudessa käsitellä eri versioita rikkomatta vanhaa.

---

## CRM-repoon tehty muutos tässä sessiossa

- `app/search/job_scheduler.py`: poistettu identtinen duplikaatti `reset_running_test_jobs`-funktiosta ja `_RESET_RUNNING_ERROR`-vakiosta (copy-paste-tuplaus). Toiminnallisuus ennallaan. Muuta EI muutettu.
