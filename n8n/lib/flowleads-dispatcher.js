/**
 * FlowLeads Job Dispatcher helpers for n8n Code nodes.
 * Keep in sync with app/search/oikotie_pagination.py
 */

const OIKOTIE_PAGE_SIZE = 24;
const DEFAULT_MAX_PAGES = 5;

function extractTotalCount(payload) {
  if (!payload || typeof payload !== 'object') return 0;

  for (const key of ['total', 'totalCount', 'count']) {
    if (Number.isInteger(payload[key]) && payload[key] >= 0) {
      return payload[key];
    }
  }

  for (const nestedKey of ['data', 'meta']) {
    const nested = payload[nestedKey];
    if (!nested || typeof nested !== 'object') continue;
    for (const key of ['total', 'totalCount', 'count']) {
      if (Number.isInteger(nested[key]) && nested[key] >= 0) {
        return nested[key];
      }
    }
  }
  return 0;
}

function buildPageItems(total, maxPages = DEFAULT_MAX_PAGES, pageSize = OIKOTIE_PAGE_SIZE) {
  const cap = Math.max(1, maxPages || DEFAULT_MAX_PAGES);
  const limit = Math.max(1, pageSize || OIKOTIE_PAGE_SIZE);

  if (!total || total <= 0) {
    return [{ page: 1, offset: 0, limit }];
  }

  const totalPages = Math.min(cap, Math.max(1, Math.ceil(total / limit)));
  const items = [];
  for (let page = 1; page <= totalPages; page += 1) {
    items.push({
      page,
      offset: (page - 1) * limit,
      limit,
    });
  }
  return items;
}

function expandRegions(job) {
  const regions = Array.isArray(job.regions) ? job.regions : [];
  return regions.map((current_region) => ({
    ...job,
    current_region,
    max_pages: job.max_pages ?? DEFAULT_MAX_PAGES,
  }));
}

function aggregateWorkerResults(items) {
  const totals = {
    leads_found: 0,
    leads_sent: 0,
    duplicates: 0,
    failed_pages: 0,
    worker_error: '',
  };

  for (const item of items) {
    const row = item.json || item;
    if (row.worker_error) {
      totals.worker_error = row.worker_error;
    }
    totals.leads_found += Number(row.leads_found || 0);
    totals.leads_sent += Number(row.leads_sent || 0);
    totals.duplicates += Number(row.duplicates || 0);
    if (row.failed || row.page_failed) {
      totals.failed_pages += 1;
    }
  }

  return totals;
}

module.exports = {
  OIKOTIE_PAGE_SIZE,
  DEFAULT_MAX_PAGES,
  extractTotalCount,
  buildPageItems,
  expandRegions,
  aggregateWorkerResults,
};
