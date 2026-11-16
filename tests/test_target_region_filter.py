"""Kohteet filter bar: every maakunta is offered, and a chosen maakunta offers
all of its municipalities plus the place names its targets carry."""

from app.search.constants import FINNISH_REGIONS
from app.search.municipalities import MUNICIPALITIES_BY_REGION, region_municipalities
from app.listings import target_services as targets


def test_every_region_has_its_municipalities():
    assert list(MUNICIPALITIES_BY_REGION) == list(FINNISH_REGIONS)
    names = [name for group in MUNICIPALITIES_BY_REGION.values() for name in group]
    assert len(names) == len(set(names)) == 293  # mainland Finland
    assert "Tampere" in region_municipalities("Pirkanmaa")
    assert "Kuhmoinen" in region_municipalities("Pirkanmaa")
    assert "Iitti" in region_municipalities("Päijät-Häme")
    assert region_municipalities("Ei tällaista") == ()


def test_filter_options_offer_all_regions_and_municipalities(app):
    with app.app_context():
        options = targets.target_filter_options(organization_id=999999)
        assert options["regions"][: len(FINNISH_REGIONS)] == list(FINNISH_REGIONS)
        assert options["cities"] == []  # no region chosen, no targets

        lappi = targets.target_filter_options(999999, filters={"region": "Lappi"})["cities"]
        assert lappi == sorted(MUNICIPALITIES_BY_REGION["Lappi"], key=str.lower)
        assert lappi[0] == "Enontekiö"


def test_merge_place_names_keeps_villages_and_drops_duplicates():
    merged = targets._merge_place_names(("Espoo", "Helsinki"), ["helsinki", " Nuuksio ", "Espoo"])
    assert merged == ["Espoo", "Helsinki", "Nuuksio"]


def test_sorting_puts_finnish_letters_last():
    merged = targets._merge_place_names(("Ähtäri", "Alavus", "Öja", "Vimpeli"))
    assert merged == ["Alavus", "Vimpeli", "Ähtäri", "Öja"]
