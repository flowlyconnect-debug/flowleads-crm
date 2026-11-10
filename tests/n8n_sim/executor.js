/*
 * Minimal n8n workflow executor used by tests/test_n8n_catalogue_e2e.py.
 *
 * It runs an exported workflow JSON node by node with the semantics the
 * FlowLeads workflows rely on: Code (all items / each item), IF, SplitInBatches
 * v3 (incl. the `reset` option and loop-back), Set, Wait (no-op), HTTP Request
 * (CRM calls go to a real local FlowLeads server; Oikotie, Finder, Asiakastieto
 * are mocked), OpenAI (mocked), `$('Node').first()/.all()/.last()/.item`,
 * `$prevNode`, `onError: continueRegularOutput`, `alwaysOutputData`.
 *
 * It is NOT n8n: it exists to catch wiring / data-flow mistakes (wrong node
 * names, lost context in loops, wrong field names) without an n8n instance.
 *
 * usage: node executor.js <workflow.json> <crmBaseUrl> <mocks.js> [triggerName]
 */
const fs = require('fs');
const path = require('path');
const http = require('http');

const [, , wfPath, crmBase, mocksPath, triggerName = 'Manual Trigger'] = process.argv;
const wf = JSON.parse(fs.readFileSync(wfPath, 'utf8'));
const mocks = require(path.resolve(mocksPath));
const byName = Object.fromEntries(wf.nodes.map(n => [n.name, n]));

const runs = {};          // nodeName -> array of output arrays (per run): [[items for out0], [out1]...]
const sibState = {};      // SplitInBatches state
const log = [];
let steps = 0;

const clone = v => JSON.parse(JSON.stringify(v));
const mkItem = (json, lineage = {}) => ({ json, lineage });

function lastOutput(name) {
  const r = runs[name];
  if (!r || !r.length) throw new Error(`Node '${name}' hasn't been executed`);
  const last = r[r.length - 1];
  // the main data of a node is its first non-empty output of the last run
  return (last.find(o => o && o.length) || last[0] || []);
}

function makeDollar(currentItem) {
  return (name) => {
    if (!byName[name]) throw new Error(`Referenced node doesn't exist: '${name}'`);
    return {
      first: () => lastOutput(name)[0] && { json: lastOutput(name)[0].json },
      last: () => { const o = lastOutput(name); return o.length ? { json: o[o.length - 1].json } : undefined; },
      all: () => lastOutput(name).map(i => ({ json: i.json })),
      get item() {
        if (currentItem && currentItem.lineage[name]) return { json: currentItem.lineage[name] };
        throw new Error(`Paired item for '${name}' not found`);
      },
      isExecuted: !!runs[name],
    };
  };
}

function evalExpr(str, item, prevNode) {
  if (typeof str !== 'string' || !str.startsWith('=')) return str;
  const body = str.slice(1);
  const ctx = { $json: item ? item.json : {}, $: makeDollar(item), $prevNode: { name: prevNode } };
  const run = (expr) => Function('$json', '$', '$prevNode', `return (${expr});`)(ctx.$json, ctx.$, ctx.$prevNode);
  const whole = body.match(/^\{\{([\s\S]*)\}\}$/);
  if (whole && !whole[1].includes('}}')) return run(whole[1]);
  return body.replace(/\{\{([\s\S]*?)\}\}/g, (_, e) => {
    const v = run(e);
    return typeof v === 'object' ? JSON.stringify(v) : String(v);
  });
}

function deepEval(value, item, prevNode) {
  if (Array.isArray(value)) return value.map(v => deepEval(v, item, prevNode));
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = deepEval(v, item, prevNode);
    return out;
  }
  return evalExpr(value, item, prevNode);
}

function crmRequest(method, url, headers, body) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const req = http.request({ hostname: u.hostname, port: u.port, path: u.pathname + u.search, method, headers }, res => {
      let data = '';
      res.on('data', c => (data += c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text: data }));
    });
    req.on('error', reject);
    if (body !== undefined) req.write(body);
    req.end();
  });
}

async function runHttp(node, item, prevNode) {
  const p = deepEval(node.parameters, item, prevNode);
  let url = p.url;
  if (p.sendQuery && p.queryParameters) {
    const qs = new URLSearchParams();
    for (const q of p.queryParameters.parameters || []) qs.append(q.name, q.value);
    url += (url.includes('?') ? '&' : '?') + qs.toString();
  }
  const headers = {};
  for (const h of (p.headerParameters && p.headerParameters.parameters) || []) headers[h.name] = String(h.value);
  const method = p.method || 'GET';
  let body;
  if (p.sendBody) body = typeof p.jsonBody === 'string' ? p.jsonBody : JSON.stringify(p.jsonBody);
  const resp = p.response || (p.options && p.options.response && p.options.response.response) || {};
  let res;
  if (url.startsWith(crmBase)) {
    res = await crmRequest(method, url, headers, body);
  } else {
    res = mocks.http({ method, url, headers, body });
  }
  log.push(`${node.name}: ${method} ${url.slice(0, 120)} -> ${res.status}`);
  if (res.status >= 400 && !resp.neverError) {
    throw new Error(`HTTP ${res.status} from ${url}: ${String(res.text).slice(0, 200)}`);
  }
  let parsed = res.text;
  if (resp.responseFormat !== 'text') { try { parsed = JSON.parse(res.text); } catch (e) { parsed = { data: res.text }; } }
  if (resp.fullResponse) {
    const out = { headers: res.headers || {}, statusCode: res.status };
    const isText = typeof parsed === 'string' || (parsed && Object.keys(parsed).length === 1 && typeof parsed.data === 'string');
    const value = parsed && typeof parsed === 'object' && Object.keys(parsed).length === 1 && typeof parsed.data === 'string' ? parsed.data : parsed;
    out[resp.outputPropertyName || (isText ? 'data' : 'body')] = value;
    return out;
  }
  if (typeof parsed === 'string') return { [resp.outputPropertyName || 'data']: parsed };
  return parsed;
}

async function runCode(node, input) {
  const js = node.parameters.jsCode;
  const each = node.parameters.mode === 'runOnceForEachItem';
  const fn = new Function('$input', '$json', 'items', '$', '$prevNode', 'Date', `return (async () => {\n${js}\n})();`);
  const outs = [];
  if (each) {
    for (const it of input) {
      const r = await fn({ first: () => ({ json: it.json }), all: () => [{ json: it.json }], item: { json: it.json } },
        it.json, [{ json: it.json }], makeDollar(it), {}, Date);
      if (r && r.json) outs.push(mkItem(r.json, { ...it.lineage }));
    }
  } else {
    const wrapped = input.map(i => ({ json: i.json }));
    const r = await fn({ first: () => wrapped[0], all: () => wrapped, item: wrapped[0] },
      input[0] ? input[0].json : {}, wrapped, makeDollar(input[0]), {}, Date);
    const arr = Array.isArray(r) ? r : (r ? [r] : []);
    arr.forEach((x, i) => outs.push(mkItem(x.json, { ...((input[i] || input[0] || {}).lineage || {}) })));
  }
  return [outs];
}

function evalIf(node, item, prevNode) {
  const c = node.parameters.conditions.conditions[0];
  const left = evalExpr(c.leftValue, item, prevNode);
  const op = c.operator;
  if (op.type === 'boolean' && op.operation === 'true') return left === true || left === 'true';
  if (op.type === 'boolean' && op.operation === 'false') return !(left === true || left === 'true');
  if (op.type === 'string' && op.operation === 'equals') return String(left) === String(evalExpr(c.rightValue, item, prevNode));
  if (op.type === 'string' && op.operation === 'notEquals') return String(left) !== String(evalExpr(c.rightValue, item, prevNode));
  if (op.operation === 'notEmpty') return left !== undefined && left !== null && String(left) !== '';
  throw new Error(`IF operator not supported in sim: ${JSON.stringify(op)}`);
}

async function execNode(name, input, prevNode) {
  const node = byName[name];
  if (++steps > 20000) throw new Error('step limit exceeded (infinite loop?)');
  const t = node.type.split('.').pop();
  let outputs;
  try {
    if (t === 'manualTrigger' || t === 'scheduleTrigger') outputs = [[mkItem({})]];
    else if (t === 'set') {
      outputs = [input.map(it => {
        const json = node.parameters.includeOtherFields ? { ...it.json } : {};
        for (const a of node.parameters.assignments.assignments) json[a.name] = evalExpr(a.value, it, prevNode);
        return mkItem(json, { ...it.lineage });
      })];
    } else if (t === 'code') outputs = await runCode(node, input);
    else if (t === 'wait') outputs = [input];
    else if (t === 'if') {
      const yes = [], no = [];
      for (const it of input) (evalIf(node, it, prevNode) ? yes : no).push(it);
      outputs = [yes, no];
    } else if (t === 'splitInBatches') {
      const size = node.parameters.batchSize || 1;
      const reset = node.parameters.options && node.parameters.options.reset
        ? evalExpr(node.parameters.options.reset, input[0], prevNode) : false;
      let st = sibState[name];
      if (!st || reset === true) {
        st = sibState[name] = { queue: input.slice(), done: [] };
      } else {
        st.done.push(...input);
      }
      if (st.queue.length) outputs = [[], st.queue.splice(0, size)];
      else { outputs = [st.done.slice(), []]; delete sibState[name]; }
    } else if (t === 'httpRequest') {
      const out = [];
      for (const it of input) {
        let json;
        const tries = node.retryOnFail ? (node.maxTries || 3) : 1;
        let lastErr;
        for (let i = 0; i < tries; i++) {
          try { json = await runHttp(node, it, prevNode); lastErr = null; break; } catch (e) { lastErr = e; }
        }
        if (lastErr) {
          if (node.onError === 'continueRegularOutput') json = { error: { message: lastErr.message } };
          else throw lastErr;
        }
        out.push(mkItem(json, { ...it.lineage }));
      }
      outputs = [out];
    } else if (t === 'openAi') {
      const out = [];
      for (const it of input) {
        const values = deepEval(node.parameters.responses.values, it, prevNode);
        out.push(mkItem({ output: [{ content: [{ text: mocks.openai(values, node.name) }] }] }, { ...it.lineage }));
      }
      outputs = [out];
    } else throw new Error(`node type not supported in sim: ${node.type}`);
  } catch (e) {
    if (node.onError === 'continueRegularOutput') outputs = [input.map(it => mkItem({ error: { message: e.message } }, it.lineage))];
    else throw new Error(`[${name}] ${e.message}`);
  }
  if (node.alwaysOutputData && !(outputs[0] && outputs[0].length)) outputs[0] = [mkItem({})];
  // record lineage of this node on its own outputs
  outputs = outputs.map(o => (o || []).map(it => ({ json: it.json, lineage: { ...it.lineage, [name]: it.json } })));
  (runs[name] = runs[name] || []).push(outputs);
  return outputs;
}

async function main() {
  // depth-first execution, like n8n's v1 execution order
  const stack = [{ name: triggerName, input: [], prev: '' }];
  while (stack.length) {
    const { name, input, prev } = stack.pop();
    const outputs = await execNode(name, input, prev);
    const conns = (wf.connections[name] && wf.connections[name].main) || [];
    const next = [];
    conns.forEach((targets, idx) => {
      const items = outputs[idx] || [];
      if (!items.length) return;
      for (const t of targets || []) next.push({ name: t.node, input: items, prev: name });
    });
    for (let i = next.length - 1; i >= 0; i--) stack.push(next[i]);
  }
  const summary = {};
  for (const [n, r] of Object.entries(runs)) summary[n] = r.length;
  process.stdout.write(JSON.stringify({ ok: true, runs: summary, log }) + '\n');
}

main().catch(e => {
  process.stdout.write(JSON.stringify({ ok: false, error: e.message, log: log.slice(-40) }) + '\n');
  process.exit(1);
});
