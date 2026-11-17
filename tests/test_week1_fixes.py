"""Research round 11/2026, week 1: automation webhooks cannot reach the
internal network (SSRF), saved-search links are URL-encoded, and the
text-type backfill reaches every hinted listing."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from app.automations.models import AutomationAction
from app.automations.services import AutomationEngine, AutomationService, AutomationServiceError
from app.extensions import db
from app.listings import target_services as targets
from app.listings import text_types
from app.listings.models import Listing, ListingRenovation
from app.notifications.models import Notification
from tests.test_automations import _create_automation, _setup_org
from tests.test_insights import NOW, _org, _target


def _webhook(url):
    return [{"action_type": "send_webhook", "action_config": {"url": url, "method": "POST", "body_template": "{}"}}]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",  # not https
        "https://127.0.0.1/hook",
        "https://169.254.169.254/latest/meta-data/",
        "https://10.0.0.5/hook",
        "https://localhost/hook",
        "https://render-db.internal/hook",
    ],
)
def test_automation_webhook_to_internal_address_is_refused_on_save(app, url):
    ctx = _setup_org(app, "ssrf-save")
    with app.app_context():
        with pytest.raises(AutomationServiceError) as error:
            _create_automation(ctx["org_id"], ctx["admin_id"], actions=_webhook(url))
        assert error.value.code == "validation_error"


@patch("app.automations.services.requests.request")
def test_stored_internal_url_is_refused_at_send_time(mock_request, app):
    """An action saved before the check existed (or a name that now resolves
    to a private address) never reaches the network."""
    ctx = _setup_org(app, "ssrf-send")
    with app.app_context():
        automation = _create_automation(ctx["org_id"], ctx["admin_id"], actions=[])
        action = AutomationAction(
            automation_id=automation.id,
            order_index=0,
            action_type="send_webhook",
            action_config={"url": "https://hooks.example.test/x", "method": "POST", "body_template": "{}"},
        )
        db.session.add(action)
        db.session.commit()
        with patch("app.webhooks.url_safety._resolve", return_value=["10.1.2.3"]):
            ok, err = AutomationEngine.execute_action(action, None, {}, organization_id=ctx["org_id"])
        assert ok is False and "sisäverkon" in err
        mock_request.assert_not_called()


def test_a_hostname_resolving_to_a_private_address_is_refused():
    from app.webhooks.url_safety import UnsafeWebhookUrl, check_webhook_url

    with patch("app.webhooks.url_safety._resolve", return_value=["93.184.216.34", "10.1.2.3"]):
        with pytest.raises(UnsafeWebhookUrl):
            check_webhook_url("https://intranet.example.com/hook")
    with patch("app.webhooks.url_safety._resolve", return_value=["93.184.216.34"]):
        assert check_webhook_url("https://hooks.example.com/x") == "https://hooks.example.com/x"


@patch("app.webhooks.url_safety._resolve", return_value=["93.184.216.34"])
@patch("app.automations.services.requests.request")
def test_a_redirect_is_not_followed(mock_request, _resolve, app):
    mock_request.return_value.status_code = 302
    ctx = _setup_org(app, "ssrf-redirect")
    with app.app_context():
        automation = _create_automation(ctx["org_id"], ctx["admin_id"], actions=_webhook("https://hooks.example.com/x"))
        db.session.commit()
        action = automation.actions[0]
        ok, err = AutomationEngine.execute_action(action, None, {}, organization_id=ctx["org_id"])
        assert ok is False and err == "Webhook HTTP 302"
        assert mock_request.call_args.kwargs["allow_redirects"] is False


def test_saved_search_link_is_url_encoded(app):
    with app.app_context():
        org, user, profile = _org("encode")
        params = {"city": "Hämeenlinna", "search": "A & B", "renovation": ""}
        targets.save_search(org.id, user.id, "Häme", params, now=NOW + timedelta(minutes=1))
        db.session.commit()
        listing_city = "Hämeenlinna"
        _target(org, profile, 7, city=listing_city, company="As Oy A & B", created=NOW + timedelta(minutes=5))
        targets.notify_saved_searches(now=NOW + timedelta(minutes=6))
        note = Notification.query.filter_by(user_id=user.id).one()
        assert note.link in (
            "/targets?search=A+%26+B&city=H%C3%A4meenlinna",
            "/targets?city=H%C3%A4meenlinna&search=A+%26+B",
        )


def test_backfill_reaches_listings_past_the_first_page(app, monkeypatch):
    """Listings that match the SQL hint but not the pattern used to fill the
    first page forever; the cursor moves past them."""
    monkeypatch.setattr(text_types, "_BACKFILL_CURSOR", {})
    with app.app_context():
        for i in range(3):  # hinted ("IV-") but not a ventilation renovation
            db.session.add(
                Listing(source="import", external_id=f"x{i}", maakunta="Uusimaa", analysis_status="done",
                        tulevat_remontit="Aktiivi-ikäisten kerho kokoontuu tiistaisin.")
            )
        real = Listing(source="import", external_id="real", maakunta="Uusimaa", analysis_status="done",
                       tulevat_remontit="Ilmanvaihdon puhdistus ja säätö tehdään 2027.")
        db.session.add(real)
        db.session.commit()

        changed = text_types.backfill_text_types(limit=2)  # page 1: two non-matching rows
        changed += text_types.backfill_text_types(limit=2)  # page 2 reaches the real one
        assert changed >= 1
        assert "Ilmanvaihdon puhdistus ja säätö" in {r.remonttityyppi for r in ListingRenovation.query.filter_by(listing_id=real.id)}
