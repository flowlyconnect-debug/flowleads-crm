/**
 * FlowLeads CRM — dashboard charts (Chart.js)
 */
(function () {
  'use strict';

  var chartInstances = {};

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function withAlpha(color, alpha) {
    if (!color) return 'rgba(29, 107, 243, ' + alpha + ')';
    var hex = color.trim();
    if (hex.indexOf('#') === 0 && hex.length === 7) {
      var r = parseInt(hex.slice(1, 3), 16);
      var g = parseInt(hex.slice(3, 5), 16);
      var b = parseInt(hex.slice(5, 7), 16);
      return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
    }
    return color;
  }

  function destroyChart(key) {
    if (chartInstances[key]) {
      chartInstances[key].destroy();
      chartInstances[key] = null;
    }
  }

  function initFunnelBars() {
    document.querySelectorAll('.pipeline-funnel__bar[data-pct]').forEach(function (bar) {
      var pct = parseFloat(bar.getAttribute('data-pct') || '0', 10);
      if (Number.isNaN(pct)) pct = 0;
      pct = Math.max(0, Math.min(100, pct));
      var color = bar.getAttribute('data-color');
      if (color) {
        bar.style.setProperty('--stage-color', color);
      }
      requestAnimationFrame(function () {
        bar.style.width = pct + '%';
      });
    });
  }

  function initForecastProgress() {
    document.querySelectorAll('[data-forecast-pct]').forEach(function (el) {
      var pct = parseFloat(el.getAttribute('data-forecast-pct') || '0', 10);
      if (Number.isNaN(pct)) pct = 0;
      pct = Math.max(0, Math.min(100, pct));
      el.style.width = pct + '%';
    });
  }

  function initEmailBars() {
    document.querySelectorAll('.dashboard-email-bar__fill[data-pct]').forEach(function (el) {
      var pct = parseFloat(el.getAttribute('data-pct') || '0', 10);
      if (Number.isNaN(pct)) pct = 0;
      el.style.width = Math.max(0, Math.min(100, pct)) + '%';
    });
  }

  function setCurrentDate() {
    var el = document.getElementById('dashboard-current-date');
    if (!el) return;
    try {
      el.textContent = new Intl.DateTimeFormat('fi-FI', {
        weekday: 'long',
        day: 'numeric',
        month: 'long',
        year: 'numeric',
      }).format(new Date());
    } catch (e) {
      el.textContent = new Date().toLocaleDateString('fi-FI');
    }
  }

  function initRangeSwitcher() {
    var wrap = document.querySelector('.dashboard-range-switcher');
    if (!wrap) return;
    wrap.querySelectorAll('[data-range]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        wrap.querySelectorAll('[data-range]').forEach(function (b) {
          b.classList.remove('is-active');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('is-active');
        btn.setAttribute('aria-pressed', 'true');
      });
    });
  }

  function initCharts() {
    var root = document.getElementById('dashboard-analytics');
    if (!root || typeof Chart === 'undefined') return;

    var leadsRaw = root.getAttribute('data-leads-per-day');
    var sourcesRaw = root.getAttribute('data-sources-pie');
    var leadsPerDay = [];
    var sourcesPie = [];

    try {
      leadsPerDay = leadsRaw ? JSON.parse(leadsRaw) : [];
    } catch (e) {
      leadsPerDay = [];
    }
    try {
      sourcesPie = sourcesRaw ? JSON.parse(sourcesRaw) : [];
    } catch (e) {
      sourcesPie = [];
    }

    var accent = cssVar('--color-accent') || '#1D6BF3';
    var success = cssVar('--color-success') || '#10B981';
    var gridColor = cssVar('--color-border-light') || '#F0F2F8';
    var muted = cssVar('--color-text-muted') || '#9CA3AF';

    var leadsCanvas = document.getElementById('leadsPerDayChart');
    var leadsEmpty = document.getElementById('leads-chart-empty');
    var hasLeadsData = leadsPerDay.some(function (d) {
      return Number(d.count) > 0;
    });

    if (leadsCanvas) {
      destroyChart('leads');
      if (hasLeadsData) {
        if (leadsEmpty) leadsEmpty.hidden = true;
        leadsCanvas.hidden = false;

        var ctx = leadsCanvas.getContext('2d');
        var gradient = ctx.createLinearGradient(0, 0, 0, 240);
        var fillTop = withAlpha(accent, 0.14);
        var fillBottom = withAlpha(accent, 0.02);
        gradient.addColorStop(0, fillTop);
        gradient.addColorStop(1, fillBottom);

        chartInstances.leads = new Chart(leadsCanvas, {
          type: 'line',
          data: {
            labels: leadsPerDay.map(function (d) {
              return d.date ? d.date.slice(5) : '';
            }),
            datasets: [
              {
                label: 'Uudet liidit',
                data: leadsPerDay.map(function (d) {
                  return d.count;
                }),
                borderColor: accent,
                backgroundColor: gradient,
                fill: true,
                tension: 0.4,
                pointRadius: 0,
                pointHoverRadius: 4,
                borderWidth: 2,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: cssVar('--color-text-primary'),
                padding: 10,
                cornerRadius: 8,
              },
            },
            scales: {
              x: {
                grid: { display: false },
                border: { display: false },
                ticks: { color: muted, maxTicksLimit: 8, font: { size: 11 } },
              },
              y: {
                beginAtZero: true,
                grid: { color: gridColor },
                border: { display: false },
                ticks: { color: muted, precision: 0, font: { size: 11 } },
              },
            },
          },
        });
      } else {
        leadsCanvas.hidden = true;
        if (leadsEmpty) leadsEmpty.hidden = false;
      }
    }

    var sourcesCanvas = document.getElementById('sourcesDonutChart');
    var sourcesEmpty = document.getElementById('sources-chart-empty');
    var sourceTotal = sourcesPie.reduce(function (sum, s) {
      return sum + (Number(s.count) || 0);
    }, 0);

    if (sourcesCanvas) {
      destroyChart('sources');
      if (sourceTotal > 0) {
        if (sourcesEmpty) sourcesEmpty.hidden = true;
        sourcesCanvas.hidden = false;

        var colorMap = {
          n8n: cssVar('--color-accent'),
          manual: cssVar('--color-info'),
          webform: cssVar('--color-success'),
          import: cssVar('--color-warning'),
          other: cssVar('--color-text-muted'),
        };

        chartInstances.sources = new Chart(sourcesCanvas, {
          type: 'doughnut',
          data: {
            labels: sourcesPie.map(function (s) {
              return s.source;
            }),
            datasets: [
              {
                data: sourcesPie.map(function (s) {
                  return s.count;
                }),
                backgroundColor: sourcesPie.map(function (s) {
                  return colorMap[s.source] || cssVar('--color-neutral-emphasis');
                }),
                borderWidth: 0,
                hoverOffset: 4,
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '68%',
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: cssVar('--color-text-primary'),
                padding: 10,
                cornerRadius: 8,
              },
            },
          },
        });

        var centerVal = document.getElementById('sources-donut-total');
        if (centerVal) centerVal.textContent = String(sourceTotal);
      } else {
        sourcesCanvas.hidden = true;
        if (sourcesEmpty) sourcesEmpty.hidden = false;
      }
    }
  }

  function init() {
    setCurrentDate();
    initRangeSwitcher();
    initFunnelBars();
    initForecastProgress();
    initEmailBars();
    initCharts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.addEventListener('pagehide', function () {
    Object.keys(chartInstances).forEach(destroyChart);
  });
})();
