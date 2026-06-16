/**
 * FlowLeads — Action-first Dashboard (Aloita tästä)
 */
(function () {
  'use strict';

  var SKIP_KEY = 'flowleads-dashboard-skipped';
  var STAGE_BY_KIND = {
    overdue_task: { label: 'Tarjous odottaa', color: 'var(--stage-proposal)' },
    hot_lead_no_contact: { label: 'Reagoi seuraavaksi', color: 'var(--stage-interested)' },
    warm_lead_no_contact: { label: 'Liukumassa pois', color: 'var(--color-warning)' },
    new_unprocessed_lead: { label: 'Uusi liidi', color: 'var(--stage-new)' },
  };

  function getCsrfToken() {
    return (
      document.querySelector('meta[name=csrf-token]')?.content ||
      document.getElementById('csrf-token')?.value ||
      ''
    );
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

  function formatEuro(value) {
    if (value == null || Number(value) <= 0) return '—';
    return (
      '€' +
      new Intl.NumberFormat('fi-FI', { maximumFractionDigits: 0 }).format(Number(value))
    );
  }

  function displayMetric(el, value, fallback) {
    if (!el) return;
    if (value == null || value === '' || (typeof value === 'number' && isNaN(value))) {
      el.textContent = fallback != null ? fallback : '—';
      return;
    }
    el.textContent = String(value);
  }

  function parseLeadIdFromUrl(url) {
    var match = (url || '').match(/\/leads\/(\d+)/);
    return match ? parseInt(match[1], 10) : null;
  }

  function parseActionText(actionText) {
    var text = (actionText || '').trim();
    var company = '';
    var contact = '';
    var paren = text.match(/\(([^)]+)\)\s*$/);

    if (paren) {
      company = paren[1].trim();
      contact = text
        .replace(/\s*\([^)]+\)\s*$/, '')
        .replace(/^(Soita|Lähetä follow-up|Käy läpi)\s+/i, '')
        .trim();
    } else {
      contact = text.replace(/^(Soita|Lähetä follow-up|Käy läpi)\s+/i, '').trim();
      company = contact;
      contact = '';
    }

    return { company: company, contact: contact };
  }

  function actionVerb(actionText) {
    if (!actionText) return 'Avaa';
    if (actionText.indexOf('Soita') === 0) return 'Soita';
    if (actionText.indexOf('Lähetä') === 0) return 'Lähetä follow-up';
    if (actionText.indexOf('Käy läpi') === 0) return 'Käy läpi';
    return 'Seuraava toimi';
  }

  function stageForItem(item) {
    if (item && STAGE_BY_KIND[item.kind]) return STAGE_BY_KIND[item.kind];
    return { label: 'Seuraava toimi', color: 'var(--color-text-muted)' };
  }

  function getLeadMetaMap() {
    var root = document.getElementById('dashboard-command-center');
    if (!root) return {};
    try {
      var rows = JSON.parse(root.getAttribute('data-lead-meta') || '[]');
      var map = {};
      rows.forEach(function (row) {
        if (row.lead_id != null) map[row.lead_id] = row;
      });
      return map;
    } catch (e) {
      return {};
    }
  }

  function enrichItem(item, metaMap) {
    var parsed = parseActionText(item.action_text);
    var leadId = parseLeadIdFromUrl(item.url);
    var meta = leadId != null ? metaMap[leadId] : null;
    var company = (meta && meta.company) || parsed.company || '—';
    var contact = (meta && meta.lead_name) || parsed.contact || '';
    var dealValue = meta && meta.deal_value != null ? meta.deal_value : null;

    return {
      item: item,
      company: company,
      contact: contact,
      signal: item.reason || '',
      value: dealValue,
      stage: stageForItem(item),
      verb: actionVerb(item.action_text),
      leadId: leadId,
    };
  }

  function getSkippedUrls() {
    try {
      var raw = sessionStorage.getItem(SKIP_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function skipHeroUrl(url) {
    if (!url) return;
    var skipped = getSkippedUrls();
    if (skipped.indexOf(url) === -1) skipped.push(url);
    try {
      sessionStorage.setItem(SKIP_KEY, JSON.stringify(skipped.slice(-20)));
    } catch (e) {}
  }

  function filterSkipped(items) {
    var skipped = getSkippedUrls();
    return items.filter(function (item) {
      return skipped.indexOf(item.url) === -1;
    });
  }

  function updateActionCount(count) {
    var el = document.getElementById('dashboard-action-count');
    if (el) el.textContent = String(count);
  }

  function updateListMeta(count) {
    var el = document.getElementById('dashboard-list-meta');
    if (!el) return;
    if (!count) {
      el.textContent = 'Ei muita kiireellisiä liidejä';
      return;
    }
    var word = count === 1 ? 'liidi' : 'liidiä';
    el.textContent = count + ' ' + word + ' · reagoi seuraavaksi';
  }

  function heroHeadline(row) {
    var verb = row.verb;
    var company = row.company !== '—' ? row.company : '';
    var contact = row.contact || '';
    if (company) return verb + ' ' + company;
    if (contact) return verb + ' ' + contact;
    return row.item.action_text || verb;
  }

  function heroReasonHtml(row) {
    var signal = row.signal || '';
    var value = row.value;
    if (signal && value != null && Number(value) > 0 && signal.indexOf('€') === -1) {
      return (
        escapeHtml(signal) +
        ' (<strong>' +
        escapeHtml(formatEuro(value)) +
        '</strong>)'
      );
    }
    return escapeHtml(signal);
  }

  function heroContactLine(row) {
    if (row.contact && row.company && row.company !== '—' && row.company !== row.contact) {
      return escapeHtml(row.contact) + ' · ' + escapeHtml(row.company);
    }
    if (row.contact) return escapeHtml(row.contact);
    if (row.company && row.company !== '—') return escapeHtml(row.company);
    return '';
  }

  function renderHeroSideEmpty() {
    return (
      '<aside class="dash-hero__side dash-hero__side--summary" aria-label="Yhteenveto">' +
      '<div class="dash-hero__side-label">Yhteenveto</div>' +
      '<div class="dash-hero__side-value">0</div>' +
      '<div class="dash-hero__side-value-sub">toimea odottaa</div>' +
      '<div class="dash-hero__side-divider" aria-hidden="true"></div>' +
      '<div class="dash-hero__side-signal">' +
      '<span class="dash-hero__side-signal-dot dash-hero__side-signal-dot--ok" aria-hidden="true"></span>' +
      '<span>Liidit ajan tasalla</span>' +
      '</div>' +
      '</aside>'
    );
  }

  function renderHeroEmpty() {
    var hero = document.getElementById('dashboardHero');
    if (!hero) return;
    hero.className = 'dash-hero card dash-hero--idle';
    hero.innerHTML =
      '<div class="dash-hero__main">' +
      '<div class="dash-hero__eyebrow"><span class="dash-hero__dot" aria-hidden="true"></span>Aloita tästä</div>' +
      '<h2 class="dash-hero__action">Ei kiireellisiä toimia juuri nyt.</h2>' +
      '<p class="dash-hero__reason">Kaikki liidit ovat ajan tasalla — hyvää työtä!</p>' +
      '</div>' +
      renderHeroSideEmpty();
  }

  function renderHero(row) {
    var hero = document.getElementById('dashboardHero');
    if (!hero || !row) {
      renderHeroEmpty();
      return;
    }

    var item = row.item;
    var label = row.company !== '—' ? row.company : row.contact || 'liidi';
    var contactLine = heroContactLine(row);
    var signalText = row.signal || row.stage.label;

    hero.className = 'dash-hero card';
    hero.innerHTML =
      '<div class="dash-hero__main">' +
      '<div class="dash-hero__eyebrow"><span class="dash-hero__dot" aria-hidden="true"></span>Aloita tästä</div>' +
      '<h2 class="dash-hero__action">' + escapeHtml(heroHeadline(row)) + '</h2>' +
      (row.signal ? '<p class="dash-hero__reason">' + heroReasonHtml(row) + '</p>' : '') +
      '<div class="dash-hero__cta-row">' +
      '<a class="btn btn-primary btn-lg" href="' + escapeHtml(item.url) + '">Avaa ' + escapeHtml(label) + ' →</a>' +
      '<button type="button" class="dash-hero__skip" data-skip-url="' + escapeHtml(item.url) + '">Ohita</button>' +
      '</div>' +
      '</div>' +
      '<aside class="dash-hero__side" aria-label="Liidin tiedot">' +
      '<div class="dash-hero__side-label">Kaupan arvo</div>' +
      '<div class="dash-hero__side-value">' + escapeHtml(formatEuro(row.value)) + '</div>' +
      '<div class="dash-hero__side-divider" aria-hidden="true"></div>' +
      '<div class="dash-hero__side-signal">' +
      '<span class="dash-hero__side-signal-dot" style="background:' + escapeHtml(row.stage.color) + '"></span>' +
      '<span>' + escapeHtml(signalText) + '</span>' +
      '</div>' +
      (contactLine ? '<div class="dash-hero__side-contact">' + contactLine + '</div>' : '') +
      '</aside>';

    var skipBtn = hero.querySelector('.dash-hero__skip');
    if (skipBtn) {
      skipBtn.addEventListener('click', function () {
        skipHeroUrl(item.url);
        refreshAiWorklist();
      });
    }
  }

  function renderLeadList(rows) {
    var body = document.getElementById('aiWorklistBody');
    if (!body) return;

    if (!rows.length) {
      body.innerHTML =
        '<div class="dash-list-empty">' +
        '<p class="dash-list-empty__title">Ei muita kiireellisiä liidejä tänään.</p>' +
        '<p class="dash-list-empty__sub">Prioriteettitoimet näkyvät yllä olevassa hero-kortissa.</p>' +
        '</div>';
      return;
    }

    body.innerHTML = rows
      .map(function (row) {
        var contactPart = row.contact
          ? '<span class="dash-lead-row__title-muted"> · ' + escapeHtml(row.contact) + '</span>'
          : '';
        return (
          '<a class="dash-lead-row" href="' +
          escapeHtml(row.item.url) +
          '">' +
          '<div class="dash-lead-row__main">' +
          '<div class="dash-lead-row__title">' +
          escapeHtml(row.company) +
          contactPart +
          '</div>' +
          '<div class="dash-lead-row__signal">' +
          escapeHtml(row.signal) +
          '</div>' +
          '</div>' +
          '<div class="dash-lead-row__value">' +
          escapeHtml(formatEuro(row.value)) +
          '</div>' +
          '<div class="dash-lead-row__stage">' +
          '<span class="dash-lead-row__stage-dot" style="background:' +
          escapeHtml(row.stage.color) +
          '"></span>' +
          escapeHtml(row.stage.label) +
          '</div>' +
          '<span class="dash-lead-row__next">' +
          escapeHtml(row.verb) +
          ' →</span>' +
          '</a>'
        );
      })
      .join('');
  }

  function renderDashboardWorklist(items) {
    var metaMap = getLeadMetaMap();
    var available = filterSkipped(items || []);
    var enriched = available.map(function (item) {
      return enrichItem(item, metaMap);
    });

    updateActionCount(enriched.length);

    if (!enriched.length) {
      renderHeroEmpty();
      renderLeadList([]);
      updateListMeta(0);
      var tasksEl = document.getElementById('metricTasksToday');
      if (tasksEl) tasksEl.textContent = '0';
      return;
    }

    renderHero(enriched[0]);
    renderLeadList(enriched.slice(1));
    updateListMeta(enriched.length > 1 ? enriched.length - 1 : 0);

    var tasksEl = document.getElementById('metricTasksToday');
    if (tasksEl) tasksEl.textContent = String(enriched.length);
  }

  function renderNearCloseStats(metaMap) {
    var rows = Object.keys(metaMap).map(function (key) {
      return metaMap[key];
    });
    var near = rows.filter(function (row) {
      return Number(row.probability || 0) >= 0.5 && Number(row.deal_value || 0) > 0;
    });
    var countEl = document.getElementById('metricHotLeads');
    var valueEl = document.getElementById('metricTasksOverdue');
    if (countEl) countEl.textContent = String(near.length);
    if (valueEl) {
      var total = near.reduce(function (sum, row) {
        return sum + Number(row.deal_value || 0);
      }, 0);
      valueEl.textContent = total > 0 ? formatEuro(total) : '';
    }
  }

  function getOrgQueryParams() {
    var root = document.getElementById('dashboard-command-center');
    if (!root) return '';
    var orgQuery = {};
    try {
      orgQuery = JSON.parse(root.getAttribute('data-org-query') || '{}');
    } catch (e) {
      orgQuery = {};
    }
    var params = new URLSearchParams(orgQuery);
    return params.toString() ? '?' + params.toString() : '';
  }

  function fetchDashboardJson(path) {
    return fetch(path + getOrgQueryParams(), { credentials: 'same-origin' }).then(function (r) {
      if (!r.ok) throw new Error('request failed');
      return r.json();
    });
  }

  function renderDashboardMetrics(metrics) {
    if (!metrics) return;
    var newLeads = document.getElementById('metricNewLeads');
    var trend = document.getElementById('metricNewLeadsTrend');
    var pipeline = document.getElementById('metricPipelineValue');

    displayMetric(newLeads, metrics.new_leads_7d != null ? metrics.new_leads_7d : 0, '0');
    if (trend) {
      var delta = Number(metrics.new_leads_delta_pct);
      if (!isNaN(delta) && delta !== 0) {
        var sign = delta >= 0 ? '+' : '';
        trend.textContent = sign + Math.abs(delta) + '% vs edellinen vko';
      } else {
        trend.textContent = '7 pv';
      }
    }
    if (pipeline) {
      pipeline.textContent =
        metrics.pipeline_value == null || Number(metrics.pipeline_value) <= 0
          ? '—'
          : formatEuro(metrics.pipeline_value);
    }
  }

  function refreshDashboardMetrics() {
    fetchDashboardJson('/api/dashboard/metrics')
      .then(function (data) {
        if (!data.success || !data.data) return;
        renderDashboardMetrics(data.data);
      })
      .catch(function () {});
  }

  function refreshAiWorklist() {
    fetchDashboardJson('/api/dashboard/ai-worklist')
      .then(function (data) {
        if (!data.success || !data.data) {
          renderDashboardWorklist([]);
          return;
        }
        var items = data.data.items;
        renderDashboardWorklist(Array.isArray(items) ? items : []);
      })
      .catch(function () {
        var hero = document.getElementById('dashboardHero');
        var body = document.getElementById('aiWorklistBody');
        if (hero) {
          hero.className = 'dash-hero card dash-hero--idle';
          hero.innerHTML =
            '<div class="dash-hero__main">' +
            '<div class="dash-hero__eyebrow"><span class="dash-hero__dot" aria-hidden="true"></span>Aloita tästä</div>' +
            '<p class="dash-hero__action dash-hero__action--muted">Tietoja ei voitu ladata</p>' +
            '<p class="dash-hero__reason"><button type="button" class="lead-retry" onclick="location.reload()">Yritä uudelleen</button></p>' +
            '</div>' +
            renderHeroSideEmpty();
        }
        if (body) {
          body.innerHTML =
            '<div class="dash-list-empty dash-list-empty--error">' +
            '<p class="dash-list-empty__title">Lista ei latautunut</p>' +
            '<p class="dash-list-empty__sub"><button type="button" class="lead-retry" onclick="location.reload()">Yritä uudelleen</button></p>' +
            '</div>';
        }
      });
  }

  window.completeTask = function (taskId, btn) {
    var url =
      btn.getAttribute('data-complete-url') ||
      '/tasks/' + taskId + '/complete';

    fetch(url, {
      method: 'POST',
      headers: {
        Accept: 'application/json',
        'X-CSRFToken': getCsrfToken(),
      },
      credentials: 'same-origin',
    })
      .then(function (r) {
        return r.json().then(function (data) {
          return { ok: r.ok, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data.success) return;
        refreshDashboardMetrics();
        refreshAiWorklist();
      })
      .catch(function () {});
  };

  function init() {
    setCurrentDate();
    renderNearCloseStats(getLeadMetaMap());
    refreshDashboardMetrics();
    refreshAiWorklist();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
