"""`flask smoke-check` (app/admin/smoke.py): every step runs and reports."""

from app.admin.smoke import run_smoke
from app.extensions import db
from app.users.services import create_organization


def test_smoke_steps_run(app):
    with app.app_context():
        create_organization("Savu Oy", "savu")
        db.session.commit()
        results = {r["name"]: r for r in run_smoke()}
        # the test database has no alembic_version table (create_all): that one step reports it
        for name, result in results.items():
            if name != "Tietokanta ja migraatiot":
                assert result["ok"], (name, result["detail"])
        assert "Savu Oy" in results["Kohteet jokaiselle aktiiviselle asiakkaalle"]["detail"]
        assert "EXPORT_LIMITS_ENABLED=päällä" in results["Pois-kytkimet"]["detail"]
