from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

UNSUBSCRIBE_SALT = "sequence-unsubscribe"
UNSUBSCRIBE_MAX_AGE = 60 * 60 * 24 * 365 * 5  # 5 years


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        current_app.config["SECRET_KEY"],
        salt=UNSUBSCRIBE_SALT,
    )


def generate_unsubscribe_token(lead_id: int, sequence_id: int) -> str:
    return _serializer().dumps({"lead_id": lead_id, "sequence_id": sequence_id})


def verify_unsubscribe_token(token: str) -> dict | None:
    try:
        data = _serializer().loads(token, max_age=UNSUBSCRIBE_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    lead_id = data.get("lead_id")
    sequence_id = data.get("sequence_id")
    if not isinstance(lead_id, int) or not isinstance(sequence_id, int):
        return None
    return {"lead_id": lead_id, "sequence_id": sequence_id}
