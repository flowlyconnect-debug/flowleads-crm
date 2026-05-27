/**
 * FlowLeads CRM — dashboard charts (Coupler.io style)
 */
(function () {
  'use strict';

  var chartInstances = {};

  function destroyChart(key) {
    if (chartInstances[key]) {
      chartInstances[key].destroy();
      chartInstances[key] = null;
    }
  }

  function parseJsonAttr(root, name) {
    var raw = root.getAttribute(name);
    if (!raw) return null;
    try {
      return JSON.parse(raw);
    } catch (e) {
      return null;
    }
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

  function initCharts() {
    var root = document.getElementById('dashboard-analytics');
    if (!root || typeof Chart === 'undefined') return;

    var wonData = parseJsonAttr(root, 'data-won-deals') || {};
    var projectionData = parseJsonAttr(root, 'data-sales-projection') || {};
    var pipelineData = parseJsonAttr(root, 'data-pipeline-donut') || [];
    var lossData = parseJsonAttr(root, 'data-loss-reasons') || [];

    var gridColor = '#F0F2F8';
    var muted = '#9CA3AF';

    var wonCanvas = document.getElementById('wonDealsChart');
    if (wonCanvas) {
      destroyChart('won');
      chartInstances.won = new Chart(wonCanvas, {
        type: 'line',
        data: {
          labels: wonData.labels || [],
          datasets: [
            {
              label: 'Suljettu arvo (€)',
              data: wonData.closed_values || [],
              borderColor: '#1D6BF3',
              backgroundColor: 'rgba(29,107,243,0.06)',
              fill: true,
              tension: 0.4,
              pointRadius: 4,
              pointBackgroundColor: '#1D6BF3',
              yAxisID: 'y',
            },
            {
              label: 'Voitetut kaupat',
              data: wonData.won_counts || [],
              borderColor: '#38BDF8',
              backgroundColor: 'transparent',
              tension: 0.4,
              pointRadius: 4,
              pointBackgroundColor: '#38BDF8',
              yAxisID: 'y1',
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: { display: false } },
          scales: {
            x: {
              grid: { display: false },
              border: { display: false },
              ticks: { color: muted, font: { size: 11 } },
            },
            y: {
              grid: { color: gridColor },
              border: { display: false },
              ticks: {
                color: muted,
                font: { size: 11 },
                callback: function (v) {
                  return '€' + Number(v).toLocaleString('fi-FI');
                },
              },
            },
            y1: {
              position: 'right',
              grid: { display: false },
              border: { display: false },
              ticks: { color: muted, font: { size: 11 } },
            },
          },
        },
      });
    }

    var projectionCanvas = document.getElementById('salesProjectionChart');
    if (projectionCanvas) {
      destroyChart('projection');
      chartInstances.projection = new Chart(projectionCanvas, {
        type: 'line',
        data: {
          labels: projectionData.labels || [],
          datasets: [
            {
              label: 'Ennustettu arvo (€)',
              data: projectionData.forecasted_values || [],
              borderColor: '#7C3AED',
              backgroundColor: 'rgba(124,58,237,0.06)',
              fill: true,
              tension: 0.4,
              pointRadius: 4,
              pointBackgroundColor: '#7C3AED',
            },
            {
              label: 'Erääntyvät kaupat (€)',
              data: projectionData.due_values || [],
              borderColor: '#1D6BF3',
              backgroundColor: 'transparent',
              borderDash: [6, 4],
              tension: 0.4,
              pointRadius: 4,
              pointBackgroundColor: '#1D6BF3',
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: 'index', intersect: false },
          plugins: { legend: { display: false } },
          scales: {
            x: {
              grid: { display: false },
              border: { display: false },
              ticks: { color: muted, font: { size: 11 } },
            },
            y: {
              grid: { color: gridColor },
              border: { display: false },
              ticks: {
                color: muted,
                font: { size: 11 },
                callback: function (v) {
                  return '€' + Number(v).toLocaleString('fi-FI');
                },
              },
            },
          },
        },
      });
    }

    var pipelineCanvas = document.getElementById('pipelineDonut');
    if (pipelineCanvas) {
      destroyChart('pipeline');
      var pipelineColors = pipelineData.map(function (s) {
        return s.color;
      });
      var pipelinePcts = pipelineData.map(function (s) {
        return s.percentage;
      });
      chartInstances.pipeline = new Chart(pipelineCanvas, {
        type: 'doughnut',
        data: {
          labels: pipelineData.map(function (s) {
            return s.name;
          }),
          datasets: [
            {
              data: pipelinePcts.length ? pipelinePcts : [1],
              backgroundColor: pipelineColors.length
                ? pipelineColors
                : ['#1D6BF3', '#38BDF8', '#7C3AED', '#F59E0B', '#10B981', '#EF4444'],
              borderWidth: 0,
              hoverOffset: 4,
            },
          ],
        },
        options: {
          cutout: '68%',
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  return ctx.label + ': ' + ctx.parsed + '%';
                },
              },
            },
          },
        },
      });
    }

    var lossCanvas = document.getElementById('lossReasonsDonut');
    if (lossCanvas) {
      destroyChart('loss');
      chartInstances.loss = new Chart(lossCanvas, {
        type: 'doughnut',
        data: {
          labels: lossData.map(function (r) {
            return r.name;
          }),
          datasets: [
            {
              data: lossData.map(function (r) {
                return r.percentage;
              }),
              backgroundColor: lossData.map(function (r) {
                return r.color;
              }),
              borderWidth: 0,
              hoverOffset: 4,
            },
          ],
        },
        options: {
          cutout: '68%',
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              callbacks: {
                label: function (ctx) {
                  return ctx.label + ': ' + ctx.parsed + '%';
                },
              },
            },
          },
        },
      });
    }
  }

  function init() {
    setCurrentDate();
    initCharts();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.setPeriod = function (days) {
    var url = new URL(window.location.href);
    url.searchParams.set('period', String(days));
    window.location.href = url.toString();
  };

  window.addEventListener('pagehide', function () {
    Object.keys(chartInstances).forEach(destroyChart);
  });
})();
