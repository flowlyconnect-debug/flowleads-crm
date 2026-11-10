"""Patched node library derived from the production "Lead Worker" export.

`build_worker()` loads n8n/source/lead-worker-export.json and returns the
worker with its bugs fixed (see n8n/README.md). It is no longer published as
a workflow of its own: build_catalogue_workflows.py lifts its Oikotie fetch,
detail-page and Finder/Asiakastieto nodes into the catalogue workflows.
Run build_catalogue_workflows.py, not this file.
"""

from __future__ import annotations

import copy
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "n8n" / "workflows"

WORKER_NAME = "FlowLeads – Lead Worker (patched library)"

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
