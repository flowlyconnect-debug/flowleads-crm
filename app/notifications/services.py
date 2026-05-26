from __future__ import annotations

from datetime import datetime, timezone

from app.extensions import db
from app.notifications.models import Notification
from app.users.models import User


class NotificationServiceError(Exception):
    def __init__(self, message: str, code: str = "notification_error"):
        self.message = message
        self.code = code
        super().__init__(message)


class NotificationService:
    @staticmethod
    def create(
        *,
        user_id: int,
        organization_id: int,
        type: str,
        title: str,
        message: str,
        link: str | None = None,
    ) -> Notification:
        user = User.query.filter_by(id=user_id, organization_id=organization_id).first()
        if not user:
            raise NotificationServiceError("User not found in organization.", "not_found")
        notification = Notification(
            user_id=user_id,
            organization_id=organization_id,
            type=type,
            title=title.strip()[:255],
            message=message.strip(),
            link=(link or "").strip()[:500] or None,
            is_read=False,
        )
        db.session.add(notification)
        db.session.flush()
        return notification

    @staticmethod
    def get_for_user(user_id: int, organization_id: int, *, limit: int = 10) -> dict:
        unread_count = Notification.query.filter_by(
            user_id=user_id,
            organization_id=organization_id,
            is_read=False,
        ).count()
        recent = (
            Notification.query.filter_by(user_id=user_id, organization_id=organization_id)
            .order_by(Notification.created_at.desc())
            .limit(limit)
            .all()
        )
        return {
            "unread_count": unread_count,
            "notifications": [
                {
                    "id": n.id,
                    "type": n.type,
                    "title": n.title,
                    "message": n.message,
                    "link": n.link,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat() if n.created_at else None,
                }
                for n in recent
            ],
        }

    @staticmethod
    def mark_read(notification_id: int, user_id: int, organization_id: int) -> Notification:
        notification = Notification.query.filter_by(
            id=notification_id,
            user_id=user_id,
            organization_id=organization_id,
        ).first()
        if not notification:
            raise NotificationServiceError("Notification not found.", "not_found")
        notification.is_read = True
        db.session.flush()
        return notification

    @staticmethod
    def mark_all_read(user_id: int, organization_id: int) -> int:
        count = (
            Notification.query.filter_by(
                user_id=user_id,
                organization_id=organization_id,
                is_read=False,
            )
            .update({"is_read": True})
        )
        db.session.flush()
        return count

    @staticmethod
    def count_unread(user_id: int, organization_id: int) -> int:
        return Notification.query.filter_by(
            user_id=user_id,
            organization_id=organization_id,
            is_read=False,
        ).count()
