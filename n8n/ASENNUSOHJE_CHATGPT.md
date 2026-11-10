> **VANHENTUNUT (2026-11-10):** korvattu v2-pipelinella, ks. [README.md](README.md). Säilytetty historian vuoksi.

# FlowLeads n8n — asennusohje (anna tämä ChatGPT:lle)

> **Konteksti ChatGPT:lle:** Olen rakentamassa FlowLeads-CRM:n (Flask + Postgres, Render) ja
> n8n Cloudin välistä liidipipelineä. Arkkitehtuuri: CRM luo viikoittain SearchJobeja → n8n Job
> Poller poimii ne → Job Dispatcher laajentaa alueet/sivut → Lead Worker (Enriched) hakee Oikotiesta,
> rikastaa isännöitsijätiedot ja postaa liidit takaisin CRM:ään. Tässä 3 importattavaa workflowta ja
> asennusvaiheet. Tehtäväsi: auta minua käymään nämä läpi n8n Cloudissa ja debugata jos jokin kohta ei toimi.

Importattavat tiedostot (tässä kansiossa / outputs):

| Tiedosto | Workflow | Rooli |
|---|---|---|
| `flowleads-lead-worker-enriched.json` | **FlowLeads – Lead Worker (Enriched)** | Oikotie-haku + AI-relevanssi + isännöitsijärikastus → POST CRM:ään |
| `flowleads-job-dispatcher.json` | FlowLeads Job Dispatcher | Laajentaa alueet, sivuttaa, kutsuu workeria per sivu |
| `flowleads-job-poller.json` | FlowLeads Job Poller | Cron 15 min: hakee pending-jobit, PATCH running/completed/failed |

---

## Vaihe 0 — Ympäristömuuttujat (tee ennen importtia)

**Render (CRM):**
- `APP_BASE_URL = https://<sinun-crm>.onrender.com`
- `N8N_MASTER_SECRET = <vahva satunnaismerkkijono>`

**n8n Cloud → Settings → Variables (tai instanssin env):**
- `CRM_BASE_URL = https://<sinun-crm>.onrender.com`
- `N8N_MASTER_SECRET = <TÄSMÄLLEEN sama arvo kuin Renderissä>`

> Jos secretit eivät täsmää, jokainen `/api/v1/n8n/*`-kutsu palauttaa 401.

---

## Vaihe 1 — Importtaa workflowt (järjestys on tärkeä)

n8n Cloud → **Workflows → Import from File**. Importtaa **tässä järjestyksessä**:

1. **`flowleads-lead-worker-enriched.json`** ensin (muut viittaavat tähän).
2. **`flowleads-job-dispatcher.json`**.
3. **`flowleads-job-poller.json`**.

---

## Vaihe 2 — Kytke sub-workflow-linkit (pakollinen)

executeWorkflow-nodet eivät säilytä viittausta importissa — valitse kohde käsin:

1. Avaa **Job Dispatcher** → node **`CALL – Lead Worker Universal`** → kentässä *Workflow* valitse
   **FlowLeads – Lead Worker (Enriched)**.
2. Avaa **Job Poller** → node **`CALL – Job Dispatcher`** → valitse **FlowLeads Job Dispatcher**.

---

## Vaihe 3 — Credentiaalit (Enriched Worker)

Lead Worker (Enriched) sisältää AI- ja HTTP-nodeja, jotka tarvitsevat tilisi credentiaalit
(credential-id:t eivät siirry importissa). Avaa worker ja aseta:

- **AI-nodet** (`AI – Classify Lead Relevance`, `AI – Extract Company and Contact Clues`,
  `AI – Extract Fallback Contact Info`) → valitse OpenAI-credentiaalisi.
- **HTTP-haut** (Finder.fi, Asiakastieto, Oikotie) → toimivat ilman authia, mutta jos käytät
  proxya/headeria, aseta se.
- **CRM-kutsut** käyttävät Bearer-avainta payloadista (`{{ $json.crm_api_key }}`) ja
  `X-N8N-Secret`-headeria `$env`:stä → ei erillistä credentiaalia.

> Jokaisen `httpRequest`-noden vastausoptioksi on jo asetettu `neverError: true`, jotta yksittäinen
> 4xx/5xx ei kaada koko ajoa.

---

## Vaihe 4 — Aktivointi

- **Aktivoi VAIN Job Poller.** Dispatcher ja Worker jäävät passiivisiksi sub-workfloweiksi (niitä
  kutsutaan, ei ajeta itsenäisesti).
- Poller-cron on 15 min. Viikkokadenssi tulee CRM:n puolelta (CRM luo weekly-jobin kerran viikossa),
  joten Pollerin tiheys ei vaikuta kuinka usein haut tehdään — se vain poimii valmiit jobit.

---

## Vaihe 5 — Testiajo (yksi profiili)

1. CRM: **Liidiasetukset / Search profiles** → luo aktiivinen profiili: 1 alue, remonttityyppi,
   aikataulu. Profiililla pitää olla `crm_api_key` (org:n API-avain).
2. CRM: paina **"Luo testijob"** (`POST /settings/search-profiles/<id>/create-test-job`).
3. Varmista job: `GET {CRM_BASE_URL}/api/v1/n8n/jobs?status=pending` headerilla `X-N8N-Secret`.
4. n8n: avaa **Job Poller** → **Execute Workflow** (manuaalinen ajo, Manual Trigger -haara).
5. Tarkista:
   - CRM: job-status → `completed`, `leads_sent` > 0
   - CRM: uudet liidit pipelinessä, isännöitsijätiedot liidin metadatassa
   - CRM: `search_dedupe`-taulussa rivit `oikotie-*`
   - Toinen ajo EI luo duplikaatteja (dedupe toimii)

---

## Datakontrakti (workflowien välillä)

**Poller → Dispatcher → Worker** kuljettavat saman objektin:
```
job_id, organization_id, profile_id, remonttityyppi, regions[],
source, crm_api_key, crm_endpoint, current_region, page, offset, limit
```

**Worker → CRM** (`POST {crm_endpoint}`, header `Authorization: Bearer {crm_api_key}`):
```json
{
  "source": "oikotie",
  "source_ref": "oikotie-<cardId>",
  "company": "Isännöitsijätoimisto X",
  "phone": "+358...",
  "email": null,
  "title": "Isännöitsijä",
  "tags": ["<remonttityyppi>", "oikotie"],
  "gdpr_legal_basis": "legitimate_interest",
  "metadata": { "isannoitsija_puh": "...", "taloyhtio": "...", "oikotie_url": "...", "...": "..." }
}
```

**Worker → CRM dedupe** (`POST /api/v1/n8n/dedupe/check` ja `/mark`, header `X-N8N-Secret`):
```json
{ "organization_id": 1, "source_ids": ["oikotie-123"] }
```

---

## Riippuvuus CRM-muutoksiin (tee nämä ensin Cursorilla)

Worker olettaa että CRM:ssä on tehty suunnitelman Faasi 1:n muutokset:
- **1A** `"oikotie"` lisätty `LEAD_SOURCES`-listaan (`app/leads/models.py`) — muuten POST → "Invalid source".
- **1B** sähköposti ei pakollinen (`upsert_lead`: `require_email=False, require_identifier=True`).
- **1C** viikkoajastus (`app/search/job_scheduler.py` + `app/scheduler.py`).

Jos 1A/1B puuttuvat, worker importtuu ja ajaa, mutta CRM hylkää liidit. Tee ne ensin.

---

## Yleisimmät virheet

| Oire | Syy | Korjaus |
|---|---|---|
| 401 kaikissa CRM-kutsuissa | `N8N_MASTER_SECRET` ei täsmää | Aseta sama arvo Renderiin ja n8n:ään |
| POST lead → "Invalid source" | 1A tekemättä | Lisää "oikotie" `LEAD_SOURCES`:iin |
| POST lead → "Email is required" | 1B tekemättä | `require_email=False` upsertissä |
| Worker ei löydä listauksia | Oikotie-alue ei mäppää | Tarkista `current_region` = Suomen maakunnan näyttönimi (esim. "Pirkanmaa") |
| `CALL`-node punainen | sub-workflow-linkki valitsematta | Vaihe 2 |
| Liidit ilman isännöitsijää | Finder/Asiakastieto ei löytänyt | Normaalia osalle; liidi menee silti läpi jos minimidata täyttyy |
