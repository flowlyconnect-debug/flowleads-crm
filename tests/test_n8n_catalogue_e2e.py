"""End-to-end check of the three catalogue workflows against a real FlowLeads
server: n8n/workflows/flowleads-{crawler,analyzer,enricher}.json are executed
node by node by tests/n8n_sim/executor.js (a small n8n emulator), with
Oikotie / Finder / Asiakastieto / OpenAI mocked in tests/n8n_sim/mocks.js.

Skipped when Node.js is not installed.
"""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "tests" / "n8n_sim"
WORKFLOWS = ROOT / "n8n" / "workflows"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js not installed")


@pytest.fixture
def live_crm(tmp_path):
    from app import create_app
    from app.config import TESTING_EMAIL_DEFAULTS, TestingConfig, _env_or_default, apply_testing_defaults
    from app.extensions import db

    os.environ["FLASK_ENV"] = "testing"
    for key, default in TESTING_EMAIL_DEFAULTS.items():
        os.environ[key] = _env_or_default(key, default)

    class E2EConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'e2e.db'}"
        SERVER_NAME = None

    app = create_app(E2EConfig)
    apply_testing_defaults(app)
    app.config["SERVER_NAME"] = None
    with app.app_context():
        db.create_all()

    server = make_server("127.0.0.1", 0, app, threaded=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        with app.app_context():
            db.session.remove()
            db.drop_all()


def _workflow_with_config(name, base_url, tmp_path):
    wf = json.loads((WORKFLOWS / name).read_text(encoding="utf-8"))
    for node in wf["nodes"]:
        if node["name"] == "SET – Config":
            for a in node["parameters"]["assignments"]["assignments"]:
                if a["name"] == "crm_base_url":
                    a["value"] = base_url
                if a["name"] == "n8n_secret":
                    a["value"] = "test-n8n-secret"
    out = tmp_path / name
    out.write_text(json.dumps(wf, ensure_ascii=False), encoding="utf-8")
    return out


def _run(name, base_url, tmp_path):
    path = _workflow_with_config(name, base_url, tmp_path)
    proc = subprocess.run(
        [NODE, str(SIM / "executor.js"), str(path), base_url, str(SIM / "mocks.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    lines = [l for l in proc.stdout.splitlines() if l.startswith("{")]
    assert lines, f"no output from executor: {proc.stdout}\n{proc.stderr}"
    result = json.loads(lines[-1])
    assert result["ok"], json.dumps(result, ensure_ascii=False, indent=1)
    return result


def test_crawler_analyzer_enricher_produce_enriched_leads(live_crm, tmp_path):
    app, base = live_crm
    from app.extensions import db
    from app.leads.models import Lead
    from app.listings.models import CrawlState, Listing, ListingMatch
    from app.search.models import SearchProfile
    from app.users.services import create_organization

    with app.app_context():
        org = create_organization("E2E Org", "e2e-org")
        db.session.flush()
        other = create_organization("Other Org", "other-org")
        db.session.flush()
        db.session.add_all(
            [
                SearchProfile(organization_id=org.id, name="Lukot Uusimaa", remonttityyppi="Lukkoremontti / kulunvalvonta",
                              regions=["Uusimaa"], source="oikotie", is_active=True, needs_rematch=False,
                              daily_lead_limit=0),
                SearchProfile(organization_id=other.id, name="Putket", remonttityyppi="Putkiremontti",
                              regions=["Uusimaa"], source="oikotie", is_active=True, needs_rematch=False),
            ]
        )
        db.session.commit()
        org_id, other_id = org.id, other.id

    # 1. Crawler: 18 incremental tasks + 2 full-pass slices.
    crawl = _run("flowleads-crawler.json", base, tmp_path)
    assert crawl["runs"]["HTTP – CRM Ingest"] >= 18
    with app.app_context():
        assert Listing.query.count() == 30
        assert Listing.query.filter_by(maakunta="Uusimaa").count() == 30
        assert {l.analysis_status for l in Listing.query.all()} == {"pending"}
        uusimaa = CrawlState.query.filter_by(maakunta="Uusimaa").one()
        assert uusimaa.last_incremental_at is not None
        assert uusimaa.last_error is None

    # Second crawl: nothing new -> incremental stops on page 1.
    _run("flowleads-crawler.json", base, tmp_path)
    with app.app_context():
        assert Listing.query.count() == 30

    # 2. Analyzer: claims in batches of 20 until the queue is empty.
    analysis = _run("flowleads-analyzer.json", base, tmp_path)
    assert analysis["runs"]["AI – Analyze Listing"] == 29  # 1004's detail page fails before AI
    with app.app_context():
        done = Listing.query.filter_by(analysis_status="done").count()
        assert done == 29
        failed = Listing.query.filter_by(external_id="1004").one()
        assert failed.analysis_status == "pending"  # retried on a later run
        assert "Detail page failed" in failed.analysis_error

        l1 = Listing.query.filter_by(external_id="1001").one()
        assert l1.taloyhtio == "As Oy Lukkotesti"
        # building data: from the Oikotie card (1001) or from the AI (1002)
        assert (l1.build_year, l1.building_type) == (1974, "Kerrostalo")
        l2b = Listing.query.filter_by(external_id="1002").one()
        assert (l2b.build_year, l2b.building_type) == (1988, "Rivitalo")
        assert l1.renovations[0].timing_year == 2027
        assert [r.remonttityyppi for r in l1.renovations] == ["Lukkoremontti / kulunvalvonta"]
        assert l1.contact_status == "none"  # contacts were on the page
        l2 = Listing.query.filter_by(external_id="1002").one()
        assert l2.contact_status == "pending"

        assert ListingMatch.query.count() == 2
        leads = Lead.query.filter_by(organization_id=org_id).order_by(Lead.source_ref).all()
        assert [l.source_ref for l in leads] == ["oikotie-1001", "oikotie-1002"]
        assert leads[0].company == "Isännöinti Testi Oy"
        assert leads[0].phone == "040 123 4567"
        assert leads[0].email == "testi@isannointi.fi"
        assert not leads[1].phone
        # The pipe profile matches nothing (1003's pipe renovation is already done).
        assert Lead.query.filter_by(organization_id=other_id).count() == 0

    # 3. Enricher: finds contacts for 1002 and updates the lead.
    enrich = _run("flowleads-enricher.json", base, tmp_path)
    assert enrich["runs"]["HTTP – CRM Save Contacts"] == 1
    with app.app_context():
        l2 = Listing.query.filter_by(external_id="1002").one()
        assert l2.contact_status == "done"
        assert l2.contact_source == "finder"
        lead = Lead.query.filter_by(organization_id=org_id, source_ref="oikotie-1002").one()
        assert lead.phone == "0409998888"
        assert lead.company == "Avaimeton Isännöinti Oy"
