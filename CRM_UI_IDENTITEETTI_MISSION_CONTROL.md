# FlowLeads — Valittu visuaalinen identiteetti: "Mission Control"

> Status: **visuaalinen identiteetti, ei toteutusta.** Ei koodia, ei CSS:ää tässä vaiheessa.
> Valittu 4 konseptin joukosta (A=Mission Control, B=The Briefing, C=Flow, D=Copilot).
> Päivätty: 2026-06-16. Jatkaa dokumenttia `CRM_UI_COMMAND_CENTER_SUUNTA.md`.

## Ydinlause

FlowLeads on **reaaliaikainen valvomo**, jossa tausta-automaatio (n8n + AI) tuottaa jatkuvaa telemetriaa ja käyttäjä istuu komentopöydässä lukemassa signaaleja ja reagoimassa. Tieto tulee käyttäjälle — ei käyttäjä tiedolle.

## Tunne, joka tavoitellaan

Vireä, ammattimainen, tilanteen tasalla. Trading-desk / lennonjohto. Käyttäjä tuntee hallitsevansa konetta, joka tekee töitä hänen puolestaan ja raportoi reaaliajassa.

## Identiteetin kantavat periaatteet

1. **Tumma = perussävy, vaalea = kosketuspinta.** Toisin kuin nykyisin (tumma vain sidebarissa), tummaa `#0B0F1A` käytetään koko valvomon perustana. Vaalea `#F4F6FB` varataan paikkoihin joihin käyttäjä aktiivisesti koskee (lomakkeet, editorit, listojen työtila). Tämä erottaa "koneen" ja "työn".
2. **Tiheys on ominaisuus, ei vika.** Valvomo näyttää paljon kerralla ja on suunniteltu nopeaan skannaukseen. Ei piiloteta dataa klikkausten taakse — nostetaan hierarkialla esiin.
3. **Signaalit elävät.** Pienet syaani `#38BDF8` status-pisteet, päivittyvät luvut, kevyt live-virta. Liike kertoo että järjestelmä on hereillä — alle 300 ms, ease-out, ei koristetta.
4. **Yksi monitori on aina tärkein.** Vaikka näkymä on tiheä, AI:n "mitä nyt" -paneeli on selvästi sankarielementti. Muu on hiljaista taustatelemetriaa.

## Paletin käyttö (lukittu — ei muutoksia)

- `#0B0F1A` — valvomon perustausta ja avainmittarit.
- `#111827` / `#1A2235` — paneelit, kortit, hover tummalla pinnalla.
- `#1D6BF3` — ensisijaiset toiminnot, aktiiviset tilat.
- `#38BDF8` — live-signaalit, status-pisteet, "kuuma nyt" -korostus.
- `#F4F6FB` / valkoinen — työtila ja kosketuspinnat.
- Status (success/warning/danger) — vain merkityksen kantajina, ei koristeena.

## Typografia & luvut

Inter pysyy. Otsikoissa tiukka `letter-spacing` (negatiivinen). **Kaikki luvut tabulaarisina** (score, €, %, ajat) — tämä yksin tekee valvomotunteen ja estää datan tärinän. Pienet versaalit ryhmäotsikoissa, hiljaisella kontrastilla.

## Allekirjoituselementti (signature)

**Telemetria-virta:** ohut, jatkuvasti päivittyvä signaalinauha (uudet liidit, AI-rikastukset, avatut tarjoukset) joka tekee taustalla pyörivästä n8n+AI-koneesta näkyvän. Tämä on se yksi asia, josta FlowLeads muistetaan — ja jota geneerinen CRM ei tee.

## Identiteetti per näkymä (ei toteutusohjeita — suunta)

**Dashboard.** Tumma, monipaneelinen valvomo. Ylhäällä live-tickeri. Vasemmalla signaalivirta, keskellä AI:n "kuumat nyt / aloita näistä" -monitori statusvaloineen, oikealla putken pulssi. Luvut päivittyvät ilman reloadia.

**Pipeline.** Tumma kanban, jossa vaiheet ovat "asemia". Tiiviit kortit: score-mittari + viimeisin signaali heti näkyvissä. Kuumat liidit erottuvat syaanilla. Valvontaruutu, ei pinottu taulu.

**Liidit.** Tiheä datataulukko, tabulaariset luvut, värikoodatut rivit (kylmä→kuuma). Nopea skannaus, vähän tyhjää. Inspectori sivupaneeliin riviä vaihtamatta.

**Yritykset.** "Account-tutka": yrityskortit, joissa liidimäärä, kokonaisarvo ja aktiivisuusviiva. Lajittuu lämpötilan mukaan.

## Mikä tekee tästä ei-geneerisen

Geneerinen CRM on staattinen, vaalea ja passiivinen tietokanta. Mission Control on elävä, tumma ja reaaliaikainen valvomo, jossa AI näkyy telemetriana eikä alaviitteenä. HubSpot tai Pipedrive ei koskaan tunnu komentopöydältä.

## Reunaehdot (yhä voimassa)

- Värit eivät muutu, ei uusia päävärejä.
- Ei navigaation rakenteen, reittien tai endpointtien muutoksia.
- Ei gradientteja, glow-efektejä tai "AI-neon"-tyyliä — premium = hillintä, kontrasti syntyy tumma/vaalea-erosta.
- Yksi näkymä per toteutuskierros, hyväksyntä välissä — kun toteutus joskus aloitetaan.

## Seuraava päätös (kun valmis)

Identiteetti on lukittu suunnaksi. Seuraava askel olisi viedä Mission Control yhteen näkymään kerrallaan (suositus: dashboard ensin, koska se asettaa valvomon sävyn). Ei vielä toteutettu.
