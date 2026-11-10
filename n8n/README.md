# FlowLeads n8n – v2-pipeline (Poller → Liidi Ohjaaja → Lead Worker)

```
CRM: SearchProfile → SearchJob (pending)
  → FlowLeads v2 – Job Poller        (15 min välein, ottaa 1 jobin kerrallaan)
  → FlowLeads v2 – Liidi Ohjaaja     (maakunnat → KAIKKI Oikotie-sivut, yksi sivu kerrallaan)
  → FlowLeads v2 – Lead Worker       (1 sivu: dedupe → AI-relevanssi → isännöitsijä → POST CRM)
  → CRM: liidit + dedupe + jobin edistyminen
```

## Tiedostot

| Tiedosto | Rooli |
|---|---|
| `workflows/flowleads-v2-job-poller.json` | Hakee (ja lukitsee) yhden pending-jobin, kutsuu Ohjaajaa, merkitsee jobin `completed`/`failed`. |
| `workflows/flowleads-v2-liidi-ohjaaja.json` | Vanhan *Liidi Ohjaajan* logiikka jobipohjaisena: jokaiselle maakunnalle luetaan "NNN kpl" Oikotien hakusivulta, lasketaan sivumäärä (24/sivu) ja **jokainen sivu lähetetään Workerille erikseen, yksi kerrallaan** (SplitInBatches, batch 1, odottaa Workerin valmistumista). Jokaisen sivun jälkeen heartbeat CRM:ään. |
| `workflows/flowleads-v2-lead-worker.json` | Tuotannon *Lead Worker* -export, josta Pinsale + Google Sheets on korvattu CRM-kutsuilla. Oikotie-haku, AI-luokittelu ja isännöitsijärikastus ovat ennallaan. |
| `tools/build_workflows.py` | Generoi yllä olevat kolme tiedostoa. Worker rakennetaan tiedostosta `source/lead-worker-export.json`. |
| `source/` | Alkuperäiset exportit (Lead Worker, Liidi Ohjaaja), joista v2 on johdettu. |

Generointi uudelleen (esim. jos muokkaat Lead Workeria n8n:ssä ja exporttaat uudestaan):

```bash
python n8n/tools/build_workflows.py n8n/source/lead-worker-export.json
```

## Asennus n8n:ään

Nimet ovat tarkoituksella `FlowLeads v2 – …`, joten vanhoihin workfloweihin ei kosketa.

1. **Import** `flowleads-v2-lead-worker.json` → tarkista, että kolme OpenAI-nodea käyttävät "OpenAi account" -credentialia → Save.
2. **Import** `flowleads-v2-liidi-ohjaaja.json` → avaa `CALL – Lead Worker` → valitse *FlowLeads v2 – Lead Worker* → Save.
3. **Import** `flowleads-v2-job-poller.json` → avaa `SET – Config`:
   - `crm_base_url` = CRM:n osoite (esim. `https://flowleads-crm.onrender.com`)
   - `n8n_secret` = sama arvo kuin Renderin `N8N_MASTER_SECRET`

   Avaa `CALL – Liidi Ohjaaja` → valitse *FlowLeads v2 – Liidi Ohjaaja* → Save.
4. Testaa ensin käsin (alla). Aktivoi sen jälkeen **vain Job Poller**. Ohjaaja ja Worker ovat alityönkulkuja, eikä niitä aktivoida.
5. Kun v2 toimii, poista käytöstä vanha Liidi Ohjaaja / Lead Worker / FlowLeads – Job Poller, ettei sama haku pyöri kahdesti.

## Miten tiedot kulkevat

- Poller lisää jobiin `crm_base_url` ja `n8n_secret`. Koko item (job + maakunta + sivu) kulkee
  passthrough-triggereillä Ohjaajalle ja Workerille, joten mitään ei tarvitse mapata käsin.
- `crm_api_key` jobissa on **jobikohtainen token** (ei organisaation pysyvä avain). Jokainen
  sivu-heartbeat jatkaa sen voimassaoloa, ja jobin sulkeminen mitätöi sen.
- **Dedupe ennen AI:ta**: Worker kysyy CRM:ltä koko sivun ID:t kerralla. Ohitetaan
  `oikotie-<id>` (jo lähetetty) ja `oikotie-<id>:p<profile_id>` (AI totesi jo epärelevantiksi
  tälle profiilille). Kalliit detaljisivuhaut ja AI-kutsut tehdään siis vain uusille kohteille.
- **Laskurit kerää CRM**: `leads_found` = maakuntien Oikotie-kohteiden määrä (sivun 1 heartbeatissa),
  `leads_sent` = job-tokenilla *luodut* liidit (päivitys ei laske). Poller ei lähetä lukuja.
- Sivu epäonnistuu → heartbeat `page_failed: true` + virhe, ja ajo jatkuu seuraavalla sivulla.
  Job merkitään `failed`, vain jos **kaikki** sivut epäonnistuivat.
- Stuck-job reaper katsoo viimeistä heartbeatia (60 min), joten tuntien mittainen koko maakunnan
  ajo ei mene uudelleen jonoon kesken kaiken.

## Lead Workerin korjaukset (verrattuna tuotannon exporttiin)

- Trigger ottaa vastaan koko jobin (passthrough), joten `job_id`, `organization_id` ja CRM-kentät eivät enää katoa.
- Detaljisivun HTTP oli kytketty sekä looppiin että suoraan parseen, jolloin jokainen kohde ajettiin AI:n läpi kahdesti. Nyt kytkentä menee vain loopin kautta.
- `CHECK – Missing Company`, `BYPASS`- ja `Normalize Fallback Candidates` -nodet ajettiin "once for all items" `$json`:lla, jolloin vain sivun **ensimmäinen** liidi eteni. Nyt ne ajetaan per item.
- Fallback-AI:n `MERGE (lead_key)` ei koskaan täsmännyt, koska AI:n outputissa ei ole `lead_key`:tä, joten rikastus hävisi. Nyt liidi liitetään takaisin pairingin avulla.
- Bootstrap-cookie-header kaatui, jos Oikotie asetti alle 4 evästettä. Hakusivun `limit`-parametrista puuttui `=`.
- Keski-Pohjanmaa lisätty. Tuntematon maakunta antaa selkeän virheen.
- AI-nodeilla on retry (3 yritystä), eikä virhe kaada koko sivua. Virheellistä AI-vastausta ei merkitä "nähdyksi", joten se yritetään uudelleen seuraavalla ajolla.
- Relevanssiprompti seuraa profiilin `remonttityyppi`ä: lukko/kulunvalvonta käyttää alkuperäistä promptia sanasta sanaan, ja muut tyypit geneeristä tulevan remontin promptia.
- Pinsale ja Google Sheets on poistettu. Tilalle tulivat `POST /api/v1/leads` (`gdpr_legal_basis: legitimate_interest`, isännöitsijätiedot `metadata`:ssa) ja `dedupe/mark`.

## Käsitesti (yksi profiili)

1. CRM: Asetukset → Hakuprofiilit → aktiivinen profiili, **yksi pieni maakunta** (esim. Kainuu).
2. Luo testijob (profiilin "Luo testijob" -painike, `ENABLE_TEST_JOBS=true`).
3. n8n: aja **FlowLeads v2 – Job Poller** käsin (Manual Trigger).
4. Seuraa CRM:ssä jobin `progress`-kenttää ("Kainuu sivu 3/12 …"). Lopuksi tarkista, että job on
   `completed`, uudet liidit (lähde `oikotie`) näkyvät, ja `search_dedupe`-taulussa on `oikotie-*`-rivit.
5. Aja Poller uudelleen samalle profiilille → liidejä ei tule tuplana, ja ajo on selvästi nopeampi (dedupe ohittaa).

## Huomioita

- n8n Cloudissa on suoritusaikaraja ja samanaikaisten suoritusten raja. Uusimaa voi olla satoja sivuja,
  ja Pollerin suoritus odottaa koko ajon ajan. Tarkista oman planisi raja ennen koko Suomen ajoa ja aloita yhdellä maakunnalla.
  Halutessasi voit rajata sivuja asettamalla jobiin `max_pages` (0 = kaikki sivut).
- Oikotie-skreippaus on hauras. Jos "NNN kpl" -tekstiä ei löydy, Ohjaaja ajaa sivun 1 ja Worker hakee
  sen API:sta normaalisti. Seuraa jobien `progress`- ja `error`-kenttiä.

## CRM-testit

```bash
pytest tests/test_n8n_search.py tests/test_search_job_scheduler.py -q
```
