/* Campaign Studio — core.js
   Defines window.Studio: the API helper, job polling, safe markdown,
   the confirm modal, toasts, and the tab system.
   Loaded first; every tab file only touches window.Studio. */
(function () {
  'use strict';

  const Studio = {
    state: { campaigns: [], campaign: null, activeTab: 'maps' },
    tabs: {}
  };
  window.Studio = Studio;

  /* ---------- text + DOM helpers ---------- */

  // esc is THE escaping rule (js/esc.js) — everything server- or LLM-derived
  // goes through it or textContent before touching innerHTML
  const esc = window.StudioEsc.esc;

  function el(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = String(html).trim();
    // first element ONLY: multi-root content must be built as separate
    // S.el() calls or the rest is silently dropped
    const node = tpl.content.firstElementChild;
    if (!node) throw new Error('el(): template produced no element');
    return node;
  }

  // log-tail boxes under forms: open() clears and shows, push() appends a
  // line and keeps the view scrolled to the newest
  Studio.makeLogBox = function (boxEl) {
    return {
      open() {
        boxEl.classList.remove('hidden');
        boxEl.textContent = '';
      },
      push(line) {
        boxEl.textContent += String(line == null ? '' : line) + '\n';
        boxEl.scrollTop = boxEl.scrollHeight;
      }
    };
  };

  // The one job-run flow shared by every form that POSTs a job: disable the
  // button, clear the error box, open the log, POST, poll (log lines pushed
  // as they arrive), toast doneMsg(result) on success. On failure: an
  // errorPanel in errBox + a failLabel toast, and the error is RETHROWN so
  // the caller can remember or decorate it. The button re-enables either
  // way unless reenable === false (callers that gate the button on their
  // own state re-enable in their own catch).
  //
  // Queue awareness (ticket 02): while the job sits in the campaign's FIFO
  // queue, errBox shows a banner with its position and a Cancel button; the
  // log gets one line per position change. Cancelling is a deliberate user
  // act, so it ends the watch with a note, not an error panel.
  Studio.runJob = async function (o) {
    if (o.btn) o.btn.disabled = true;
    if (o.errBox) o.errBox.innerHTML = '';
    if (o.log && o.log.open) o.log.open();
    let lastPosition = null;
    const dropBanner = () => {
      lastPosition = null;
      if (o.errBox) o.errBox.innerHTML = '';
    };
    const onJob = (job) => {
      if (!o.errBox) return;
      if (job.status !== 'queued') {
        if (lastPosition !== null) dropBanner();
        return;
      }
      const pos = (job.position != null) ? job.position : 0;
      if (pos === lastPosition) return;
      lastPosition = pos;
      const where = (pos === 0) ? 'next in line — it runs when the current job finishes'
        : ('behind ' + pos + ' job' + (pos === 1 ? '' : 's') + ' — it runs when its turn comes');
      if (o.log) o.log.push('— queued: ' + where + ' —');
      o.errBox.innerHTML = '';
      const banner = el(
        '<div class="queue-banner" role="status"><span class="queue-text">Queued: ' +
        esc(where) + '</span> <button type="button" class="btn queue-cancel">Cancel</button></div>');
      banner.querySelector('.queue-cancel').addEventListener('click', () => {
        const b = banner.querySelector('.queue-cancel');
        b.disabled = true;
        b.textContent = 'Cancelling…';
        Studio.cancelJob(job.id).catch(() => {});
      });
      o.errBox.appendChild(banner);
    };
    try {
      const resp = await api(o.path, { method: 'POST', body: o.body });
      if (!resp || !resp.job_id) throw { status: 0, error: 'Server returned no job_id' };
      const result = await pollJob(resp.job_id,
        o.log ? (line) => o.log.push(line) : null, o.timeoutMs, onJob);
      dropBanner();
      if (o.doneMsg) {
        toast(typeof o.doneMsg === 'function' ? o.doneMsg(result) : o.doneMsg, 'ok');
      }
      return result;
    } catch (err) {
      dropBanner();
      if (err && err.cancelled) {
        if (o.errBox) o.errBox.innerHTML = '';
        if (o.log) o.log.push('— cancelled before it started —');
        toast('Job cancelled — it was still queued, nothing ran.', 'info');
        return null;
      }
      if (o.errBox) {
        o.errBox.innerHTML = '';
        o.errBox.appendChild(errorPanel(err));
      }
      if (o.failLabel) {
        toast(o.failLabel + (err && err.error ? ': ' + err.error : ''), 'error');
      }
      throw err;
    } finally {
      if (o.btn && o.reenable !== false) o.btn.disabled = false;
    }
  };

  function fmtInt(value) {
    const n = Number(value);
    if (value === '' || value == null || Number.isNaN(n)) return String(value == null ? '' : value);
    return n.toLocaleString();
  }

  /* ---------- API ---------- */

  async function api(path, opts) {
    opts = opts || {};
    const init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (e) {
      throw {
        status: 0,
        error: 'Cannot reach the studio server (' + (e && e.message ? e.message : 'network error') + ')'
      };
    }
    const text = await res.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch (_) { data = null; }
    }
    if (!res.ok) {
      const msg = (data && data.error) ? data.error : (text || ('HTTP ' + res.status));
      throw { status: res.status, error: msg };
    }
    return data;
  }

  function pollJob(id, onLog, timeoutMs, onJob) {
    // A wedged job (engine killed, LLM hanging) must not tick forever: after
    // timeoutMs (default 31 min = the week job's 1800 s engine cap + slack)
    // polling gives up with a readable error. The job itself keeps running
    // server-side; reopen this tab after it finishes to see the result.
    const giveUpAfter = timeoutMs || (31 * 60 * 1000);
    return new Promise((resolve, reject) => {
      // rotating window — see logtail.js for why anchoring, not indexing
      let logCursor = { seen: 0, last: null };
      let failures = 0;           // one hiccup must not kill a 30-minute watch
      let inFlight = false;       // slow polls must not overlap and double-log
      let settled = false;        // resolve/reject exactly once
      const startedAt = Date.now();
      const finish = (fn, arg) => {
        if (settled) return;
        settled = true;
        clearInterval(timer);
        fn(arg);
      };
      const tick = async () => {
        if (!settled && Date.now() - startedAt > giveUpAfter) {
          finish(reject, {
            status: 504,
            error: 'Gave up waiting for the job after ' + Math.round(giveUpAfter / 60000)
              + ' minutes (the job keeps running server-side — reopen this '
              + 'tab in a few minutes to see the result)'
          });
          return;
        }
        if (inFlight || settled) return;
        inFlight = true;
        try {
          let job;
          try {
            job = await api('/api/jobs/' + encodeURIComponent(id));
            failures = 0;
          } catch (err) {
            // a gone job (404/410: restart wiped the in-memory registry) is
            // final; a transient hiccup gets a few more ticks
            if (err && (err.status === 404 || err.status === 410)) throw err;
            failures += 1;
            if (failures > 15) {
              throw { status: 0, error: 'Lost contact with the studio server '
                + 'while waiting for the job — it may still finish; reopen '
                + 'this tab later to see the result' };
            }
            return;
          }
          const merged = StudioLogTail.mergeLogTail(job.log_tail, logCursor);
          logCursor = merged.cursor;
          if (typeof onLog === 'function') merged.lines.forEach((line) => onLog(line));
          if (typeof onJob === 'function') onJob(job);
          if (job.status === 'done') {
            finish(resolve, job.result);
          } else if (job.status === 'error') {
            finish(reject, { status: 500, error: job.error || 'Job failed' });
          } else if (job.status === 'cancelled') {
            // dequeued before it ever ran (ticket 02): not a failure — the
            // caller (runJob) reads .cancelled and says so, quietly
            finish(reject, { status: 0, cancelled: true,
              error: 'Job cancelled — it was still queued and never ran' });
          }
        } catch (err) {
          finish(reject, err);
        } finally {
          inFlight = false;
        }
      };
      const timer = setInterval(tick, 1000);
      tick();
    });
  }

  /* ---------- the campaign job queue (ticket 02) ---------- */

  Studio.cancelJob = async function (jobId) {
    return api('/api/jobs/' + encodeURIComponent(jobId) + '/cancel',
      { method: 'POST' });
  };

  // The queue strip under the header: what is running (with its wait
  // estimate) and what is queued, for the SELECTED campaign. A GM who
  // reopened the app mid-evening sees the queue they left behind — and a
  // 409's "Blocked by" line has a visible counterpart. Cheap: one small
  // GET on a slow poll, only while a campaign is selected.
  let queueStripTimer = null;
  function renderQueueStrip(snap) {
    const old = document.querySelector('.queue-strip');
    const busy = snap && ((snap.running && snap.running.id) ||
                          (snap.queued && snap.queued.length));
    if (!busy) { if (old) old.remove(); return; }
    const parts = [];
    if (snap.running) {
      parts.push('<strong>Running:</strong> ' + esc(snap.running.kind));
      const est = snap.running_estimate;
      if (est) parts.push(' <span class="muted">(' + esc(etaText(est)) + ')</span>');
    }
    (snap.queued || []).forEach((j) => {
      const pos = (j.position != null) ? j.position : '?';
      parts.push('<strong>Queued:</strong> ' + esc(j.kind) +
        ' <span class="muted">(position ' + esc(String(pos)) + ')</span>');
    });
    if (old) { old.remove(); }
    const strip = el('<div class="queue-strip" role="status">' +
      '<div class="queue-strip-inner">' + parts.join(' &middot; ') + '</div></div>');
    const header = document.querySelector('.app-header');
    if (header) header.insertAdjacentElement('afterend', strip);
  }

  function etaText(est) {
    // "typically 8-15 minutes" — the same phrasing the 409s carry; the
    // estimate label is server-computed, the UI only renders it
    const fmt = (s) => (s < 120 ? Math.max(1, Math.round(s)) + ' s'
      : Math.round(s / 60) + ' min');
    const lo = est.low_s, hi = est.high_s;
    if (typeof lo !== 'number' || typeof hi !== 'number') return 'an estimate is not available yet';
    return 'typically ' + (fmt(lo) === fmt(hi) ? fmt(lo) : fmt(lo) + '-' + fmt(hi)) +
      ' — an estimate';
  }

  function pollQueueStrip() {
    const camp = Studio.state.campaign;
    if (!camp) { renderQueueStrip(null); return; }
    api('/api/campaign/' + encodeURIComponent(camp) + '/jobs')
      .then(renderQueueStrip)
      .catch(() => { renderQueueStrip(null); });
  }

  /* ---------- history snapshot recovery ---------- */

  // A failed snapshot means "your change published, but the git undo trail
  // does not have it" — the one recovery path that must never require curl.
  Studio.snapshotBanner = async function () {
    try {
      const summary = await api(campUrl(''));
      const txs = (summary && summary.publication &&
                   Array.isArray(summary.publication.transactions))
        ? summary.publication.transactions : [];
      const broken = txs.some((t) => t && t.snapshot &&
        (t.snapshot.state === 'failed' || t.snapshot.state === 'unavailable'));
      if (!broken) return null;
    } catch (_) {
      return null; // advisory: a missing summary never blocks the tab
    }
    const banner = el(
      '<div class="snapshot-banner" role="status">' +
        '<span>Your change published, but the <strong>history snapshot</strong> ' +
          'failed &mdash; the git undo trail is missing it.</span>' +
        '<button type="button" class="btn snapshot-retry">Retry snapshot</button>' +
      '</div>');
    const btn = banner.querySelector('.snapshot-retry');
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      await Studio.retrySnapshot();
    });
    return banner;
  };

  Studio.retrySnapshot = async function () {
    try {
      const r = await api(campUrl('/snapshot'), { method: 'POST' });
      const snap = r && r.snapshot;
      if (snap && (snap.state === 'failed' || snap.state === 'unavailable')) {
        toast('Snapshot still ' + snap.state +
          ' — see the studio log for the reason.', 'error');
      } else {
        toast('History snapshot committed — the undo trail is whole again.', 'ok');
      }
    } catch (err) {
      toast('Snapshot retry failed' +
        (err && err.error ? ': ' + err.error : ''), 'error');
    }
    showTab(Studio.state.activeTab); // re-render: the banner reflects the retry
  };

  /* ---------- markdown (escape FIRST, then a minimal subset) ---------- */

  function mdInline(s) {
    // s is already HTML-escaped by md()
    return s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/\*([^*\n]+)\*/g, '<em>$1</em>')
      // vault wiki-links render as their display text here (the real links
      // live in Obsidian; chronicles are linkified on write, vault §3)
      .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, '$2')
      .replace(/\[\[([^\]]+)\]\]/g, '$1');
  }

  function mdRowCells(line) {
    return line.trim().replace(/^\|/, '').replace(/\|$/, '')
      .split('|').map((c) => c.trim());
  }
  const mdIsTableRow = (line) => /^\s*\|.*\|\s*$/.test(line);
  const mdIsSepRow = (cells) =>
    cells.length > 0 && cells.every((c) => c === '' || /^:?-{2,}:?$/.test(c));

  function md(text) {
    const lines = esc(text).replace(/\r\n/g, '\n').split('\n');
    const out = [];
    let para = [];

    const flushPara = () => {
      if (para.length) {
        out.push('<p>' + para.map(mdInline).join('<br>') + '</p>');
        para = [];
      }
    };

    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      const t = line.trim();

      // pipe table: a pipe row followed by another pipe row (chronicle roll tables)
      if (mdIsTableRow(line) && i + 1 < lines.length && mdIsTableRow(lines[i + 1])) {
        flushPara();
        const head = mdRowCells(line);
        const second = mdRowCells(lines[i + 1]);
        let j = i + 1;
        if (mdIsSepRow(second)) j = i + 2;
        const rows = [];
        while (j < lines.length && mdIsTableRow(lines[j])) {
          rows.push(mdRowCells(lines[j]));
          j += 1;
        }
        let html = '<table class="md-table"><thead><tr>' +
          head.map((c) => '<th>' + mdInline(c) + '</th>').join('') +
          '</tr></thead><tbody>';
        rows.forEach((r) => {
          html += '<tr>';
          for (let k = 0; k < head.length; k++) html += '<td>' + mdInline(r[k] || '') + '</td>';
          html += '</tr>';
        });
        html += '</tbody></table>';
        out.push(html);
        i = j;
        continue;
      }

      let m;
      if ((m = t.match(/^(#{1,3})\s+(.+)$/))) {
        flushPara();
        const lvl = m[1].length;
        out.push('<h' + lvl + '>' + mdInline(m[2]) + '</h' + lvl + '>');
        i += 1;
        continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
        flushPara();
        out.push('<hr>');
        i += 1;
        continue;
      }
      if (t === '') { flushPara(); i += 1; continue; }
      para.push(line);
      i += 1;
    }
    flushPara();
    return out.join('\n');
  }

  /* ---------- modal + toasts ---------- */

  function confirmRun(message) {
    return new Promise((resolve) => {
      const root = document.getElementById('modal-root');
      const overlay = el(
        '<div class="modal-overlay">' +
          '<div class="modal" role="dialog" aria-modal="true" aria-label="Confirm">' +
            '<h3 class="modal-title">This costs real credits</h3>' +
            '<p class="modal-body">' + esc(message) + '</p>' +
            '<div class="modal-actions">' +
              '<button type="button" class="btn" data-act="cancel">Cancel</button>' +
              '<button type="button" class="btn btn-primary" data-act="confirm">Confirm</button>' +
            '</div>' +
          '</div>' +
        '</div>'
      );
      const done = (answer) => { overlay.remove(); resolve(answer); };
      overlay.addEventListener('click', (ev) => {
        const act = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-act') : null;
        if (act === 'confirm') done(true);
        else if (act === 'cancel') done(false);
        else if (ev.target === overlay) done(false);
      });
      overlay.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') done(false);
        if (ev.key === 'Tab') {
          // minimal focus trap (modal: Tab must not reach the page behind)
          const focusables = Array.from(
            overlay.querySelectorAll('button')).filter((b) => !b.disabled);
          if (!focusables.length) return;
          const idx = focusables.indexOf(document.activeElement);
          ev.preventDefault();
          const next = ev.shiftKey
            ? focusables[(idx - 1 + focusables.length) % focusables.length]
            : focusables[(idx + 1) % focusables.length];
          next.focus();
        }
      });
      root.appendChild(overlay);
      const btn = overlay.querySelector('[data-act="confirm"]');
      if (btn) btn.focus();
    });
  }

  function toast(msg, kind) {
    const host = document.getElementById('toasts');
    if (!host) return;
    const cls = kind === 'error' ? 'toast-error' : (kind === 'info' ? 'toast-info' : 'toast-ok');
    const t = el('<div class="toast ' + cls + '"></div>');
    t.textContent = String(msg == null ? '' : msg);
    host.appendChild(t);
    setTimeout(() => {
      t.classList.add('toast-gone');
      setTimeout(() => t.remove(), 350);
    }, 4200);
  }

  /* ---------- URL helpers ---------- */

  function campUrl(suffix) {
    return '/api/campaign/' + encodeURIComponent(Studio.state.campaign || '') + (suffix || '');
  }

  function fileUrl(rel) {
    return '/campaign-file/' + encodeURIComponent(Studio.state.campaign || '') + '/' +
      String(rel || '').replace(/^\/+/, '');
  }

  /* ---------- campaign search (header) ---------- */

  let searchPanel = null;

  function closeSearch() {
    if (searchPanel) { searchPanel.remove(); searchPanel = null; }
  }

  function highlightMatch(text, q) {
    // find the hit on the RAW text, then escape the three pieces separately:
    // escape-first can match INSIDE an entity ("amp" hits "&amp;") and mangles
    // the display; matching raw and escaping per segment cannot
    const idx = text.toLowerCase().indexOf(String(q).toLowerCase());
    if (!q || idx < 0) return esc(text);
    return esc(text.slice(0, idx)) + '<mark>' + esc(text.substr(idx, q.length)) +
      '</mark>' + esc(text.slice(idx + q.length));
  }

  function renderSearchResults(body, q, data) {
    const results = (data && Array.isArray(data.results)) ? data.results : [];
    body.innerHTML = '';
    if (!results.length) {
      body.appendChild(emptyState('Nothing matches "' + q + '".'));
      return;
    }
    body.appendChild(el(
      '<p class="search-count">' + results.length + ' hit' +
      (results.length === 1 ? '' : 's') + '</p>'));
    const list = el('<ul class="search-results"></ul>');
    results.forEach((r) => {
      list.appendChild(el(
        '<li class="search-hit">' +
          '<div class="search-hit-head">' +
            '<span class="pill search-kind search-kind-' + esc(r.kind) + '">' +
              esc(r.kind) + '</span>' +
            '<span class="review-file">' + esc(r.file) + ':' +
              esc(String(r.line)) + '</span>' +
          '</div>' +
          (r.context ? '<div class="search-context">' + esc(r.context) + '</div>' : '') +
          '<div class="search-text">' + highlightMatch(r.text || '', q) + '</div>' +
        '</li>'
      ));
    });
    body.appendChild(list);
  }

  function runSearch(q) {
    if (!Studio.state.campaign) {
      toast('No campaign selected — nothing to search.', 'error');
      return;
    }
    closeSearch();
    const panel = el(
      '<section class="search-panel" role="region" aria-label="Search results">' +
        '<header class="search-head">' +
          '<span class="search-title">Search</span>' +
          '<strong class="search-q"></strong>' +
          '<button type="button" class="btn search-close" aria-label="Close search">&times;</button>' +
        '</header>' +
        '<div class="search-body"><p class="loading">Searching…</p></div>' +
      '</section>'
    );
    panel.querySelector('.search-q').textContent = q;
    panel.querySelector('.search-close').addEventListener('click', closeSearch);
    document.body.appendChild(panel);
    searchPanel = panel;
    const body = panel.querySelector('.search-body');
    api(campUrl('/search?q=' + encodeURIComponent(q)))
      .then((data) => {
        if (searchPanel === panel) renderSearchResults(body, q, data);
      })
      .catch((err) => {
        if (searchPanel !== panel) return;
        body.innerHTML = '';
        body.appendChild(errorPanel(err));
      });
  }

  /* ---------- shared UI fragments ---------- */

  function errorPanel(err, hint) {
    const status = err && err.status;
    const msg = err && err.error ? err.error : String(err);
    let hintHtml = '';
    if (hint) hintHtml = '<p class="error-hint">' + esc(hint) + '</p>';
    else if (status === 501) hintHtml = '<p class="error-hint">Not implemented yet &mdash; this arrives in a later wave.</p>';
    else if (status === 0) hintHtml = '<p class="error-hint">Is the studio server running?</p>';
    else if (status === 422) hintHtml = '<p class="error-hint">Generation is fed or refused &mdash; the ground data it needs is missing.</p>';
    return el(
      '<div class="error-panel" role="alert"><strong>Error' +
      (status ? ' ' + esc(status) : '') + '</strong> ' + esc(msg) + hintHtml + '</div>'
    );
  }

  function emptyState(msg) {
    return el('<div class="empty-state">' + esc(msg) + '</div>');
  }

  function noCampaign() {
    return el(
      '<div class="empty-state">' +
        'No campaign selected. Click <strong>+ New</strong> in the header to ' +
        'create one, or pick one from the Campaign list. New here? The ' +
        '<a href="/tutorial" target="_blank" rel="noopener">first-campaign ' +
        'tutorial</a> turns an evening at the keyboard into days or weeks ' +
        'of world history.' +
      '</div>'
    );
  }

  /* ---------- help modal (header ? button) ---------- */

  function helpModal() {
    const root = document.getElementById('modal-root');
    const overlay = el(
      '<div class="modal-overlay">' +
        '<div class="modal" role="dialog" aria-modal="true" aria-label="About Campaign Studio">' +
          '<h3 class="modal-title">What is this?</h3>' +
          '<p class="modal-body">Campaign Studio is a localhost workbench for ' +
            'a D&amp;D campaign&rsquo;s world: generate maps, grow lore, and ' +
            'simulate factions week by week. You are the DM &mdash; everything ' +
            'the machine produces is a <em>proposal</em> until you approve it ' +
            'in the Review tab.</p>' +
          '<ul class="help-tabs">' +
            '<li><strong>Maps</strong> &mdash; generate a world from a seed, then city and battlemap scenes (Foundry-ready).</li>' +
            '<li><strong>World Building</strong> &mdash; geography, timeline, naming voice; generates backstory as pending lore.</li>' +
            '<li><strong>Agent Simulation</strong> &mdash; the faction sandbox: create factions, <em>Propose week N</em>, then accept or discard the proposal. <em>Review the goals</em> and <em>Forecast the next weeks</em> stage projections you steer with &mdash; neither is canon until you say so.</li>' +
            '<li><strong>Knowledge</strong> &mdash; what is true and who may know it: approved facts, the DM briefing, and what the party has been told.</li>' +
            '<li><strong>Review</strong> &mdash; one queue: nothing enters the campaign until you approve it.</li>' +
          '</ul>' +
          '<p class="modal-body"><strong>First campaign?</strong><br>' +
            '1. Click <strong>+ New</strong> and name it<br>' +
            '2. Maps &rarr; <em>Generate a world</em> with any seed<br>' +
            '3. Maps &rarr; map a burg, roll a battlemap scene<br>' +
            '4. World Building &rarr; <em>Generate backstory</em>, approve it in Review<br>' +
            '5. Agent Simulation &rarr; <em>Create a simulation</em>, then ' +
            '<em>Propose week 1</em> &mdash; accepting the proposal advances ' +
            'the canon clock</p>' +
          '<p class="modal-body">The first campaign is an evening, not a ' +
            'quick setup: days or weeks of world history, compressed into a ' +
            'couple of hours at the keyboard &mdash; each long step says its ' +
            'expected duration before it starts. Full walkthrough with ' +
            'troubleshooting: ' +
            '<a href="/tutorial" target="_blank" rel="noopener">the tutorial</a> ' +
            '(also at <code>TUTORIAL.md</code> in the repo). Something not ' +
            'booting? Run <code>python studio/doctor.py</code>.</p>' +
          '<div class="modal-actions">' +
            '<button type="button" class="btn btn-primary" data-act="close">Close</button>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    const done = () => overlay.remove();
    overlay.addEventListener('click', (ev) => {
      const act = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-act') : null;
      if (act === 'close' || ev.target === overlay) done();
    });
    overlay.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') done();
    });
    root.appendChild(overlay);
    overlay.querySelector('[data-act="close"]').focus();
  }

  /* ---------- setup health strip (doctor, quick mode) ---------- */

  // checks worth a line on the page: anything required that fails, plus the
  // optional LLM key (lore generation and live weeks silently need it).
  // vale/week-run are advisory noise for a first run — the doctor CLI has them.
  function setupStripRelevant(check) {
    if (!check || check.ok) return false;
    return !!check.required || check.name === 'llm key';
  }

  function renderSetupStrip(reportData) {
    const old = document.querySelector('.setup-strip');
    if (old) old.remove();
    const checks = (reportData && Array.isArray(reportData.checks))
      ? reportData.checks.filter(setupStripRelevant) : [];
    if (!checks.length) return;
    const strip = el(
      '<div class="setup-strip" role="status">' +
        '<div class="setup-strip-inner">' +
          '<div class="setup-items"></div>' +
          '<div class="setup-foot">' +
            '<span class="muted">Full check: <code>python studio/doctor.py</code></span>' +
            '<button type="button" class="btn setup-dismiss" aria-label="Dismiss setup notes">Dismiss</button>' +
          '</div>' +
        '</div>' +
      '</div>'
    );
    const items = strip.querySelector('.setup-items');
    checks.forEach((c) => {
      items.appendChild(el(
        '<p class="setup-item"><span class="pill setup-pill">' + esc(c.name) + '</span> ' +
          esc(c.detail) +
          (c.fix ? ' <span class="muted">Fix: ' + esc(c.fix) + '</span>' : '') +
        '</p>'
      ));
    });
    strip.querySelector('.setup-dismiss').addEventListener('click', () => {
      strip.remove();
      try { sessionStorage.setItem('studio-setup-dismissed', '1'); } catch (_) {}
    });
    const header = document.querySelector('.app-header');
    if (header) header.insertAdjacentElement('afterend', strip);
  }

  function checkSetup() {
    try { if (sessionStorage.getItem('studio-setup-dismissed')) return; } catch (_) {}
    api('/api/doctor?quick=1')
      .then(renderSetupStrip)
      .catch(() => {}); // a missing doctor never blocks the page
  }

  function loading(msg) {
    return el('<div class="loading">' + esc(msg || 'Loading…') + '</div>');
  }

  /* ---------- tab system ---------- */

  function showTab(name) {
    Studio.state.activeTab = name;
    try { localStorage.setItem('campaign-studio.tab', name); } catch (_) {} // survives reloads
    document.querySelectorAll('.tab').forEach((btn) => {
      btn.classList.toggle('active', btn.getAttribute('data-tab') === name);
    });
    document.querySelectorAll('.tab-section').forEach((sec) => {
      sec.classList.toggle('active', sec.getAttribute('data-tab') === name);
    });
    const section = document.querySelector('.tab-section[data-tab="' + name + '"]');
    if (!section) return;
    section.innerHTML = '';
    const render = Studio.tabs[name];
    if (typeof render !== 'function') {
      section.appendChild(emptyState('This tab has no renderer registered.'));
      return;
    }
    Promise.resolve(render(section)).catch((err) => {
      section.innerHTML = '';
      section.appendChild(errorPanel(err));
    });
  }

  /* ---------- boot ---------- */

  async function refreshCampaigns(prefer) {
    const select = document.getElementById('campaign-select');
    try {
      const data = await api('/api/campaigns');
      const list = (data && Array.isArray(data.campaigns)) ? data.campaigns : [];
      Studio.state.campaigns = list;
      select.innerHTML = '';
      if (!list.length) {
        Studio.state.campaign = null;
        select.appendChild(el('<option value="">No campaigns found</option>'));
      } else {
        let picked = null;
        list.forEach((c) => {
          const opt = el(
            '<option value="' + esc(c.name) + '">' + esc(c.title || c.name) + '</option>'
          );
          if (prefer && c.name === prefer) { opt.selected = true; picked = c.name; }
          select.appendChild(opt);
        });
        if (!picked) {
          select.firstChild.selected = true;
          picked = list[0].name;
        }
        Studio.state.campaign = picked;
        // a reload used to reset to the first campaign (found 2026-09-25)
        try { localStorage.setItem('campaign-studio.campaign', picked); } catch (_) {}
      }
    } catch (err) {
      select.innerHTML = '';
      select.appendChild(el('<option value="">Unavailable</option>'));
      Studio.state.campaign = null;
      const section = document.querySelector('.tab-section.active');
      if (section) {
        section.innerHTML = '';
        section.appendChild(errorPanel(err, 'The campaign list could not be loaded.'));
      }
      return false;
    }
    return true;
  }

  function newCampaignModal(onCreated) {
    const root = document.getElementById('modal-root');
    const overlay = el(
      '<div class="modal-overlay">' +
        '<form class="modal" role="dialog" aria-modal="true" aria-label="New campaign">' +
          '<h3 class="modal-title">New campaign</h3>' +
          '<p class="modal-body">A campaign is a folder of files under ' +
            '<code>campaigns/&lt;name&gt;/</code> in this checkout &mdash; ' +
            'portable by zip, editable by hand.</p>' +
          '<div class="modal-fields">' +
            '<label class="field"><span class="field-label">Name (lowercase, hyphens)</span>' +
              '<input type="text" class="input" id="new-camp-name" ' +
                'placeholder="e.g. ember-coast" autocomplete="off" spellcheck="false"></label>' +
            '<label class="field"><span class="field-label">Title (optional)</span>' +
              '<input type="text" class="input" id="new-camp-title" ' +
                'placeholder="e.g. The Ember Coast" autocomplete="off" spellcheck="false"></label>' +
          '</div>' +
          '<div class="modal-actions">' +
            '<button type="button" class="btn" data-act="cancel">Cancel</button>' +
            '<button type="submit" class="btn btn-primary" data-act="create">Create</button>' +
          '</div>' +
          '<div class="modal-error form-error"></div>' +
        '</form>' +
      '</div>'
    );
    const done = () => overlay.remove();
    overlay.addEventListener('click', (ev) => {
      const act = ev.target && ev.target.getAttribute ? ev.target.getAttribute('data-act') : null;
      if (act === 'cancel' || ev.target === overlay) done();
    });
    overlay.querySelector('form').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const name = overlay.querySelector('#new-camp-name').value.trim();
      const title = overlay.querySelector('#new-camp-title').value.trim();
      if (!name) { toast('Give the campaign a name first.', 'info'); return; }
      api('/api/campaigns', { method: 'POST', body: { name: name, title: title } })
        .then((created) => {
          done();
          toast('Campaign created: ' + (created.title || created.name) +
                ' — next: generate a world on the Maps tab', 'ok');
          if (onCreated) onCreated(created);
        })
        .catch((err) => {
          // the toast alone was missable: keep the dialog open with the
          // reason inline so a refused name is never a silent no-op
          const box = overlay.querySelector('.modal-error');
          if (box) { box.innerHTML = ''; box.appendChild(errorPanel(err)); }
          toast((err && err.error) ? err.error : 'Campaign could not be created.', 'error');
        });
    });
    root.appendChild(overlay);
    overlay.querySelector('#new-camp-name').focus();
  }

  async function init() {
    document.getElementById('tab-bar').addEventListener('click', (ev) => {
      const btn = ev.target.closest('.tab');
      if (btn) showTab(btn.getAttribute('data-tab'));
    });

    const select = document.getElementById('campaign-select');
    select.addEventListener('change', () => {
      Studio.state.campaign = select.value || null;
      try { localStorage.setItem('campaign-studio.campaign', select.value); } catch (_) {}
      closeSearch(); // results belong to the campaign that was searched
      showTab(Studio.state.activeTab); // campaign switch re-renders the active tab
    });

    const newBtn = document.getElementById('new-campaign-btn');
    if (newBtn) {
      newBtn.addEventListener('click', () => {
        newCampaignModal((created) => {
          refreshCampaigns(created.name).then(() => showTab(Studio.state.activeTab));
        });
      });
    }

    const helpBtn = document.getElementById('help-btn');
    if (helpBtn) helpBtn.addEventListener('click', helpModal);

    const searchForm = document.getElementById('header-search');
    const searchInput = document.getElementById('search-input');
    if (searchForm && searchInput) {
      searchForm.addEventListener('submit', (ev) => {
        ev.preventDefault();
        const q = searchInput.value.trim();
        if (!q) {
          toast('Type something to search for first.', 'info');
          return;
        }
        runSearch(q);
      });
    }
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') closeSearch();
    });

    let remembered = null;
    try { remembered = localStorage.getItem('campaign-studio.campaign'); } catch (_) {}
    // restore the remembered tab, but only if it is still a real tab — a
    // renamed section must never boot into "this tab has no renderer"
    try {
      const rememberedTab = localStorage.getItem('campaign-studio.tab');
      if (rememberedTab &&
          document.querySelector('.tab[data-tab="' + rememberedTab + '"]')) {
        Studio.state.activeTab = rememberedTab;
      }
    } catch (_) {}
    const campaignsOk = await refreshCampaigns(remembered);
    if (campaignsOk) showTab(Studio.state.activeTab);
    // a failed list left the error panel in the active section: rendering a
    // tab now would wipe it with 'No campaign selected' advice that cannot
    // work while the server is down
    checkSetup(); // fire-and-forget: the strip appears only if setup is incomplete
    // the job-queue strip (ticket 02): a slow poll while a campaign is
    // selected — what is running, what is queued, and the honest wait
    queueStripTimer = setInterval(pollQueueStrip, 5000);
    pollQueueStrip();
  }

  /* ---------- exports ---------- */
  // Honest durations, single-sourced (ticket 01 wrote the copy; ticket 16
  // gave it one home): every NUMBER the UI shows about how long something
  // takes reads from here, so a re-measured estimate is updated once.
  // 'a few minutes' stays prose in place — it carries no number that can
  // contradict another string. TUTORIAL.md mirrors these in prose.
  Studio.DURATIONS = {
    minutesPerWeek: 10,   // the honest midpoint; the band is half-to-full
    band: function (weeks) {
      return Math.round(weeks * 5) + '\u2013' + (weeks * 10) + ' minutes';
    },
    approx: function (weeks) {
      return (weeks * 10) + ' minutes';
    },
    weekRun: '10\u201320 minutes'  // one live week, roster-dependent
  };
  Studio.esc = esc;
  Studio.el = el;
  Studio.fmtInt = fmtInt;
  Studio.api = api;
  Studio.stringList = function (items, cls) {
    return '<ul class="findings-list' + (cls ? ' ' + esc(cls) : '') + '">' +
      items.map((f) => '<li>' + esc(String(f)) + '</li>').join('') + '</ul>';
  };
  // refresh a tab's content only if the user is still looking at it: a job
  // finishing server-side must not yank the DM across tabs (or wipe a form
  // they are typing in) — the caller's .then just skips the re-render
  Studio.rerenderIfCurrent = function(name) {
    if (Studio.state.activeTab === name) showTab(name);
  };

  Studio.pollJob = pollJob;
  Studio.md = md;
  Studio.confirmRun = confirmRun;
  Studio.toast = toast;
  Studio.campUrl = campUrl;
  Studio.fileUrl = fileUrl;
  Studio.errorPanel = errorPanel;
  Studio.emptyState = emptyState;
  Studio.noCampaign = noCampaign;
  Studio.helpModal = helpModal;
  Studio.loading = loading;
  Studio.showTab = showTab;

  document.addEventListener('DOMContentLoaded', init);
}());
