import pytest

from app.search.oikotie_pagination import build_page_items, extract_total_count


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"total": 120}, 120),
        ({"totalCount": 48}, 48),
        ({"count": 10}, 10),
        ({"data": {"total": 72}}, 72),
        ({"meta": {"totalCount": 5}}, 5),
        ({}, 0),
        (None, 0),
    ],
)
def test_extract_total_count(payload, expected):
    assert extract_total_count(payload) == expected


def test_build_page_items_defaults_to_single_page_when_total_unknown():
    items = build_page_items(0)
    assert items == [{"page": 1, "offset": 0, "limit": 24}]


def test_build_page_items_respects_max_pages():
    items = build_page_items(240, max_pages=5)
    assert len(items) == 5
    assert items[0] == {"page": 1, "offset": 0, "limit": 24}
    assert items[-1] == {"page": 5, "offset": 96, "limit": 24}


def test_build_page_items_caps_to_available_pages():
    items = build_page_items(36, max_pages=5)
    assert len(items) == 2
    assert items[1]["offset"] == 24
