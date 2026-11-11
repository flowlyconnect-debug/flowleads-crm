# Tuotantoon vienti – tilanne ja jatko

Päivitetty 11.11.2026. Tämä on jatkomuistio: kun käyttäjä sanoo
**"Jatka FlowLeadsin tuotantoon vientiä"**, lue tämä tiedosto ja jatka kohdasta
"Seuraavaksi". Päivitä tiedosto, kun kohtia valmistuu.

Liidien tuonti (Oikotie / n8n / CSV) on rajattu tämän työn ulkopuolelle; se tehdään erikseen.

## Tuotantoympäristö (Render, Frankfurt)

| Osa | Nimi | Tunniste |
|---|---|---|
| Web | `flowleads-crm-zv0a` | `srv-dbpm5tuc6qcc73deuce0`, https://flowleads-crm-zv0a.onrender.com |
| Ajastin (worker) | `flowleads-scheduler` | `srv-dbpmkh8u01pc73dqmud0` |
| Postgres 18 | `flowleads-db-zv0a` | `dpg-dbpm566c6qcc73derts0-a`, plan basic_256mb |
| Workspace | Matias's workspace | `tea-dbpm0kuc6qcc73deds50` |

- Molemmat palvelut deployaavat automaattisesti `main`-haarasta. Kirjautumissivu on `/auth/login`.
- Render-liitännällä (MCP) voi lukea palvelut, deployt ja lokit. Kantaan on vain lukuoikeus, ja
  ympäristömuuttujia ei voi lukea. Pyyntölokit (HTTP-statukset) eivät ole saatavilla.
- Komentojen ajo tuotannossa: Renderin dashboard → web-palvelu → Shell. Selaintyökalulla Shell ottaa
  vastaan vain yksittäiset näppäinpainallukset (`key`), ei `type`-syötettä.
- Kannassa 11.11.: 4 organisaatiota, 1 superadmin (2FA päällä), 3 adminia (2FA asettamatta, pakotetaan
  kirjautuessa), 3 hakuprofiilia (2 aktiivista). Liidejä ja kohteita ei ole (ks. alla).

## Tehty 11.11.2026

- Tarkastus: migraatiot ajan tasalla (`f4a5b6c7d8e9`), health-päätepisteet OK, HTTPS-ohjaus ja evästeliput
  kunnossa, julkinen rekisteröinti pois, ei virheitä web-palvelun lokeissa käyttöönoton jälkeen.
- PR #11 mergetty ja tuotannossa:
  - tietoturvaotsakkeet (`app/__init__.py::_register_security_headers`); lomakkeiden upotussivu
    `/forms/<token>/embed` on tarkoituksella ilman `X-Frame-Options`-otsaketta
  - pyyntöraja 16 Mt (`MAX_CONTENT_LENGTH_MB`) ja 413-käsittelijä
  - `healthCheckPath: /api/v1/health` tiedostossa `render.yaml`
  - ajastimen lokitus: apscheduler stdoutiin, varmuuskopioajolle aloitusrivi ja kaatumisen käsittely
  - `flask purge-leads [--with-listings] [--yes]` (`app/leads/purge.py`), oletuksena kuiva-ajo
  - `exports/*.zip` pois gitin seurannasta
- Tuotannosta poistettu käyttäjän pyynnöstä kaikki liidit ja koko kohdekatalogi: 45 liidiä,
  111 aktiviteettia, 45 tehtävää, 64 kohdetta, 44 osumaa. Audit-lokissa `leads_purged`.
- Testit: 669 läpi (`tests/test_security_headers.py`, `tests/test_purge_leads.py` uusina).

## Tehty 11.11.2026 (2): koko järjestelmän kartoitus ja korjaukset

Koko sovellus käytiin läpi selaimella (Playwright) paikallista Postgresia vasten kolmella roolilla
(superadmin, admin, user) sekä koodikatselmuksena. Korjaukset ovat `tests/test_audit_fixes.py`:n ja
päivitettyjen testien takana. Migraatio `a5b6c7d8e9f0` (ALV-oletus 25,5 %, `listing_matches.profile_id`
sallii NULLin).

- Tietoturva: `/api/v1/debug/last-error` oli tuotannossa auki ilman kirjautumista (nyt kiinni, ellei
  `DEBUG_DIAGNOSTICS=true`); kirjautumisen `next`-parametrin avoin uudelleenohjaus; tarjous-PDF:n
  otsake/alatunniste pystyi lukemaan palvelimen tiedostoja (`file://`); yritysrajoitus 2FA-koodille ja
  salasanan palautuspyynnölle.
- Toimimattomat toiminnot: automaation luonti/muokkaus (500), liidin arkistointi, liidin merkitseminen
  hävityksi (liidisivu ja massatoiminto), GDPR-anonymisointi (salasanatarkistus väärin päin, kuukausiajo
  kaatui Postgresissa), tarjouksen summat eivät päivittyneet tallennettaessa, sentit eivät kelvanneet uusille
  tarjousriveille, sähköpostimallin muokkaus loi kaksoiskappaleen, järjestelmämallien esikatselu (404),
  sekvenssin poisto (500), tietosuojaselosteen linkki kaatoi kaiken liidisähköpostin, ajastimen lähettämien
  viestien peruutuslinkki oli `http://None/...`, Mailgunin JSON-webhookit hylättiin, automaation "lähetä
  sähköposti" ja "lisää sekvenssiin" kaatuivat aina, järjestelmäilmoitukset (tarjous hyväksytty, GDPR-vienti)
  lähtivät SMTP:llä väärillä tunnuksilla (nyt Mailgun-API), kolme rikkinäistä "Luo…"-linkkiä.
- Superadmin: valittu organisaatio muistetaan istunnossa (`app/core/tenant.py`), eikä linkki, josta
  `organization_id` puuttuu, pääty enää 400-sivulle; `/dashboard` ilman parametria on organisaation valitsin.
- Navigaatio: sivupalkissa Tarjoukset, Lomakkeet, Sekvenssit ja Automaatiot; adminin asetuksissa Käyttäjät,
  Liidiasetukset, Tarjousasetukset, Kalenteri ja Webhookit (aiemmin ilman linkkiä).
- Kohteet: piilotettu kohde ei palaa toisen profiilin kautta; profiilin poisto säilyttää käsitellyt osumat;
  virheellinen isännöitsijän sähköposti ei enää hukkaa kohdetta; CSV-tuonti hyväksyy pilkulla erotetut
  remonttityypit.
- Kieli ja aika: käyttöliittymän ilmoitukset suomeksi (`app/core/messages_fi.py`, suodatin `fi_msg`),
  järjestelmän sähköpostimallit suomeksi, aikaleimat Suomen ajassa (suodatin `helsinki`), CSV-viennit
  avautuvat suomalaisessa Excelissä (BOM + puolipiste).

Tiedossa olevat, vielä avoimet (ei korjattu tässä):

- Organisaation datavienti (`app/gdpr/exports.py`) kirjoittaa ZIPin ajastimen levylle, jota web ei näe →
  lataus ei toimi Renderissä ennen kuin vienti tallennetaan S3:een. "Luo varmuuskopio nyt" webistä vaatii
  kirjoitusavaimen, jota webillä ei ole.
- Tehtävien eräpäivät tallennetaan käyttäjän kellonaikana ilman aikavyöhykemuunnosta (näkyvät oikein, mutta
  "myöhässä" ja muistutukset laukeavat 2–3 h myöhässä). Korjaus vaatii datamuunnoksen.
- Kojelaudan tehtäväluvut ovat koko organisaation, Tehtävät-sivu näyttää omat; voitettu/hävitty-raportit
  käyttävät `updated_at`-kenttää `stage_changed_at`:n sijaan.
- Sekvenssien avaus-/klikkaustilastot eivät päivity; `lead_no_activity`-automaatio laukeaa joka päivä;
  Slack/Teams-webhookin viestiteksti on aina "Uusi liidi"; automaation muokkaus pudottaa salatun
  webhook-otsakkeen; webhook-URLeissa ei ole SSRF-suodatusta.
- Väliaikainen salasana ja uusi API-avain kulkevat allekirjoitetussa istuntoevästeessä yhden sivulatauksen.
- Content-Security-Policy puuttuu (kirjastot ovat nyt omalla palvelimella, joten sen voi ottaa käyttöön, kun
  inline-skriptit on käyty läpi).

## Tehty 11.11.2026 (3): uudet toiminnot

Migraatio `b6c7d8e9f0a1` (päiväkoosteen asetus, tarjouksen logo). Testit: `tests/test_feature_round.py`.

- **Suodatus.** Kohteet-sivulla remontti, maakunta, kaupunki, talotyyppi, "vain yhteystiedolliset" ja järjestys
  (`app/listings/target_services.py::_apply_target_filters`); Liidit-sivulla pikasuodattimet vaihe, remontti,
  kaupunki, maakunta ja vastuuhenkilö (`LeadService.quick_filter_options`). Valinnat tulevat voimaan heti ja
  säilyvät sivutuksen ja siirtojen yli. Tarjolla on vain arvoja, joita aineistossa oikeasti on.
- **Kohteen siirto.** Vastuuhenkilön valinta massasiirrossa ja linkit syntyneisiin liideihin.
- **Päiväkooste uusista kohteista.** Ajastintyö `target_digest` klo 7.30 Suomen aikaa: sovelluksen ilmoitus
  ja sähköposti organisaation käyttäjille, jos uusia kohteita on tullut edellisen koosteen jälkeen. Kytkin
  Kohteet-sivun alareunassa.
- **Liidisivu.** "Kirjaa soitto" / "Kirjaa sähköposti" yhdellä klikkauksella (päivittää viimeisimmän
  yhteydenoton), "Palauta arkistosta" ja "Avaa uudelleen" (hävitty liidi).
- **Tehtävät.** Muokkaus, siirto (+1 pv / +3 pv / +1 vko) ja poisto (`/tasks/<id>/edit`).
- **Liidien CSV-tuonti asiakkaalle** (`/leads/import`, `app/leads/import_services.py`): mallitiedosto,
  kaksoiskappaleiden ohitus, rivikohtaiset virheet.
- **Superadmin.** Organisaation poisto käytöstä / käyttöönotto asiakaslistalta (estää kirjautumisen,
  katkaisee istunnot ja API-avaimet; tiedot säilyvät), käyttäjän 2FA:n nollaus (Käyttäjät-sivu), audit-lokin
  selain (`/admin/audit-log`).
- **Tarjous-PDF.** Logo (asetus, vain https), suomalainen rahamuoto ja ALV-erittely (veroton, ALV €,
  verollinen); sama erittely asiakkaan tarjoussivulla.
- **Ei ulkoisia CDN-latauksia.** Chart.js, Sortable, Quill ja Inter-fontti ovat hakemistossa
  `app/static/vendor/` (ks. sen README).
- **CI.** `.github/workflows/tests.yml` ajaa migraatiot ja testit jokaiselle PR:lle ja mainille.

Huom. organisaation `is_active`-lippu on nyt oikeasti voimassa: jos jokin tuotannon organisaatio on jäänyt
passiiviseksi, sen käyttäjät eivät pääse kirjautumaan ennen kuin se otetaan käyttöön asiakaslistalta.

## Tehty 11.11.2026 (4): tuotantotestaus ja "Hae kohteita"

- Tuotanto testattu selaimella julkiselta puolelta (kirjautuminen, virhesivut, otsakkeet, omat kirjastot) ja
  kannasta: kaikki organisaatiot aktiivisia, migraatiot ajan tasalla. Mailgun hyväksyy lähetykset (testiviestit
  11.11.), `MAILGUN_WEBHOOK_SIGNING_KEY` on asetettu, webhook-muoto vastaa Mailgunin dokumentaatiota.
  Kalenteriliitos on yhä kokeilematta (yhtään kalenteria ei ole yhdistetty).
- Testiviestin vastaanottajan voi kirjoittaa itse (Sähköpostiloki-sivu); aiemmin viesti meni aina
  kirjautuneen käyttäjän osoitteeseen.
- **Hae kohteita** (`TARGET_FETCH_ENABLED`, oletuksena päällä; migraatio `c7d8e9f0a1b2`): uudet osumat
  jäävät varastoon (`ListingMatch.released_at IS NULL`) eivätkä näy Kohteet-sivulla, sivupalkissa tai
  kojelaudalla. Asiakas painaa Kohteet-sivun painiketta, sivu näyttää 10–15 sekunnin hakuanimaation ja
  varastosta vapautuu vaihteleva määrä kohteita (`TARGET_FETCH_MIN`–`TARGET_FETCH_MAX`, oletus 3–8, uusimmat
  ensin). Yksi haku tunnissa per organisaatio (`TARGET_FETCH_COOLDOWN_MINUTES`), myös kun mitään ei löytynyt;
  superadminia raja ei koske. Superadmin näkee Kohteet-sivulla varaston koon. Päiväkooste kertoo tässä tilassa
  vain, että uusia kohteita on haettavissa. Testit: `tests/test_target_fetch.py`.
- Tämän repon pilviympäristö `flowleads-crm` sallii tuotanto-osoitteen, joten tuotantoa voi testata
  Claude-istunnosta.

## Tehty 11.11.2026 (5): isännöitsijät, taloyhtiökortti ja tallennetut haut

Kolme toimintoa, jotka rakentuvat jo olemassa olevasta tiedosta (migraatio `d8e9f0a1b2c3`, testit
`tests/test_insights.py`). Mikään niistä ei näytä varastossa olevia kohteita.

- **Isännöitsijät** (`/managers`, sivupalkissa Myyntiputken alla): isännöintitoimistot, niiden taloyhtiöt,
  saatavilla olevat kohteet, avoimet liidit, voitetut kaupat ja viimeisin yhteydenotto. Yhdellä soitolla voi
  käydä läpi saman isännöitsijän kaikki kohteet.
- **Taloyhtiökortti** (`/housing-companies/<listing_id>`): perustiedot, tulevat remontit aikajanana,
  isännöinti, liidit ja avoimet tehtävät sekä lähdeilmoitukset. Ei omaa valikkokohtaa: avautuu kohteen
  nimestä Kohteet-sivulla, isännöitsijän sivulta ja liidin sivulta ("Taloyhtiön tiedot").
- **Tallennetut haut** (Kohteet-sivun rajausten yläpuolella): rajausyhdistelmän voi tallentaa nimellä, ottaa
  käyttöön yhdellä painalluksella ja saada ilmoituksen (kellokuvake) uusista osumista. Ilmoitukset lähettää
  ajastimen `listing_matching` (10 min välein, `notify_saved_searches()`); enintään 15 hakua käyttäjää kohden.
- Sivupalkin järjestys noudattaa työnkulkua: Kojelauta, Kohteet, Liidit, Myyntiputki, Isännöitsijät.

Seuraava askel tästä: karttanäkymä (vaatii osoitteiden geokoodauksen) ja uudet tietolähteet (rakennusluvat,
hankintailmoitukset).

## Seuraavaksi

Käyttäjän tehtävät (vaativat tilejä ja avaimia):

1. **Varmuuskopiot.** S3-yhteensopiva bucket (esim. Cloudflare R2 tai Backblaze B2) ja `BACKUP_S3_*`-muuttujat:
   kirjoitusavain vain ajastimelle, lukuavain webille (ks. `render.yaml`). Audit-lokissa on yksi
   `backup_failed` eikä yhtään onnistunutta; syytä ei saatu selville. Sen jälkeen testikopio ja palautuskoe.
2. **Redis.** Renderissä ei ole Key Value -instanssia; rate limit on muistissa ja per worker. Luo instanssi ja
   aseta `REDIS_URL` web-palveluun (ajastin perii sen).
3. **Ympäristömuuttujien tarkistus** Renderin dashboardista: `APP_BASE_URL`, Mailgun-avaimet ja
   `MAILGUN_WEBHOOK_SIGNING_KEY`, `N8N_MASTER_SECRET`, `CALENDAR_ENCRYPTION_KEY`, `WEBHOOK_ENCRYPTION_KEY`,
   `OPENAI_API_KEY`, `SENTRY_DSN`.
4. **Sähköposti.** `email_logs` on tyhjä eikä millään organisaatiolla ole Mailgun-domainia. Aja
   `flask send-test-email` ja testaa kutsu- ja salasananpalautusviestit.
5. **Renderin health check** käyttöön: blueprint-synkka tai polku käsin palvelun asetuksiin.

Claude voi tehdä:

6. Tarkistaa klo 02 UTC jälkeen ajastimen lokista rivit `Scheduled backup starting` / `completed` / `failed`.
   Ennen lokituskorjausta ajastin ei kirjoittanut yöllä mitään.
7. Selvittää `pg_dump`-version Renderin Python-ympäristössä. Kanta on Postgres 18; vanhempi `pg_dump`
   epäonnistuu, jolloin varmuuskopiointi vaatii Docker-ajoympäristön tai muun ratkaisun.
8. Korjata `purge-leads`-kuiva-ajon tuloste: se laskee osumat kahteen kertaan, kun `--with-listings` on mukana.
9. (Tehty) CI: `.github/workflows/tests.yml`. Halutessa mainin suojaus GitHubissa niin, että punainen ajo estää yhdistämisen.
10. Content-Security-Policy (vaatii inline-skriptien läpikäynnin).

Suositeltavat, ei kiireelliset: oma domain (päivitä `APP_BASE_URL`), Postgresin IP-sallintalistan rajaus
(nyt `0.0.0.0/0`), Sentry, käyttöehdot ja DPA, laskutusprosessi, juuren vanhojen suunnitelmadokumenttien siivous.

## Huomioitavaa

- Kartoituksen korjaukset muuttivat myös tiedostoa `app/auth/routes.py`; alkuperäisen koneen commitoimattomat
  2FA-sivun muutokset pitää yhdistää siihen käsin.

- Alkuperäisellä koneella on commitoimatta toisen session muutoksia, jotka eivät ole gitissä eivätkä siis
  muilla koneilla: 2FA-sivu (`app/auth/routes.py`, `app/static/css/auth-card.css`,
  `app/templates/auth/two_fa_setup.html`, `tests/test_auth_pages.py`) ja n8n-rakentaja (`n8n/README.md`,
  `n8n/tools/build_catalogue_workflows.py`, `tests/test_n8n_catalogue_e2e.py`). Käyttäjä pyysi jättämään ne.
- Käyttäjä kirjoittaa suomeksi; vastaa suomeksi.
