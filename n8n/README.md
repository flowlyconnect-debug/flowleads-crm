# FlowLeads n8n – kohdekatalogi (Crawler, Analyzer, Enricher)

Arkkitehtuuri: [docs/ARKKITEHTUURI_KOHDEKATALOGI.md](../docs/ARKKITEHTUURI_KOHDEKATALOGI.md)

```
Crawler  (1 h)    Oikotie → CRM:n yhteinen kohdekatalogi
Analyzer (10 min) yksi AI-kutsu per kohde → tulevat remontit tyypeittäin → CRM tekee osumista liidit
Enricher (30 min) Finder/Asiakastieto → isännöitsijän yhteystiedot osuneille kohteille → CRM päivittää liidit
```

Asiakaskohtaisia ajoja ei ole. Hakuprofiilit ovat CRM:ssä suodattimia, joita n8n ei tarvitse tietää.

## Tiedostot

| Tiedosto | Rooli |
|---|---|
| `workflows/flowleads-crawler.json` | **FlowLeads – Kohdekeruu (Crawler)** |
| `workflows/flowleads-analyzer.json` | **FlowLeads – Kohdeanalyysi (Analyzer)** (OpenAI) |
| `workflows/flowleads-enricher.json` | **FlowLeads – Yhteystietohaku (Enricher)** (OpenAI) |
| `tools/build_catalogue_workflows.py` | Generoi kolme workflowta. **Älä muokkaa JSONeja käsin**, vaan muokkaa tätä ja aja uudelleen. |
| `tools/build_workflows.py` | Kirjasto: lataa tuotannon Lead Worker -exportin (`source/`) ja korjaa sen bugit. Oikotie- ja Finder/Asiakastieto-nodet otetaan sieltä. |
| `source/` | Alkuperäiset tuotantoexportit (Lead Worker, Liidi Ohjaaja). |

```bash
python n8n/tools/build_catalogue_workflows.py
```

## Asennus n8n:ään

Uudet nimet eivät törmää vanhoihin workfloweihin. Vanhoihin ei kosketa.

1. Deployaa CRM ensin (Render ajaa `flask db upgrade`).
2. Importtaa kolme JSONia: Workflows → Import from file.
3. Avaa jokaisesta **`SET – Config`** ja aseta:
   - `crm_base_url` = esim. `https://flowleads-crm.onrender.com`
   - `n8n_secret` = sama arvo kuin Renderin `N8N_MASTER_SECRET`
4. Analyzer ja Enricher: tarkista, että OpenAI-nodet käyttävät "OpenAi account" -credentialia.
5. Testaa käsin (alla) ja aktivoi sitten kaikki kolme.
6. Kun liidejä tulee, poista käytöstä vanhat (Liidi Ohjaaja, Lead Worker, FlowLeads – Job Poller/Dispatcher/Lead Worker…).

## Käsitesti

1. CRM: luo hakuprofiili (esim. Lukkoremontti / kulunvalvonta, Uusimaa).
2. Aja **Crawler** käsin. Executionissa `HTTP – CRM Ingest` vastaa `created: 24` (ensimmäisellä kerralla).
   Jos vastauksessa on virhe, katso CRM:n `listing_crawl_state.last_error`.
3. Aja **Analyzer** käsin. `HTTP – CRM Save Analysis` vastaa `status: done`, ja osumista näkyy `matches`/`leads_created`.
4. Profiilisivulla (Asetukset → Hakuprofiili) näkyvät osumat ja luodut liidit. Katalogin koko näkyy sivupaneelin alareunassa.
5. Aja **Enricher** käsin, jos jollakin osumalla ei ollut yhteystietoja.

Alkutäyttö kestää useita päiviä (ks. arkkitehtuuridokumentin riskit). Liidejä tulee jo sitä ennen,
koska uusimmat kohteet kerätään ja analysoidaan ensin.

## Testaus ilman n8n:ää

`tests/test_n8n_catalogue_e2e.py` ajaa generoidut workflowt mini-n8n-simulaattorilla (`tests/n8n_sim/`)
oikeaa paikallista CRM:ää vasten mock-Oikotiella. Aja se aina generoinnin jälkeen:

```bash
pytest tests/test_n8n_catalogue_e2e.py tests/test_listings_catalogue.py -q
```
