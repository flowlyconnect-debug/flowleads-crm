/**
 * FlowLeads CRM — presentation-layer utilities (sidebar, layout)
 */
(function () {
  'use strict';

  function initSidebar() {
    const sidebar = document.getElementById('app-sidebar');
    const toggle = document.getElementById('sidebar-toggle');
    const backdrop = document.getElementById('sidebar-backdrop');
    if (!sidebar || !toggle) return;

    function setOpen(open) {
      sidebar.classList.toggle('is-open', open);
      if (backdrop) backdrop.classList.toggle('is-visible', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      document.body.classList.toggle('sidebar-open', open);
    }

    function close() {
      setOpen(false);
    }

    toggle.addEventListener('click', function () {
      setOpen(!sidebar.classList.contains('is-open'));
    });

    if (backdrop) {
      backdrop.addEventListener('click', close);
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') close();
    });

    window.addEventListener('resize', function () {
      if (window.innerWidth > 1024) close();
    });

    sidebar.querySelectorAll('.sidebar-item').forEach(function (link) {
      link.addEventListener('click', function () {
        if (window.innerWidth <= 1024) close();
      });
    });
  }

  function initModals() {
    const backdrops = document.querySelectorAll('.modal-backdrop');
    if (!backdrops.length) return;

    function setBodyLock(locked) {
      document.body.classList.toggle('modal-open', locked);
    }

    function openModal(modal) {
      if (!modal) return;
      modal.classList.add('open');
      modal.setAttribute('aria-hidden', 'false');
      setBodyLock(true);
    }

    function closeModal(modal) {
      if (!modal) return;
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
      if (!document.querySelector('.modal-backdrop.open')) {
        setBodyLock(false);
      }
    }

    document.querySelectorAll('[data-open-modal]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        openModal(document.getElementById(btn.getAttribute('data-open-modal')));
      });
    });

    document.querySelectorAll('[data-close-modal]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        closeModal(document.getElementById(btn.getAttribute('data-close-modal')));
      });
    });

    backdrops.forEach(function (backdrop) {
      backdrop.setAttribute('aria-hidden', 'true');
      backdrop.addEventListener('click', function (e) {
        if (e.target === backdrop) closeModal(backdrop);
      });
    });

    document.addEventListener('keydown', function (e) {
      if (e.key !== 'Escape') return;
      document.querySelectorAll('.modal-backdrop.open').forEach(closeModal);
    });
  }

  function initReportsFilters() {
    const rangeSelect = document.getElementById('range-select');
    const customRow = document.getElementById('reports-custom-dates');
    if (!rangeSelect || !customRow) return;
    function syncCustom() {
      customRow.classList.toggle('is-visible', rangeSelect.value === 'custom');
    }
    rangeSelect.addEventListener('change', syncCustom);
    syncCustom();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      initSidebar();
      initModals();
      initReportsFilters();
    });
  } else {
    initSidebar();
    initModals();
    initReportsFilters();
  }
})();
