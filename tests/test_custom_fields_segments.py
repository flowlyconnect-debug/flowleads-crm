import json
from datetime import datetime, timedelta, timezone

import pytest

from app.api.services import create_api_key
from app.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from app.custom_fields.services import CustomFieldService, CustomFieldServiceError
from app.extensions import db
from app.leads.models import Lead, PipelineStage
from app.leads.services import LeadService, get_default_stage
from app.segments.filter_engine import FilterEngineError, apply_segment_filters
from app.segments.models import Segment
from app.segments.relative_dates import is_relative_date_token, parse_relative_date
from app.segments.services import SegmentService, SegmentServiceError
from app.users.services import create_organization, create_user


def _setup_org(app, slug="cf-org"):
    with app.app_context():
        org = create_organization(f"Org {slug}", slug)
        db.session.flush()
        create_user(f"admin-{slug}@test.com", "securepassword1", role="admin", organization_id=org.id)
        other = create_organization(f"Other {slug}", f"{slug}-other")
        db.session.commit()
        return {"org_id": org.id, "other_org_id": other.id}


def _api_key(app, org_id):
    with app.app_context():
        _, full_key = create_api_key(org_id, "test", test_mode=True)
        db.session.commit()
        return full_key


def _headers(key):
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _create_lead(app, org_id, **kwargs):
    with app.app_context():
        stage = get_default_stage(org_id)
        lead = Lead(
            organization_id=org_id,
            email=kwargs.get("email", "lead@example.com"),
            company=kwargs.get("company", "Acme"),
            stage_id=stage.id,
            status="active",
            source="manual",
            score=kwargs.get("score"),
            last_contacted_at=kwargs.get("last_contacted_at"),
        )
        db.session.add(lead)
        db.session.commit()
        return lead.id


# --- Custom field validation ---


def test_invalid_select_option(app):
    ctx = _setup_org(app, "select")
    with app.app_context():
        defn = CustomFieldService.create_definition(
            ctx["org_id"],
            {
                "name": "industry",
                "label": "Industry",
                "field_type": "select",
                "options": ["SaaS", "Retail"],
            },
        )
        db.session.commit()
        with pytest.raises(CustomFieldServiceError) as exc:
            CustomFieldService.validate_value(defn, "Healthcare")
        assert exc.value.code == "invalid_option"


def test_invalid_url(app):
    ctx = _setup_org(app, "url")
    with app.app_context():
        defn = CustomFieldService.create_definition(
            ctx["org_id"],
            {"name": "website_cf", "label": "Site", "field_type": "url"},
        )
        with pytest.raises(CustomFieldServiceError) as exc:
            CustomFieldService.validate_value(defn, "not a valid url!!!")
        assert exc.value.code == "invalid_url"


def test_multiselect_validation(app):
    ctx = _setup_org(app, "multi")
    with app.app_context():
        defn = CustomFieldService.create_definition(
            ctx["org_id"],
            {
                "name": "tags_cf",
                "label": "Tags",
                "field_type": "multiselect",
                "options": ["A", "B"],
            },
        )
        CustomFieldService.validate_value(defn, ["A", "B"])
        with pytest.raises(CustomFieldServiceError) as exc:
            CustomFieldService.validate_value(defn, ["A", "Z"])
        assert exc.value.code == "invalid_option"


def test_custom_field_name_unique_per_org(app):
    ctx = _setup_org(app, "uniq")
    with app.app_context():
        CustomFieldService.create_definition(
            ctx["org_id"],
            {"name": "industry", "label": "Industry", "field_type": "text"},
        )
        with pytest.raises(CustomFieldServiceError) as exc:
            CustomFieldService.create_definition(
                ctx["org_id"],
                {"name": "industry", "label": "Industry 2", "field_type": "text"},
            )
        assert exc.value.code == "duplicate_name"


def test_tenant_isolation_custom_fields(app):
    ctx = _setup_org(app, "tenant-cf")
    with app.app_context():
        defn = CustomFieldService.create_definition(
            ctx["org_id"],
            {"name": "secret", "label": "Secret", "field_type": "text"},
        )
        db.session.commit()
        with pytest.raises(CustomFieldServiceError):
            CustomFieldService.get_definition(defn.id, ctx["other_org_id"])


# --- Relative dates ---


def test_relative_date_token_parsing():
    assert is_relative_date_token("{{now-14d}}")
    fixed = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    result = parse_relative_date("{{now-14d}}", now=fixed)
    assert result == fixed - timedelta(days=14)


def test_relative_date_invalid_token():
    with pytest.raises(ValueError):
        parse_relative_date("now-14d")


def test_relative_date_filter_on_last_contacted(app):
    ctx = _setup_org(app, "rel-date")
    old_id = _create_lead(
        app,
        ctx["org_id"],
        email="old@example.com",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    new_id = _create_lead(
        app,
        ctx["org_id"],
        email="new@example.com",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=2),
    )

    filters = {
        "logic": "AND",
        "conditions": [
            {
                "field": "last_contacted_at",
                "operator": "after",
                "value": "{{now-14d}}",
            }
        ],
    }
    with app.app_context():
        query = apply_segment_filters(Lead.query, ctx["org_id"], filters)
        ids = {row.id for row in query.all()}
    assert new_id in ids
    assert old_id not in ids


# --- Segment filter engine ---


def _seed_scored_leads(app, org_id):
    with app.app_context():
        stage = get_default_stage(org_id)
        high = Lead(
            organization_id=org_id,
            email="high@example.com",
            company="High Co",
            stage_id=stage.id,
            status="active",
            source="n8n",
            score=80,
        )
        low = Lead(
            organization_id=org_id,
            email="low@example.com",
            company="Low Co",
            stage_id=stage.id,
            status="active",
            source="manual",
            score=40,
        )
        db.session.add_all([high, low])
        db.session.commit()
        return high.id, low.id


def test_segment_and_or_nesting(app):
    ctx = _setup_org(app, "nest")
    high_id, low_id = _seed_scored_leads(app, ctx["org_id"])

    filters = {
        "logic": "OR",
        "rules": [
            {"field": "score", "operator": "gte", "value": 75},
            {
                "type": "group",
                "logic": "AND",
                "rules": [
                    {"field": "source", "operator": "eq", "value": "manual"},
                    {"field": "score", "operator": "lt", "value": 50},
                ],
            },
        ],
    }
    with app.app_context():
        query = apply_segment_filters(Lead.query, ctx["org_id"], filters)
        ids = {row.id for row in query.all()}
    assert high_id in ids
    assert low_id in ids


def test_segment_custom_field_filter(app):
    ctx = _setup_org(app, "cf-filter")
    lead_id = _create_lead(app, ctx["org_id"], email="saas@example.com")
    with app.app_context():
        defn = CustomFieldService.create_definition(
            ctx["org_id"],
            {
                "name": "industry",
                "label": "Industry",
                "field_type": "select",
                "options": ["SaaS", "Retail"],
            },
        )
        CustomFieldService.set_value(lead_id, "lead", defn.id, "SaaS", ctx["org_id"])
        db.session.commit()

        filters = {
            "logic": "AND",
            "conditions": [
                {"field": "custom.industry", "operator": "eq", "value": "SaaS"},
            ],
        }
        query = apply_segment_filters(Lead.query, ctx["org_id"], filters)
        ids = {row.id for row in query.all()}
    assert lead_id in ids


def test_segment_lead_count_cache(app):
    ctx = _setup_org(app, "count")
    _seed_scored_leads(app, ctx["org_id"])
    filters = {
        "logic": "AND",
        "conditions": [{"field": "score", "operator": "gte", "value": 70}],
    }
    with app.app_context():
        segment = SegmentService.save("High score", filters, ctx["org_id"])
        db.session.commit()
        assert segment.lead_count_cache == 1
        updated = SegmentService.refresh_counts(ctx["org_id"])
        db.session.commit()
        assert updated >= 0
        segment = db.session.get(Segment, segment.id)
        assert segment.lead_count_cache == 1


def test_tenant_isolation_segments(app):
    ctx = _setup_org(app, "tenant-seg")
    with app.app_context():
        segment = SegmentService.save(
            "Private",
            {"logic": "AND", "conditions": [{"field": "score", "operator": "gte", "value": 1}]},
            ctx["org_id"],
        )
        db.session.commit()
        with pytest.raises(SegmentServiceError):
            SegmentService.get_segment(segment.id, ctx["other_org_id"])


# --- API ---


def test_api_custom_fields_on_lead(client, app):
    ctx = _setup_org(app, "api-cf")
    key = _api_key(app, ctx["org_id"])

    resp = client.post(
        "/api/v1/custom-fields",
        data=json.dumps(
            {
                "name": "employees",
                "label": "Employees",
                "field_type": "number",
            }
        ),
        headers=_headers(key),
    )
    assert resp.status_code == 201

    resp = client.post(
        "/api/v1/leads",
        data=json.dumps(
            {
                "email": "api-cf@example.com",
                "custom_fields": {"employees": 25},
            }
        ),
        headers=_headers(key),
    )
    assert resp.status_code == 201
    data = resp.get_json()["data"]["lead"]
    assert data["custom_fields"]["employees"] == 25


def test_api_list_leads_includes_custom_fields_bulk(client, app):
    ctx = _setup_org(app, "api-bulk")
    key = _api_key(app, ctx["org_id"])
    client.post(
        "/api/v1/custom-fields",
        data=json.dumps({"name": "industry", "label": "Industry", "field_type": "text"}),
        headers=_headers(key),
    )
    client.post(
        "/api/v1/leads",
        data=json.dumps(
            {"email": "bulk1@example.com", "custom_fields": {"industry": "SaaS"}}
        ),
        headers=_headers(key),
    )

    resp = client.get("/api/v1/leads", headers=_headers(key))
    assert resp.status_code == 200
    leads = resp.get_json()["data"]["leads"]
    assert any(l.get("custom_fields", {}).get("industry") == "SaaS" for l in leads)


def test_api_segments_crud_and_leads(client, app):
    ctx = _setup_org(app, "api-seg")
    key = _api_key(app, ctx["org_id"])
    _create_lead(app, ctx["org_id"], email="seg@example.com", score=90)

    create = client.post(
        "/api/v1/segments",
        data=json.dumps(
            {
                "name": "Hot leads",
                "filters": {
                    "logic": "AND",
                    "conditions": [{"field": "score", "operator": "gte", "value": 70}],
                },
            }
        ),
        headers=_headers(key),
    )
    assert create.status_code == 201
    segment_id = create.get_json()["data"]["segment"]["id"]

    leads_resp = client.get(f"/api/v1/segments/{segment_id}/leads", headers=_headers(key))
    assert leads_resp.status_code == 200
    assert leads_resp.get_json()["data"]["pagination"]["total"] >= 1

    other_key = _api_key(app, ctx["other_org_id"])
    forbidden = client.get(f"/api/v1/segments/{segment_id}/leads", headers=_headers(other_key))
    assert forbidden.status_code == 404


def test_filter_engine_unknown_field_raises(app):
    ctx = _setup_org(app, "bad-filter")
    with app.app_context():
        with pytest.raises(FilterEngineError):
            apply_segment_filters(
                Lead.query,
                ctx["org_id"],
                {
                    "logic": "AND",
                    "conditions": [{"field": "not_a_field", "operator": "eq", "value": "x"}],
                },
            )
