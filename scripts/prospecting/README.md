# FlowLeads-prospektointi (oma myynti)

`crawl_prospects.py` etsii korjausurakoitsijoita, joille FlowLeadsia voi tarjota, ja kerää jokaiselle
nimen, Y-tunnuksen, yhteyshenkilön, puhelimen ja sähköpostin. Rivi hyväksytään vain, jos kaikki viisi löytyvät.

## Mistä tiedot tulevat

1. **PRH / YTJ avoin data** (`avoindata.prh.fi`): yritykset päätoimialan mukaan (LVI 4322, sähkö 4321,
   katto 4391, maalaus 4334, eristys/muu asennus 4329, muu erikoistunut 4399, viimeistely 4339,
   rakentaminen 4120, rakennuspuusepät 4332). Mukaan vain toimivat Oy/Ky/Ay/osuuskunnat, ei konkurssi- tai
   selvitystiloja. Toiminimet jätetään pois, koska toiminimi on luonnollinen henkilö ja sähköinen
   suoramarkkinointi hänelle vaatisi ennakkoluvan.
2. **Verkkosivu**: PRH:n www-kenttä tai nimestä johdettu domain (`.fi`/`.com`), tarkistetaan DNS:stä.
   Arvattu sivu kelpaa vain, jos siinä on yrityksen Y-tunnus tai nimi (ja kotikunta).
3. **Yhteystiedot**: etusivu ja korkeintaan viisi yhteystieto-/meistä-sivua (robots.txt huomioidaan):
   `mailto:`/`tel:`-linkit, tekstin osoitteet (myös `(at)`-muoto), suomalaiset numerot, nimi tittelin
   vierestä (toimitusjohtaja > yrittäjä/omistaja > myyntipäällikkö ...) tai `etunimi.sukunimi@`-osoitteesta.
   Sähköpostiksi valitaan yhteyshenkilön oma osoite, jos sellainen löytyy, muuten info@/myynti@.

## Ei vielä kontaktoitu

- `--exclude tiedosto`: domainit tai sähköpostit, yksi per rivi, jätetään pois jo crawlatessa.
- Lopuksi tulos tarkistetaan Gmailia vasten (`gmail_dedupe.py batches` tekee kyselyt
  `{to:domain from:domain}`). Osumat merkitään tiedostoon ja `gmail_dedupe.py finalize` kirjoittaa
  lopullisen listan ilman niitä.
- Sama domain usealla Y-tunnuksella (konserni) otetaan vain kerran.

## Ajo

```bash
pip install httpx beautifulsoup4
python scripts/prospecting/crawl_prospects.py --target 1500 --out exports/prospektit --exclude kontaktoidut.txt
```

Ajo jatkaa siitä mihin jäi (`exports/prospektit/state.json`, PRH-välimuisti). Tulokset:
`exports/prospektit/prospektit_raw.jsonl` ja `prospektit_kaikki.csv` (puolipiste-CSV, avautuu Excelissä).
`exports/prospektit*` on `.gitignore`ssa: henkilötietoja ei viedä repoon.

## Huomioita

- B2B-sähköpostimarkkinointi yritysosoitteisiin on sallittua, kun viesti liittyy vastaanottajan työhön
  ja jokaisessa viestissä on helppo kieltomahdollisuus. Kirjaa kiellot ja poista ne listalta.
- Puhelinnumerot normalisoidaan muotoon `+358...`. Tarkista numero ennen soittoa: sivuilla voi olla vanhaa tietoa.
