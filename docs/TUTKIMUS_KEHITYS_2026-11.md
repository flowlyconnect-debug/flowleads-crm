# FlowLeads: miten tuotteesta saa paremman (tutkimus 17.11.2026)

Jatkoa dokumentille `docs/TUTKIMUS_KEHITYS_JA_METROC.md` (12.11.2026). Lähteet: koodin läpikäynti (HEAD
`3bed334`) ja julkiset verkkolähteet (lista lopussa). Osa viranomaissivuista (ryhti.syke.fi, avoindata.prh.fi,
sttinfo.fi, isannointiliitto.fi, varke.fi) ei auennut tästä ympäristöstä, joten niiden tiedot ovat
hakukoneotteista. **[tarkista]** = ei varmistettu ensisijaisesta lähteestä.

---

## 1. Tiivistelmä

1. **Tuote on edellä, data jäljessä.** 12.11. tiekartan tuote- ja CRM-kohdat on pääosin tehty (kartta,
   AI-haku, pisteytys, taloyhtiökortti, HubSpot/Pipedrive, kuukausiraportti, hinnasto). **Yhtään uutta
   datalähdettä ei ole rakennettu.** Oikotie estää n8n Cloudin, joten ainoa elävä kohdelähde on CSV/API-tuonti.
   Uusi asiakas näkee todennäköisesti tyhjän Kohteet-sivun.
2. **Uusi uhka: Alma Media.** Asuntopuntarin "remonttinäkymä" (6/2026) yhdistää ~76 000 taloyhtiön
   remonttihistorian ja ennusteen Etuovi-datasta. Se on sama signaali kuin FlowLeadsilla, nyt kiinteistönvälittäjille
   ja pankeille. Urakoitsijan myyntityökalua Almalla ei ole – se on FlowLeadsin rako.
3. **Seuraavat 4 viikkoa kannattaa käyttää dataan:** Ryhti (avoin, CC BY 4.0) + PRH YTJ (avoin) taloyhtiörekisteriksi
   ja ikäperusteisiksi kohteiksi, sekä viisi sähköpostia datakumppaneille (luku 3.3).
4. **Ajankohtainen myyntikoukku:** Varken väliaikainen energia-avustus taloyhtiöille 2026–2027 (110 M€, haku
   syksystä 2026). Ilmanvaihto-, yläpohja-, ikkuna- ja lämmitysurakoitsijoille syy soittaa *nyt*.
5. **Myy isännöintitoimistoille, ei vain taloyhtiöille.** Mediaani-isännöitsijällä on ~21 taloyhtiötä; yksi
   tapaaminen = monta kohdetta. Pohja on jo olemassa (`/managers`, `PropertyManagerOffice`).
6. **Skaalautuvuus ja tietoturva:** `listing_matching`-työ ja Kohteet-sivu eivät kestä 100k kohdetta / 50 asiakasta
   (N+1, Python-järjestys, raskas laskuri jokaisella sivulla). Automaation webhook-toiminnosta puuttuu SSRF-suoja.
   Tuotannon varmuuskopiota ei ole todennettu.

---

## 2. Missä ollaan (koodin perusteella)

| 12.11. tiekartan kohta | Tila |
|---|---|
| Ryhti, HTJ, Hilma, kuntien kuulutukset | **Ei aloitettu** |
| Ikäperusteinen ennuste | Osittain: `housing.age_based_hints()` näkyy vain taloyhtiökortilla, ei luo kohteita eikä vaikuta pisteisiin |
| Lähdekohtainen katalogin terveys | Ei: `catalogue_health()` katsoo vain Oikotien `CrawlState`a |
| Taloyhtiö keskiöön, signaalien aikajana, pisteytys 0–100 | Tehty (`app/listings/housing.py`) |
| Remonttityyppien tarkennus | Osittain: 10 tyyppiä; ikkunat/ovet, parvekkeet, piha/salaojat, hissit, aurinko/maalämpö/latauspisteet puuttuvat |
| Hallituksen pj, huoltoyhtiö, isännöintitoimistot | Malli tehty (`contacts.py`), täyttyy vain tuonnista |
| Kartta, AI-haku, CRM-viennit, kuukausiraportti | Tehty (kartta kunnan keskipisteeseen, geokoodaus pois; viennit testaamatta oikeilla tileillä) |
| Kohteiden Excel-vienti, Easoft/Admicom | Ei |
| Hinnasto | Tehty (149 €/kk, kokeilu poistettu 16.11.) |
| Stripe-laskutus | Ei |
| Tuotantovalmius | Koodi valmis; S3-varmuuskopio, Redis, Mailgun-domain ja Sentry DSN odottavat konfigurointia |

---

## 3. Data (P1 – liiketoiminnan edellytys)

### 3.1 Rakenna heti (avoin, laillinen, edullinen)

**Ryhti (SYKE), CC BY 4.0, maksuton.**
- OGC API Features: `https://paikkatiedot.ymparisto.fi/geoserver/ryhti_building/ogc/features/v1` (valmiit
  rakennukset, kokoelma `open_building`) ja `.../ryhti_permit/ogc/features/v1` (lupavaiheen rakennukset).
- CQL2-suodatus `modified_timestamp_utc > '...'` mahdollistaa päivittäisen muutossynkan; kunta- ja bbox-suodatus.
- Kentät mm. pysyvä rakennustunnus, käyttötarkoitus, valmistumispäivä, julkisivu, rakentamistapa, lämmitys ja
  huoneistomäärä. **Korjauspäiviä avoimessa datassa ei ole** (tarkistettu rajapinnasta 17.11.2026).
  Huoneistomäärän, kerrosalan ja lupatyypin kenttänimet **[tarkista]** datasanakirjasta `Ryhti_rakennustiedot_EN.pdf`.
- Osa kutsuista ei palauta sijaintia → koordinaatit osoitekokoelmasta tai MML:n maastotietokannasta.
- Kattavuus: kuntien pitää myöntää luvat tietomallin mukaisena 1.1.2026 alkaen, siirtymäaika vuoden 2028 loppuun.
  Lupadata on siis osittaista mutta kasvaa.

**PRH YTJ v3 (avoindata.prh.fi), CC BY 4.0.** Kaikki asunto-osakeyhtiöt: nimi, Y-tunnus, osoite, rekisteröintipäivä,
tila. Lisäksi `opendata-registerednotices-api/v3` (rekisteröidyt ilmoitukset, esim. yhtiöjärjestysmuutokset, joita
tehdään usein ennen putkiremonttia **[tarkista ilmoitustyyppikoodit]**). Hallitus ei ole avointa dataa (vain Virre,
maksullinen sopimus).

**Mitä niistä tehdään:**
1. **Kansallinen taloyhtiörekisteri** (PRH + Ryhti, yhdistetty `company_key`-avaimeen / Y-tunnukseen). Jokainen
   taloyhtiö on olemassa ennen ilmoitusta – kohteet eivät ole enää riippuvaisia yhdestä portaalista.
2. **Ennustekohteet omana signaalityyppinä** (`signal_kind = "ennuste"`, matala varmuus, oma merkki UI:ssa):
   rakennusvuosi + tyyppi (+ julkisivu, lämmitys) → "putkiremontti todennäköisesti edessä". Nykyinen
   `age_based_hints()` on hyvä alku, mutta sen pitää luoda kohteita ja vaikuttaa pisteisiin.
3. **Lupasignaalit** `ryhti_permit`-kokoelmasta (korjaus- ja muutostyöluvat) vahvimpana tuoreena signaalina.
4. Tuonti olemassa olevan polun kautta: uusi `source="ryhti"` / `"prh"` → `app/listings/import_services.py`.
   Ajastettu tuonti scheduleriin (ei n8n:ää tarvita, avoimet rajapinnat toimivat Renderistä).
5. **Lähdekohtainen terveys** `/admin/catalogue`-näkymään (viimeisin onnistunut ajo, rivit/päivä per lähde).

Arvio: 1–2 viikkoa. Ratkaisee tyhjän Kohteet-sivun ongelman.

**Tila 17.11.2026:** keruu, yhdistäminen, signaalit ja "ennuste"-merkityt kohteet on rakennettu (`app/registry/`,
superadminin sivu `/admin/registry`), mutta mitään ei vielä näytetä asiakkaille (julkaisu ja asiakaskohtainen toiminto
ovat pois päältä). Oikealla datalla todettu: Ryhdin avoimessa datassa ei ole
korjauspäiviä, ja taloyhtiöön yhdistyy noin 19 % rakennuksista (60 % yhtiöistä on rekisteröity isännöintitoimiston
osoitteeseen). Ks. `docs/ARKKITEHTUURI_KOHDEKATALOGI.md`
("Julkiset rekisterit").

### 3.2 Lisämoduulit (myöhemmin)

- **Hilma (AVP-Read API, ilmainen avain Azure APIM -portaalista)**, CPV-suodatus (45453000, 45262700, 45421130,
  45331000, 45261000). Kattaa vain julkiset vuokrataloyhtiöt (esim. Heka, Espoon Asunnot), ei tavallisia
  taloyhtiöitä. Hyvä isommille asiakkaille.
- **MML maastotietokanta** (`rakennus`-kokoelma, ilmainen API-avain): geometria ja koordinaatit kartalle.
- **Energiatodistukset** (Varke, Palveluväylä, vaatii tietoluvan): E–G-luokka vanhassa talossa = energiaremontin vihje.
- **Tilastokeskus Paavo**: vain alueiden priorisointiin. Huom. taulurakenne muuttui 8.6.2026 ja 3.11.2026.

### 3.3 Kysy kirjallisesti tällä viikolla (viisi sähköpostia)

| Kenelle | Mitä | Miksi |
|---|---|---|
| Oikotie (Schibsted/Adevinta, *ei* Alma) B2B | Ilmoitusdatan lisenssi | Tilastokeskus lisensoi Oikotien dataa vuosisopimuksella (~70 muuttujaa, kuukausittain rajapinnasta) – lisenssi on siis mahdollinen. Tämä on laillinen korvaaja crawlerille. |
| Alma Media (Etuovi, Asuntopuntari) | Datalisenssi tai kumppanuus | Almalla on jo remonttiennuste 76 000 taloyhtiölle; kumppani tai kilpailija – parempi tietää |
| Varke | Tietopyyntö (julkisuuslaki): energia-, hissi- ja esteettömyysavustusten saajat (taloyhtiö, kunta, toimenpide) | Avustuksen saanut taloyhtiö on päättänyt remontoida *nyt* |
| ryhti@syke.fi | Mitkä kunnat toimittavat lupia suoraan, lupatyyppikentät | Tuonnin mitoitus |
| Evolta (Lupapiste) | Julkipanon RSS-syötteet (raportti 1/2026 mainitsee) | Lupasignaali ennen kuin Ryhti kattaa kaiken; älä raaputa julkipano-HTML:ää |
| MML / Asiakastieto | HTJ:n kunnossapitotarveselvitysten luovutus B2B-käyttöön | Paras signaali Suomessa, mutta luovutus on käyttötarkoitussidonnaista; todennäköisesti ei sallittu, varmista |

### 3.4 Pitkä aikaväli

- **Suostumuspohjainen PTS-jako** isännöintijärjestelmistä (Visma Tampuuri – API ja PTS-moduuli, Fivaldi, Hausvise):
  "pyydä tarjous" -integraatio, jossa taloyhtiö itse jakaa tarpeensa. Liiketoiminnan kehitystä, ei valmis rajapinta.
- **"Lataa PTS ja saa tarjouksia"** -lomake taloyhtiöille/isännöitsijöille (julkinen, suostumuksella): FlowLeads
  saa signaalin, asiakkaat saavat kohteen. Pohja on jo olemassa (`app/forms/`).

---

## 4. Signaalin laatu ja tekoäly (P2)

Nyt: Analyzer = n8n + gpt-4.1-mini, 12 000 merkkiä, ei JSON-tilaa, ei lämpötilaa; `confidence` on mallin itse
ilmoittama luku; mallia/promptin versiota ei tallenneta; tarkkuutta ei mitata mitenkään.

1. **Piilotuksen syy** Kohteet-sivulle (väärä tyyppi / jo tehty / ei kiinnosta / muu). Halpa, ja antaa sekä
   tarkkuusmittarin että paremman opetussignaalin `fit_services.learn_preferences`ille.
2. **Todisteen tarkistus:** `evidence`-lainauksen pitää löytyä lähdetekstistä, muuten varmuutta lasketaan.
3. **Kultajoukko:** ~100 käsin merkittyä ilmoitusta, ajetaan CI:ssä mockattuna ja oikeasti promptin muuttuessa.
4. Tallenna `model` ja `prompt_version` jokaiselle analyysille; JSON-tila / structured output.
5. **Analyysi CRM:ään n8n:n sijaan** kun lähteet ovat avoimia rajapintoja: vähemmän liikkuvia osia, testattava
   Pythonissa. n8n:ää tarvitaan vain lähteisiin, jotka vaativat sitä.
6. Lisää remonttityypit asiakaskysynnän mukaan. Tilastokeskuksen mukaan suunnitteilla: julkisivu 18 %, katto 17 %,
   piha 16 %, käyttövesi 15 %, lämmitys 12 %, viemäri 12 % taloyhtiöistä → **piha/salaojat** ja **lämmitys
   (maalämpö, lämpöpumput)** ovat isoimmat puuttuvat. Muista `pts.py`-merkki ja `nl_search.py`-sanat.

---

## 5. Tuote ja käyttökokemus (P2)

### 5.1 Ensimmäinen kirjautuminen
- Kohteet tulevat vain 30 päivän ajalta (`LISTING_MATCH_HISTORY_DAYS`), ja katalogi on ohut → tyhjä sivu.
- Lisää: aloituslista (valitse urakat → katso kohteet → siirrä 5 liidiksi → soita), ennustekohteet (3.1) heti
  näkyviin, "Tuo oma listasi" -kehotus (`/leads/import` on jo olemassa).

### 5.2 Myyjän päivä: "Tänään"-näkymä
Yksi jono: uudet parhaiten sopivat kohteet, erääntyvät soitot, vastausta odottavat tarjoukset. Soittolistan
tulokset liikuttamaan kauppaa (`app/tasks/call_list.py`):
- "Pyysi tarjouksen" → vaihe + tarjousluonnos esitäytettynä (taloyhtiö, remonttityyppi, huoneistot).
- "Ei kiinnostunut" → hävitty (syy talteen).
- "Tapaaminen sovittu" → kalenteritapahtuma.
- "Soita uudelleen klo …" -tulos, automaattinen siirtyminen seuraavaan puheluun.
- **Soittokäsikirjoitus** kohteen todisteista: "Ilmoituksessa mainitaan putkiremontti 2027, talo vuodelta 1972…"

### 5.3 Kaupan loppupää
- Tarjouksen avausseuranta ja automaattinen muistutus, kevyt sähköinen hyväksyntä (hyväksymissivu on jo).
- **Referenssipaketti:** lähellä tehdyt saman tyypin urakat + RALA / Luotettava Kumppani -merkit sähköposteihin ja
  tarjouksiin. Hinta ja maine ovat taloyhtiöiden kaksi tärkeintä valintakriteeriä (Putkiremonttibaro 2026).
- Voitto/häviö- ja tarjoushintakirjaus → syöte pisteytykselle.

### 5.4 Isännöintitoimistomyynti (account-based)
"Tämä toimisto hallinnoi 14 kohdettasi" -kokoomanäkymä, toimistokohtainen soittolista ja sekvenssi. Lisäksi
**rakennuttajakonsulttien hakemisto**: isoissa urakoissa (putki, julkisivu) konsultti päättää, kuka pääsee
tarjouslistalle. Pienissä taloyhtiöissä ilman konsulttia hallituksen pj on päättäjä – ja juuri ne kertovat
saavansa tarjouksia huonosti (Kiinteistöliiton barometri 11/2026): hyvä kapea rako pienille urakoitsijoille.

### 5.5 Ajastetut kampanjat
- **Energia-avustus-tunniste** kohteille (vanha talo + ilmanvaihto/yläpohja/ikkunat/lämmitys), valmis sekvenssi.
- **Yhtiökokouskausi:** tammi–maaliskuu yhteydenotot ennen kevään kokouksia, syksyllä PTS-päivitykset.

### 5.6 Muuta
- Kohteiden CSV/Excel-vienti.
- Sekvenssit ovat oletuksena pois kaikilta asiakkailta → "siirrä ja aloita sekvenssi" ei näy. Päätä, milloin moduuli
  on valmis kytkettäväksi päälle oletuksena.

---

## 6. Tekninen velka, skaalautuvuus ja tietoturva

### 6.1 Korjaa ennen asiakasmäärän kasvua (100k kohdetta / 50 asiakasta)
1. `merge_into_existing_leads` (`app/listings/services.py:~1021`) lataa kaikki pending-osumat ja tekee kyselyn
   per osuma → yksi joukko-operaatio `company_key`-avaimella vain yhtiöille, joilla on liidi.
2. `refresh_scores` (`housing.py:~346`) pisteyttää kaiken 10 min välein N+1:llä → kerran päivässä tai vain muuttuneet.
3. Kohteet "Sopivimmat ensin" (`target_services._list_by_fit`) hakee kaikki näkyvät kohteet ja järjestää Pythonissa
   → tallenna sopivuus (tai pisteet + organisaation oppima bonus) ja järjestä SQL:ssä.
4. `nav_targets_count` (`app/__init__.py:~470`) ajaa raskaan laskurin jokaisella sivulatauksella (Kohteet-sivulla
   neljästi, `/admin/customers` per organisaatio) → välimuisti tai `listing_matching`-työn päivittämä laskuri.
5. Indeksit: `listing_matches(organization_id, status)`, `listing_matches.lead_id` (FK, ON DELETE SET NULL),
   `listings.published_at` / `first_seen_at`, pg_trgm hakukentille.

### 6.2 Bugit ja tietoturva
- **SSRF:** automaation `send_webhook` (`app/automations/services.py:479-505`) avaa käyttäjän URL:n suoraan
  `urlopen`illa ilman `app/webhooks/url_safety.py`:n tarkistusta (seuraa myös uudelleenohjauksia). Korjaa ensin.
- Tallennetun haun ilmoituslinkki rakennetaan ilman URL-koodausta (`target_services.py:885`) → ääkköset, välilyönnit
  ja `&` rikkovat linkin. Käytä `urlencode`a.
- `backfill_text_types` (`text_types.py:~122`) voi jumittua: SQL-vihje `%IV-%` osuu sanoihin kuten "aktiivi-",
  osumattomia ei merkitä tarkistetuiksi, ja sama 2000 rivin joukko haetaan joka 10 min **[todennäköinen]**.
- GDPR-vienti kirjoittaa ajastimen levylle, jota web ei näe → lataus ei toimi Renderissä (S3).
- Kohdekatalogin henkilötietoja (isännöitsijä `Listing`issä) ei voi poistaa GDPR-pyynnöllä.
- CSP sallii yhä `'unsafe-inline'` (25 templatea, 54 inline-käsittelijää).
- Rajoitukset ovat muistissa per worker (ei Redistä).

### 6.3 Siivous
- Poista SearchJob-perintö (~700 riviä: `app/search/services.py`, `job_scheduler.py`, `n8n_routes.py`,
  `n8n_auth.py`, `oikotie_pagination.py`, job token -auth, scheduler-lohkot, kentät `schedule_description` ja
  `crm_api_key`) sekä ~20 vanhaa suunnitelma-`.md`:tä juuresta.
- 27 kopioitua `_aware`/`_now`-apufunktiota → yksi `app/core/time.py`.

### 6.4 Operointi
- **Varmuuskopio:** S3 puuttuu, audit-lokissa yksi `backup_failed` eikä yhtään onnistunutta. Ennen ensimmäistä
  maksavaa asiakasta: konfiguroi ja tee palautustesti.
- Sentry on koodissa valmiina, aseta `SENTRY_DSN`. Lisää `PYTHONUNBUFFERED=1` `render.yaml`iin.
- **Tuoteanalytiikka puuttuu:** kevyt `events`-taulu (kirjautuminen, kohde nähty, siirretty, piilotettu, soitto,
  tarjous, voitto) → aktivointi- ja suppilomittarit per asiakas `/admin/reports`-sivulle. Ilman tätä ei tiedä,
  kuka asiakas on lähdössä.

---

## 7. Markkina ja kilpailu

| | Mitä | Uhka / mahdollisuus |
|---|---|---|
| **Alma Media, Asuntopuntari remonttinäkymä** (6/2026) | ~76 000 taloyhtiön remonttihistoria + ennuste Etuovi-datasta; välittäjille ja pankeille | **Suurin uusi uhka** ja myös mahdollinen datakumppani |
| Metroc | ~1,4 M€ liikevaihto 2025, 23 hlö, tappiollinen; painopiste uudis- ja julkisissa hankkeissa | Ei 2026 tuoteuutisia; ei taloyhtiötason remonttiajoitusta |
| RPT Byggfakta | Pääomasijoittajien omistuksessa 2024–, ostanut US Construction Monitorin (lupadataliidit) | Enterprise-hinnoittelu, ei pk-urakoitsijoille |
| Vainu | Yritysdata, ~3,5–4 k€/v **[tarkista]** | Ei projektisignaaleja |
| RALA (9/2026) | Urakoitsijan valintatyökalut taloyhtiöille | RALA-pätevyys = luotettavuusmerkki; kumppani |
| Tarjouspalvelu.fi | Julkiset hankinnat, maksullinen hälytys ~2 000 €/v/käyttäjä | Hintavertailu |

**Markkina:** ~91 000 asunto-osakeyhtiötä (HTJ 3/2026), 850–900 isännöintiyritystä. Taloyhtiöt käyttivät
korjauksiin 2,7 mrd € (2025). Kiinteistöliiton korjausrakentamisbarometri 3.11.2026: "käänne kasvuun", 60–70-lukujen
talot vetävät, pienet taloyhtiöt saavat tarjouksia huonosti. Putkiremontin mediaani 890 €/m² (pk-seutu 1 000 €/m²).
Yksi voitettu urakka maksaa FlowLeadsin vuosimaksun moninkertaisesti.

**Päätösketju:** yhtiökokous → hallitus + isännöitsijä valitsevat suunnittelijan/rakennuttajakonsultin →
konsultti tekee tarjouspyynnön ja vertailun → yhtiökokous valtuuttaa. Paras ikkuna on **ennen tarjouslistaa**.

---

## 8. Myynti ja kanavat

1. **Yksi liitto- tai tukkukumppanuus:** Talotekniikkaliitto (LVI-TU + STUL), Maalarimestariliitto, Kattoliitto;
   LVI- ja rakennustarviketukkujen urakoitsijaohjelmat. Tarjous: jäsenetu + webinaari energia-avustuksesta.
2. **Taloyhtiö-messut** (Messukeskus, huhtikuu) ja alueelliset taloyhtiöpäivät (halpoja, päättäjät paikalla).
3. **Datasisältö ja SEO:** kuukausittainen "remonttivelka alueittain/urakoittain", urakkakohtaiset laskeutumissivut
   ("putkiremonttikohteet Pirkanmaa"). Alma ja Kiinteistöliitto julkaisevat makroluvut, FlowLeads toimintalistan.
4. **Hinnoittelu:** pidä yksi hinta, mutta harkitse vuosisopimusta ja alue-/urakkalisää. Vertailukohdat (2–4 k€/v)
   viittaavat varaan nostaa hintaa, kun ROI on näytetty **[päätelmä]**.
5. **Asiakastarinat** yksi per segmentti (putki, julkisivu, katto, ilmanvaihto, maalaus) mitatulla tuloksella –
   kuukausiraportti (`app/analytics/monthly.py`) antaa luvut.

---

## 9. Tiekartta

| Milloin | Mitä |
|---|---|
| **Viikko 1** | SSRF-korjaus automaatioon, URL-koodaus, varmuuskopio S3 + palautustesti, `SENTRY_DSN`, `PYTHONUNBUFFERED`. Viisi datasähköpostia (3.3). |
| **Viikot 2–4** | Ryhti + PRH -tuonti, kansallinen taloyhtiörekisteri, ennustekohteet signaalityyppinä, lähdekohtainen terveys. Ensimmäisen kirjautumisen aloituslista. Piilotuksen syy. |
| **Kuukausi 2** | Skaalautuvuuskorjaukset (6.1) ja indeksit. "Tänään"-näkymä ja soittolistan tulokset kauppaan. Energia-avustus-tunniste ja sekvenssi. Tuoteanalytiikan `events`-taulu. |
| **Kuukausi 3** | Isännöintitoimistonäkymä ja rakennuttajakonsulttihakemisto. Tarjousseuranta ja referenssipaketti. Uudet remonttityypit (piha/salaojat, lämmitys). Kultajoukko Analyzerille. SearchJob-perinnön poisto. |
| **3–6 kk** | Datalisenssi (Oikotie/Alma) jos saadaan, Lupapiste-RSS, Hilma-moduuli, energiatodistukset, PTS-lomake taloyhtiöille, Stripe-laskutus, Taloyhtiö-messut huhtikuussa. |

---

## 10. Avoimet kysymykset

1. Onko budjettia datalisenssille (Oikotie/Alma) ja PRH Virrelle (hallitukset)?
2. Halutaanko lähestyä Almaa kumppanina vai pitää kilpailijana?
3. Saako tämän ympäristön verkkoasetuksiin sallia ryhti.syke.fi, paikkatiedot.ymparisto.fi, avoindata.prh.fi ja
   varke.fi, jotta rajapintojen kentät voidaan varmistaa suoraan ennen toteutusta?

---

## Lähteet

- Ryhti: [avointen rajapintojen ohje](https://ryhti.syke.fi/en/front-page/services/instructions-for-using-open-interfaces-in-the-ryhti-system/),
  [avoindata](https://avoindata.suomi.fi/data/fi/dataset/rakennetun-ympariston-tietojarjestelman-rakennustiedot),
  [datasanakirja](https://geoportal.ymparisto.fi/meta/julkinen/dokumentit/Ryhti_rakennustiedot_EN.pdf),
  [Kuntaliitto RYTJ](https://www.kuntaliitto.fi/julkaisut/rakentamislaki-ja-rakennusvalvonta/4-rytj),
  [lupapäätösten tietomalli](https://valtioneuvosto.fi/-/1410903/rakentamisen-lupapaatosten-tietomalli-koodistoineen-vahvistettu-kayttoonotettavaksi)
- HTJ: [MML aikataulu](https://www.maanmittauslaitos.fi/node/18207),
  [tietopalvelukonsepti](https://tiedostopalvelu.maanmittauslaitos.fi/tp/julkinen/lataus/tuotteet/HTJ2_loppuraportin_liitteet/Liite%207_Konseptikuvaus,%20Huoneistotietoj%C3%A4rjestelm%C3%A4,%20Tietopalvelut.pdf),
  [Taloustaito: 10 000 ilmoittamatta](https://www.taloustaito.fi/koti/yli-10000-isoa-taloyhtiota-jatti-tiedot-ilmoittamatta-huoneistotietojarjestelmaan/)
- PRH: [rekisteröidyt ilmoitukset 2.12.2024](https://www.prh.fi/fi/tietoa_prhsta/uutislistaus/tiedotteet/2024/avoin-data-rekisteroidyt-ilmoitukset_2.12.2024.html),
  [YTJ API](https://avoindata.suomi.fi/data/en/dataset/prh-avoin-data), [Swagger](https://avoindata.prh.fi/en/info/swagger-ui)
- Hilma: [R-asiakas](https://rdrr.io/github/hansel-oy/hilma/src/R/dl.R)
- Lupapiste: [hankeraportti 1/2026](https://api.hankeikkuna.fi/asiakirjat/ad1914a5-be30-4ea6-b3bd-5396164788af/4be7f8df-0d25-438b-8558-ef979913cc16/RAPORTTI_20260114145849.PDF)
- Oikotie-lisenssin ennakkotapaus: [Tilastokeskus / Eurostat](https://cros.ec.europa.eu/book-page/issue-21-gathering-data-advertisements-houses-and-apartments-contract)
- Isännöintijärjestelmät: [Isännöintiliiton järjestelmäselvitys 2026](https://www.isannointiliitto.fi/wp-content/uploads/2026/03/Jarjestelmaselvitys-2026-nettiversio.pdf)
- Energia-avustus: [YM lausuntopyyntö](https://valtioneuvosto.fi/-/1410903/ymparistoministerio-pyytaa-lausuntoja-asetuksesta-asuinrakennusten-korjausrakentamisen-avustukseksi),
  [Verkkouutiset](https://www.verkkouutiset.fi/a/remontteihin-tulossa-merkittava-tuki-jopa-4000-euroa-asunnolta/),
  [Varke 2024](https://www.varke.fi/fi/document/korjaus-ja-latausinfra-avustukset-2024)
- Energiatodistukset: [Varke Palveluväylä](https://liityntakatalogi.suomi.fi/en_GB/dataset/energiatodistusrekisteri-varke-svc)
- MML maastotietokanta: [OGC API](https://avoindata.suomi.fi/data/en/apiset/maastotietokannan-kyselypalvelu-ogc-api-features)
- Alma remonttinäkymä: [STT](https://www.sttinfo.fi/tiedote/72104683/data-paljastaa-49-prosentilla-taloyhtioista-on-vahintaan-yksi-iso-remontti-tekematta),
  [Alma blogi](https://www.almamedia.fi/en/blog/2026/05/06/renovation-risk-in-housing-companies-concentrating-in-the-same-properties/)
- Metroc: [Asiakastieto](https://www.asiakastieto.fi/yritykset/fi/metroc-oy/30192269/taloustiedot)
- Byggfakta: [TA Associates](https://www.ta.com/news/stirling-square-ta-associates-and-macquarie-capital-complete-acquisition-of-byggfakta/)
- RALA: [STT 9/2026](https://www.sttinfo.fi/tiedote/72328601/taloyhtioissa-tehdaan-remontteja-nyt-miljardeilla-ralan-palvelut-auttavat-urakoitsijan-valinnassa)
- Markkina: [Tilastokeskus korjaukset 2025](https://stat.fi/fi/julkaisu/cmfqhu3td7sgr07w3bh0fbu5q),
  [Kiinteistöliitto barometri 11/2026](https://www.sttinfo.fi/tiedote/72466148/taloyhtioiden-korjausrakentaminen-kaantynyt-kasvuun?lang=fi),
  [Putkiremonttibaro 2026](https://www.isannointiliitto.fi/wp-content/uploads/2026/05/Putkiremonttibaro-2026-tulokset.pdf),
  [Isännöintiliitto toimialakatsaus 2025](https://www.isannointiliitto.fi/wp-content/uploads/2025/09/Toimialakatsaus_2025.pdf),
  [päätösketju](https://www.mtvuutiset.fi/artikkeli/putkiremontin-suunnittelu-ja-urakoitsijan-valinta/3224420)
- Kanavat: [Talotekniikkaliitto](https://www.sttinfo.fi/tiedote/72431388/talotekniikan-kaanne-rakentuu-suurten-investointien-varaan?publisherId=3612),
  [Taloyhtiö 2026 -messut](https://www.kiinteistolehti.fi/media/Rakennustiedot-Taloyhtio2026.pdf)
