from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.backups.forms import RestoreBackupForm
from app.backups.services import (
    BackupServiceError,
    create_backup,
    get_backup_path,
    list_backups,
    restore_backup,
)
from app.core.permissions import require_2fa, require_role

backups_bp = Blueprint("backups", __name__)


@backups_bp.route("/backups", methods=["GET"])
@login_required
@require_role("superadmin")
@require_2fa
def backups_list():
    backups = list_backups()
    return render_template("admin/backups.html", backups=backups)


@backups_bp.route("/backups/create", methods=["POST"])
@login_required
@require_role("superadmin")
@require_2fa
def backups_create():
    try:
        filename = create_backup(triggered_by_user_id=current_user.id)
        flash(f"Backup created: {filename}", "success")
    except BackupServiceError as exc:
        flash(exc.message, "danger")
    return redirect(url_for("backups.backups_list"))


@backups_bp.route("/backups/<filename>/download", methods=["GET"])
@login_required
@require_role("superadmin")
@require_2fa
def backups_download(filename: str):
    try:
        path = get_backup_path(filename)
        return send_file(path, as_attachment=True, download_name=path.name)
    except BackupServiceError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("backups.backups_list"))


@backups_bp.route("/backups/<filename>/restore", methods=["GET", "POST"])
@login_required
@require_role("superadmin")
@require_2fa
def backups_restore(filename: str):
    form = RestoreBackupForm()
    try:
        get_backup_path(filename)
    except BackupServiceError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("backups.backups_list"))

    if form.validate_on_submit():
        if not form.confirm_overwrite.data:
            flash("You must confirm that you understand data will be overwritten.", "danger")
        else:
            try:
                restore_backup(
                    filename,
                    confirmed_by_user_id=current_user.id,
                    totp_code=form.totp_code.data or "",
                    password=form.password.data or "",
                )
                flash("Backup restored successfully.", "success")
                return redirect(url_for("backups.backups_list"))
            except BackupServiceError as exc:
                flash(exc.message, "danger")

    return render_template(
        "admin/backups_restore.html",
        form=form,
        filename=filename,
    )
