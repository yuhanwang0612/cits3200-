/* ============================================================
   G8 Research Output Database — shared front-end helpers
   CITS3200 Team 20. Site design: Jamie Taylor.

   Everything here is vanilla JS with no build step and no CDN,
   so every page works by opening it in a browser or serving the
   folder with `python -m http.server`.

   The site is CLIENT-SIDE (agreed with the client, 2 Sep): pages
   fetch JSON from data/ and render in the browser. The back end's
   only job is to write those JSON files.
   ============================================================ */

/* ---------- data loading ----------------------------------- */

/** Fetch a JSON file from data/. Returns null and shows a message on failure. */
async function loadJSON(path) {
  try {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) throw new Error(res.status + ' ' + res.statusText);
    return await res.json();
  } catch (err) {
    console.error('Failed to load ' + path, err);
    return null;
  }
}

/** Render a friendly failure into a container instead of leaving it blank. */
function showLoadError(el, path) {
  el.innerHTML = '<div class="empty">Could not load <code>' + esc(path) + '</code>.' +
    '<br><span class="small">If you opened this file directly, serve the folder instead: ' +
    '<code>python -m http.server</code></span></div>';
}

/* ---------- formatting ------------------------------------- */

/** Escape text before putting it in innerHTML. Use for every value from data. */
function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** A value we do not hold. Always renders as an em-dash, never blank or "undefined". */
function na() { return '<span class="na" title="not available">—</span>'; }

/** Render a possibly-missing value. */
function val(v, fmt) {
  if (v === null || v === undefined || v === '') return na();
  return fmt ? fmt(v) : esc(v);
}

/** ABDC rating pill. Accepts 'A*', 'A', 'B', 'C', 'none', '' or null. */
function abdcPill(r) {
  if (r === null || r === undefined || r === '') return na();
  return '<span class="abdc" data-r="' + esc(r) + '">' + esc(r) + '</span>';
}

function num(v, dp) {
  if (v === null || v === undefined || v === '') return na();
  const n = Number(v);
  return isNaN(n) ? na() : n.toFixed(dp === undefined ? 0 : dp);
}

/* ---------- sorting ---------------------------------------- */

/* ABDC is a quality scale, not an alphabet. Sorting it as text puts A before
   A*, which is wrong. 'none' means checked-and-unranked, so it sorts after C;
   null means not-yet-checked and sinks to the bottom like any missing value. */
const ABDC_ORDER = { 'A*': 0, 'A': 1, 'B': 2, 'C': 3, 'none': 4 };

/** Comparator for one column. Missing values always sink, in both directions. */
function compareBy(key, dir, type) {
  const s = dir === 'asc' ? 1 : -1;
  return (a, b) => {
    let x = a[key], y = b[key];
    const xm = x === null || x === undefined || x === '';
    const ym = y === null || y === undefined || y === '';
    if (xm && ym) return 0;
    if (xm) return 1;
    if (ym) return -1;
    if (type === 'num')       { x = Number(x); y = Number(y); }
    else if (type === 'abdc') {
      x = ABDC_ORDER[x] !== undefined ? ABDC_ORDER[x] : 99;
      y = ABDC_ORDER[y] !== undefined ? ABDC_ORDER[y] : 99;
    }
    else { x = String(x).toLowerCase(); y = String(y).toLowerCase(); }
    return x < y ? -s : x > y ? s : 0;
  };
}

/** Chain several column comparators: first is primary, then tie-breakers. */
function multiCompare(specs) {
  const fns = specs.map(s => compareBy(s.key, s.dir, s.type));
  return (a, b) => {
    for (let i = 0; i < fns.length; i++) {
      const r = fns[i](a, b);
      if (r !== 0) return r;
    }
    return 0;
  };
}

/**
 * Make a table's headers sortable by click OR keyboard (Tab to a header, then
 * Enter/Space). Shift+click, or Shift+Enter, adds a secondary sort rather than
 * replacing the primary one.
 *
 *  thead th needs  class="sortable" data-key="field" [data-type="num|text|abdc"]
 *  onSort(specs) receives [{key, dir, type}, ...], primary first.
 *
 * Returns { set(specs), get() } so a page can drive the indicators from its own
 * initial sort state.
 */
function attachSorting(table, onSort) {
  const heads = Array.prototype.slice.call(table.querySelectorAll('th.sortable'));
  let specs = [];

  function paint() {
    heads.forEach(th => {
      const arrow = th.querySelector('.arrow');
      const i = specs.findIndex(s => s.key === th.dataset.key);
      if (i === -1) {
        th.removeAttribute('aria-sort');
        if (arrow) arrow.textContent = '↕';
      } else {
        const s = specs[i];
        th.setAttribute('aria-sort', s.dir === 'asc' ? 'ascending' : 'descending');
        if (arrow) arrow.textContent = (s.dir === 'asc' ? '↑' : '↓') + (specs.length > 1 ? (i + 1) : '');
      }
    });
  }

  function activate(th, additive) {
    const key  = th.dataset.key;
    const type = th.dataset.type || 'text';
    const existing = specs.find(s => s.key === key);
    if (additive) {
      if (existing) existing.dir = existing.dir === 'asc' ? 'desc' : 'asc';
      else specs.push({ key: key, dir: 'asc', type: type });
    } else {
      specs = [{ key: key, dir: (existing && existing.dir === 'asc') ? 'desc' : 'asc', type: type }];
    }
    paint();
    onSort(specs.slice());
  }

  heads.forEach(th => {
    th.tabIndex = 0;
    th.setAttribute('role', 'columnheader');
    th.addEventListener('click', e => activate(th, e.shiftKey));
    th.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        activate(th, e.shiftKey);
      }
    });
  });

  return {
    set: function (initial) { specs = initial.slice(); paint(); },
    get: function () { return specs.slice(); }
  };
}

/* ---------- pagination ------------------------------------- */

/**
 * Render pager controls.
 *  el     container element
 *  total  total row count
 *  page   current page, 1-based
 *  size   rows per page
 *  onGo   callback(newPage)
 */
function renderPager(el, total, page, size, onGo) {
  const pages = Math.max(1, Math.ceil(total / size));
  page = Math.min(page, pages);
  const from = total === 0 ? 0 : (page - 1) * size + 1;
  const to = Math.min(total, page * size);

  const btn = (label, target, opts) => {
    opts = opts || {};
    return '<button type="button" data-go="' + target + '"' +
      (opts.disabled ? ' disabled' : '') +
      (opts.current ? ' aria-current="true"' : '') +
      '>' + label + '</button>';
  };

  // window of page numbers around the current page
  let nums = [];
  const span = 2;
  for (let p = Math.max(1, page - span); p <= Math.min(pages, page + span); p++) nums.push(p);
  if (nums[0] > 1) nums = [1, '…'].concat(nums);
  if (nums[nums.length - 1] < pages) nums = nums.concat(['…', pages]);

  el.innerHTML =
    '<div class="small muted">Showing ' + from + '–' + to + ' of ' + total + '</div>' +
    '<div class="pages">' +
      btn('‹ Prev', page - 1, { disabled: page <= 1 }) +
      nums.map(n => n === '…'
        ? '<button type="button" disabled>…</button>'
        : btn(n, n, { current: n === page })).join('') +
      btn('Next ›', page + 1, { disabled: page >= pages }) +
    '</div>';

  el.querySelectorAll('button[data-go]').forEach(b => {
    b.addEventListener('click', () => {
      const t = Number(b.dataset.go);
      if (!isNaN(t) && t >= 1 && t <= pages) onGo(t);
    });
  });
}

/* ---------- CSV export ------------------------------------- */

/**
 * Download rows as CSV. This covers the client's minimum-goal
 * "download as Excel" requirement — Excel opens CSV natively.
 *  rows     array of objects
 *  columns  [{key, label}]
 *  filename e.g. 'researchers.csv'
 */
function exportCSV(rows, columns, filename) {
  const q = v => {
    if (v === null || v === undefined) return '';
    const s = String(v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  };
  const lines = [columns.map(c => q(c.label)).join(',')];
  rows.forEach(r => lines.push(columns.map(c => q(r[c.key])).join(',')));

  // BOM so Excel reads UTF-8 (accented author names) correctly
  const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ---------- misc ------------------------------------------- */

/** Read a query-string parameter. */
function param(name) {
  return new URLSearchParams(window.location.search).get(name);
}

/**
 * Only let http/https URLs out of the data and into an href. A DOI or profile
 * URL is scraped from a third-party site; a javascript: or data: URL arriving
 * in that field should never become a clickable link on our page.
 */
function safeUrl(u) {
  if (!u) return null;
  const s = String(u).trim();
  return /^https?:\/\//i.test(s) ? s : null;
}

/** Fill a <select> with options. */
function fillSelect(sel, values, allLabel) {
  sel.innerHTML = '<option value="">' + (allLabel || 'All') + '</option>' +
    values.map(v => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
}

/** Unique sorted values of a field across rows. */
function distinct(rows, key) {
  return [...new Set(rows.map(r => r[key]).filter(v => v !== null && v !== undefined && v !== ''))].sort();
}

/** Mark the current page in the nav. Detail pages highlight their parent. */
const NAV_PARENT = { 'researcher.html': 'researchers.html' };
document.addEventListener('DOMContentLoaded', () => {
  let here = location.pathname.split('/').pop() || 'index.html';
  here = NAV_PARENT[here] || here;
  document.querySelectorAll('.nav a').forEach(a => {
    if (a.getAttribute('href') === here) a.setAttribute('aria-current', 'page');
  });
});
