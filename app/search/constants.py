"""Search profile UI choices (remonttityypit, maakunnat, schedules)."""

from __future__ import annotations

# Toimialat / urakkatyypit, joihin kohteen tuleva remontti luokitellaan. Avain on
# kannassa (search_profiles, listing_renovations) ja n8n Analyzerin promptissa
# käytetty nimi; arvo kertoo AI:lle (ja käyttäjälle), mitä tyyppiin kuuluu.
REMONTTITYYPPI_KUVAUKSET: dict[str, str] = {
    "Lukitus, kulunvalvonta ja oviautomatiikka": (
        "lukitus, lukkojen tai avainten uusiminen, sarjoitus, sähkölukitus, iLOQ, kulunvalvonta, "
        "kulkuoikeudet, ovipuhelinjärjestelmä, oviautomatiikka ja automaattiovet. EI kameravalvontaa "
        "eikä murtohälytintä."
    ),
    "Ovi- ja ikkunaremontit": "ikkunoiden, parvekeovien, ulko-ovien tai porrashuoneen ovien uusiminen tai kunnostus.",
    "Julkisivu- ja parvekeurakointi": (
        "julkisivun, rappauksen, elementtisaumojen tai parvekkeiden korjaus, parvekelasitus. "
        "Pelkät ikkunat kuuluvat tyyppiin Ovi- ja ikkunaremontit."
    ),
    "Putki- ja viemäriurakointi": (
        "linjasaneeraus, putkiremontti, viemärien, käyttövesi- tai vesijohtoputkien uusiminen, "
        "viemärien sukitus tai pinnoitus."
    ),
    "Ilmanvaihtohuolto ja IV-urakointi": (
        "ilmanvaihtokanavien nuohous tai puhdistus, ilmanvaihdon säätö, IV-koneiden tai huippuimurien "
        "uusiminen, ilmanvaihtojärjestelmän remontti."
    ),
    "Kattourakointi": "vesikaton, katteen, yläpohjan, räystäiden, kattokaivojen tai kattoturvatuotteiden uusiminen tai korjaus.",
    "Paloturvallisuus": (
        "erityisesti taloyhtiön palovaroittimien asennus tai uusinta; myös palo-ovet, palokatkot, "
        "savunpoisto ja paloilmoitin."
    ),
    "Piha-, salaoja- ja hulevesiurakointi": (
        "salaojien, sadevesi- tai hulevesijärjestelmän, sokkelin vedeneristyksen, pihan, pihakannen, "
        "asfaltoinnin tai piha-alueen kuivatuksen remontti."
    ),
    "Märkätilasaneeraus ja vedeneristys": (
        "kylpyhuoneiden, saunaosaston, pesutuvan tai muiden märkätilojen saneeraus ja vedeneristys."
    ),
    "Sähköautojen latausratkaisut": (
        "sähköautojen latauspisteet tai latausvalmius taloyhtiön pysäköintipaikoille, myös sitä varten "
        "tehtävä sähköliittymän tai pysäköintialueen sähköistyksen uusiminen."
    ),
    "Lämmitys- ja energiaremontit": (
        "lämmönjakokeskus, maalämpö, poistoilmalämpöpumppu, kaukolämmön laitteet, patteriverkoston "
        "perussäätö tai uusiminen, aurinkopaneelit ja muut energiatehokkuushankkeet."
    ),
    "Hissihuolto ja hissien modernisointi": "hissin uusiminen, modernisointi, peruskorjaus tai jälkiasennus.",
}

REMONTTITYYPIT: tuple[str, ...] = tuple(REMONTTITYYPPI_KUVAUKSET)

# Vanha nimi -> nykyinen nimi. Käytössä tuonnissa (vanhat CSV:t) ja
# migraatiossa g5b6c7d8e9f0. Vanhoille tyypeille "Sähkö- ja
# tietoliikennejärjestelmät", "Huoneistoremontit" ja "Muu remontti" ei ole
# vastinetta.
VANHAT_REMONTTITYYPIT: dict[str, str] = {
    "Lukkoremontti / kulunvalvonta": "Lukitus, kulunvalvonta ja oviautomatiikka",
    "Putkiremontti": "Putki- ja viemäriurakointi",
    "Lämmitysjärjestelmän remontti": "Lämmitys- ja energiaremontit",
    "Julkisivuremontti": "Julkisivu- ja parvekeurakointi",
    "Katto- ja vesikattoremontti": "Kattourakointi",
}

# Talotyypit, joihin kohde luokitellaan (Oikotien kortti / AI-analyysi).
BUILDING_TYPES: tuple[str, ...] = (
    "Kerrostalo",
    "Rivitalo",
    "Paritalo",
    "Omakotitalo",
    "Muu",
)

ALL_FINLAND_REGION = "Koko Suomi"

FINNISH_REGIONS: tuple[str, ...] = (
    "Uusimaa",
    "Varsinais-Suomi",
    "Satakunta",
    "Kanta-Häme",
    "Pirkanmaa",
    "Päijät-Häme",
    "Kymenlaakso",
    "Etelä-Karjala",
    "Etelä-Savo",
    "Pohjois-Savo",
    "Pohjois-Karjala",
    "Keski-Suomi",
    "Etelä-Pohjanmaa",
    "Pohjanmaa",
    "Keski-Pohjanmaa",
    "Pohjois-Pohjanmaa",
    "Kainuu",
    "Lappi",
)

SEARCH_SCHEDULE_LABELS: dict[str, str] = {
    "daily": "Päivittäin",
    "weekly": "Viikoittain",
    "manual": "Manuaalinen",
}

SEARCH_JOB_STATUS_LABELS: dict[str, str] = {
    "pending": "Odottaa",
    "running": "Käynnissä",
    "completed": "Valmis",
    "failed": "Epäonnistui",
}
