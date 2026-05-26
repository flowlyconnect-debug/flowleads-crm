from flask_login import current_user


def sender_display_name() -> str:
    org = getattr(current_user, "organization", None)
    if org and org.email_from_name:
        return org.email_from_name
    return current_user.email.split("@")[0]
