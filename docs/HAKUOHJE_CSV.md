# Hakuohje: taloyhtiökohteet FlowLeadsiin (CSV)

Liitä tämä ohje hakuun (esim. ChatGPT, Perplexity tai oma tiedonhaku), kun etsit kohteita FlowLeadsiin.
Kun haun tulos noudattaa tätä ohjetta, tiedoston voi tuoda sellaisenaan sivulla
**Hallinta → Kohdekatalogin tila → Tuo kohteita tiedostosta**, ja jokainen kohde näkyy vain niille
asiakkaille, joille sen toimiala ja maakunta kuuluvat.

## Tehtävä

Etsi suomalaisia taloyhtiöitä (asunto-osakeyhtiöitä), joissa on **tulossa** jokin alla luetelluista
töistä. Palauta tulos yhtenä CSV-taulukkona tämän ohjeen muodossa. Älä palauta muuta tekstiä taulukon
lisäksi.

Mukaan otetaan vain työ, joka on suunniteltu, päätetty, budjetoitu, ehdotettu, mainittu kuntoarviossa
tai PTS:ssä tai muuten vielä tekemättä. **Jo tehtyjä remontteja ei oteta mukaan.**

## Tiedoston muoto

- CSV, erotin **puolipiste** (`;`), merkistö UTF-8, ensimmäinen rivi on otsikkorivi.
- Yksi rivi = yksi taloyhtiö. Sama taloyhtiö vain kerran.
- Jos kentän tekstissä on puolipiste tai rivinvaihto, laita kenttä lainausmerkkeihin (`"..."`).
- Tyhjä tieto jätetään tyhjäksi. Älä kirjoita "ei tiedossa" tai "-".
- Enintään 2000 riviä ja 5 Mt per tiedosto.

## Sarakkeet

| Sarake | Pakollinen | Sisältö |
|---|---|---|
| `taloyhtio` | kyllä* | Taloyhtiön nimi, esim. `As Oy Esimerkkikatu 1` |
| `osoite` | kyllä* | Katuosoite, esim. `Esimerkkikatu 1` |
| `kaupunki` | suositus | Kunta, esim. `Tampere` |
| `maakunta` | kyllä | Täsmälleen jokin alla olevan listan maakunnista |
| `remonttityyppi` | kyllä | Toimiala täsmälleen alla olevan listan nimellä. Useampi erotetaan merkillä `\|` |
| `ajankohta` | suositus | Milloin työ on tulossa, esim. `2027`, `PTS 2026–2028`, `suunnitteilla` |
| `teksti` | suositus | Lyhyt perustelu tai lainaus lähteestä: mitä on tulossa ja mistä se ilmenee |
| `isannoitsija` | suositus | Isännöitsijä tai isännöintiyritys |
| `isannoitsija_puh` | suositus | Isännöitsijän puhelinnumero |
| `isannoitsija_email` | suositus | Isännöitsijän sähköposti |
| `rakennusvuosi` | valinnainen | Rakennuksen valmistumisvuosi, esim. `1974` |
| `talotyyppi` | valinnainen | `Kerrostalo`, `Rivitalo`, `Paritalo`, `Omakotitalo` tai `Muu` |
| `url` | suositus | Linkki lähteeseen, josta tieto löytyi |
| `varmuus` | valinnainen | 0–100: kuinka varmasti lähde kertoo juuri tästä tulevasta työstä. Tyhjä = 100 |

\* Vähintään toinen: `taloyhtio` tai `osoite`. Mieluiten molemmat.

Yhteystiedot ovat tärkeitä: ilman isännöitsijän tietoja asiakas ei pääse soittamaan kohteeseen.
Käytä vain julkisesti saatavilla olevia isännöitsijän tai isännöintiyrityksen työyhteystietoja.

## Toimialat (sarake `remonttityyppi`)

Käytä **täsmälleen** näitä nimiä. Jos työ ei kuulu mihinkään näistä, jätä kohde pois.

| # | Nimi | Mitä tähän kuuluu |
|---|---|---|
| 1 | `Lukitus, kulunvalvonta ja oviautomatiikka` | Lukitus, lukkojen tai avainten uusiminen, sarjoitus, sähkölukitus, iLOQ, kulunvalvonta, kulkuoikeudet, ovipuhelinjärjestelmä, oviautomatiikka ja automaattiovet. EI kameravalvontaa eikä murtohälytintä. |
| 2 | `Ovi- ja ikkunaremontit` | Ikkunoiden, parvekeovien, ulko-ovien tai porrashuoneen ovien uusiminen tai kunnostus. |
| 3 | `Julkisivu- ja parvekeurakointi` | Julkisivun, rappauksen, elementtisaumojen tai parvekkeiden korjaus, parvekelasitus. Pelkät ikkunat kuuluvat tyyppiin Ovi- ja ikkunaremontit. |
| 4 | `Putki- ja viemäriurakointi` | Linjasaneeraus, putkiremontti, viemärien, käyttövesi- tai vesijohtoputkien uusiminen, viemärien sukitus tai pinnoitus. |
| 5 | `Ilmanvaihtohuolto ja IV-urakointi` | Ilmanvaihtokanavien nuohous tai puhdistus, ilmanvaihdon säätö, IV-koneiden tai huippuimurien uusiminen, ilmanvaihtojärjestelmän remontti. |
| 6 | `Kattourakointi` | Vesikaton, katteen, yläpohjan, räystäiden, kattokaivojen tai kattoturvatuotteiden uusiminen tai korjaus. |
| 7 | `Paloturvallisuus` | Erityisesti taloyhtiön palovaroittimien asennus tai uusinta; myös palo-ovet, palokatkot, savunpoisto ja paloilmoitin. |
| 8 | `Piha-, salaoja- ja hulevesiurakointi` | Salaojien, sadevesi- tai hulevesijärjestelmän, sokkelin vedeneristyksen, pihan, pihakannen, asfaltoinnin tai piha-alueen kuivatuksen remontti. |
| 9 | `Märkätilasaneeraus ja vedeneristys` | Kylpyhuoneiden, saunaosaston, pesutuvan tai muiden märkätilojen saneeraus ja vedeneristys. |
| 10 | `Sähköautojen latausratkaisut` | Sähköautojen latauspisteet tai latausvalmius taloyhtiön pysäköintipaikoille, myös sitä varten tehtävä sähköliittymän tai pysäköintialueen sähköistyksen uusiminen. |
| 11 | `Lämmitys- ja energiaremontit` | Lämmönjakokeskus, maalämpö, poistoilmalämpöpumppu, kaukolämmön laitteet, patteriverkoston perussäätö tai uusiminen, aurinkopaneelit ja muut energiatehokkuushankkeet. |
| 12 | `Hissihuolto ja hissien modernisointi` | Hissin uusiminen, modernisointi, peruskorjaus tai jälkiasennus. |

Jos samassa taloyhtiössä on tulossa useampi työ, kirjoita toimialat samaan soluun pystyviivalla
erotettuna, esim. `Kattourakointi|Julkisivu- ja parvekeurakointi`.

## Maakunnat (sarake `maakunta`)

Uusimaa, Varsinais-Suomi, Satakunta, Kanta-Häme, Pirkanmaa, Päijät-Häme, Kymenlaakso, Etelä-Karjala, Etelä-Savo, Pohjois-Savo, Pohjois-Karjala, Keski-Suomi, Etelä-Pohjanmaa, Pohjanmaa, Keski-Pohjanmaa, Pohjois-Pohjanmaa, Kainuu, Lappi.

Kirjoita maakunta, ei kaupunkia. Esimerkiksi Tampere → `Pirkanmaa`, Helsinki → `Uusimaa`,
Turku → `Varsinais-Suomi`, Oulu → `Pohjois-Pohjanmaa`.

## Säännöt

1. Vain tulevat työt. Jo valmistunut remontti ei ole kohde.
2. Älä keksi mitään. Jos tietoa ei löydy lähteestä, jätä kenttä tyhjäksi.
3. Jokaisella rivillä pitää olla lähde (`url`) ja lyhyt perustelu (`teksti`).
4. Toimiala ja maakunta ratkaisevat, kenelle kohde näytetään. Väärä toimiala vie kohteen väärälle
   asiakkaalle, joten epävarma kohde jätetään pois tai sille annetaan pieni `varmuus`.
5. Älä kerää tietoa tavoilla, jotka rikkovat lähdesivuston käyttöehtoja tai kiertävät sen suojauksia.

## Esimerkki

```csv
taloyhtio;osoite;kaupunki;maakunta;remonttityyppi;ajankohta;teksti;isannoitsija;isannoitsija_puh;isannoitsija_email;rakennusvuosi;talotyyppi;url;varmuus
As Oy Esimerkkikatu 1;Esimerkkikatu 1;Tampere;Pirkanmaa;Putki- ja viemäriurakointi;2027;Yhtiökokous on päättänyt linjasaneerauksen hankesuunnittelusta, toteutus arviolta 2027.;Isännöinti Esimerkki Oy;040 123 4567;isannointi@esimerkki.fi;1974;Kerrostalo;https://esimerkki.fi/lahde;90
As Oy Mallitie 5;Mallitie 5;Espoo;Uusimaa;Kattourakointi|Julkisivu- ja parvekeurakointi;PTS 2026–2028;"PTS:ssä vesikaton uusiminen 2026; julkisivun saumaus 2028.";Mallin Isännöinti Oy;;;1988;Rivitalo;https://esimerkki.fi/lahde2;75
```

## Tarkistuslista ennen tuontia

- Otsikkorivi on mukana ja erotin on puolipiste.
- Jokaisella rivillä on `maakunta` ja `remonttityyppi` listojen mukaisilla nimillä.
- Jokaisella rivillä on `taloyhtio` tai `osoite`.
- Yhtään jo tehtyä remonttia ei ole mukana.
- Sama taloyhtiö ei ole tiedostossa kahdesti.

## Tuonti FlowLeadsiin

1. Avaa **Hallinta → Kohdekatalogin tila** (`/admin/catalogue`).
2. Valitse tiedosto ja paina **Tuo kohteet**. Jos koko tiedosto on yhtä toimialaa tai yhtä maakuntaa
   eikä siinä ole näitä sarakkeita, valitse ne lomakkeelta koko tiedostolle.
3. Sivu kertoo, mille asiakkaille kohteet menivät, montako ei osunut kenellekään ja mitkä rivit
   ohitettiin virheen takia (rivinumeroineen).

Saman tiedoston voi tuoda uudelleen: olemassa olevat kohteet päivittyvät, kaksoiskappaleita ei synny.
