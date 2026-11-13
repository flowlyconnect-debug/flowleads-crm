# FlowLeads CRM: kehityssuunnat ja vertailu Metrociin

Päivitetty 12.11.2026. Lähteet: tämän repon koodi ja dokumentit (`docs/ARKKITEHTUURI_KOHDEKATALOGI.md`,
`docs/TUOTANTOON_VIENTI_TILANNE.md`) sekä julkiset verkkolähteet (lista lopussa). Metrocin omia sivuja
(metroc.ai) ei voitu avata suoraan tästä ympäristöstä, joten Metrocin tiedot perustuvat hakukoneiden
otteisiin metroc.ai:n sivuista ja kolmansien osapuolten lähteisiin. Ne kannattaa tarkistaa demossa.

---

## 1. Tiivistelmä

- **Metroc on laaja rakennusalan tietopalvelu** (uudisrakentaminen, kiinteistöt, julkiset investoinnit,
  kilpailutukset, taloyhtiöt). FlowLeads on **kapea ja syvä**: taloyhtiöiden korjaushankkeet, tunnistettu
  tekoälyllä, ja niiden myynti omassa CRM:ssä alusta tarjoukseen asti.
- **Metroc myy jo täsmälleen FlowLeadsin kohdeasiakkaille.** Metrocin omat asiakastarinat ovat
  sähköurakoitsija (Voltaco), maalausliike (Priimamaalaus) ja sukitusvalvonta (Sukitusvalvonta PRO), jotka
  kaikki etsivät taloyhtiöitä ja isännöitsijöitä. Kilpailu on suoraa, ei teoreettista.
- **FlowLeadsin suurin heikkous on data, ei ominaisuudet.** Ainoa automaattinen lähde (Oikotie) on estänyt
  n8n Cloudin 11/2026 alkaen. Metroc kertoo kokoavansa tiedot "sadoista lähteistä" (päätöksentekodata,
  uutiset, kilpailutukset, Maanmittauslaitos, PRH).
- **FlowLeadsin vahvuudet:** kohdekohtainen remonttityyppi + varmuus + ajoitus + lainaus ilmoituksesta,
  taloyhtiökortti, isännöitsijänäkymä ja kokonainen myynnin työkalu (putki, tehtävät, sähköposti,
  sekvenssit, PDF-tarjoukset, API). Metrocin oma CRM on kuvausten mukaan kevyt (muistutukset, muistiinpanot,
  putki) ja se nojaa integraatioihin (Lime CRM, Easoft).
- **Suositus:** asemoi FlowLeads "korjausrakentamisen myyntityökaluksi pienille ja keskisuurille
  urakoitsijoille", hinnoittele avoimesti Metrocia edullisemmaksi ja käytä seuraavat 3 kuukautta ennen
  kaikkea **datalähteiden monipuolistamiseen** (luvut 5.1 ja 6).

---

## 2. Metroc lyhyesti

| | |
|---|---|
| Yritys | Metroc Oy, Helsinki, perustettu 2019, toimitusjohtaja Jussi Virnala |
| Koko | Vainun mukaan liikevaihto n. 1,4 M€ (2025), 23 henkeä, tulos lievästi tappiollinen |
| Rahoitus | 2 M€ seed 8/2023 (vetäjänä Lifeline Ventures), aiemmin n. 0,3 M€ enkelirahoitus |
| Markkina | yli 300 asiakasta Suomessa ja Ruotsissa (2023), tavoitteena laajentua Eurooppaan |
| Hinnoittelu | ei julkinen. Yleisten ehtojen mukaan hinnat sovitaan sopimuksessa, maksuja ei palauteta irtisanottaessa |

### Tuotteet ja data (metroc.ai:n sivujen mukaan)

- **Projects / Lead:** tulevat ja käynnissä olevat rakennushankkeet tontinmyynnistä toteutukseen,
  hankintailmoitukset ja -päätökset (julkiset hankinnat, Hilma).
- **Real estates:** kiinteistödata ja yli 70 karttatasoa (asemakaavat, yleiskaavat, pohjavesialueet,
  väestörakenne, muuttoliike). Rakennusoikeus ja kaavatilanne rakennuttajille.
- **Public investments:** tekoäly lukee kuntien budjetteja ja pöytäkirjoja ("miljoona sivua"),
  organisaatiohakemisto (budjetit, päätökset, yhteystiedot), **AI-chat** ("Paljonko Tampere on budjetoinut
  koulurakentamiseen?") ja **automaattiset hälytykset** alueista, organisaatioista ja aiheista.
- **Urakoitsijat ja huoltoyhtiöt:** urakoiden yhteyshenkilöt, **isännöitsijät, huoltoyhtiöt ja hallitusten
  jäsenet**, suodatus alueen, hanketyypin tai kiinteistön mukaan. Korjaushankkeiden "aikaisiksi
  signaaleiksi" mainitaan hankesuunnittelun käynnistyminen, rahoituspäätökset ja kuntotutkimukset.
- **Kohderyhmät:** rakennusliikkeet, urakoitsijat ja huoltoyhtiöt, suunnittelijat, tavarantoimittajat ja
  valmistajat.
- **CRM:** oma kevyt CRM (muistutukset, muistiinpanot, myyntiputki) sekä integraatiot **Lime CRM** (hankkeet
  ja kiinteistökaupat valituilla suodattimilla ja alueilla Limeen) ja **Easoft** (tarjouslaskenta).

### Huomioita

- Metrocin data on laaja, mutta painottuu uudis- ja julkiseen rakentamiseen. Taloyhtiöiden korjaushankkeiden
  tunnistus näyttää perustuvan päätös-, uutis- ja rekisteridataan. Ilmoitustekstien "tulevat remontit"
  -tietoa (FlowLeadsin ydin) ei mainita.
- Ei mainintaa HubSpot-, Pipedrive- tai Salesforce-integraatioista.
- Vuonna 2023 Wired uutisoi, että Metroc käytti vankeja tekoälyn opetusdatan merkitsemiseen. Tällä on vähän
  vaikutusta ostopäätöksiin, mutta se kertoo, että Metroc rakentaa omia NLP-mallejaan suomenkieliseen
  päätösdataan.

---

## 3. FlowLeads nyt (koodin perusteella)

- **Hankinta:** jaettu kohdekatalogi (`app/listings/`). n8n Crawler → Analyzer (gpt-4.1-mini, 8 remonttityyppiä,
  varmuus 0–100, ajoitus, lainaus) → Enricher (Finder/Asiakastieto vain osuneille). Tuonti CSV:llä tai
  `POST /api/v1/n8n/listings/import`.
- **Asiakkaalle:** Kohteet (suodattimet, tallennetut haut, päivittäinen kooste, "Hae kohteita" -varanto),
  Isännöitsijät, taloyhtiökortti, liidit ja kanban, tehtävät, kalenterisynkka, sähköposti (Mailgun),
  sekvenssit, automaatiot, PDF-tarjoukset hyväksymissivulla, raportit ja ennuste, webhookit, Slack/Teams,
  REST API, GDPR-työkalut, 2FA.
- **Ylläpito:** asiakkaiden luonti/kutsu (vain urakat kysytään), katalogin terveysnäkymä, audit-loki.
  Laskutus manuaalinen.
- **Tilanne:** tuotannon tietokanta tyhjennettiin 11.11. Oikotie estää n8n Cloudin. Varmuuskopiot
  (S3, Postgres 18 + pg_dump), sähköpostin toimivuus ja Renderin health check ovat kesken. Avoimia bugeja mm.
  tehtävien aikavyöhyke, voitto/häviöraportin päivämääräkenttä ja CSP:n puuttuminen.

---

## 4. Vertailu

| Osa-alue | Metroc | FlowLeads | Arvio |
|---|---|---|---|
| Datan laajuus | Sadat lähteet: päätökset, uutiset, kilpailutukset, MML, PRH, kaavat | Yksi ilmoituslähde + tuonti | **Metroc selvästi edellä** |
| Korjaushankkeen signaali taloyhtiötasolla | Päätökset, rahoitus, kuntotutkimukset (kuvausten mukaan) | Ilmoitustekstin tulevat remontit, tyyppi, varmuus, vuosi, lainaus | **FlowLeads tarkempi omalla kapealla alueellaan** |
| Ajoitus ("milloin remontti alkaa") | Ei kuvattu kohdetasolla | `timing_year` + suodatin "alkaa viimeistään" | FlowLeads |
| Yhteystiedot | Isännöitsijät, huoltoyhtiöt, hallitusten jäsenet | Isännöitsijä (toimisto, henkilö, puh, email), ei hallitusta, ei huoltoyhtiötä | Metroc |
| Hälytykset | Automaattiset hälytykset alueista/organisaatioista/aiheista | Tallennetut haut + kellon ilmoitus + päivittäinen sähköpostikooste | Tasaväkinen |
| AI-käyttöliittymä | AI-chat budjetteihin ja päätöksiin | AI-työlista, liidin AI-rikastus ja ennuste, ei chattia | Metroc |
| Kartta | 70+ karttatasoa | Ei karttaa (suunnitteilla) | Metroc |
| CRM ja myynnin työ | Kevyt CRM + Lime/Easoft | Täysi CRM: putki, tehtävät, sähköposti, sekvenssit, tarjoukset, raportit | **FlowLeads selvästi edellä** |
| Integraatiot | Lime CRM, Easoft | REST API, webhookit, Slack/Teams, kalenteri | Eri painotus. FlowLeadsilta puuttuu valmis vienti asiakkaan omaan CRM:ään |
| Kohdeasiakas | Isot ja keskisuuret, monta segmenttiä | Pienet ja keskisuuret korjausurakoitsijat | Erottuminen mahdollista |
| Hinnoittelu | Sopimuspohjainen, ei julkinen | Manuaalinen, ei julkinen | Avoin hinnoittelu olisi FlowLeadsille kilpailuetu |
| Luotettavuus ja jatkuvuus | Vakiintunut, rahoitettu, 300+ asiakasta | Tuotanto kesken, yksi estetty lähde | Metroc |

**Johtopäätös:** FlowLeads ei voita Metrocia datan laajuudessa, eikä sen kannata yrittää. Se voi voittaa
**korjausrakentamisen erikoistumisella**: tarkempi signaali (mikä remontti, milloin, miksi uskomme niin),
koko myyntiprosessi samassa työkalussa ja pienen urakoitsijan budjettiin sopiva hinta.

---

## 5. Kehitysehdotukset

Priorisointi: **P1** = välttämätön liiketoiminnan jatkumiselle, **P2** = tärkein erottautuminen,
**P3** = myöhemmin.

### 5.1 Data (P1)

1. **Ryhti (rakennetun ympäristön tietojärjestelmä, SYKE).** Avoin OGC API Features -rajapinta ilman
   kirjautumista, avattu vaiheittain 2025–2026. Kunnat siirtävät tietoja viimeistään 2028 loppuun mennessä.
   Hyöty: rakennusluvat ja rakennustiedot (rakennusvuosi, käyttötarkoitus, kerrosala).
   - **Luvat suoraan signaaleiksi:** julkisivu-, katto- tai putkiremontin lupa on vahva ja tuore signaali.
     Uusi tuontilähde `source="ryhti"` olemassa olevan `import_services.py`-polun kautta, uudet tai
     lupatyypin mukaan luokitellut rivit suoraan matchaukseen.
   - **Ikäperusteinen ennuste:** taloyhtiöt, joiden rakennusvuosi ja tyyppi ennustavat putki-, julkisivu- tai
     kattoremonttia (esim. 1965–1985 kerrostalot ilman tiedossa olevaa putkiremonttia). Merkitään selvästi
     omaksi signaalityypikseen (ennuste, matala varmuus), jotta asiakas ei sekoita sitä varmaan tietoon.
   - Varmista ennen toteutusta, mitkä kentät rajapinta todella antaa ja millä käyttöehdoilla.
2. **Huoneistotietojärjestelmä (HTJ, Maanmittauslaitos).** Taloyhtiöiden piti ilmoittaa hallinnolliset
   tiedot 30.6.2026 mennessä (n. 59 000 / 91 000 ilmoitti), ja tulossa ovat kunnossapitotarveselvitykset ja
   tehdyt korjaukset. Tämä olisi alan paras korjaussignaali, mutta tieto **ei ole avointa**: selvitä MML:ltä,
   saako sitä käyttää kaupallisesti ja millä luvalla. Älä rakenna mitään ennen kuin käyttöoikeus on kirjallisesti
   selvä.
3. **Ilmoitusdatan korvaaminen sopimuksella, ei kiertämisellä.** CLAUDE.md:n mukaisesti bot-suojauksia ei
   kierretä. Vaihtoehdot: datakumppanuus tai lisensointi (Alma/Oikotie, Etuovi, välittäjäketjut),
   isännöitsijätoimistojen kumppanuudet (heillä on PTS-tiedot) tai asiakkaiden omien listojen tuonti.
4. **Kuntien päätökset ja kuulutukset.** Rakennuslupien julkipanot ja naapurikuulemiset kuntien sivuilla
   (sama tekniikka kuin Metrocilla, mutta vain korjausluvat). Vaativampi ylläpitää, tee Ryhdin jälkeen.
5. **Hilma (julkiset hankinnat).** Kuntien ja vuokrataloyhtiöiden (esim. kaupunkien vuokra-asuntoyhtiöt)
   korjausurakat. Avoin rajapinta. Hyvä lisä isommille asiakkaille.
6. **Katalogin terveys:** jokaiselle lähteelle oma tila `/admin/catalogue`-näkymään, jotta yhden lähteen
   katkos näkyy heti (nyt `catalogue_health_check` sähköpostilla).

### 5.2 Signaali ja kohteen arvo (P2)

1. **Taloyhtiö keskiöön.** Nyt kohde = ilmoitus. Yhdistä kaikki signaalit (ilmoitus, lupa, ikäennuste,
   hankinta) samaan taloyhtiöön (Y-tunnus tai osoite) ja näytä taloyhtiökortilla **signaalien aikajana**.
   Tämä on laajennus olemassa olevaan `insight_services.py`-malliin.
2. **Kohteen pisteytys:** yksi luku (0–100), joka yhdistää varmuuden, ajoituksen läheisyyden, signaalien
   määrän, yhteystietojen saatavuuden ja koon (huoneistoja, kerrosala). Käytä Kohteet-sivun oletusjärjestyksenä.
3. **Remonttityyppien tarkennus:** nykyiset 8 tyyppiä ovat karkeita. Esim. ikkunat ja ovet, parvekkeet,
   pihat ja salaojat, hissit, LVI-lisät (aurinkopaneelit, maalämpö, latauspisteet) ovat kaikki omia
   urakoitsijasegmenttejään. Muutos `REMONTTITYYPIT`-listaan ja Analyzerin promptiin.
4. **Selitettävyys myyntiargumenttina:** näkyvä "miksi tämä kohde" -laatikko (lainaus ilmoituksesta,
   lupa, rakennusvuosi). Metroc ei kuvausten mukaan tarjoa tätä kohdetasolla.

### 5.3 Yhteystiedot (P2)

1. **Hallituksen puheenjohtaja.** PRH:n kaupparekisterissä asunto-osakeyhtiöiden hallitus on julkinen
   (maksullinen haku, ei avoin rajapinta). Metroc tarjoaa hallitusten jäsenet, joten tämä on pariteettipuute.
   Huomioi GDPR: oikeutettu etu ja informointi, kuten liideille nyt (`legal_basis`).
2. **Isännöitsijärekisteri isännöintitoimistoista:** toimiston tiedot, koko, kohdemäärä. Täydentää
   Isännöitsijät-sivua, jolloin urakoitsija voi tehdä "toimistomyyntiä" (yksi isännöitsijä, monta taloyhtiötä).
3. **Huoltoyhtiöt** taloyhtiökohtaisesti (Metrocilla on).

### 5.4 Tuote ja käyttökokemus (P2–P3)

1. **Karttanäkymä** Kohteille (suunnitteilla). Ryhdin koordinaatit poistavat geokoodauksen tarpeen osittain.
   Käytä vendoroitua kirjastoa (esim. Leaflet `app/static/vendor/`-kansioon), ei CDN:ää.
2. **"Kysy kohteista" -AI-haku:** luonnollinen kieli Kohteet-suodattimiksi ("putkiremontit Tampereella
   2027 mennessä, kerrostalot 60-luvulta"). Turvallinen toteutus: AI tuottaa vain suodatinparametrit, ei
   vapaata SQL:ää, ja kysyy vain organisaation julkaisemista kohteista.
3. **Valmiit CRM-viennit:** HubSpot ja Pipedrive (yleisimmät pk-urakoitsijoilla), sekä Excel-vienti
   Kohteista. Näin myös asiakkaat, joilla on jo CRM, voivat ostaa pelkän datan. Metrocilla on Lime ja Easoft.
4. **Tarjouslaskenta-integraatio** (Easoft, Admicom) tai ainakin tarjouksen vienti, koska Metroc on jo
   Easoftin kumppani.
5. **Mobiili:** urakoitsijan myyjä on usein työmaalla. Responsiivinen Kohteet ja liidi-sivu sekä "soita
   isännöitsijälle" -painike ja puhelun kirjaus yhdellä napautuksella.
6. **Tulosraportti asiakkaalle:** "tässä kuussa sait 42 kohdetta, siirsit 18, tarjouksia 5, voitettu 1
   (x €)". Tämä on tärkein syy jatkaa tilausta ja puuttuu Metrocin kuvauksista.

### 5.5 Liiketoiminta (P1–P2)

1. **Avoin hinnoittelu ja kokeilujakso.** Metroc ei julkaise hintoja eikä palauta maksuja. FlowLeads voi
   erottua julkisella hinnastolla, kuukausisopimuksella ja 14 päivän kokeilulla. Hinnasto esim. urakkatyypin
   ja alueen mukaan (hypoteesi, testaa asiakkailla). Ennen hinnoittelua kannattaa pyytää Metrocilta demo ja
   tarjous vertailukohdaksi.
2. **Laskutus CRM:ään** (Stripe tai laskutuspalvelu) kun asiakkaita on enemmän kuin muutama.
3. **Asiakastarinat** samoista segmenteistä kuin Metrocilla (sähkö, maalaus, putki, sukitus, julkisivu):
   yksi mitattu tulos per segmentti.
4. **Kanava:** isännöitsijätoimistot ovat sekä datalähde että mahdollinen jakelukanava ("löydä urakoitsija
   taloyhtiöllesi").

### 5.6 Tuotantovalmius (P1, ennen myyntiä)

Nämä ovat jo listalla `docs/TUOTANTOON_VIENTI_TILANNE.md`:ssä, mutta ne ovat ehto kaikelle yllä olevalle:
varmuuskopiot (S3, pg_dump-versio Postgres 18:lle), sähköpostin todentaminen (Mailgun-domain), Render health
check, Redis rajoituksille, tehtävien aikavyöhykebugi, CSP-otsake sekä väliaikaisen salasanan ja
API-avaimen poistaminen session-evästeestä.

---

## 6. Tiekartta-ehdotus

| Aikajänne | Tehtävät |
|---|---|
| 0–1 kk | Tuotantovalmius (5.6). Datan käyttöoikeuksien selvitys: Ryhti-rajapinnan kentät, HTJ:n kaupallinen käyttö MML:ltä, kumppanuuskysely ilmoitusportaaleille. Metrocin demo ja tarjous vertailuun. |
| 1–3 kk | Ryhti-tuonti (luvat + rakennustiedot), ikäperusteinen ennustesignaali, signaalityyppi kohteelle, taloyhtiöpohjainen yhdistäminen, kohteen pisteytys, tulosraportti asiakkaalle, avoin hinnasto ja kokeilu. |
| 3–6 kk | Karttanäkymä, HubSpot/Pipedrive-vienti, tarkennetut remonttityypit, hallituksen puheenjohtaja ja huoltoyhtiö, Hilma-tuonti, mobiilikäyttö. |
| 6–12 kk | AI-haku ("kysy kohteista"), kuntien kuulutukset, HTJ-data jos lupa saadaan, laskutus CRM:ään, Ruotsin tai muun markkinan arviointi vasta kun Suomi toimii. |

---

## 7. Avoimet kysymykset käyttäjälle

1. Halutaanko kilpailla Metrocin kanssa samoista asiakkaista (pienet korjausurakoitsijat), vai tähdätäänkö
   segmenttiin, jota Metroc ei palvele hyvin (esim. yhden hengen urakoitsijat tai tietty urakkatyyppi)?
2. Onko budjettia maksulliselle datalle (PRH, Asiakastieto, lisensoitu ilmoitusdata)?
3. Saako metroc.ai:n sallia tämän ympäristön verkkoasetuksissa, jotta vertailu voidaan tarkistaa suoraan
   Metrocin sivuilta?

---

## Lähteet

- Metroc: [urakoitsijat ja huoltoyhtiöt](https://metroc.ai/urakoitsijat-ja-huoltoyhtiot/),
  [rakennusliikkeet](https://metroc.ai/rakennusliikkeet/),
  [tavarantoimittajat](https://metroc.ai/tavarantoimittajat-ja-valmistajat/),
  [suunnittelijat](https://metroc.ai/suunnittelijat-ja-asiantuntijat/),
  [Projects](https://metroc.ai/palvelumme/projects/), [Real estates](https://metroc.ai/palvelumme/real-estates/),
  [Public investments](https://metroc.ai/palvelumme/public-investments/),
  [Solution (EN)](https://metroc.ai/en/solution/), [yleiset ehdot](https://metroc.ai/yleiset-ehdot/)
- Metrocin asiakastarinat: [Voltaco](https://metroc.ai/asiakkaamme/voltaco-loytaa-uusia-taloyhtioita-ja-isannoitsijoita-metrocin-avulla/),
  [Priimamaalaus](https://metroc.ai/asiakkaamme/priimamaalaus-tehostaa-taloyhtiomyyntiaan-metrocin-avulla/),
  [Sukitusvalvonta PRO](https://metroc.ai/asiakkaamme/sukitusvalvonta_pro/),
  [Myynti on asiantuntijatyötä](https://metroc.ai/materiaalit/rakennusalan-myynti-on-asiantuntijatyota/)
- Integraatiot: [Lime CRM](https://www.lime-technologies.com/en/products/lime-crm/integrations/metroc/),
  [Easoft](https://easoft.fi/kumppani/metroc-rakennustietokanta/),
  [KIRAHub](https://kirahub.org/metroc-tekee-rakennushankkeista-ja-kumppaneista-loydettavia-datalla-ja-tekoalylla/)
- Yritystiedot ja rahoitus: [Vainu](https://search.vainu.com/company/metroc-oy-taloustiedot-ja-liikevaihto/FI30192269/yritystiedot),
  [EU-Startups](https://www.eu-startups.com/2023/08/helsinki-based-saas-startup-metroc-secures-e2-million-to-develop-ai-for-construction-industry/),
  [Tech.eu](https://tech.eu/2023/08/25/metroc-raises-2m-construction-projects-using-ai),
  [AIN (Wired-uutisesta)](https://en.ain.ua/2023/09/18/finnish-metroc-startup-uses-inmate-labor-to-train-ai),
  [CB Insights](https://www.cbinsights.com/company/metroc)
- Muut kilpailijat: [RPT Byggfakta, trendiraportti](https://smart.rpt.fi/info/wp-content/uploads/2022/04/RPT_Rakennusalan_trendit_Q2_2022_8_4.pdf)
- Datalähteet: [Ryhti (SYKE)](https://ryhti.syke.fi/en/about-the-system/),
  [Ryhti kumppanitestaus, avoin rajapinta](https://ryhti.syke.fi/wp-content/uploads/sites/2/2024/11/20241107-Kumppanitestaus-avoin-info.pdf),
  [Isännöintiliitto: kunnossapitotiedot HTJ:hin](https://www.isannointiliitto.fi/wp-content/uploads/2025/02/Esitysmateriaali-Kunnossapitotietojen-siirtaminen-huoneistotietojarjestelmaan-webinaari-12022025.pdf),
  [Taloustaito: HTJ-ilmoitukset](https://www.taloustaito.fi/koti/yli-10000-isoa-taloyhtiota-jatti-tiedot-ilmoittamatta-huoneistotietojarjestelmaan/),
  [Putkiremonttibarometri 2025](https://www.lvi-tu.fi/uutiset/putkiremonttibarometri-2025-rahoitusvaikeudet-uhkaavat-taloyhtioiden-korjaushankkeita/)
