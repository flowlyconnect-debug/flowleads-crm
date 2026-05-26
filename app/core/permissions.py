from functools import wraps

from flask import redirect, request, session, url_for
from flask_login import current_user

TWO_FA_SESSION_KEY = "two_fa_verified"


def is_2fa_verified() -> bool:
    return bool(session.get(TWO_FA_SESSION_KEY))


def set_2fa_verified(value: bool = True) -> None:
    if value:
        session[TWO_FA_SESSION_KEY] = True
    else:
        session.pop(TWO_FA_SESSION_KEY, None)


def clear_2fa_session() -> None:
    session.pop(TWO_FA_SESSION_KEY, None)


def require_role(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))
            if current_user.role not in roles:
                from flask import abort

                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def require_2fa(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.url))

        if current_user.is_superadmin():
            if not current_user.totp_enabled:
                return redirect(url_for("auth.two_fa_setup"))
            if not is_2fa_verified():
                return redirect(url_for("auth.two_fa_verify"))

        return view(*args, **kwargs)

    return wrapped

