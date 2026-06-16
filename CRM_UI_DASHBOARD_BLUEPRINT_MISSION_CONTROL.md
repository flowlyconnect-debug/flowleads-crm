# FlowLeads — Dashboard-blueprint: Mission Control

> Status: **HYVÄKSYTTY rakenne — toteutuksen lähtöpiste.** Ei koodia, ei CSS:ää, ei backend-muutoksia vielä.
> Toteutussuunnitelma: `CRM_UI_DASHBOARD_TOTEUTUSSUUNNITELMA.md`.
> Identiteetti: Mission Control (ks. `CRM_UI_IDENTITEETTI_MISSION_CONTROL.md`).
> Päivätty: 2026-06-16.

## Rautalankamalli (ASCII)

```
┌──────────────────────────────────────────────────────────────┐
│ TELEMETRIA-VIRTA — liikkuva live-ticker (uudet › AI › avatut) │  ylhäällä
├──────────────────────────────────────────────────────────────┤
│ Komento-otsikko: "Hei Matias · ti 16.6 · 3 odottaa"  [org][Rap]│
├──────────────┬──────────────┬──────────────┬──────────────────┤
│   KPI Uudet  │  KPI Kuumat  │ KPI Tehtävät │  KPI Pipeline-arvo │  KPI-band (tumma)
├──────────────┴──────────────┴───────┬──────┴──────────────────┤
│                                      │  OIKEA · ylä            │
│   AI-MONITORI  (SANKARI)             │  Putken pulssi          │
│   "Aloita näistä"                    │                         │
│   1 · kuka › mitä › miksi › [Avaa]   ├─────────────────────────┤
│   2 · kuka › mitä › miksi › [Avaa]   │  OIKEA · ala            │
│   3 · kuka › mitä › miksi › [Avaa]   │  AI-pulssi (signaalit)  │
├──────────────────────────────────────┴─────────────────────────┤
│ LIVE-AKTIVITEETTI — raaka tapahtumaloki (matalin prioriteetti) │  alhaalla
└──────────────────────────────────────────────────────────────┘
```

## 1. Rakenne

- **Ylhäällä:** telemetria-virta (ohut full-width ticker) + komento-otsikko (tervehdys, päivä, tilamerkki "n8n syöttää", org-valinta, Raportit).
- **Keskellä:** KPI-band (4 tummaa mittaria) → sen alla pääruudukko: leveä vasen/keski + kapea oikea.
- **Oikealla:** kapea pystysarake — putken pulssi (ylä), AI-pulssi (ala).
- **Alhaalla:** live-aktiviteetti full-width, matalin hierarkiassa.
- **Sankarielementti:** keskialueen AI-monitori ("Aloita näistä"). Suurin ja ainoa korostettu paneeli.

## 2. Komponenttien roolit

- **AI-monitori** — aivojen ulostulo. AI:n järjestämä toimintolista: *kuka › mitä › miksi nyt* + suora toiminto. Sankari.
- **Telemetria-virta** — allekirjoituselementti. Ohut, jatkuvasti päivittyvä signaalinauha ylhäällä; tekee n8n+AI-koneen näkyväksi. Korkein abstraktiotaso.
- **Putken pulssi** — "missä mennään" -mittari. Tiivistetty putken terveys: vaiheet, lukumäärät, summat, riskidiilit. Ei kanban.
- **Live-aktiviteetti** — raaka kronologinen tapahtumaloki (sähköposti lähti, vaihe vaihtui). Audit-näkymä, matalin prioriteetti.
- **KPI-alue** — "vitaalit". 4 tummaa instrumenttia, tabulaariset luvut, pieni trendi/status kunkin alla.

Kolme virtaa eri korkeudella, ei päällekkäisiä: ticker (luvut liikkeessä) › AI-pulssi (tulkitut signaalit) › live-aktiviteetti (raaka loki).

## 3. Layout & tärkeysjärjestys

- **Vasen/keski** leveä (~2/3): AI-monitori, painavin.
- **Oikea** kapea (~1/3): putken pulssi (ylä) + AI-pulssi (ala).
- **Ylä/ala** full-width-nauhat: telemetria + otsikko / live-aktiviteetti.
- **Prioriteetti:** 1) AI-monitori, 2) KPI-band, 3) putken pulssi, 4) telemetria-virta, 5) AI-pulssi, 6) live-aktiviteetti.

## 4. Nykyisten elementtien muutokset

| Nykyinen elementti | Mitä tapahtuu |
| --- | --- |
| 4 KPI-korttia | **Säilyy + muuttuu** — samat mittarit, litteistä tummaksi instrumentti-bandiksi, tabulaariset luvut, live-päivitys. |
| AI:n ehdottama järjestys tänään | **Säilyy + nousee sankariksi** (AI-monitori). Ranking-numerot, syy-rivi, suora toiminto. |
| AI-pulssi | **Säilyy + siirtyy + tarkentuu** — oikeaan sarakkeeseen; vain AI:n tulkitsemat signaalit, ei raakaa lokia. |
| Live-aktiviteetti | **Säilyy + siirtyy alas** — raaka kronologinen loki, matalin prioriteetti. |
| Org-valinta + Raportit | **Säilyy ennallaan** — komento-otsikkoon kompaktina, toiminta ei muutu. |
| Hälytysrivi ("X odottaa") | **Yhdistetään telemetria-virtaan** — hälytykset osaksi ylälaidan nauhaa. Ei uutta backend-tarvetta. |

## 5. Mitä EI saa tehdä

- Ei värimuutoksia, ei uusia päävärejä — kontrasti vain tumma/vaalea-erosta.
- Ei uusia JS-kirjastoja.
- Ei backend-logiikan muutoksia (sama data, sama reitti).
- Ei neon/sci-fi-tyyliä — tumma hillitty perussävy, syaania vain pieninä signaaleina.
- Ei liian tiheää tai sekavaa — kolme virtaa selvästi eri rooleissa, yksi sankari per näkymä.
- Ei toteutusta vielä.

## Seuraava askel (kun pyydetään)

Viedään blueprint koodiin yhtenä näkymänä (dashboard), hyväksyntä välissä. Ei vielä aloitettu.
