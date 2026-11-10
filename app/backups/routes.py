import shutil

from flask import Blueprint, after_this_request, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from app.backups.forms import RestoreBackupForm
from app.backups.services import (
    BackupServiceError,
    backup_exists,
    create_backup,
    download_backup_to_temp,
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
        path = download_backup_to_temp(filename)
    except BackupServiceError as exc:
        flash(exc.message, "danger")
        return redirect(url_for("backups.backups_list"))

    s3_backed = (current_app.config.get("BACKUP_STORAGE_BACKEND") or "local").lower() == "s3"
    if s3_backed:
        download_dir = path.parent

        @after_this_request
        def _cleanup_s3_download(response):
            shutil.rmtree(download_dir, ignore_errors=True)
            return response

    return send_file(path, as_attachment=True, download_name=path.name)


@backups_bp.route("/backups/<filename>/restore", methods=["GET", "POST"])
@login_required
@require_role("superadmin")
@require_2fa
def backups_restore(filename: str):
    form = RestoreBackupForm()
    if not backup_exists(filename):
        flash("Backup file not found.", "danger")
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
                    target_database_url=(form.target_database_url.data or "").strip() or None,
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
