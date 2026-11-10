/**
 * FlowLeads — Kojelauta: päivän työjono.
 *
 * Reading order is the same as in Liidit and Myyntiputki:
 * kohteen nimi → lähde ja vaihe → seuraava toimi.
 */
(function () {
  'use strict';

  var SKIP_KEY = 'flowleads-dashboard-skipped';

  // Why an item is in the queue right now. Colour always comes with a label
  // and an icon: blue = arrival, amber = real urgency.
  var WHY_BY_KIND = {
    overdue_task: { label: 'Tehtävä myöhässä', tone: 'urgent' },
    hot_lead_no_contact: { label: 'Odottaa yhteydenottoa', tone: 'urgent' },
    warm_lead_no_contact: { label: 'Hiljentynyt kohde', tone: 'urgent' },
    new_unprocessed_lead: { label: 'Uusi liidi', tone: 'arrival' },
    new_lead: { label: 'Uusi liidi', tone: 'arrival' },
    recommendation: { label: 'Jatkotoimi odottaa', tone: 'neutral' },
  };

  var ICONS = {
    arrival:
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11"/><path d="m7 11 5 5 5-5"/><path d="M5 20h14"/></svg>',
    urgent:
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
    neutral:
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>',
    done:
      '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m5 13 4 4L19 7"/></svg>',
  };

  var root = null;
  var state = {
    worklist: null, // null = not loaded yet
    today: null,
    metrics: null,
    failed: false,
  };

  function getCsrfToken() {
    return (
      document.querySelector('meta[name=csrf-token]')?.content ||
      document.getElementById('csrf-token')?.value ||
      ''
    );
  }

  function readJsonAttr(name, fallback) {
    if (!root) return fallback;
    try {
      return JSON.parse(root.getAttribute(name) || '') || fallback;
    } catch (e) {
      return fallback;
    }
  }

  function setCurrentDate() {
    var el = document.getElementById('dashboard-current-date');
    if (!el) return;
    try {
      var formatted = new Intl.DateTimeFormat('fi-FI', {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
      }).format(new Date());
      el.textContent = formatted.charAt(0).toUpperCase() + formatted.slice(1);
      el.setAttribute('datetime', new Date().toISOString().slice(0, 10));
    } catch (e) {
      el.textContent = new Date().toLocaleDateString('fi-FI');
    }
  }

  function escapeHtml(text) {
    var d = document.createElement('div');
    d.textContent = text == null ? '' : String(text);
    return d.innerHTML;
  }

  function currency() {
    return (root && root.getAttribute('data-currency')) || '€';
  }

  /** null/'' = not estimated (distinct from a real zero). */
  function hasValue(value) {
    return value !== null && value !== undefined && value !== '' && !isNaN(Number(value));
  }

  function formatMoney(value) {
    return (
      currency() +
      new Intl.NumberFormat('fi-FI', { maximumFractionDigits: 0 }).format(Number(value))
    );
  }

  function stageLabel(name) {
    if (!name) return '';
    var map = readJsonAttr('data-stage-labels', {});
    return map[String(name).trim().toLowerCase()] || name;
  }

  function sourceLabel(code) {
    if (!code) return '';
    var map = readJsonAttr('data-source-labels', {});
    return map[String(code).trim().toLowerCase()] || code;
  }

  function orgQueryString() {
    var params = new URLSearchParams(readJsonAttr('data-org-query', {}));
    return params.toString();
  }

  function withOrg(url) {
    var qs = orgQueryString();
    if (!qs || !url) return url;
    return url + (url.indexOf('?') === -1 ? '?' : '&') + qs;
  }

  function parseLeadIdFromUrl(url) {
    var match = (url || '').match(/\/leads\/(\d+)/);
    return match ? parseInt(match[1], 10) : null;
  }

  function getLeadMetaMap() {
    var rows = readJsonAttr('data-lead-meta', []);
    var map = {};
    rows.forEach(function (row) {
      if (row.lead_id != null) map[row.lead_id] = row;
    });
    return map;
  }

  /* ── Normalising the three data sources into one row shape ── */

  function verbFromActionText(text) {
    if (!text) return 'Avaa';
    if (text.indexOf('Soita') === 0) return 'Soita';
    if (text.indexOf('Lähetä') === 0) return 'Lähetä follow-up';
    if (text.indexOf('Käy läpi') === 0) return 'Käy läpi';
    return 'Avaa';
  }

  function nameFromActionText(text) {
    var t = (text || '').trim();
    var paren = t.match(/\(([^)]+)\)\s*$/);
    if (paren) return paren[1].trim();
    return t.replace(/^(Soita|Lähetä follow-up|Käy läpi)\s+/i, '').trim();
  }

  function rowFromWorklist(item, metaMap) {
    var leadId = item.lead_id != null ? item.lead_id : parseLeadIdFromUrl(item.url);
    var meta = leadId != null ? metaMap[leadId] : null;
    var name = item.company || item.name || nameFromActionText(item.action_text) || 'Liidi';
    var contact = item.name && item.company && item.name !== item.company ? item.name : '';
    var value = hasValue(item.deal_value)
      ? item.deal_value
      : meta && Number(meta.deal_value) > 0 // forecast rows report unestimated as 0
        ? meta.deal_value
        : null;
    return {
      key: leadId != null ? 'lead-' + leadId : item.url,
      leadId: leadId,
      url: withOrg(item.url),
      rawUrl: item.url,
      kind: item.kind,
      name: name,
      contact: contact,
      source: item.source || '',
      stage: item.stage || '',
      stageColor: item.stage_color || '',
      value: value,
      reason: item.reason || '',
      // Same wording as Liidit and Myyntiputki use for an uncontacted arrival.
      next: item.kind === 'new_unprocessed_lead' ? 'Ensikontakti' : verbFromActionText(item.action_text),
      suggested: true,
    };
  }

  function rowFromNewLeadEl(el) {
    var leadId = parseInt(el.getAttribute('data-lead-id'), 10);
    var days = el.getAttribute('data-created-days');
    var when = '';
    if (days !== '' && days != null) {
      var d = Number(days);
      when = d === 0 ? 'Saapui tänään' : d === 1 ? 'Saapui eilen' : 'Saapui ' + d + ' pv sitten';
    }
    var rawValue = el.getAttribute('data-deal-value');
    return {
      key: 'lead-' + leadId,
      leadId: leadId,
      url: el.getAttribute('href'),
      rawUrl: '/leads/' + leadId,
      kind: 'new_lead',
      name: el.getAttribute('data-name') || 'Liidi',
      contact: el.getAttribute('data-contact') || '',
      source: el.getAttribute('data-source') || '',
      stage: el.getAttribute('data-stage') || '',
      stageColor: el.getAttribute('data-stage-color') || '',
      value: rawValue === '' || rawValue == null ? null : Number(rawValue),
      reason: when ? when + ', ei vielä kontaktia' : 'Ei vielä kontaktia',
      next: 'Ensikontakti',
      suggested: true,
      el: el,
    };
  }

  function rowFromRecommendation(rec) {
    return {
      key: 'lead-' + rec.id,
      leadId: rec.id,
      url: withOrg(rec.url),
      rawUrl: rec.url,
      kind: 'recommendation',
      tone: rec.type === 'risk' ? 'urgent' : 'neutral',
      name: rec.company || rec.name || 'Liidi',
      contact: rec.name && rec.company && rec.name !== rec.company ? rec.name : '',
      source: '',
      stage: '',
      stageColor: '',
      value: null,
      reason: '',
      next: rec.recommendation || 'Avaa',
      suggested: true,
    };
  }

  function rowFromOverdueTask(task) {
    var leadId = parseLeadIdFromUrl(task.lead_url);
    var days = Number(task.days_overdue || 0);
    return {
      key: leadId != null ? 'lead-' + leadId : 'task-' + task.id,
      leadId: leadId,
      url: withOrg(task.lead_url || '/tasks'),
      rawUrl: task.lead_url || '/tasks',
      kind: 'overdue_task',
      name: task.lead_name && task.lead_name !== '—' ? task.lead_name : task.title,
      contact: '',
      source: '',
      stage: '',
      stageColor: '',
      value: null,
      reason: days > 0 ? 'Tehtävä myöhässä ' + days + ' pv' : 'Tehtävä erääntyi tänään',
      next: task.title || 'Avaa tehtävä',
      suggested: false, // a task the user recorded, not a system suggestion
    };
  }

  /* ── Skip (Ohita) — session-scoped, unchanged behaviour ── */

  function getSkipped() {
    try {
      var raw = sessionStorage.getItem(SKIP_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function skip(rawUrl) {
    if (!rawUrl) return;
    var skipped = getSkipped();
    if (skipped.indexOf(rawUrl) === -1) skipped.push(rawUrl);
    try {
      sessionStorage.setItem(SKIP_KEY, JSON.stringify(skipped.slice(-20)));
    } catch (e) {}
  }

  /* ── Shared row pieces ── */

  function whyFor(row) {
    var why = WHY_BY_KIND[row.kind] || WHY_BY_KIND.recommendation;
    return { label: why.label, tone: row.tone || why.tone };
  }

  function metaHtml(row) {
    var parts = [];
    if (row.source) {
      parts.push('<span class="lead-source">' + escapeHtml(sourceLabel(row.source)) + '</span>');
    }
    if (row.stage) {
      parts.push(
        '<span class="lead-stage"><span class="lead-stage__dot" style="background:' +
          escapeHtml(row.stageColor || '#94A3B8') +
          '"></span>' +
          escapeHtml(stageLabel(row.stage)) +
          '</span>'
      );
    }
    if (row.contact) {
      parts.push('<span class="work-row__when">' + escapeHtml(row.contact) + '</span>');
    }
    if (row.reason) {
      parts.push('<span class="work-row__when">' + escapeHtml(row.reason) + '</span>');
    }
    return parts.join('');
  }

  function rowHtml(row) {
    var why = whyFor(row);
    return (
      '<a class="work-row" href="' + escapeHtml(row.url) + '">' +
      '<span class="work-row__main">' +
      '<span class="work-row__name">' + escapeHtml(row.name) + '</span>' +
      '<span class="work-row__meta">' + metaHtml(row) + '</span>' +
      '</span>' +
      (hasValue(row.value)
        ? '<span class="work-row__value">' + escapeHtml(formatMoney(row.value)) + '</span>'
        : '') +
      '<span class="lead-next' + (why.tone === 'urgent' ? ' lead-next--urgent' : '') + '">' +
      escapeHtml(row.next) +
      '<span class="lead-next__arrow" aria-hidden="true">→</span></span>' +
      '</a>'
    );
  }

  /* ── Focus panel (the dark one) ── */

  function renderFocus(row, counts) {
    var hero = document.getElementById('dashboardHero');
    if (!hero) return;
    var why = whyFor(row);
    var valueText = hasValue(row.value) ? formatMoney(row.value) : 'Arvoa ei ole arvioitu';

    var facts = [];
    if (row.source) facts.push('<span class="lead-source">' + escapeHtml(sourceLabel(row.source)) + '</span>');
    if (row.stage) {
      facts.push(
        '<span class="lead-stage"><span class="lead-stage__dot" style="background:' +
          escapeHtml(row.stageColor || '#94A3B8') + '"></span>' + escapeHtml(stageLabel(row.stage)) + '</span>'
      );
    }
    if (row.contact) facts.push('<span>' + escapeHtml(row.contact) + '</span>');
    facts.push(
      '<span class="' + (hasValue(row.value) ? 'dash-focus__value' : 'dash-focus__value dash-focus__value--missing') + '">' +
        escapeHtml(valueText) + '</span>'
    );

    hero.className = 'dash-focus';
    hero.innerHTML =
      '<div class="dash-focus__main">' +
      '<div class="dash-focus__why dash-focus__why--' + why.tone + '">' +
      ICONS[why.tone] + '<span>' + escapeHtml(why.label) + '</span>' +
      (row.reason ? '<span class="dash-focus__why-detail">' + escapeHtml(row.reason) + '</span>' : '') +
      '</div>' +
      '<h2 class="dash-focus__name"><a href="' + escapeHtml(row.url) + '">' + escapeHtml(row.name) + '</a></h2>' +
      '<div class="dash-focus__facts">' + facts.join('') + '</div>' +
      '<div class="dash-focus__action">' +
      '<div class="dash-focus__next">' +
      '<span class="dash-focus__next-label">' + (row.suggested ? 'Ehdotettu seuraava toimi' : 'Kirjattu seuraava toimi') + '</span>' +
      '<span class="dash-focus__next-text">' + escapeHtml(row.next) + '</span>' +
      '</div>' +
      '<div class="dash-focus__cta">' +
      '<a class="btn btn-primary btn-lg dash-focus__open" href="' + escapeHtml(row.url) + '">Avaa liidi <span aria-hidden="true">→</span></a>' +
      '<button type="button" class="dash-focus__skip">Ohita toistaiseksi</button>' +
      '</div>' +
      '</div>' +
      '</div>' +
      queueSummaryHtml(counts);

    var skipBtn = hero.querySelector('.dash-focus__skip');
    if (skipBtn) {
      skipBtn.addEventListener('click', function () {
        skip(row.rawUrl);
        render();
      });
    }
  }

  function countLine(n, one, many, extraClass) {
    return (
      '<li class="dash-queue__item' + (n === 0 ? ' is-zero' : '') + (extraClass && n > 0 ? ' ' + extraClass : '') + '">' +
      '<span class="dash-queue__num">' + n + '</span>' +
      '<span class="dash-queue__label">' + (n === 1 ? one : many) + '</span>' +
      '</li>'
    );
  }

  function queueSummaryHtml(counts) {
    return (
      '<aside class="dash-focus__side" aria-label="Työjonon tilanne">' +
      '<div class="dash-queue__title">Jonossa nyt</div>' +
      '<ul class="dash-queue">' +
      countLine(counts.newLeads, 'uusi liidi', 'uutta liidiä') +
      countLine(counts.follow, 'seurattava kohde', 'seurattavaa kohdetta') +
      countLine(counts.urgent, 'kiireellinen', 'kiireellistä', 'is-urgent') +
      '</ul>' +
      '</aside>'
    );
  }

  /** Nothing named to open: one compact, light status line. */
  function renderFocusIdle(counts) {
    var hero = document.getElementById('dashboardHero');
    if (!hero) return;
    var newCount = state.metrics && state.metrics.new_leads_7d != null ? Number(state.metrics.new_leads_7d) : 0;
    var newUrl = (root && root.getAttribute('data-new-leads-url')) || '/leads';
    hero.className = 'dash-focus dash-focus--idle';

    if (newCount > 0 && counts.newLeads === 0) {
      // Only a count is available (e.g. the leads already have activity).
      hero.innerHTML =
        '<div class="dash-idle">' +
        '<span class="dash-idle__icon dash-idle__icon--arrival">' + ICONS.arrival + '</span>' +
        '<div class="dash-idle__text"><strong>' +
        newCount + (newCount === 1 ? ' uusi liidi' : ' uutta liidiä') +
        ' viimeisen 7 päivän aikana.</strong> Ei kiireellisiä toimia.</div>' +
        '<a class="btn btn-primary btn-sm" href="' + escapeHtml(newUrl) + '">' +
        (newCount === 1 ? 'Katso uusi liidi' : 'Katso uudet liidit') + ' <span aria-hidden="true">→</span></a>' +
        '</div>';
      return;
    }

    hero.innerHTML =
      '<div class="dash-idle">' +
      '<span class="dash-idle__icon">' + ICONS.done + '</span>' +
      '<div class="dash-idle__text"><strong>Työjono on tyhjä.</strong> ' +
      'Uudet liidit ja jatkotoimet ilmestyvät tähän, kun niitä tulee.</div>' +
      '<a class="btn btn-secondary btn-sm" href="' + escapeHtml(withOrg('/leads/pipeline')) + '">Avaa myyntiputki</a>' +
      '</div>';
  }

  function renderFocusError() {
    var hero = document.getElementById('dashboardHero');
    if (!hero) return;
    hero.className = 'dash-focus dash-focus--idle';
    hero.innerHTML =
      '<div class="dash-idle">' +
      '<div class="dash-idle__text"><strong>Työjonoa ei voitu ladata.</strong></div>' +
      '<button type="button" class="btn btn-secondary btn-sm" onclick="location.reload()">Yritä uudelleen</button>' +
      '</div>';
  }

  /* ── Main render ── */

  function render() {
    if (state.failed) {
      renderFocusError();
      var bodyErr = document.getElementById('aiWorklistBody');
      if (bodyErr) bodyErr.innerHTML = '<p class="dash-panel__empty">Listaa ei voitu ladata.</p>';
      return;
    }
    if (state.worklist === null || state.today === null) return; // still loading

    var skipped = getSkipped();
    var metaMap = getLeadMetaMap();
    var notSkipped = function (row) {
      return skipped.indexOf(row.rawUrl) === -1;
    };

    var newEls = Array.prototype.slice.call(document.querySelectorAll('#dashNewLeads .work-row'));
    var newRows = newEls.map(rowFromNewLeadEl);
    var newIds = {};
    newRows.forEach(function (r) {
      newIds[r.key] = true;
    });

    var workRows = state.worklist.map(function (item) {
      return rowFromWorklist(item, metaMap);
    });

    // Focus: the ranked worklist first (existing prioritisation), otherwise
    // the newest arrived lead.
    var focus = workRows.filter(notSkipped)[0] || newRows.filter(notSkipped)[0] || null;

    // Seurattavat kohteet: everything that is not a fresh arrival.
    var follow = [];
    var seen = {};
    function pushFollow(row) {
      if (!row || seen[row.key] || newIds[row.key]) return;
      if (focus && row.key === focus.key) return;
      seen[row.key] = true;
      follow.push(row);
    }
    workRows.forEach(function (row) {
      if (row.kind !== 'new_unprocessed_lead') pushFollow(row);
    });
    (state.today.overdue_tasks || []).map(rowFromOverdueTask).forEach(pushFollow);
    (state.today.ai_recommendations || []).map(rowFromRecommendation).forEach(pushFollow);

    // Uudet liidit: hide the one already shown in the focus panel.
    var visibleNew = 0;
    newRows.forEach(function (row) {
      var hide = !!(focus && row.key === focus.key);
      row.el.hidden = hide;
      if (!hide) visibleNew += 1;
    });

    var urgent = follow.filter(function (r) {
      return whyFor(r).tone === 'urgent';
    }).length + (focus && whyFor(focus).tone === 'urgent' ? 1 : 0);

    var counts = {
      newLeads: newRows.length,
      follow: follow.length + (focus && !newIds[focus.key] ? 1 : 0),
      urgent: urgent,
    };

    if (focus) renderFocus(focus, counts);
    else renderFocusIdle(counts);

    // Lists
    var body = document.getElementById('aiWorklistBody');
    var columns = document.getElementById('dashColumns');
    var newPanel = document.getElementById('dashNewPanel');
    var followPanel = document.getElementById('dashFollowPanel');
    var newEmpty = document.getElementById('dashNewEmpty');
    var listMeta = document.getElementById('dashboard-list-meta');

    if (body) body.innerHTML = follow.slice(0, 6).map(rowHtml).join('');
    if (listMeta) {
      listMeta.textContent = follow.length > 6 ? '6 / ' + follow.length + ' tärkeintä' : '';
    }

    var hasNew = visibleNew > 0;
    var hasFollow = follow.length > 0;
    if (columns) {
      // When neither section has work, the focus panel already says so.
      columns.hidden = !hasNew && !hasFollow;
      columns.classList.toggle('dash-columns--single', hasNew !== hasFollow);
    }
    if (newPanel) newPanel.hidden = !hasNew;
    if (followPanel) followPanel.hidden = !hasFollow;
    if (newEmpty) newEmpty.hidden = true;
  }

  /* ── Stats ── */

  function setStat(id, text, isZero) {
    var el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('is-zero', !!isZero);
  }

  function renderNearCloseStats() {
    var metaMap = getLeadMetaMap();
    var near = Object.keys(metaMap)
      .map(function (k) {
        return metaMap[k];
      })
      .filter(function (row) {
        return Number(row.probability || 0) >= 0.5 && Number(row.deal_value || 0) > 0;
      });
    setStat('metricHotLeads', String(near.length), near.length === 0);
    var total = near.reduce(function (sum, row) {
      return sum + Number(row.deal_value || 0);
    }, 0);
    var valueEl = document.getElementById('metricHotLeadsValue');
    if (valueEl) valueEl.textContent = total > 0 ? formatMoney(total) : '';
  }

  function renderMetrics(metrics) {
    if (!metrics) return;
    var newLeads = Number(metrics.new_leads_7d || 0);
    setStat('metricNewLeads', String(newLeads), newLeads === 0);

    var trend = document.getElementById('metricNewLeadsTrend');
    if (trend) {
      // A percentage is only meaningful against a non-zero previous week.
      var prev = Number(metrics.new_leads_prev_7d || 0);
      var delta = Number(metrics.new_leads_delta_pct);
      if (prev > 0 && !isNaN(delta) && delta !== 0) {
        trend.textContent = (delta > 0 ? '+' : '−') + Math.abs(delta) + ' % ed. 7 pv:ään';
      } else if (prev > 0) {
        trend.textContent = 'sama kuin ed. 7 pv';
      } else {
        trend.textContent = '';
      }
    }

    if (metrics.pipeline_value == null) {
      setStat('metricPipelineValue', 'Ei arvioitu', true);
    } else {
      setStat('metricPipelineValue', formatMoney(metrics.pipeline_value), Number(metrics.pipeline_value) === 0);
    }

    var tasksToday = Number(metrics.tasks_today || 0);
    setStat('metricTasksToday', String(tasksToday), tasksToday === 0);
    var overdueEl = document.getElementById('metricTasksOverdue');
    if (overdueEl) {
      var overdue = Number(metrics.overdue_tasks || 0);
      overdueEl.textContent = overdue > 0 ? overdue + ' myöhässä' : '';
      overdueEl.classList.toggle('dash-stat__sub--urgent', overdue > 0);
    }
  }

  /* ── Data loading ── */

  function fetchJson(path) {
    var qs = orgQueryString();
    return fetch(path + (qs ? '?' + qs : ''), { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('request failed');
      return r.json();
    });
  }

  function refreshMetrics() {
    fetchJson('/api/dashboard/metrics')
      .then(function (data) {
        if (!data.success || !data.data) return;
        state.metrics = data.data;
        renderMetrics(data.data);
        render();
      })
      .catch(function () {});
  }

  function refreshWorklist() {
    fetchJson('/api/dashboard/ai-worklist')
      .then(function (data) {
        var items = data && data.success && data.data ? data.data.items : [];
        state.worklist = Array.isArray(items) ? items : [];
        state.failed = false;
        render();
      })
      .catch(function () {
        state.failed = true;
        render();
      });
  }

  function refreshToday() {
    fetchJson('/api/dashboard/today')
      .then(function (data) {
        state.today = data && data.success && data.data ? data.data : {};
        render();
      })
      .catch(function () {
        // Secondary source: the queue still works from the worklist alone.
        state.today = {};
        render();
      });
  }

  window.completeTask = function (taskId, btn) {
    var url = btn.getAttribute('data-complete-url') || '/tasks/' + taskId + '/complete';
    fetch(url, {
      method: 'POST',
      headers: { Accept: 'application/json', 'X-CSRFToken': getCsrfToken() },
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data.success) return;
        refreshMetrics();
        refreshWorklist();
        refreshToday();
      })
      .catch(function () {});
  };

  function init() {
    root = document.getElementById('dashboard-command-center');
    if (!root) return;
    setCurrentDate();
    renderNearCloseStats();
    refreshMetrics();
    refreshWorklist();
    refreshToday();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
