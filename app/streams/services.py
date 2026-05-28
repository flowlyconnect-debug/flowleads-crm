from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.extensions import db
from app.leads.models import LeadStream, PipelineStage
from app.users.models import Organization, User


class LeadStreamService:
    @staticmethod
    def find_matching_stream(
        organization_id: int, source: str, segment_key: str | None = None
    ) -> LeadStream | None:
        streams = (
            LeadStream.query.filter_by(organization_id=organization_id, is_active=True)
            .order_by(LeadStream.priority.asc())
            .all()
        )

        if segment_key:
            for stream in streams:
                if stream.source_match == source and stream.segment_key == segment_key:
                    return stream

        for stream in streams:
            if stream.source_match == source and not stream.segment_key:
                return stream

        if segment_key:
            for stream in streams:
                if stream.segment_key == segment_key and not stream.source_match:
                    return stream

        return None

    @staticmethod
    def apply_stream_to_lead(lead, stream: LeadStream) -> None:
        if stream.pipeline_stage_id:
            lead.stage_id = stream.pipeline_stage_id

        if stream.owner_id:
            lead.assigned_to = stream.owner_id

        if stream.default_tags:
            existing_tags = lead.tags or []
            merged = list(dict.fromkeys(existing_tags + list(stream.default_tags)))
            lead.tags = merged

        stream.last_lead_at = datetime.now(timezone.utc)
        stream.lead_count = (stream.lead_count or 0) + 1

    @staticmethod
    def get_fallback_stage(organization_id: int) -> PipelineStage | None:
        return (
            PipelineStage.query.filter_by(organization_id=organization_id)
            .order_by(PipelineStage.order_index.asc())
            .first()
        )


class StreamHealthService:
    STALE_DAYS = 3

    @staticmethod
    def get_stale_streams(organization_id: int) -> list[LeadStream]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=StreamHealthService.STALE_DAYS)
        return (
            LeadStream.query.filter(
                LeadStream.organization_id == organization_id,
                LeadStream.is_active.is_(True),
                LeadStream.last_lead_at.isnot(None),
                LeadStream.last_lead_at < cutoff,
            )
            .order_by(LeadStream.priority.asc())
            .all()
        )

    @staticmethod
    def check_all_orgs() -> None:
        from app.email.services import EmailService

        orgs = Organization.query.filter_by(is_active=True).all()
        for org in orgs:
            stale = StreamHealthService.get_stale_streams(org.id)
            if not stale:
                continue
            admins = User.query.filter_by(
                organization_id=org.id, role="admin", is_active=True
            ).all()
            for admin in admins:
                EmailService.send_stream_health_alert(
                    to_email=admin.email,
                    org_name=org.name,
                    stale_streams=[
                        {"name": s.name, "last_lead_at": s.last_lead_at} for s in stale
                    ],
                )
        db.session.commit()
