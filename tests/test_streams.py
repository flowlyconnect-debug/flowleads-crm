from datetime import datetime, timedelta, timezone

from app.ai.services import apply_score_routing
from app.extensions import db
from app.leads.models import Lead, LeadStream, PipelineStage
from app.streams.services import LeadStreamService, StreamHealthService
from app.users.services import create_organization


def _org_with_stages(slug: str):
    org = create_organization(f"Org {slug}", slug)
    db.session.flush()
    stages = (
        PipelineStage.query.filter_by(organization_id=org.id)
        .order_by(PipelineStage.order_index.asc())
        .all()
    )
    return org, stages


def test_lead_stream_matching(app):
    with app.app_context():
        org, stages = _org_with_stages("stream-match")
        db.session.add_all(
            [
                LeadStream(
                    organization_id=org.id,
                    name="LinkedIn",
                    source_match="linkedin",
                    priority=10,
                    pipeline_stage_id=stages[1].id,
                ),
                LeadStream(
                    organization_id=org.id,
                    name="LinkedIn ICP",
                    source_match="linkedin",
                    segment_key="icp",
                    priority=1,
                    pipeline_stage_id=stages[2].id,
                ),
            ]
        )
        db.session.commit()

        exact = LeadStreamService.find_matching_stream(org.id, "linkedin", "icp")
        assert exact and exact.name == "LinkedIn ICP"
        source_only = LeadStreamService.find_matching_stream(org.id, "linkedin", None)
        assert source_only and source_only.name == "LinkedIn"
        no_match = LeadStreamService.find_matching_stream(org.id, "website", None)
        assert no_match is None
        fallback = LeadStreamService.get_fallback_stage(org.id)
        assert fallback and fallback.order_index == 0


def test_lead_stream_routing(app):
    with app.app_context():
        org, stages = _org_with_stages("stream-routing")
        stream = LeadStream(
            organization_id=org.id,
            name="Website B2B",
            source_match="website",
            default_tags=["b2b", "saas"],
            pipeline_stage_id=stages[1].id,
        )
        lead = Lead(
            organization_id=org.id,
            stage_id=stages[0].id,
            status="active",
            source="website",
            email="route@example.com",
            tags=["existing", "saas"],
        )
        db.session.add_all([stream, lead])
        db.session.flush()
        previous = stream.last_lead_at
        LeadStreamService.apply_stream_to_lead(lead, stream)
        db.session.commit()

        assert set(lead.tags) == {"existing", "saas", "b2b"}
        assert stream.lead_count == 1
        assert stream.last_lead_at is not None
        assert stream.last_lead_at != previous


def test_score_routing(app):
    with app.app_context():
        org, stages = _org_with_stages("stream-score")
        lead = Lead(
            organization_id=org.id,
            stage_id=stages[0].id,
            status="active",
            source="manual",
            email="score@example.com",
            tags=["hot"],
        )
        db.session.add(lead)
        db.session.flush()

        lead.score = 85
        apply_score_routing(lead, org.id)
        assert lead.tags.count("hot") == 1

        lead.score = 60
        apply_score_routing(lead, org.id)
        assert "warm" in lead.tags

        lead.score = 20
        apply_score_routing(lead, org.id)
        assert "cold" in lead.tags


def test_stream_health(app):
    with app.app_context():
        org, _stages = _org_with_stages("stream-health")
        old = LeadStream(
            organization_id=org.id,
            name="Old",
            is_active=True,
            last_lead_at=datetime.now(timezone.utc) - timedelta(days=4),
        )
        fresh = LeadStream(
            organization_id=org.id,
            name="Fresh",
            is_active=True,
            last_lead_at=datetime.now(timezone.utc),
        )
        never = LeadStream(
            organization_id=org.id,
            name="Never",
            is_active=True,
            last_lead_at=None,
        )
        db.session.add_all([old, fresh, never])
        db.session.commit()
        stale = StreamHealthService.get_stale_streams(org.id)
        names = {s.name for s in stale}
        assert "Old" in names
        assert "Fresh" not in names
        assert "Never" not in names


def test_stream_cross_tenant_matching(app):
    with app.app_context():
        org_a, _ = _org_with_stages("stream-org-a")
        org_b, _ = _org_with_stages("stream-org-b")
        db.session.add_all(
            [
                LeadStream(
                    organization_id=org_a.id,
                    name="A LinkedIn",
                    source_match="linkedin",
                    is_active=True,
                ),
                LeadStream(
                    organization_id=org_b.id,
                    name="B LinkedIn",
                    source_match="linkedin",
                    is_active=True,
                ),
            ]
        )
        db.session.commit()
        match_a = LeadStreamService.find_matching_stream(org_a.id, "linkedin")
        match_b = LeadStreamService.find_matching_stream(org_b.id, "linkedin")
        assert match_a and match_a.name == "A LinkedIn"
        assert match_b and match_b.name == "B LinkedIn"
