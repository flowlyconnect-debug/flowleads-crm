"""Sopivuus: the Kohteet page sorts by a score that learns from what the
organization has moved to leads and hidden."""

from app.extensions import db
from app.listings import fit_services as fit
from app.listings import target_services as targets
from app.listings.models import ListingMatch
from tests.test_targets import LOCK, PIPE, _listing, _login, _match_ids, _org, _profile


def _ids_by_name(org):
    return {m.listing.taloyhtio: m.id for m in ListingMatch.query.filter_by(organization_id=org.id, status="pending")}


def test_contact_info_and_certainty_raise_the_score(app):
    with app.app_context():
        org, _ = _org("fit-base")
        _profile(org)
        _listing(1, taloyhtio="As Oy Varma", renovations=((LOCK, 95),), isannoitsija_puh="040 1", isannoitsija_email="a@b.fi")
        _listing(2, taloyhtio="As Oy Epävarma", renovations=((LOCK, 61),))
        page = targets.list_targets(org.id)  # default sort: Sopivimmat ensin
        assert [m.listing.taloyhtio for m in page.items] == ["As Oy Varma", "As Oy Epävarma"]
        scores = fit.score_matches(page.items, fit.learn_preferences(org.id))
        best, worst = (scores[m.id] for m in page.items)
        assert best[0] > worst[0]
        assert "Puhelin ja sähköposti" in best[1]


def test_history_moves_liked_types_and_regions_up(app):
    with app.app_context():
        org, _ = _org("fit-learn")
        _profile(org, rtype=LOCK)
        _profile(org, rtype=PIPE, name="Putket")
        # history: pipe renovations in Uusimaa moved to leads, lock ones hidden
        for n in range(3):
            _listing(100 + n, taloyhtio=f"As Oy Putki {n}", renovations=((PIPE, 80),))
            _listing(200 + n, taloyhtio=f"As Oy Lukko {n}", renovations=((LOCK, 80),))
        ids = _ids_by_name(org)
        targets.transfer_targets(org.id, [ids[f"As Oy Putki {n}"] for n in range(3)])
        targets.dismiss_targets(org.id, [ids[f"As Oy Lukko {n}"] for n in range(3)])
        db.session.commit()

        # two new targets that are otherwise equal
        _listing(1, taloyhtio="As Oy Uusi Lukko", renovations=((LOCK, 80),))
        _listing(2, taloyhtio="As Oy Uusi Putki", renovations=((PIPE, 80),))
        prefs = fit.learn_preferences(org.id)
        assert prefs.decisions == 6
        page = targets.list_targets(org.id)
        assert [m.listing.taloyhtio for m in page.items] == ["As Oy Uusi Putki", "As Oy Uusi Lukko"]
        scores = fit.score_matches(page.items, prefs)
        pipe, lock = (scores[m.id] for m in page.items)
        assert pipe[0] > lock[0]
        assert "Viet usein liideiksi: Putkiremontti" in pipe[1]
        assert "Piilotat usein: " + LOCK in lock[1]


def test_newest_sort_is_still_available_and_unknown_sort_falls_back(app):
    with app.app_context():
        org, _ = _org("fit-sorts")
        _profile(org)
        _listing(1, taloyhtio="As Oy Eka")
        _listing(2, taloyhtio="As Oy Toka", isannoitsija_puh="040 1")
        assert targets.list_targets(org.id, filters={"sort": "newest"}).total == 2
        page = targets.list_targets(org.id, filters={"sort": "nonsense"})
        assert page.items[0].listing.taloyhtio == "As Oy Toka"


def test_list_page_paginates_like_the_query_pagination():
    page = fit.ListPage(list(range(30)), 2, 25)
    assert (page.total, page.pages, page.items, page.has_prev, page.has_next) == (30, 2, list(range(25, 30)), True, False)
    assert page.prev_num == 1 and page.next_num is None
    empty = fit.ListPage([], 1, 25)
    assert empty.total == 0 and empty.pages == 0 and not empty.has_next


def test_targets_page_shows_the_score(app, client):
    with app.app_context():
        org, user = _org("fit-page")
        _profile(org)
        _listing(1, taloyhtio="As Oy Pisteet", isannoitsija_puh="040 1", isannoitsija_email="a@b.fi")
        email = user.email
    _login(client, email)
    html = client.get("/targets").get_data(as_text=True)
    assert "Sopivuus " in html and "Puhelin ja sähköposti" in html
    assert '<option value="" selected>Sopivimmat ensin</option>' in html
