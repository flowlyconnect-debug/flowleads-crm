from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.leads.models import Lead
from app.segments.filter_engine import FilterEngineError, apply_segment_filters
from app.segments.models import Segment


class SegmentServiceError(Exception):
    def __init__(self, message: str, code: str = "segment_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class SegmentService:
    @staticmethod
    def get_segment(segment_id: int, organization_id: int) -> Segment:
        segment = Segment.query.filter_by(
            id=segment_id, organization_id=organization_id
        ).first()
        if not segment:
            raise SegmentServiceError("Segment not found.", "not_found")
        return segment

    @staticmethod
    def list_segments(organization_id: int, *, pinned_only: bool = False) -> list[Segment]:
        query = Segment.query.filter_by(organization_id=organization_id)
        if pinned_only:
            query = query.filter_by(is_pinned=True)
        return query.order_by(Segment.name.asc()).all()

    @staticmethod
    def save(
        name: str,
        filters: dict,
        organization_id: int,
        *,
        user_id: int | None = None,
        description: str | None = None,
        is_pinned: bool = False,
    ) -> Segment:
        name = (name or "").strip()
        if not name:
            raise SegmentServiceError("Segment name is required.", "validation_error")
        if len(name) > 200:
            raise SegmentServiceError("Segment name is too long.", "validation_error")

        existing = Segment.query.filter_by(
            organization_id=organization_id, name=name
        ).first()
        if existing:
            raise SegmentServiceError(
                "A segment with this name already exists.", "duplicate_name"
            )

        SegmentService._validate_filters(filters, organization_id)

        segment = Segment(
            organization_id=organization_id,
            name=name,
            description=(description or "").strip() or None,
            created_by=user_id,
            filters=filters,
            is_pinned=is_pinned,
            lead_count_cache=None,
        )
        db.session.add(segment)
        db.session.flush()
        segment.lead_count_cache = SegmentService.count_for_filters(
            organization_id, filters
        )
        db.session.flush()
        return segment

    @staticmethod
    def update(
        segment_id: int,
        organization_id: int,
        data: dict,
    ) -> Segment:
        segment = SegmentService.get_segment(segment_id, organization_id)

        if "name" in data and data["name"]:
            new_name = str(data["name"]).strip()
            clash = Segment.query.filter(
                Segment.organization_id == organization_id,
                Segment.name == new_name,
                Segment.id != segment.id,
            ).first()
            if clash:
                raise SegmentServiceError(
                    "A segment with this name already exists.", "duplicate_name"
                )
            segment.name = new_name

        if "description" in data:
            segment.description = (data["description"] or "").strip() or None
        if "is_pinned" in data:
            segment.is_pinned = bool(data["is_pinned"])
        if "filters" in data:
            SegmentService._validate_filters(data["filters"], organization_id)
            segment.filters = data["filters"]
            segment.lead_count_cache = None

        segment.updated_at = datetime.now(timezone.utc)
        db.session.flush()
        return segment

    @staticmethod
    def delete(segment_id: int, organization_id: int) -> None:
        segment = SegmentService.get_segment(segment_id, organization_id)
        db.session.delete(segment)
        db.session.flush()

    @staticmethod
    def set_pinned(segment_id: int, organization_id: int, pinned: bool) -> Segment:
        segment = SegmentService.get_segment(segment_id, organization_id)
        segment.is_pinned = pinned
        segment.updated_at = datetime.now(timezone.utc)
        db.session.flush()
        return segment

    @staticmethod
    def _validate_filters(filters: dict | None, organization_id: int) -> None:
        if not filters or not isinstance(filters, dict):
            raise SegmentServiceError("Filters are required.", "validation_error")
        try:
            apply_segment_filters(Lead.query, organization_id, filters)
        except FilterEngineError as exc:
            raise SegmentServiceError(exc.message, exc.code) from exc

    @staticmethod
    def apply_filters(organization_id: int, filters: dict | None):
        """Return a SQLAlchemy query for leads matching filters."""
        query = Lead.query.options(joinedload(Lead.stage), joinedload(Lead.assignee))
        try:
            return apply_segment_filters(query, organization_id, filters)
        except FilterEngineError as exc:
            raise SegmentServiceError(exc.message, exc.code) from exc

    @staticmethod
    def count_for_filters(organization_id: int, filters: dict | None) -> int:
        query = SegmentService.apply_filters(organization_id, filters)
        return query.with_entities(func.count(Lead.id.distinct())).scalar() or 0

    @staticmethod
    def get_lead_count(segment_id: int, organization_id: int, *, use_cache: bool = True) -> int:
        segment = SegmentService.get_segment(segment_id, organization_id)
        if use_cache and segment.lead_count_cache is not None:
            return segment.lead_count_cache
        return SegmentService.count_for_filters(organization_id, segment.filters)

    @staticmethod
    def refresh_counts(organization_id: int | None = None) -> int:
        """Refresh lead_count_cache for segments (scheduler job)."""
        query = Segment.query
        if organization_id is not None:
            query = query.filter_by(organization_id=organization_id)

        updated = 0
        for segment in query.all():
            count = SegmentService.count_for_filters(
                segment.organization_id, segment.filters
            )
            if segment.lead_count_cache != count:
                segment.lead_count_cache = count
                updated += 1
        db.session.flush()
        return updated

    @staticmethod
    def paginate_leads(
        organization_id: int,
        filters: dict | None,
        *,
        page: int = 1,
        per_page: int = 25,
    ):
        page = max(1, int(page))
        per_page = max(1, min(100, int(per_page)))
        query = SegmentService.apply_filters(organization_id, filters)
        query = query.order_by(Lead.created_at.desc())
        return query.paginate(page=page, per_page=per_page, error_out=False)
