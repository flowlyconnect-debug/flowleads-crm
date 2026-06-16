# FlowLeads — Visuaalinen suunta: "AI Sales Command Center"

> Status: **suunnitelma, ei toteutettu.** Tämä on visuaalinen suunta, ei refaktorointi.
> Lähde: frontend-design + emil-design-eng + ui-ux-pro-max -skillit, sovellettuna olemassa olevaan koodiin.
> Päivätty: 2026-06-16.

## Reunaehdot (lukitut)

- **Älä vaihda värejä.** Paletti `design-system.css`:ssä on lukittu: tumma sidebar `#0B0F1A`, primary `#1D6BF3`, highlight `#38BDF8`, vaalea tausta `#F4F6FB`. Ei uusia päävärejä.
- Älä koske navigaation rakenteeseen, reitteihin eikä endpointteihin.
- Älä koske Flask/Jinja/SQLAlchemy-logiikkaan — tämä on CSS + template-markkup -kierros.
- Älä riko multi-tenant- / superadmin-ehtoja templateissa.
- Ei uusia JS-kirjastoja. `design-system.css`-tokenit riittävät.
- **Yksi näkymä per kierros, hyväksyntä välissä.** Ei kaikkea kerralla.

## 1. Design direction

FlowLeads ei ole hallintapaneeli jossa on dataa, vaan komentokeskus joka kertoo mitä tehdä seuraavaksi. Erottautuminen kolmesta asiasta, ei uusista väreistä:

1. **Tumma = järjestelmä, vaalea = työ.** Nyt tumma näkyy vain sidebarissa. Tuomalla `#0B0F1A` myös avainmittareihin ja AI-elementteihin syntyy "system feeling" — käyttäjä tunnistaa heti mikä on koneen älyä ja mikä hänen työtilaansa.
2. **Tiheys ja hierarkia.** Geneerisen templaten tunnusmerkki on tasapaksu: kaikki kortit samannäköisiä. Premium syntyy siitä että yksi asia per näkymä on selvästi tärkein (AI-worklist), muu on hiljaista.
3. **Liike kertoo syyn.** `scale(0.97)` painalluksessa, hover joka nostaa korttia 1px, score joka animoituu. Alle 300 ms, ease-out sisääntuloon. Ei koristeita.

Typografia: Inter pysyy. Lisää `letter-spacing: -0.3px` otsikoihin ja **tabular-nums kaikkiin lukuihin** (score, €, %). Tämä yksin nostaa datan ammattimaiseksi.

## 2. Mitkä elementit muuttuvat (tärkeysjärjestys)

1. **Metric-kortit** — litteistä tummaksi "hero-bandiksi". Suurin yksittäinen erottautuja.
2. **AI-worklist** — dashboardin sankariksi: ranking-numerot + syy-rivi per liidi.
3. **Liidit-taulukko** — liian table-heavy → kevyemmät rivit.
4. **Pipeline-kortit** — passiivisista tilannekuviksi.
5. **Login** — paljas `card` → brändätty ensivaikutelma.
6. **Status-badget** — yhtenäistä yhdeksi komponentiksi (nyt eri tyylejä eri sivuilla).

## 3. Miltä dashboardin pitää tuntua

Kuin myyjän aamu: avaat, kone on jo tehnyt työn.
- Ylin rivi: "miten menee" — tumma mittari-band, 4 lukua + trendi.
- Heti alla **yksi iso kysymys: mistä aloitan** — AI:n järjestämä 3–5 rivin lista, jossa jokainen rivi kertoo *kuka, mitä, miksi juuri nyt* ("avasi tarjouksen 2× eilen").
- Sitten kevyemmät: AI-pulssi ja putken tilanne.
- Ei "Hae liidit" -nappia. Liidit virtaavat, dashboard reagoi.
- Tunne: **fokus, ei kojelauta.**

## 4. Sidebar

Rakenne on jo hyvä — älä koske navigaatiologiikkaan. Hienosäätö:
- Aktiivinen kohta: vasemman reunan syaani-aksentti `box-shadow: inset 2px 0 0 #38BDF8` pelkän taustan sijaan.
- Ryhmäotsikot pienemmiksi ja hiljaisemmiksi: `rgba(255,255,255,.30)`, 10px, uppercase.
- Ikonit yhteen kokoon (16px, sama stroke-leveys).
- Badge-luvut (myöhässä / uudet) näkyviin.
- Logo: pieni aksenttilaatta.
- Disabled-kohdat (Tarjoukset, Ennuste) himmeämmiksi ettei näytä rikkinäiseltä.

## 5. Myyntiputki

Kanban pysyy, kortit aktivoituvat:
- Jokaiseen korttiin **score-piste + viimeisin signaali** ("avasi sähköpostin 2h sitten").
- Vaihe-otsikkoon summa ja lukumäärä.
- Kuumat liidit erottuvat reunaviivalla.
- Hover nostaa kortin (`translateY(-1px)` + varjo); raahaus `cursor: grab`, pudotuskohta korostuu.
- Tyhjä sarake saa kehotteen, ei tyhjää laatikkoa.
- Erottuminen: kortti = tilannekuva, ei nimilappu.

## 6. Liidit-lista

Vähemmän taulua, enemmän rivejä:
- Avatar + nimi + yritys yhdeksi vahvaksi soluksi (on jo).
- Score visuaaliseksi palkiksi numeron sijaan.
- "Viimeisin aktiviteetti" värikoodatuksi: vihreä tänään → punainen "ei kontaktia 14 pv".
- Koko rivi klikattava: `cursor: pointer` + hover-korostus.
- Suodatin-paneeli (nyt 12 kenttää auki) piiloon "Suodattimet"-napin taakse, aktiiviset chippeinä.
- Lähde-tabit (n8n / manuaalinen) pysyvät — kertovat automaatiotarinaa.

## 7. Mitä EI saa tehdä

- Älä vaihda värejä äläkä lisää uusia päävärejä.
- Älä koske navigaation rakenteeseen, reitteihin tai endpointteihin.
- Älä tee kaikkea kerralla — yksi näkymä per kierros.
- Ei gradientteja, glow-efektejä tai "AI-neon"-tyyliä. Premium = hillintä.
- Älä koske Flask/Jinja/SQLAlchemy-logiikkaan tässä vaiheessa.
- Älä riko multi-tenant- tai superadmin-ehtoja.
- Ei uusia JS-kirjastoja.

## Periaatteet (skilleistä)

- **Yksi sankari per näkymä** (frontend-design) — boldness yhteen paikkaan, muu hiljaista.
- **Liike kertoo syyn, < 300 ms, ease-out sisään / nopeampi ulos** (emil-design-eng).
- **Tumma = järjestelmä, vaalea = työ** kontrastierona (ui-ux-pro-max).

## Ehdotettu toteutusjärjestys (kun aloitetaan)

1. Dashboard (asettaa sävyn) → 2. Login → 3. Pipeline → 4. Liidit → 5. Yritykset.
Jokainen erikseen, hyväksyntä välissä.
