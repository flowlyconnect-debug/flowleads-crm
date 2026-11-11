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

## Tehty 11.11.2026 (iltapäivä)

- **Redis:** Render Key Value `flowleads-redis` (`red-dbq4l8gu01pc739rr9bg`, free, Frankfurt, ei pysyvyyttä,
  `allkeys_lru`). `REDIS_URL=redis://red-dbq4l8gu01pc739rr9bg:6379` asetettu sekä webiin että ajastimeen.
  Ilmaistaso riittää rate limitille ja välimuistille; jos Redis ei vastaa, sovellus palaa muistiin
  (`_configure_redis_fallback`). Tarkistettu uudelleenkäynnistyksen jälkeen: lokissa ei enää
  `flask_limiter`in "in-memory storage" -varoitusta eikä riviä `REDIS_URL unreachable`.
- **Varmuuskopion virheen syy:** audit-lokin `backup_failed` 11.11. klo 02.00 UTC oli
  `BACKUP_S3_BUCKET is not configured`.
- **Health check** on käytössä (blueprint-synkka toi polun `/api/v1/health`).
- `flask delete-organizations --id N [--id M] [--yes]` (`app/leads/purge.py::delete_organizations`): poistaa
  organisaatiot kaikkine riveineen; jaettu kohdekatalogi ja superadmin säilyvät. Oletuksena kuiva-ajo.
  Ajettu läpi myös paikallista Postgresia vasten (viiteavaimet valvottuina).
- `purge-leads`-kuiva-ajo tekee nyt poistot ja peruu transaktion, joten luvut ovat oikeat.
- GitHub Actions `.github/workflows/tests.yml` ajaa testit PR:ssä ja mainissa. Render deployaa yhä heti
  commitista; jos deploy halutaan vasta testien jälkeen, vaihda palveluiden Auto-Deploy-asetukseksi
  "After CI Checks Pass".

## Seuraavaksi

Käyttäjän tehtävät:

1. **Testiorganisaatioiden poisto** (käyttäjä pyytänyt 11.11.). Kun `delete-organizations` on mainissa ja
   deployattu: Render → web-palvelu → Shell:
   `flask delete-organizations --id 1 --id 2 --id 4 --id 5` (kuiva-ajo), sitten sama `--yes`-valitsimella.
   Tunnisteet 11.11.: 1 ja 2 "Flowly Test Oy", 4 "Testi Asiakas", 5 "grergergergerg". Tarkista ne ensin,
   jos oikeita asiakkaita on jo luotu. Kantaan ei ole MCP:n kautta kirjoitusoikeutta, joten Claude ei voi ajaa tätä.
2. Samassa Shellissä: `pg_dump --version`. Kanta on Postgres 18; vanhempi `pg_dump` epäonnistuu, jolloin
   varmuuskopiointi vaatii Docker-ajoympäristön tai muun ratkaisun.
3. **Varmuuskopiot.** Päätös: Cloudflare R2, bucket `flowleads-backups` (EU-sijainti), kaksi API-tokenia:
   Object Read & Write ajastimelle (`BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY`) ja Object Read
   webille (`BACKUP_S3_READONLY_*`). Webiin lisäksi `BACKUP_S3_BUCKET=flowleads-backups`,
   `BACKUP_S3_REGION=auto`, `BACKUP_S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com`
   (ajastimelle samat kolme). Sen jälkeen testikopio ja palautuskoe.
4. **Ympäristömuuttujien tarkistus** Renderin dashboardista: `APP_BASE_URL`, Mailgun-avaimet ja
   `MAILGUN_WEBHOOK_SIGNING_KEY`, `N8N_MASTER_SECRET`, `CALENDAR_ENCRYPTION_KEY`, `WEBHOOK_ENCRYPTION_KEY`,
   `OPENAI_API_KEY`, `SENTRY_DSN`.
5. **Sähköposti.** `email_logs` on tyhjä eikä millään organisaatiolla ole Mailgun-domainia. Aja
   `flask send-test-email` ja testaa kutsu- ja salasananpalautusviestit.

Claude voi tehdä:

6. Tarkistaa klo 02 UTC jälkeen ajastimen lokista rivit `Scheduled backup starting` / `completed` / `failed`.
7. Content-Security-Policy (vaatii inline-skriptien läpikäynnin).

Suositeltavat, ei kiireelliset: oma domain (päivitä `APP_BASE_URL`), Postgresin IP-sallintalistan rajaus
(nyt `0.0.0.0/0`), Sentry, käyttöehdot ja DPA, laskutusprosessi, juuren vanhojen suunnitelmadokumenttien siivous.

## Huomioitavaa

- Alkuperäisellä koneella on commitoimatta toisen session muutoksia, jotka eivät ole gitissä eivätkä siis
  muilla koneilla: 2FA-sivu (`app/auth/routes.py`, `app/static/css/auth-card.css`,
  `app/templates/auth/two_fa_setup.html`, `tests/test_auth_pages.py`) ja n8n-rakentaja (`n8n/README.md`,
  `n8n/tools/build_catalogue_workflows.py`, `tests/test_n8n_catalogue_e2e.py`). Käyttäjä pyysi jättämään ne.
- Käyttäjä kirjoittaa suomeksi; vastaa suomeksi.
