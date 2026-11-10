"""Build the FlowLeads v2 n8n workflows (Job Poller, Liidi Ohjaaja, Lead Worker).

The Lead Worker is generated from the production "Lead Worker" export so the
Oikotie fetch + AI relevance + isännöitsijä enrichment chain stays exactly as
it runs today; only the parts that talked to Pinsale / Google Sheets are
replaced with FlowLeads CRM calls, and known wiring bugs are fixed.

Usage:
    python n8n/tools/build_workflows.py n8n/source/lead-worker-export.json

Writes into n8n/workflows/:
    flowleads-v2-job-poller.json
    flowleads-v2-liidi-ohjaaja.json
    flowleads-v2-lead-worker.json
"""

from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "n8n" / "workflows"

POLLER_NAME = "FlowLeads v2 – Job Poller"
OHJAAJA_NAME = "FlowLeads v2 – Liidi Ohjaaja"
WORKER_NAME = "FlowLeads v2 – Lead Worker"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

# Maakunta -> Oikotie slug. Matches app/search/constants.py FINNISH_REGIONS
# (the old n8n map was missing Keski-Pohjanmaa).
REGION_SLUGS = {
    "Uusimaa": "uusimaa",
    "Varsinais-Suomi": "varsinais-suomi",
    "Satakunta": "satakunta",
    "Kanta-Häme": "kanta-hame",
    "Pirkanmaa": "pirkanmaa",
    "Päijät-Häme": "paijat-hame",
    "Kymenlaakso": "kymenlaakso",
    "Etelä-Karjala": "etela-karjala",
    "Etelä-Savo": "etela-savo",
    "Pohjois-Savo": "pohjois-savo",
    "Pohjois-Karjala": "pohjois-karjala",
    "Keski-Suomi": "keski-suomi",
    "Etelä-Pohjanmaa": "etela-pohjanmaa",
    "Pohjanmaa": "pohjanmaa",
    "Keski-Pohjanmaa": "keski-pohjanmaa",
    "Pohjois-Pohjanmaa": "pohjois-pohjanmaa",
    "Kainuu": "kainuu",
    "Lappi": "lappi",
}


def _id() -> str:
    return str(uuid.uuid4())


def node(name, type_, params, pos, version, **extra):
    n = {
        "parameters": params,
        "type": type_,
        "typeVersion": version,
        "position": pos,
        "id": _id(),
        "name": name,
    }
    n.update(extra)
    return n


def code(name, js, pos, *, each=False, **extra):
    params = {"jsCode": js.strip() + "\n"}
    if each:
        params["mode"] = "runOnceForEachItem"
    return node(name, "n8n-nodes-base.code", params, pos, 2, **extra)


def connect(conns, src, dst, out_index=0, in_index=0):
    outs = conns.setdefault(src, {}).setdefault("main", [])
    while len(outs) <= out_index:
        outs.append([])
    outs[out_index].append({"node": dst, "type": "main", "index": in_index})


def secret_headers(extra=None):
    params = [
        {"name": "X-N8N-Secret", "value": "={{ $json.n8n_secret }}"},
        {"name": "Content-Type", "value": "application/json"},
    ]
    return {"parameters": params + (extra or [])}


def workflow(name, nodes, conns, *, description):
    return {
        "name": name,
        "nodes": nodes,
        "pinData": {},
        "connections": conns,
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "saveDataErrorExecution": "all",
            "saveDataSuccessExecution": "all",
            "callerPolicy": "workflowsFromSameOwner",
        },
        "meta": {"templateCredsSetupCompleted": True, "description": description},
    }


def execute_workflow_node(name, target_name, pos):
    return node(
        name,
        "n8n-nodes-base.executeWorkflow",
        {
            "workflowId": {
                "__rl": True,
                "mode": "list",
                "value": "",
                "cachedResultName": target_name,
            },
            "mode": "each",
            "options": {"waitForSubWorkflow": True},
        },
        pos,
        1.2,
        alwaysOutputData=True,
        onError="continueRegularOutput",
    )


# ---------------------------------------------------------------------------
# Job Poller
# ---------------------------------------------------------------------------


def build_poller():
    nodes, conns = [], {}
    nodes.append(
        node(
            "Every 15 minutes",
            "n8n-nodes-base.scheduleTrigger",
            {"rule": {"interval": [{"field": "minutes", "minutesInterval": 15}]}},
            [0, 0],
            1.2,
        )
    )
    nodes.append(node("Manual Trigger", "n8n-nodes-base.manualTrigger", {}, [0, 200], 1))
    nodes.append(
        node(
            "SET – Config",
            "n8n-nodes-base.set",
            {
                "assignments": {
                    "assignments": [
                        {
                            "id": _id(),
                            "name": "crm_base_url",
                            "value": "https://flowleads-crm.onrender.com",
                            "type": "string",
                        },
                        {
                            "id": _id(),
                            "name": "n8n_secret",
                            "value": "VAIHDA_TAHAN_N8N_MASTER_SECRET",
                            "type": "string",
                        },
                    ]
                },
                "options": {},
            },
            [240, 100],
            3.4,
            notes="Ainoa paikka johon CRM-osoite ja N8N_MASTER_SECRET asetetaan. "
            "Arvot kulkevat jobin mukana Liidi Ohjaajalle ja Lead Workerille.",
        )
    )
    nodes.append(
        node(
            "HTTP – Claim Pending Job",
            "n8n-nodes-base.httpRequest",
            {
                "url": "={{ $json.crm_base_url.replace(/\\/$/, '') }}/api/v1/n8n/jobs",
                "sendQuery": True,
                "queryParameters": {
                    "parameters": [
                        {"name": "status", "value": "pending"},
                        {"name": "limit", "value": "1"},
                    ]
                },
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [{"name": "X-N8N-Secret", "value": "={{ $json.n8n_secret }}"}]
                },
                "options": {},
            },
            [480, 100],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
            notes="GET pending jobs claims the job atomically (marks it running and "
            "mints a job token). limit=1: one job per poller execution.",
        )
    )
    nodes.append(
        code(
            "CODE – Split Jobs",
            """
const cfg = $('SET – Config').first().json;
const jobs = $input.first().json.jobs || [];
return jobs.map(job => ({
  json: {
    ...job,
    crm_base_url: String(cfg.crm_base_url || '').replace(/\\/$/, ''),
    n8n_secret: cfg.n8n_secret,
  },
}));
""",
            [720, 100],
        )
    )
    nodes.append(
        node("LOOP – Jobs", "n8n-nodes-base.splitInBatches", {"options": {}}, [960, 100], 3)
    )
    nodes.append(execute_workflow_node("CALL – Liidi Ohjaaja", OHJAAJA_NAME, [1200, 180]))
    nodes.append(
        code(
            "CODE – Job Outcome",
            """
const job = $('LOOP – Jobs').first().json;
const r = $input.first()?.json || {};
let err = '';
if (r.error) {
  err = typeof r.error === 'string' ? r.error : (r.error.message || JSON.stringify(r.error));
} else if (r.worker_error) {
  err = String(r.worker_error);
}
const body = err
  ? { status: 'failed', error: err.slice(0, 1000) }
  : { status: 'completed', error: '' };
return [{ json: { job_id: job.job_id, crm_base_url: job.crm_base_url, n8n_secret: job.n8n_secret, body } }];
""",
            [1440, 180],
        )
    )
    nodes.append(
        node(
            "HTTP – PATCH Job Final",
            "n8n-nodes-base.httpRequest",
            {
                "method": "PATCH",
                "url": "={{ $json.crm_base_url }}/api/v1/n8n/jobs/{{ $json.job_id }}",
                "sendHeaders": True,
                "headerParameters": secret_headers(),
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.body) }}",
                "options": {},
            },
            [1680, 180],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
            onError="continueRegularOutput",
            notes="Ei lähetä leads_found/leads_sent: CRM on kerännyt ne itse "
            "(sivu-heartbeatit + job-tokenilla luodut liidit).",
        )
    )
    connect(conns, "Every 15 minutes", "SET – Config")
    connect(conns, "Manual Trigger", "SET – Config")
    connect(conns, "SET – Config", "HTTP – Claim Pending Job")
    connect(conns, "HTTP – Claim Pending Job", "CODE – Split Jobs")
    connect(conns, "CODE – Split Jobs", "LOOP – Jobs")
    connect(conns, "LOOP – Jobs", "CALL – Liidi Ohjaaja", out_index=1)
    connect(conns, "CALL – Liidi Ohjaaja", "CODE – Job Outcome")
    connect(conns, "CODE – Job Outcome", "HTTP – PATCH Job Final")
    connect(conns, "HTTP – PATCH Job Final", "LOOP – Jobs")
    return workflow(
        POLLER_NAME,
        nodes,
        conns,
        description="Every 15 min: claim one pending FlowLeads SearchJob, run Liidi Ohjaaja, close the job.",
    )


# ---------------------------------------------------------------------------
# Liidi Ohjaaja (dispatcher)
# ---------------------------------------------------------------------------


def build_ohjaaja():
    nodes, conns = [], {}
    nodes.append(
        node(
            "TRIGGER – From Job Poller",
            "n8n-nodes-base.executeWorkflowTrigger",
            {"inputSource": "passthrough"},
            [0, 200],
            1.1,
        )
    )
    nodes.append(
        code(
            "CODE – Expand Regions",
            """
const job = $input.first().json || {};
const SLUGS = %s;

let regions = job.regions;
if (typeof regions === 'string') {
  try { regions = JSON.parse(regions); } catch (e) { regions = regions.split(','); }
}
if (!Array.isArray(regions)) regions = [];
regions = regions.map(r => String(r || '').trim()).filter(Boolean);
if (regions.includes('Koko Suomi')) regions = Object.keys(SLUGS);

const valid = [...new Set(regions.filter(r => SLUGS[r]))];
const unknown = regions.filter(r => !SLUGS[r] && r !== 'Koko Suomi');
if (!valid.length) {
  throw new Error('Jobilla ei ole yhtään tunnettua maakuntaa: ' + JSON.stringify(job.regions));
}

return valid.map((maakunta, i) => ({
  json: {
    ...job,
    maakunta,
    pageSlug: SLUGS[maakunta],
    region_index: i + 1,
    region_count: valid.length,
    unknown_regions: unknown,
  },
}));
"""
            % json.dumps(REGION_SLUGS, ensure_ascii=False),
            [240, 200],
        )
    )
    nodes.append(
        node("LOOP – Regions", "n8n-nodes-base.splitInBatches", {"options": {}}, [480, 200], 3)
    )
    nodes.append(
        node(
            "HTTP – Fetch Region Count",
            "n8n-nodes-base.httpRequest",
            {
                "url": "={{ 'https://asunnot.oikotie.fi/myytavat-asunnot/' + $json.pageSlug }}",
                "sendQuery": True,
                "queryParameters": {
                    "parameters": [
                        {"name": "cardType", "value": "100"},
                        {"name": "offset", "value": "0"},
                        {"name": "limit", "value": "24"},
                    ]
                },
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {
                            "name": "accept",
                            "value": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        },
                        {"name": "accept-language", "value": "fi-FI,fi;q=0.9,en-US;q=0.8,en;q=0.7"},
                        {"name": "user-agent", "value": BROWSER_UA},
                    ]
                },
                "options": {"response": {"response": {"neverError": True, "responseFormat": "text"}}},
            },
            [720, 280],
            4.4,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
            onError="continueRegularOutput",
        )
    )
    nodes.append(
        code(
            "CODE – Build Page Items",
            """
// Same page-count logic as the original Liidi Ohjaaja: read "NNN kpl" from the
// region's Oikotie search page, 24 cards per page, and hand EVERY page to the
// worker one at a time.
const region = $('LOOP – Regions').first().json;
const html = String($input.first()?.json?.data || '');
const PER_PAGE = 24;
const HARD_CAP = 2000; // runaway guard only (~48 000 listings)

const m = html.match(/:\\s*([\\d\\s\\u00a0]+)\\s*kpl/i);
const countFound = !!(m && m[1]);
const totalCount = countFound ? parseInt(m[1].replace(/[\\s\\u00a0]+/g, ''), 10) || 0 : 0;

// Count missing (Oikotie markup changed / blocked) -> still try page 1 so the
// worker's own API call decides; count found as 0 -> nothing to do.
let totalPages = countFound ? Math.ceil(totalCount / PER_PAGE) : 1;
const maxPages = Number(region.max_pages || 0);
if (maxPages > 0) totalPages = Math.min(totalPages, maxPages);
totalPages = Math.min(totalPages, HARD_CAP);

if (totalPages <= 0) {
  return [{ json: { ...region, no_pages: true, total_pages: 0, total_count: totalCount, count_found: countFound } }];
}

const items = [];
for (let page = 1; page <= totalPages; page++) {
  items.push({
    json: {
      ...region,
      page,
      pageLabel: String(page),
      offset: (page - 1) * PER_PAGE,
      limit: PER_PAGE,
      total_pages: totalPages,
      total_count: totalCount,
      count_found: countFound,
    },
  });
}
return items;
""",
            [960, 280],
        )
    )
    nodes.append(
        node(
            "LOOP – Pages",
            "n8n-nodes-base.splitInBatches",
            {"options": {"reset": "={{ $prevNode.name === 'CODE – Build Page Items' }}"}},
            [1200, 280],
            3,
            notes="batch size 1 = Lead Worker käsittelee aina yhden Oikotie-sivun kerrallaan. "
            "reset nollaa sivulooppiin jokaiselle maakunnalle.",
        )
    )
    nodes.append(
        node(
            "IF – Page Exists",
            "n8n-nodes-base.if",
            {
                "conditions": {
                    "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                    "conditions": [
                        {
                            "id": _id(),
                            "leftValue": "={{ $json.no_pages === true }}",
                            "rightValue": "",
                            "operator": {"type": "boolean", "operation": "false", "singleValue": True},
                        }
                    ],
                    "combinator": "and",
                },
                "looseTypeValidation": True,
                "options": {},
            },
            [1440, 360],
            2.2,
        )
    )
    nodes.append(execute_workflow_node("CALL – Lead Worker", WORKER_NAME, [1680, 280]))
    nodes.append(
        code(
            "CODE – Page Heartbeat",
            """
const page = $('LOOP – Pages').first().json;
const out = $input.all().map(i => i.json || {});
const errItem = out.find(o => o && o.error);
let errMsg = '';
if (errItem) {
  const e = errItem.error;
  errMsg = typeof e === 'string' ? e : (e.message || e.description || JSON.stringify(e));
}
const progress = `${page.maakunta} sivu ${page.page}/${page.total_pages} ` +
  `(maakunta ${page.region_index}/${page.region_count})`;
const body = {
  status: 'running',
  page_done: true,
  page_failed: !!errItem,
  // leads_found = Oikotie-kohteita läpikäyty: lisätään maakunnan kokonaismäärä kerran.
  leads_found_delta: page.page === 1 ? Number(page.total_count || 0) : 0,
  progress: progress.slice(0, 255),
};
if (errItem) body.error = `${page.maakunta} sivu ${page.page}: ${errMsg}`.slice(0, 1000);
return [{ json: { job_id: page.job_id, crm_base_url: page.crm_base_url, n8n_secret: page.n8n_secret, body } }];
""",
            [1920, 280],
        )
    )
    nodes.append(
        node(
            "HTTP – PATCH Job Heartbeat",
            "n8n-nodes-base.httpRequest",
            {
                "method": "PATCH",
                "url": "={{ $json.crm_base_url }}/api/v1/n8n/jobs/{{ $json.job_id }}",
                "sendHeaders": True,
                "headerParameters": secret_headers(),
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.body) }}",
                "options": {},
            },
            [2160, 280],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
            onError="continueRegularOutput",
            notes="Heartbeat joka sivun jälkeen: pitää jobin elossa (stuck-job reaper) "
            "ja jatkaa job-tokenin voimassaoloa.",
        )
    )
    nodes.append(
        code(
            "CODE – Final Result",
            """
let last = {};
try { last = $('HTTP – PATCH Job Heartbeat').last().json || {}; } catch (e) { last = {}; }
const pagesDone = Number(last.pages_done || 0);
const pagesFailed = Number(last.pages_failed || 0);
const allFailed = pagesDone > 0 && pagesFailed === pagesDone;
return [{
  json: {
    leads_found: Number(last.leads_found || 0),
    leads_sent: Number(last.leads_sent || 0),
    pages_done: pagesDone,
    pages_failed: pagesFailed,
    worker_error: allFailed ? (last.error || 'Kaikki sivut epäonnistuivat') : '',
  },
}];
""",
            [720, 60],
        )
    )
    connect(conns, "TRIGGER – From Job Poller", "CODE – Expand Regions")
    connect(conns, "CODE – Expand Regions", "LOOP – Regions")
    connect(conns, "LOOP – Regions", "CODE – Final Result", out_index=0)
    connect(conns, "LOOP – Regions", "HTTP – Fetch Region Count", out_index=1)
    connect(conns, "HTTP – Fetch Region Count", "CODE – Build Page Items")
    connect(conns, "CODE – Build Page Items", "LOOP – Pages")
    connect(conns, "LOOP – Pages", "LOOP – Regions", out_index=0)
    connect(conns, "LOOP – Pages", "IF – Page Exists", out_index=1)
    connect(conns, "IF – Page Exists", "CALL – Lead Worker", out_index=0)
    connect(conns, "IF – Page Exists", "LOOP – Pages", out_index=1)
    connect(conns, "CALL – Lead Worker", "CODE – Page Heartbeat")
    connect(conns, "CODE – Page Heartbeat", "HTTP – PATCH Job Heartbeat")
    connect(conns, "HTTP – PATCH Job Heartbeat", "LOOP – Pages")
    return workflow(
        OHJAAJA_NAME,
        nodes,
        conns,
        description="Expands a SearchJob into maakunnat and every Oikotie page, calls Lead Worker one page at a time.",
    )


# ---------------------------------------------------------------------------
# Lead Worker (derived from the production export)
# ---------------------------------------------------------------------------

GENERIC_CLASSIFY_PROMPT = """Analysoi vain käyttäjän syötteessä annetut tekstikentät.
Älä päättele mitään URL-osoitteen, domainin tai linkin perusteella.
Älä käytä ulkoista tietoa.

Tavoite:
Tunnista, mainitaanko tekstissä kohteen tulevissa remonteissa, suunnitelmissa tai muissa tiedoissa remonttityyppiin "__REMONTTITYYPPI__" liittyvä TULEVA työ.

REMONTTI TAI UUDISTUS EI SAA OLLA JO TEHTY.
Merkitse relevant=true VAIN jos tekstissä on viite tulevasta, suunnitellusta, päätetystä, ehdotetusta, budjetoidusta tai muuten vielä tekemättömästä työstä, joka selvästi kuuluu remonttityyppiin "__REMONTTITYYPPI__".

Merkitse relevant=false jos työ on jo tehty, valmistunut, aiemmin toteutettu tai pelkkä historiallinen tieto, tai jos yhteys remonttityyppiin ei ole selvä.

Palauta VAIN validi JSON, ei markdownia:

{
  "relevant": true,
  "confidence": 0,
  "ai_huomio": "lyhyt perustelu suomeksi",
  "evidence": ["täsmällinen tekstifragmentti 1", "täsmällinen tekstifragmentti 2"]
}"""

REMOVED_NODES = {
    "SHEET – Check Existing Lead",
    "MERGE – Combine Lead With Existing Rows",
    "IF – Lead Not Already Stored",
    "TRANSFORM – Build Pinsale CRM Payload",
    "HTTP – Create Lead in Pinsale CRM",
    "SHEET – Append Lead Log",
    "Remove Duplicates",
    "Remove Duplicates1",
    "MERGE – Combine Fallback Sources",
}


def build_worker(source_path: Path):
    src = json.loads(source_path.read_text(encoding="utf-8"))
    by_name = {n["name"]: copy.deepcopy(n) for n in src["nodes"]}

    missing = [
        n
        for n in (
            "TRIGGER – Start from Parent Workflow",
            "TRANSFORM – Normalize Input",
            "TRANSFORM – Map Region to Oikotie Area",
            "HTTP – Fetch Oikotie API Bootstrap",
            "TRANSFORM – Build Iteration Payload",
            "LOOP – Process Listings",
            "HTTP – Fetch Listing Detail Page",
            "AI – Classify Lead Relevance",
            "IF – Lead Has Minimum Required Data",
        )
        if n not in by_name
    ]
    if missing:
        raise SystemExit(f"Lead Worker export is missing expected nodes: {missing}")

    for name in REMOVED_NODES:
        by_name.pop(name, None)

    def js(name, body, *, each=None):
        n = by_name[name]
        n["parameters"]["jsCode"] = body.strip() + "\n"
        if each is True:
            n["parameters"]["mode"] = "runOnceForEachItem"
        elif each is False:
            n["parameters"].pop("mode", None)

    # 1. Trigger accepts the whole job/page item from Liidi Ohjaaja.
    by_name["TRIGGER – Start from Parent Workflow"]["parameters"] = {"inputSource": "passthrough"}

    # 2. Keep every job field (job_id, organization_id, crm_*, n8n_secret, ...).
    js(
        "TRANSFORM – Normalize Input",
        """
const j = $input.first().json || {};
const maakunta = j.maakunta || j.current_region || '';
const page = Number(j.page || 1);
const limit = 24;
const offset = (page - 1) * limit;
if (!j.organization_id || !j.crm_endpoint || !j.crm_api_key || !j.crm_base_url || !j.n8n_secret) {
  throw new Error('Lead Worker: jobin CRM-kentät puuttuvat (aja Liidi Ohjaajan kautta).');
}
return [{ json: { ...j, maakunta, page, offset, limit, pageLabel: j.pageLabel || String(page) } }];
""",
    )

    # 3. Region map: add Keski-Pohjanmaa, fail loudly on unknown maakunta.
    region_map = {k: {"pageSlug": v, "apiLocation": v} for k, v in REGION_SLUGS.items()}
    js(
        "TRANSFORM – Map Region to Oikotie Area",
        """
const maakunta = $json.filters?.maakunta || $json.maakunta || "";
const map = %s;
const region = map[maakunta];
if (!region) throw new Error('Tuntematon maakunta: ' + maakunta);
return [{ json: { ...$json, region } }];
"""
        % json.dumps(region_map, ensure_ascii=False),
    )

    # 4. Search page "limit" param was missing its "=" (sent literally).
    for p in by_name["HTTP – Fetch Oikotie Search Page"]["parameters"]["queryParameters"]["parameters"]:
        if p["name"] == "limit":
            p["value"] = "={{ $json.limit || 24 }}"

    # 5. Bootstrap cookie header crashed when Oikotie set fewer than 4 cookies.
    for h in by_name["HTTP – Fetch Oikotie API Bootstrap"]["parameters"]["headerParameters"]["parameters"]:
        if h["name"] == "cookie":
            h["value"] = "={{ ($json.headers?.['set-cookie'] || []).map(c => String(c).split(';')[0]).join('; ') }}"

    # 6. Detail page fetch result gets the listing re-attached before going back
    #    into the loop (fixes the double wiring that sent each listing through
    #    the AI chain twice).
    pos = by_name["HTTP – Fetch Listing Detail Page"]["position"]
    attach = code(
        "CODE – Attach Listing Page",
        """
const listing = $('LOOP – Process Listings').item.json;
const h = $json || {};
const html = String(h.pageHtml ?? h.body ?? h.data ?? '');
return { json: { ...listing, pageHtml: html.slice(0, 300000), detailStatus: h.statusCode || null } };
""",
        [pos[0] + 200, pos[1] + 160],
        each=True,
    )
    by_name[attach["name"]] = attach

    js(
        "PARSE – Extract Raw Listing Content",
        """
const { pageHtml, ...listing } = $json || {};
const html = String(pageHtml || "").slice(0, 200000);

let text = html
  .replace(/<script[\\s\\S]*?<\\/script>/gi, " ")
  .replace(/<style[\\s\\S]*?<\\/style>/gi, " ")
  .replace(/<noscript[\\s\\S]*?<\\/noscript>/gi, " ")
  .replace(/<svg[\\s\\S]*?<\\/svg>/gi, " ")
  .replace(/<img[^>]*>/gi, " ")
  .replace(/<[^>]+>/g, " ")
  .replace(/&nbsp;/gi, " ")
  .replace(/&amp;/gi, "&")
  .replace(/&quot;/gi, '"')
  .replace(/&#39;/gi, "'")
  .replace(/\\s+/g, " ")
  .trim();

const startMarkers = [
  "Taloyhtiö",
  "Tulevat remontit",
  "Asunnon lisätiedot",
  "Perustiedot",
  "Kohdenumero",
  "Rakennuksen tiedot",
  "Asunnon kuvaus"
];

let cropped = text;
for (const marker of startMarkers) {
  const idx = text.indexOf(marker);
  if (idx >= 0) {
    cropped = text.slice(idx);
    break;
  }
}

const finalText = cropped.slice(0, 12000);

return { json: { ...listing, pageText: finalText, pageTextLength: finalText.length } };
""",
        each=True,
    )

    # 7. Relevance prompt follows the profile's remonttityyppi. The original
    #    lukitus/kulunvalvonta prompt is kept verbatim for that type.
    classify = by_name["AI – Classify Lead Relevance"]
    values = classify["parameters"]["responses"]["values"]
    lock_prompt = next(v["content"] for v in values if v.get("role") == "system")
    for v in values:
        if v.get("role") == "system":
            v["content"] = "={{ $json.ai_system_prompt }}"
    js(
        "TRANSFORM – Build AI Classification Prompt",
        """
const LOCK_PROMPT = %s;
const GENERIC_PROMPT = %s;
const remonttityyppi = String($('TRANSFORM – Normalize Input').first().json.remonttityyppi || '').trim();
const isLock = !remonttityyppi || /lukko|lukitus|kulunvalvon/i.test(remonttityyppi);
const systemPrompt = isLock ? LOCK_PROMPT : GENERIC_PROMPT.split('__REMONTTITYYPPI__').join(remonttityyppi);

return items.map(item => {
  const tulevat = item.json["Tulev. Remontit"] || "";
  const yhteenveto = item.json["Yhteenveto"] || "";

  item.json.lead_analysis_text = `
Tulevat remontit:
${tulevat}

Yhteenveto:
${yhteenveto}
  `.trim();
  item.json.ai_system_prompt = systemPrompt;

  return item;
});
"""
        % (json.dumps(lock_prompt, ensure_ascii=False), json.dumps(GENERIC_CLASSIFY_PROMPT, ensure_ascii=False)),
    )

    for ai_name in (
        "AI – Classify Lead Relevance",
        "AI – Extract Company and Contact Clues",
        "AI – Extract Fallback Contact Info",
    ):
        n = by_name[ai_name]
        n["retryOnFail"] = True
        n["maxTries"] = 3
        n["waitBetweenTries"] = 5000
        n["onError"] = "continueRegularOutput"
        n.pop("alwaysOutputData", None)

    js(
        "PARSE – Interpret AI Classification",
        """
const modelOutput = $json;
const row = $('PARSE – Extract Raw Listing Content').item.json || {};

const aiText = String(
  modelOutput.output?.[0]?.content?.[0]?.text ||
  modelOutput.output?.[0]?.text ||
  modelOutput.text ||
  ""
).trim().replace(/^```json\\s*/i, "").replace(/^```\\s*/i, "").replace(/```$/i, "").trim();

let ai = { relevant: false, confidence: 0, evidence: [], ai_huomio: "" };
let aiFailed = false;

if (modelOutput.error) {
  aiFailed = true;
  ai.ai_huomio = "AI error";
} else {
  try {
    const parsed = JSON.parse(aiText);
    ai = {
      relevant: parsed.relevant === true,
      confidence: Number(parsed.confidence || 0),
      evidence: Array.isArray(parsed.evidence) ? parsed.evidence : [],
      ai_huomio: typeof parsed.ai_huomio === "string" ? parsed.ai_huomio : ""
    };
  } catch (err) {
    aiFailed = true;
    ai.ai_huomio = "JSON parse failed";
  }
}

const { ai_system_prompt, ...rest } = row;
return {
  json: {
    ...rest,
    "RM löyty": ai.relevant ? "Kyllä" : "Ei",
    "remontti": ai.evidence.join(", "),
    "relevant": ai.relevant,
    "confidence": ai.confidence,
    "ai_huomio": ai.ai_huomio,
    "ai_failed": aiFailed
  }
};
""",
        each=True,
    )

    # 8. Nodes that ran "once for all items" but read $json only processed the
    #    first lead of each page. Switch them to per-item.
    js(
        "CHECK – Missing Company / Manager Info",
        """
const j = $json || {};

const isEmpty = (value) => {
  if (value === undefined || value === null) return true;
  const s = String(value).trim().toLowerCase();
  return s === "" || s === "ei löytynyt" || s === "ei tiedossa" || s === "unknown" || s === "null" || s === "-";
};

const missingName = isEmpty(j["Isännöitsijä"]);
const missingPhone = isEmpty(j["IN puh"]);
const missingEmail = isEmpty(j["IN email"]);

return { json: { ...j, needsFallback: missingName || (missingPhone && missingEmail) } };
""",
        each=True,
    )
    js(
        "BYPASS – Skip Company Search",
        """
return {
  json: {
    ...$json,
    finderRaw: "",
    asiakastietoRaw: "",
    combinedSearchText: "",
    fallbackCandidateEmails: [],
    fallbackCandidatePhones: [],
    fallbackFoundSomething: false,
    fallbackStage: "company_search_skipped"
  }
};
""",
        each=True,
    )
    by_name["TRANSFORM – Normalize Fallback Candidates"]["parameters"]["mode"] = "runOnceForEachItem"

    # 9. Fallback AI output no longer goes through a Merge that could never
    #    match (the AI output has no lead_key); re-attach the lead by pairing.
    js(
        "PARSE – Normalize Fallback Contact Data",
        """
const base = $('TRANSFORM – Normalize Fallback Candidates').item.json || {};
const input = $json || {};

const raw =
  input.output?.[0]?.content?.[0]?.text ||
  input.text ||
  input.response ||
  input.content ||
  input.message ||
  "";

let cleaned = String(raw).trim()
  .replace(/^```json\\s*/i, "")
  .replace(/^```\\s*/i, "")
  .replace(/```$/i, "")
  .trim();

let parsed = {};
try {
  parsed = JSON.parse(cleaned);
} catch (e) {
  parsed = { fallback_confidence: 0, parse_error: true };
}

const normalizePhone = (value) => value ? String(value).replace(/[^\\d+]/g, "").trim() : "";
const normalizeEmail = (value) => value ? String(value).trim().toLowerCase() : "";

const fallbackPhone = normalizePhone(parsed.fallback_in_puh);
const fallbackEmail = normalizeEmail(parsed.fallback_in_email);

return {
  json: {
    ...base,
    fallback_isannoitsija: parsed.fallback_isannoitsija || "",
    fallback_isannointiyhtio: parsed.fallback_isannointiyhtio || "",
    fallback_in_puh: fallbackPhone,
    fallback_in_email: fallbackEmail,
    fallback_source: parsed.fallback_source || "",
    fallback_confidence: Number(parsed.fallback_confidence || 0),
    fallback_has_phone: !!fallbackPhone,
    fallback_has_email: !!fallbackEmail,
    fallback_has_any: !!fallbackPhone || !!fallbackEmail
  }
};
""",
        each=True,
    )

    for http_name in (
        "HTTP – Fetch Finder Company Page",
        "HTTP – Search Asiakastieto",
        "HTTP – Fetch Asiakastieto Company Page",
        "HTTP – Search Finder.fi",
    ):
        by_name[http_name]["onError"] = "continueRegularOutput"

    # 10. CRM dedupe before the expensive part (detail fetch + AI).
    bip = by_name["TRANSFORM – Build Iteration Payload"]["position"]
    loop_pos = by_name["LOOP – Process Listings"]["position"]
    by_name["LOOP – Process Listings"]["position"] = [loop_pos[0] + 560, loop_pos[1]]
    new_nodes = [
        code(
            "CODE – Build Dedupe Check",
            """
const job = $('TRANSFORM – Normalize Input').first().json;
const ids = [];
for (const item of $input.all()) {
  const id = String(item.json?.ID || '').trim();
  if (!id) continue;
  ids.push('oikotie-' + id);                          // already sent to CRM
  ids.push(`oikotie-${id}:p${job.profile_id}`);       // already judged not relevant for this profile
}
if (!ids.length) return [];
return [{ json: {
  organization_id: Number(job.organization_id),
  source_ids: [...new Set(ids)],
  crm_base_url: job.crm_base_url,
  n8n_secret: job.n8n_secret,
} }];
""",
            [bip[0] + 180, bip[1]],
        ),
        node(
            "HTTP – CRM Dedupe Check",
            "n8n-nodes-base.httpRequest",
            {
                "method": "POST",
                "url": "={{ $json.crm_base_url }}/api/v1/n8n/dedupe/check",
                "sendHeaders": True,
                "headerParameters": secret_headers(),
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify({ organization_id: $json.organization_id, source_ids: $json.source_ids }) }}",
                "options": {},
            },
            [bip[0] + 360, bip[1]],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
        ),
        code(
            "CODE – Keep New Listings",
            """
const job = $('TRANSFORM – Normalize Input').first().json;
const existing = new Set($input.first().json.existing_ids || []);
return $('TRANSFORM – Build Iteration Payload').all()
  .map(i => i.json)
  .filter(r => r.ID)
  .filter(r => !existing.has('oikotie-' + r.ID) && !existing.has(`oikotie-${r.ID}:p${job.profile_id}`))
  .map(r => ({ json: r }));
""",
            [bip[0] + 540, bip[1]],
        ),
    ]
    for n in new_nodes:
        by_name[n["name"]] = n

    # 11. CRM output replaces Google Sheets + Pinsale.
    mpos = by_name["IF – Lead Has Minimum Required Data"]["position"]
    out_nodes = [
        code(
            "TRANSFORM – Build FlowLeads Payload",
            """
const item = $json || {};
const job = $('TRANSFORM – Normalize Input').first().json;
const s = (v) => (v === undefined || v === null) ? '' : String(v).trim();
const isUrl = (u) => /^https?:\\/\\/[^\\s]+$/i.test(u);

const id = s(item.ID);
const source_ref = 'oikotie-' + id;
let email = s(item['IN email']).toLowerCase();
if (email && !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(email)) email = '';
const phone = s(item['IN puh']);
const manager = s(item['Isännöitsijä']) || s(item['Isännöintiyhtiö']);
const taloyhtio = s(item['Taloyhtiö']);
const url = s(item.URL);
const website = s(item.fallbackPrimaryWebsite);
const tulevat = s(item['Tulev. Remontit']);

const notes = [
  taloyhtio && `Taloyhtiö: ${taloyhtio}`,
  `Kohde: ${[s(item['Osoite']), s(item['Kaupunki'])].filter(Boolean).join(', ')}`,
  tulevat && `Tulevat remontit: ${tulevat}`,
  s(item.remontti) && `Osuma: ${s(item.remontti)}`,
  s(item.ai_huomio) && `AI: ${s(item.ai_huomio)}`,
  url && `Oikotie: ${url}`,
].filter(Boolean).join('\\n');

const tags = [s(job.remonttityyppi), 'oikotie', s(job.maakunta)]
  .filter(Boolean)
  .map(t => t.slice(0, 50));

const lead = {
  source: 'oikotie',
  source_ref,
  company: manager || taloyhtio || null,
  title: manager ? 'Isännöitsijä' : null,
  phone: phone || null,
  email: email || null,
  website: isUrl(website) ? website : null,
  notes,
  tags,
  gdpr_legal_basis: 'legitimate_interest',
  metadata: {
    isannoitsija_yritys: manager,
    isannoitsija_puh: phone,
    isannoitsija_email: email,
    isannoitsija_ytunnus: s(item['IN business_id']) || s(item['IN y_tunnus']) || s(item['IN Y-tunnus']),
    taloyhtio,
    remonttityyppi: s(job.remonttityyppi),
    tulevat_remontit: tulevat,
    oikotie_url: url,
    kaupunki: s(item['Kaupunki']),
    osoite: s(item['Osoite']),
    hinta: s(item['Hinta']),
    alue: s(item['Alue']),
    maakunta: s(job.maakunta),
    ai_confidence: Number(item.confidence || 0),
    ai_huomio: s(item.ai_huomio),
    yhteystieto_lahde: s(item.fallback_source_used),
    search_job_id: job.job_id,
  },
};

return { json: { lead, source_ref, crm_endpoint: job.crm_endpoint, crm_api_key: job.crm_api_key } };
""",
            [mpos[0] + 240, mpos[1] - 80],
            each=True,
        ),
        node(
            "HTTP – POST Lead to FlowLeads",
            "n8n-nodes-base.httpRequest",
            {
                "method": "POST",
                "url": "={{ $json.crm_endpoint }}",
                "sendHeaders": True,
                "headerParameters": {
                    "parameters": [
                        {"name": "Authorization", "value": "=Bearer {{ $json.crm_api_key }}"},
                        {"name": "Content-Type", "value": "application/json"},
                    ]
                },
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.lead) }}",
                "options": {"response": {"response": {"fullResponse": True, "neverError": True}}},
            },
            [mpos[0] + 480, mpos[1] - 80],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=3000,
        ),
        code(
            "CODE – Collect Sent Lead",
            """
const p = $('TRANSFORM – Build FlowLeads Payload').item.json;
const status = Number($json.statusCode || 0);
const ok = status >= 200 && status < 300;
return { json: {
  source_id: p.source_ref,
  ok,
  status,
  crm_error: ok ? '' : JSON.stringify($json.body || $json).slice(0, 300),
} };
""",
            [mpos[0] + 720, mpos[1] - 80],
            each=True,
        ),
        code(
            "CODE – Mark Not Relevant",
            """
const j = $json || {};
const job = $('TRANSFORM – Normalize Input').first().json;
// Only remember listings the AI actually judged; AI errors are retried next run.
const ok = !!j.ID && j.ai_failed !== true;
return { json: { source_id: ok ? `oikotie-${j.ID}:p${job.profile_id}` : '', ok } };
""",
            [by_name["IF – Lead Is Relevant"]["position"][0] + 240,
             by_name["IF – Lead Is Relevant"]["position"][1] + 320],
            each=True,
        ),
        code(
            "CODE – Build Dedupe Mark",
            """
const job = $('TRANSFORM – Normalize Input').first().json;
const ids = [...new Set($input.all().map(i => i.json).filter(r => r.ok && r.source_id).map(r => r.source_id))];
if (!ids.length) return [];
return [{ json: {
  organization_id: Number(job.organization_id),
  source_ids: ids,
  crm_base_url: job.crm_base_url,
  n8n_secret: job.n8n_secret,
} }];
""",
            [mpos[0] + 960, mpos[1] + 80],
        ),
        node(
            "HTTP – CRM Dedupe Mark",
            "n8n-nodes-base.httpRequest",
            {
                "method": "POST",
                "url": "={{ $json.crm_base_url }}/api/v1/n8n/dedupe/mark",
                "sendHeaders": True,
                "headerParameters": secret_headers(),
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify({ organization_id: $json.organization_id, source_ids: $json.source_ids }) }}",
                "options": {},
            },
            [mpos[0] + 1200, mpos[1] + 80],
            4.2,
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=5000,
        ),
    ]
    for n in out_nodes:
        by_name[n["name"]] = n

    # ---- connections (rebuilt from the original, then patched) ----
    conns = {}
    for src_name, spec in src["connections"].items():
        if src_name in REMOVED_NODES:
            continue
        for out_index, outs in enumerate(spec.get("main", [])):
            for o in outs or []:
                if o["node"] in REMOVED_NODES:
                    continue
                connect(conns, src_name, o["node"], out_index, o.get("index", 0))

    def drop(src_name, dst_name):
        for outs in conns.get(src_name, {}).get("main", []):
            outs[:] = [o for o in outs if o["node"] != dst_name]

    def drop_all_from(src_name):
        conns.pop(src_name, None)

    # dedupe before loop
    drop("TRANSFORM – Build Iteration Payload", "LOOP – Process Listings")
    connect(conns, "TRANSFORM – Build Iteration Payload", "CODE – Build Dedupe Check")
    connect(conns, "CODE – Build Dedupe Check", "HTTP – CRM Dedupe Check")
    connect(conns, "HTTP – CRM Dedupe Check", "CODE – Keep New Listings")
    connect(conns, "CODE – Keep New Listings", "LOOP – Process Listings")

    # detail page loop: HTTP -> attach -> back into loop; no direct HTTP -> PARSE
    drop_all_from("HTTP – Fetch Listing Detail Page")
    connect(conns, "HTTP – Fetch Listing Detail Page", "CODE – Attach Listing Page")
    connect(conns, "CODE – Attach Listing Page", "LOOP – Process Listings")

    # fallback chain without the Merge
    drop_all_from("BYPASS – Skip Company Search")
    connect(conns, "BYPASS – Skip Company Search", "TRANSFORM – Apply Company Enrichment")
    drop_all_from("PARSE – Combine Company Search Responses")
    connect(conns, "PARSE – Combine Company Search Responses", "TRANSFORM – Normalize Fallback Candidates")
    drop_all_from("AI – Extract Fallback Contact Info")
    connect(conns, "AI – Extract Fallback Contact Info", "PARSE – Normalize Fallback Contact Data")

    # not relevant -> remember as judged
    connect(conns, "IF – Lead Is Relevant", "CODE – Mark Not Relevant", out_index=1)
    connect(conns, "CODE – Mark Not Relevant", "CODE – Build Dedupe Mark")

    # relevant -> CRM
    drop_all_from("IF – Lead Has Minimum Required Data")
    connect(conns, "IF – Lead Has Minimum Required Data", "TRANSFORM – Build FlowLeads Payload")
    connect(conns, "TRANSFORM – Build FlowLeads Payload", "HTTP – POST Lead to FlowLeads")
    connect(conns, "HTTP – POST Lead to FlowLeads", "CODE – Collect Sent Lead")
    connect(conns, "CODE – Collect Sent Lead", "CODE – Build Dedupe Mark")
    connect(conns, "CODE – Build Dedupe Mark", "HTTP – CRM Dedupe Mark")

    # sanity: every connection endpoint exists
    names = set(by_name)
    for src_name, spec in conns.items():
        assert src_name in names, src_name
        for outs in spec["main"]:
            for o in outs:
                assert o["node"] in names, (src_name, o["node"])

    return workflow(
        WORKER_NAME,
        list(by_name.values()),
        conns,
        description="One Oikotie page: dedupe via CRM, AI relevance + isännöitsijä enrichment, POST leads to FlowLeads.",
    )


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    source = Path(sys.argv[1])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "flowleads-v2-job-poller.json": build_poller(),
        "flowleads-v2-liidi-ohjaaja.json": build_ohjaaja(),
        "flowleads-v2-lead-worker.json": build_worker(source),
    }
    for filename, wf in outputs.items():
        (OUT_DIR / filename).write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {filename}: {len(wf['nodes'])} nodes")


if __name__ == "__main__":
    main()
