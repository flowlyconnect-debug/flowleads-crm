"""Every mainland municipality (kunta) of Finland by maakunta, 2025 division
(Kuhmoinen in Pirkanmaa, Iitti in Päijät-Häme, Heinävesi in Pohjois-Karjala).
The Kohteet filter offers all of them once a maakunta is chosen; place names
that occur in the listings themselves (villages, old municipality names) are
added to the list on top of these. Keys match `constants.FINNISH_REGIONS`."""

from __future__ import annotations

MUNICIPALITIES_BY_REGION: dict[str, tuple[str, ...]] = {
    "Uusimaa": (
        "Askola", "Espoo", "Hanko", "Helsinki", "Hyvinkää", "Inkoo", "Järvenpää", "Karkkila",
        "Kauniainen", "Kerava", "Kirkkonummi", "Lapinjärvi", "Lohja", "Loviisa", "Myrskylä",
        "Mäntsälä", "Nurmijärvi", "Pornainen", "Porvoo", "Pukkila", "Raasepori", "Sipoo",
        "Siuntio", "Tuusula", "Vantaa", "Vihti",
    ),
    "Varsinais-Suomi": (
        "Aura", "Kaarina", "Kemiönsaari", "Koski Tl", "Kustavi", "Laitila", "Lieto", "Loimaa",
        "Marttila", "Masku", "Mynämäki", "Naantali", "Nousiainen", "Oripää", "Paimio", "Parainen",
        "Pyhäranta", "Pöytyä", "Raisio", "Rusko", "Salo", "Sauvo", "Somero", "Taivassalo", "Turku",
        "Uusikaupunki", "Vehmaa",
    ),
    "Satakunta": (
        "Eura", "Eurajoki", "Harjavalta", "Huittinen", "Jämijärvi", "Kankaanpää", "Karvia",
        "Kokemäki", "Merikarvia", "Nakkila", "Pomarkku", "Pori", "Rauma", "Siikainen", "Säkylä",
        "Ulvila",
    ),
    "Kanta-Häme": (
        "Forssa", "Hattula", "Hausjärvi", "Humppila", "Hämeenlinna", "Janakkala", "Jokioinen",
        "Loppi", "Riihimäki", "Tammela", "Ypäjä",
    ),
    "Pirkanmaa": (
        "Akaa", "Hämeenkyrö", "Ikaalinen", "Juupajoki", "Kangasala", "Kihniö", "Kuhmoinen",
        "Lempäälä", "Mänttä-Vilppula", "Nokia", "Orivesi", "Parkano", "Pirkkala", "Punkalaidun",
        "Pälkäne", "Ruovesi", "Sastamala", "Tampere", "Urjala", "Valkeakoski", "Vesilahti", "Virrat",
        "Ylöjärvi",
    ),
    "Päijät-Häme": (
        "Asikkala", "Hartola", "Heinola", "Hollola", "Iitti", "Kärkölä", "Lahti", "Orimattila",
        "Padasjoki", "Sysmä",
    ),
    "Kymenlaakso": ("Hamina", "Kotka", "Kouvola", "Miehikkälä", "Pyhtää", "Virolahti"),
    "Etelä-Karjala": (
        "Imatra", "Lappeenranta", "Lemi", "Luumäki", "Parikkala", "Rautjärvi", "Ruokolahti",
        "Savitaipale", "Taipalsaari",
    ),
    "Etelä-Savo": (
        "Enonkoski", "Hirvensalmi", "Juva", "Kangasniemi", "Mikkeli", "Mäntyharju", "Pertunmaa",
        "Pieksämäki", "Puumala", "Rantasalmi", "Savonlinna", "Sulkava",
    ),
    "Pohjois-Savo": (
        "Iisalmi", "Joroinen", "Kaavi", "Keitele", "Kiuruvesi", "Kuopio", "Lapinlahti", "Leppävirta",
        "Pielavesi", "Rautalampi", "Rautavaara", "Siilinjärvi", "Sonkajärvi", "Suonenjoki", "Tervo",
        "Tuusniemi", "Varkaus", "Vesanto", "Vieremä",
    ),
    "Pohjois-Karjala": (
        "Heinävesi", "Ilomantsi", "Joensuu", "Juuka", "Kitee", "Kontiolahti", "Lieksa", "Liperi",
        "Nurmes", "Outokumpu", "Polvijärvi", "Rääkkylä", "Tohmajärvi",
    ),
    "Keski-Suomi": (
        "Hankasalmi", "Joutsa", "Jyväskylä", "Jämsä", "Kannonkoski", "Karstula", "Keuruu", "Kinnula",
        "Kivijärvi", "Konnevesi", "Kyyjärvi", "Laukaa", "Luhanka", "Multia", "Muurame", "Petäjävesi",
        "Pihtipudas", "Saarijärvi", "Toivakka", "Uurainen", "Viitasaari", "Äänekoski",
    ),
    "Etelä-Pohjanmaa": (
        "Alajärvi", "Alavus", "Evijärvi", "Ilmajoki", "Isojoki", "Isokyrö", "Karijoki", "Kauhajoki",
        "Kauhava", "Kuortane", "Kurikka", "Lappajärvi", "Lapua", "Seinäjoki", "Soini", "Teuva",
        "Vimpeli", "Ähtäri",
    ),
    "Pohjanmaa": (
        "Kaskinen", "Korsnäs", "Kristiinankaupunki", "Kruunupyy", "Laihia", "Luoto", "Maalahti",
        "Mustasaari", "Närpiö", "Pedersöre", "Pietarsaari", "Uusikaarlepyy", "Vaasa", "Vöyri",
    ),
    "Keski-Pohjanmaa": (
        "Halsua", "Kannus", "Kaustinen", "Kokkola", "Lestijärvi", "Perho", "Toholampi", "Veteli",
    ),
    "Pohjois-Pohjanmaa": (
        "Alavieska", "Haapajärvi", "Haapavesi", "Hailuoto", "Ii", "Kalajoki", "Kempele", "Kuusamo",
        "Kärsämäki", "Liminka", "Lumijoki", "Merijärvi", "Muhos", "Nivala", "Oulainen", "Oulu",
        "Pudasjärvi", "Pyhäjoki", "Pyhäjärvi", "Pyhäntä", "Raahe", "Reisjärvi", "Sievi", "Siikajoki",
        "Siikalatva", "Taivalkoski", "Tyrnävä", "Utajärvi", "Vaala", "Ylivieska",
    ),
    "Kainuu": (
        "Hyrynsalmi", "Kajaani", "Kuhmo", "Paltamo", "Puolanka", "Ristijärvi", "Sotkamo",
        "Suomussalmi",
    ),
    "Lappi": (
        "Enontekiö", "Inari", "Kemi", "Kemijärvi", "Keminmaa", "Kittilä", "Kolari", "Muonio",
        "Pelkosenniemi", "Pello", "Posio", "Ranua", "Rovaniemi", "Salla", "Savukoski", "Simo",
        "Sodankylä", "Tervola", "Tornio", "Utsjoki", "Ylitornio",
    ),
}


def region_municipalities(region: str) -> tuple[str, ...]:
    """The municipalities of one maakunta; empty for an unknown name."""
    return MUNICIPALITIES_BY_REGION.get((region or "").strip(), ())
