# Oikotie-kohdekatalogi – arkkitehtuuri

> Tämä on FlowLeadsin liidihankinnan nykyinen malli (marraskuu 2026). Se korvaa
> aiemman asiakaskohtaisen SearchJob-putken (Job Poller → Dispatcher → Lead Worker).

## Idea yhdellä lauseella

n8n kerää **kaikki** Oikotien myytävät kohteet jatkuvasti yhteen, kaikille asiakkaille
yhteiseen tietokantaan ja analysoi jokaisen kohteen **kerran** AI:lla. Asiakkaan
**hakuprofiili on pelkkä suodatin** (maakunnat + remonttityyppi + AI-varmuus), ja
CRM tekee profiiliin osuvista kohteista liidit.

Seuraukset:

- Oikotie-haku ja AI-kulu eivät kasva asiakasmäärän mukana, vaan ne tehdään kerran per kohde.
- Uusi asiakas tai profiili saa osumat heti jo kerätystä historiasta (viim. 30 pv).
- Asiakaskohtaisia ajoja, jobeja, jobitokeneita tai tuntien mittaisia n8n-suorituksia ei ole.

## Kokonaiskuva

```
            ┌──────────── n8n ────────────┐                 ┌──────────────── CRM (Flask) ────────────────┐
 Oikotie ──►│ Crawler   (1 h välein)      │──ingest────────►│ listings            (yhteinen katalogi)     │
            │ Analyzer  (10 min välein)   │◄─claim/save────►│ listing_renovations (AI: tulevat remontit)  │
 Finder/  ─►│ Enricher  (30 min välein)   │◄─claim/save────►│ listing_matches     (profiili × kohde)      │
 Asiakast.  └─────────────────────────────┘                 │ listing_crawl_state (keruun kursorit)       │
                                                            │ search_profiles     (suodattimet)           │
                                                            │        │ scheduler 10 min: rematch + liidit │
                                                            │        ▼                                    │
                                                            │ leads (asiakkaan oma pipeline)              │
                                                            └─────────────────────────────────────────────┘
```

Kaikki n8n:n kutsut CRM:ään menevät reitteihin `/api/v1/n8n/listings/*` ja käyttävät
headeria `X-N8N-Secret` (= Renderin `N8N_MASTER_SECRET`). n8n ei koskaan käytä
asiakkaan API-avainta. CRM luo liidit itse.

## Vaiheet

### 1. Keruu – Crawler (`n8n/workflows/flowleads-crawler.json`)

1. `GET /api/v1/n8n/listings/crawl-plan` palauttaa tehtävät:
   - **incremental** jokaiselle maakunnalle: aloitetaan uusimmista (`sortBy=published_sort_desc`, offset 0).
   - **full**-viipale enintään `LISTING_FULL_REGIONS_PER_RUN` (2) maakunnalle: jatketaan
     `listing_crawl_state.full_offset`-kursorista, enintään `LISTING_FULL_PAGES_PER_RUN` (30) sivua per ajo.
     Full pass on sekä alkutäyttö (backfill) että viikoittainen päivitys (`LISTING_FULL_REFRESH_DAYS` = 7).
2. Jokaiselle tehtävälle: Oikotien hakusivu (sijainnit + evästeet) → bootstrap (ota-token) → API-sivu → `POST /ingest`.
3. CRM päättää jatkon (`continue`):
   - incremental jatkuu niin kauan kuin sivulta tuli uusia kohteita (max `LISTING_INCREMENTAL_MAX_PAGES` = 10),
   - full jatkuu ajon viipaleen loppuun,
   - alle 24 kohteen sivu = viimeinen sivu.
4. Ingest tekee upsertin tunnisteella (`source`, `external_id` = Oikotie cardId). Jos kuvausteksti muuttui, kohde menee uudelleen analysoitavaksi.
5. **Poistuneet kohteet**: kun full pass valmistuu, kohde merkitään epäaktiiviseksi vain, jos se puuttui
   **kahdesta peräkkäisestä** full passista (`prev_full_started_at`). Yksi pass voi ohittaa kohteen, koska
   offsetit siirtyvät kesken passin. Jos kohde ilmestyy uudelleen, se aktivoituu.

### 1b. Tuonti muusta lähteestä (`app/listings/import_services.py`)

> **Tilanne 11.11.2026:** Oikotien bottisuojaus (DataDome) estää n8n Cloudin pyynnöt, myös vanhan Lead Workerin
> (403 + captcha jo hakusivulla). Crawler pysähtyy ensimmäiseen estoon eikä kierrä suojausta. Kohteita saadaan
> katalogiin toistaiseksi vain tuonnilla.

Katalogi ei ole sidottu Oikotiehen: kohteen voi tuoda mistä tahansa lähteestä, johon on käyttöoikeus
(lisensoitu datasyöte, kumppanin vienti, oma selvitys). Tuodulla kohteella `listings.source` on jokin muu kuin
`oikotie` (oletus `import`), ja siitä syntyvän liidin `source` on `import`.

- **CSV** (superadmin): `/admin/catalogue` → *Tuo kohteita tiedostosta*. Mallitiedosto:
  `/admin/catalogue/import-template.csv`. Erotin `;`, `,` tai sarkain; UTF-8 tai Windows-1252.
- **API** (automaattinen syöte): `POST /api/v1/n8n/listings/import` `{source, listings:[…]}`, sama rivimuoto.
- **Rivi, jolla on `remonttityyppi`** (yksi tai useampi, erotin `;`): tallennetaan analysoituna (varmuus 100, ellei
  `varmuus` anneta), täsmäytetään heti ja liidit syntyvät samassa kutsussa. Ei AI:ta eikä n8n:ää.
- **Rivi, jolla on vain `teksti`** (≥ 30 merkkiä): menee Analyzerin jonoon. `/analysis/claim` palauttaa sille
  `fetch_detail: false`, jolloin Analyzer analysoi tuodun tekstin eikä hae sivua.
- Pakolliset: `maakunta` sekä `taloyhtio` tai `osoite`. Tunniste (`tunniste`) on vapaaehtoinen: ilman sitä se
  lasketaan taloyhtiöstä, osoitteesta ja kaupungista, joten sama rivi päivittää eikä tuplaa.
- Virheelliset rivit raportoidaan (`errors`), muut tuodaan. Enintään 2000 riviä per tuonti.
- Tuodut kohteet eivät vanhene Oikotien "poistunut"-logiikalla; profiilin historiaikkuna (30 pv) koskee niitäkin.

### 2. Analyysi – Analyzer (`flowleads-analyzer.json`)

1. `POST /analysis/claim {limit: 20}` lukitsee kohteet (`analysis_status=processing`). Uusimmat tulevat ensin,
   joten tuoreet kohteet ohittavat alkutäytön. Vastauksessa tulee myös `remonttityypit` (CRM:n lista on ainoa totuus).
2. Per kohde: detaljisivu → teksti → **yksi AI-kutsu** (gpt-4.1-mini), joka palauttaa taloyhtiön,
   isännöitsijän yhteystiedot ja **kaikki tulevat remontit luokiteltuna CRM:n remonttityyppeihin**
   (confidence 0–100, ajankohta, lainaus).
3. `POST /<id>/analysis` tallentaa tuloksen. CRM hylkää tuntemattomat tyypit ja rajaa confidencen.
   Heti tallennuksen jälkeen `match_listing` etsii osuvat profiilit.
4. Yrityskerrat lasketaan claimissa. `LISTING_MAX_ATTEMPTS` (3) jälkeen status on `failed`. Yli
   `LISTING_CLAIM_TIMEOUT_MINUTES` (30) jumissa ollut claim jaetaan uudelleen.
5. Analyzer hakee lisää eriä, kunnes jono tyhjenee tai ajoa on kulunut 7 min.

### 3. Täsmäytys ja liidit – CRM (`app/listings/services.py`)

- **Osuma** (`listing_matches`): profiili on aktiivinen, kohteen maakunta kuuluu profiilin alueisiin
  ("Koko Suomi" = kaikki), ja kohteella on jokin profiilin remonttityypeistä vähintään `min_confidence`-varmuudella.
- **Lisäsuodattimet** (kaikki valinnaisia, tyhjä = ei rajaa): `remonttityypit` (useampi tyyppi; `remonttityyppi` on
  listan ensimmäinen), `cities` (kaupungit, kirjainkoolla ei väliä), `timing_within_years` (remontti enintään N vuoden
  päässä, vertaa `listing_renovations.timing_year`), `build_year_min`/`build_year_max` ja `building_types`
  (`BUILDING_TYPES`). **Tuntematon arvo ei koskaan pudota kohdetta**: jos kohteen rakennusvuotta, talotyyppiä tai
  remontin vuotta ei tiedetä, kohde tulee mukaan. Logiikka: `_listing_passes` + `_best_renovation`.
- Asetussivun **esikatselu** (`POST /settings/search-profiles/preview`) laskee samalla funktiolla (`wanted_listings`),
  montako osumaa tallentamaton suodatin antaisi nyt.
- `match_listing(listing)` ajetaan, kun uusi kohde on analysoitu. `rematch_profile(profile)` ajetaan, kun
  profiili luodaan, **aktivoidaan** tai sen suodatin muuttuu (`needs_rematch=True`), ja se ottaa kohteet viimeiseltä
  `LISTING_MATCH_HISTORY_DAYS` (30) päivältä. Ei-aktiivisen profiilin lippu nollataan, joten aktivointi pitää tehdä
  `activate_profile()`-funktiolla (asettaa lipun takaisin).
- `materialize_pending_matches()` tekee odottavista osumista liidit uusimmat ensin, enintään profiilin
  `daily_lead_limit` (oletus 50, 0 = ei rajaa) per UTC-päivä. Loput odottavat seuraavaan päivään.
- Liidi luodaan `upsert_lead`-funktiolla (sama kuin API) ja liidireitityksellä:
  - `source=oikotie`, `source_ref=oikotie-<cardId>` (uniikki per organisaatio)
  - isännöitsijä = company
  - `gdpr_legal_basis=legitimate_interest`
  - kohteen tiedot `metadata`:ssa (`ai_contact_info`), jotka liidin sivu näyttää (`app/leads/property_info.py`)
- **Liidi = taloyhtiön remonttikohde, ei isännöitsijä.** Katalogi tunnistaa liidin vain Oikotie-viitteellä
  (`upsert_lead(..., match_by_email=False)`), koska sama isännöitsijän sähköposti on kymmenillä taloyhtiöillä.
- **Sama taloyhtiö, toinen asunto myynnissä**: jos profiililla on jo liidi samasta taloyhtiöstä (nimi + kaupunki),
  uusi osuma liitetään siihen (`status=merged`, liidille aktiviteettimerkintä). Uutta liidiä ei synny eikä päivärajaa
  kulu. Myöhemmin löytyvät yhteystiedot päivitetään yhteiseen liidiin.
- Osuman status pysyy `lead_created`, vaikka liidi poistettaisiin, joten poistettua liidiä ei luoda uudelleen.
- Ajastin (`app/scheduler.py`, `listing_matching`, 10 min) ajaa `rematch_profiles` + `materialize_pending_matches`.

### 4. Yhteystiedot – Enricher (`flowleads-enricher.json`)

- Kohde menee yhteystietojonoon (`contact_status=pending`) vain, jos **jokin profiili osui siihen** eikä
  kohteella ole isännöitsijän puhelinta eikä sähköpostia. Kalliita Finder/Asiakastieto-hakuja ei siis tehdä turhaan.
- `POST /contacts/claim` → tuotannon Finder/Asiakastieto/AI-ketju (sama kuin vanhassa Lead Workerissa) → `POST /<id>/contacts`.
- CRM täydentää kohteen tyhjät kentät ja päivittää kaikki kohteesta tehdyt liidit (puhelin, sähköposti, yritys, verkkosivu).

### 5. Asiakkaan käyttöönotto (`app/admin/`)

- **Luo asiakas** (`onboarding_services.create_customer`): organisaatio + admin + aktiivinen profiili. Ei API-avainta
  eikä hakujobia. Admin saa väliaikaisen salasanan, joka on pakko vaihtaa ensikirjautumisessa
  (`users.must_change_password`, `/auth/change-password`).
- **Kutsu asiakas** (`invite_services`): asiakas asettaa itse salasanan ja valitsee alueet. Profiili on heti
  aktiivinen, eli erillistä "aktivoi lähde" -vaihetta ei tarvita. `activate_source` on jäljellä vain pois kytketyille profiileille.
- Kummassakin tapauksessa liidit katalogin viime viikoilta tulevat seuraavalla `listing_matching`-ajolla (≤ 10 min).
- **Valinnaiset toiminnot** (`organizations.enabled_features`, `app/core/features.py`): sekvenssit, automaatiot ja
  AI-rikastus. Uudella organisaatiolla kaikki päällä; kutsuttu asiakas valitsee sallituista. Superadmin muuttaa
  asiakaslistasta. Superadminia ei rajata.
- Laskutus tehdään toistaiseksi käsin, CRM:ssä ei ole tilaustenhallintaa.

### 6. Valvonta ja säilytys

- **Kohdekatalogin tila** (`/admin/catalogue`, `catalogue_health()`): maakuntakohtainen keruun tila ja virheet, jonot
  sekä lista huomiota vaativista asioista.
- **Hälytys** (ajastin `catalogue_health_check`, 1 h): jos keruu tai analyysi on ollut pysähdyksissä yli
  `LISTING_STALE_HOURS` tai jossain maakunnassa on keruuvirhe, superadminit saavat sähköpostin, enintään kerran
  `LISTING_ALERT_INTERVAL_HOURS` aikana (kirjataan audit-lokiin `catalogue_alert_sent`).
- **Säilytys** (ajastin `listing_retention`, päivittäin): Oikotieltä poistuneet kohteet remontteineen ja osumineen
  poistetaan `LISTING_RETENTION_DAYS` päivän kuluttua poistumisesta. Asiakkaan liidit jäävät asiakkaan CRM:ään.

## Tietomalli (`app/listings/models.py`)

| Taulu | Sisältö |
|---|---|
| `listings` | Kohde (uniikki `source`+`external_id`): osoite, kaupunki, maakunta, hinta, kuvaus, `build_year`, `building_type`, `content_hash`, `first/last_seen_at`, `is_active`. Lisäksi analyysin tila ja isännöitsijätiedot sekä yhteystietojonon tila. |
| `listing_renovations` | Kohteen tulevat remontit: `remonttityyppi` (yksi `REMONTTITYYPIT`-arvoista), `confidence`, `timing`, `timing_year`, `evidence`. |
| `listing_matches` | Profiili × kohde (uniikki). `status` = pending/lead_created/merged, `lead_id` (SET NULL), `lead_created_at`. |
| `listing_crawl_state` | Per maakunta: `full_offset`, `full_started_at`, `prev_full_started_at`, `full_completed_at`, `last_incremental_at`, `last_error`. |
| `search_profiles` (+) | `min_confidence` (60), `daily_lead_limit` (50), `needs_rematch` sekä lisäsuodattimet `remonttityypit`, `cities`, `timing_within_years`, `build_year_min/max`, `building_types`. |

Rakennusvuosi ja talotyyppi tulevat Oikotien kortista, jos kortissa on ne (`buildingData`), ja muuten AI-analyysistä
(`rakennusvuosi`, `talotyyppi`). Kortin arvo voittaa. **Kortin kenttänimet on tarkistettava ensimmäisestä oikeasta
Crawler-ajosta**, koska Oikotie ei vastaa kehityskoneelle; AI-polku toimii kortista riippumatta.

Migraatiot: `e3f4a5b6c7d8_add_listing_catalogue.py` ja `f4a5b6c7d8e9_add_profile_filters_and_onboarding.py`.

## API (`app/listings/routes.py`, kaikki vaativat `X-N8N-Secret`)

| Metodi ja polku | Kuka | Mitä |
|---|---|---|
| `GET /api/v1/n8n/listings/crawl-plan` | Crawler | `{tasks:[{maakunta, slug, mode, offset, max_pages}]}` |
| `POST /api/v1/n8n/listings/ingest` | Crawler | `{maakunta, mode, offset, page_in_run, listings:[{external_id,url,kaupunki,osoite,hinta,alue,description,published_at,build_year?,building_type?}], error?}` → `{created, updated, removed, continue, next_offset}` |
| `POST /api/v1/n8n/listings/import` | muu syöte | `{source?, listings:[{taloyhtio, osoite, kaupunki, maakunta, remonttityyppi? \| renovations?, timing?, description?, isannoitsija?, isannoitsija_puh?, isannoitsija_email?, build_year?, building_type?, url?, external_id?}]}` → `{created, updated, analysed, queued, matches, leads_created, errors}` |
| `POST /api/v1/n8n/listings/analysis/claim` | Analyzer | `{limit}` → `{listings:[…, source, fetch_detail], remonttityypit:[…], talotyypit:[…]}` |
| `POST /api/v1/n8n/listings/<id>/analysis` | Analyzer | `{status:"done", taloyhtio, isannoitsija, isannoitsija_puh, isannoitsija_email, rakennusvuosi, talotyyppi, tulevat_remontit, renovations:[{remonttityyppi, confidence, timing, evidence}]}` tai `{status:"failed", error}` |
| `POST /api/v1/n8n/listings/contacts/claim` | Enricher | `{limit}` → `{listings:[…]}` |
| `POST /api/v1/n8n/listings/<id>/contacts` | Enricher | `{status:"done", isannoitsija, phone, email, website, source}`, `{status:"not_found"}` tai `{status:"failed"}` |

## Asetukset (env, oletukset `app/config.py`)

| Muuttuja | Oletus | Merkitys |
|---|---|---|
| `LISTING_CRAWL_REGIONS` | `all` | `all` = koko Suomi; `active` = vain aktiivisten profiilien maakunnat |
| `LISTING_INCREMENTAL_MAX_PAGES` | 10 | incremental-sivujen yläraja per maakunta per ajo |
| `LISTING_FULL_PAGES_PER_RUN` | 30 | full passin sivuja per maakunta per ajo |
| `LISTING_FULL_REGIONS_PER_RUN` | 2 | montako maakuntaa full passissa per ajo |
| `LISTING_FULL_REFRESH_DAYS` | 7 | kuinka usein full pass ajetaan uudelleen |
| `LISTING_MATCH_HISTORY_DAYS` | 30 | uuden profiilin historiaikkuna |
| `LISTING_MAX_ATTEMPTS` | 3 | analyysin / yhteystietohaun yrityskerrat |
| `LISTING_CLAIM_TIMEOUT_MINUTES` | 30 | jumissa olevan claimin vapautus |
| `LISTING_RETENTION_DAYS` | 90 | poistuneen kohteen säilytysaika (0 = ei poisteta) |
| `LISTING_STALE_HOURS` | 6 | näin pitkä hiljaisuus keruussa/analyysissä = hälytys |
| `LISTING_ALERT_INTERVAL_HOURS` | 24 | hälytyssähköpostien vähimmäisväli |
| `SEARCH_JOBS_ENABLED` | `false` | vanhan SearchJob-putken ajastimet (pois päältä) |

## Vanha SearchJob-putki

`app/search/` -jobikoodi (`/api/v1/n8n/jobs`, jobitokenit, heartbeatit) on yhä koodissa ja testattu,
mutta ajastin ei luo jobeja (`SEARCH_JOBS_ENABLED=false`), eikä sille ole enää n8n-workflowta.
Hakuprofiilin vanhat kentät `schedule_description` ja `crm_api_key` ovat käyttämättömiä, eikä niitä näytetä lomakkeella.
Käyttöönotto ei enää luo hakujobeja eikä n8n-API-avaimia.
Kun katalogi on todettu tuotannossa toimivaksi, jobikoodin voi poistaa.

## Testit

- `tests/test_listings_catalogue.py`: keruu, poistot, jonot, täsmäytys, päiväraja, historiaikkuna, yhteystiedot ja API.
- `tests/test_catalogue_filters_and_onboarding.py`: lisäsuodattimet, esikatselu, liidi per taloyhtiö, säilytys,
  hälytys, salasanan vaihtopakko ja valinnaiset toiminnot.
- `tests/test_listing_import.py`: CSV- ja API-tuonti, liidit tuoduista kohteista, virherivit.
- `tests/test_n8n_catalogue_e2e.py`: ajaa kolme generoitua n8n-workflowta node kerrallaan
  (`tests/n8n_sim/executor.js`, mini-n8n) oikeaa paikallista CRM-palvelinta vasten. Oikotie, Finder,
  Asiakastieto ja OpenAI on mockattu (`tests/n8n_sim/mocks.js`). Testi ohitetaan, jos Node.js puuttuu.
  Simulaattori ei ole n8n: se löytää kytkentä- ja datavirheet, mutta oikea n8n-ajo pitää silti testata.

## Tunnetut riskit

- **Oikotie-skreippaus on hauras.** Jos markup tai API muuttuu, Crawler kirjaa virheen `listing_crawl_state.last_error`:iin.
- **Alkutäyttö**: koko Suomi on kymmeniä tuhansia kohteita. Keruu kestää noin 1–2 vrk (30 sivua × 2 maakuntaa per tunti),
  ja analyysi noin 300–400 kohdetta tunnissa, eli noin viikon. AI-kulu on suurin alkutäytössä, sen jälkeen pieni.
- **Päiväraja**: alkutäytön aikana osumia voi tulla paljon. `daily_lead_limit` estää pipelinen tulvimisen.
- **GDPR**: isännöitsijätiedot ovat B2B-tietoja, ja perusteena on oikeutettu etu. Poistuneet kohteet poistetaan
  `LISTING_RETENTION_DAYS` päivän jälkeen.
- **Valinnaisten toimintojen rajaus** estää sivut, automaatioiden laukaisun ja AI-rikastuksen, mutta ei piilota
  valikkolinkkejä eikä pysäytä jo käynnissä olevia sekvenssejä, jos toiminto otetaan asiakkaalta pois jälkikäteen.
