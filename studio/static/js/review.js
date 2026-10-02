/* Campaign Studio — review.js
   Review tab (M4): the one queue for everything awaiting a human decision.
   Items grouped per pending file, with kind badges, provenance, findings
   inline (muted, level-colored), approve/delete per item and per file. */
(function () {
  'use strict';
  const S = window.Studio;

  function kindPill(kind) {
    const k = String(kind == null ? '' : kind);
    let cls = 'pill-kind-neutral';
    if (k === 'lore-entry') cls = 'kind-lore-entry';
    else if (k === 'timeline-approval') cls = 'kind-timeline-approval';
    else if (k === 'finding') cls = 'kind-finding';
    return '<span class="pill ' + cls + '">' + S.esc(k || 'item') + '</span>';
  }

  function sourcePill(source) {
    const s = String(source == null ? '' : source);
    let cls = 'pill-source-neutral';
    if (s === 'authored') cls = 'pill-source-authored';
    else if (s === 'session') cls = 'pill-source-session';
    else if (/^sim:week-/.test(s)) cls = 'pill-source-sim';
    return '<span class="pill ' + cls + '">' + S.esc(s || 'unknown') + '</span>';
  }

  function whenPill(when) {
    if (when == null || when === '') return '';
    return '<span class="pill pill-when">' + S.esc(when) + '</span>';
  }

  function levelClass(level) {
    const l = String(level == null ? '' : level).toLowerCase();
    if (l === 'error') return 'finding-error';
    if (l === 'warning') return 'finding-warning';
    return 'finding-suggestion';
  }

  /* Two-step confirm for destructive buttons: first click arms, second
     within 4 s fires. No server-side modal state (design D5 note). */
  function armed(btn) {
    if (btn.dataset.armed) return true;
    btn.dataset.armed = '1';
    btn.dataset.label = btn.textContent;
    btn.textContent = 'Really delete?';
    btn.classList.add('btn-armed');
    btn.setAttribute('aria-live', 'assertive'); // the second chance must be announced
    setTimeout(() => {
      if (!btn.isConnected) return;
      delete btn.dataset.armed;
      btn.textContent = btn.dataset.label;
      btn.classList.remove('btn-armed');
    }, 4000);
    return false;
  }

  async function act(path, body, errEl) {
    const resp = await S.api(S.campUrl('/review/' + path), { method: 'POST', body: body });
    const n = resp && (path === 'approve' ? resp.approved : resp.deleted);
    S.toast((path === 'approve' ? 'Approved ' : 'Deleted ') +
      (n != null ? n : '?') + (n === 1 ? ' entry' : ' entries'), 'ok');
    const snap = resp && resp.snapshot;
    if (snap && (snap.state === 'failed' || snap.state === 'unavailable')) {
      // the files changed fine; only the undo-trail commit did not happen
      S.toast('Published, but the history snapshot ' + snap.state +
        ' — use the Retry snapshot button at the top of this tab.', 'error');
    }
    S.rerenderIfCurrent('review'); // refresh: the queue is the file state
  }

  function actionButtons(item, revision) {
    const approveBtn = S.el('<button type="button" class="btn btn-primary review-approve">Approve</button>');
    const deleteBtn = S.el('<button type="button" class="btn btn-danger review-delete">Delete</button>');
    const inlineErr = S.el('<span class="review-inline-error" role="alert"></span>');

    async function run(path, btn) {
      if (btn.disabled) return;
      approveBtn.disabled = true;
      deleteBtn.disabled = true;
      inlineErr.textContent = '';
      // expected_revision: what this user actually viewed — the server
      // refuses (409, nothing written) if the file changed since
      const body = { file: item.file, ids: [String(item.id)] };
      if (revision) body.expected_revision = revision;
      try {
        await act(path, body, inlineErr);
      } catch (err) {
        inlineErr.textContent = (err && err.error) ? ('Failed: ' + err.error) : 'Failed.';
        S.toast('Review action failed' + (err && err.error ? ': ' + err.error : ''), 'error');
        approveBtn.disabled = false;
        deleteBtn.disabled = false;
      }
    }
    approveBtn.addEventListener('click', () => run('approve', approveBtn));
    deleteBtn.addEventListener('click', () => {
      if (!armed(deleteBtn)) return;
      run('delete', deleteBtn);
    });
    const foot = S.el('<footer class="review-actions"></footer>');
    foot.appendChild(approveBtn);
    foot.appendChild(deleteBtn);
    foot.appendChild(inlineErr);
    return foot;
  }

  function findingLine(f) {
    const level = String((f && f.level) || 'suggestion');
    const rule = (f && f.rule) ? String(f.rule) : '';
    const note = (f && f.note) ? String(f.note) : '';
    const at = (f && f.line != null) ? ('line ' + f.line) : '';
    const text = [rule, note].filter(Boolean).join(' — ');
    return S.el(
      '<li class="' + levelClass(level) + '"><span class="finding-level">' +
      S.esc(level) + '</span> ' + S.esc(text) +
      (at ? ' <span class="finding-at">(' + S.esc(at) + ')</span>' : '') + '</li>'
    );
  }

  function findingsSlot(findings) {
    if (!Array.isArray(findings) || !findings.length) return null;
    const slot = S.el('<div class="findings-slot"><h5 class="findings-title">Verification findings</h5></div>');
    const list = S.el('<ul class="findings-list"></ul>');
    findings.forEach((f) => list.appendChild(findingLine(f)));
    slot.appendChild(list);
    return slot;
  }

  function entryCard(item, revision, obsidian) {
    const title = item.title || '(untitled)';
    const card = S.el(
      '<article class="card review-card">' +
        '<header class="review-head">' +
          '<div class="review-badges">' + kindPill(item.kind) +
            sourcePill(item.source) + whenPill(item.when) +
          '</div>' +
        '</header>' +
        '<h4 class="review-title">' + S.esc(title) + '</h4>' +
        '<div class="review-body"></div>' +
      '</article>'
    );
    card.querySelector('.review-body').textContent = String(item.body == null ? '' : item.body);
    if (item.kind === 'lore-page') {
      // page drafts are markdown the moment they land: the one-line pointer
      // to the outside editor (no in-app editor, by ruling)
      const uri = obsidian && obsidian.obsidian_uri;
      card.appendChild(S.el(
        '<p class="panel-note review-obsidian-pointer">Approve, then ' +
          (uri
            ? '<a href="' + S.esc(String(uri)) + '">open the folder in Obsidian</a>'
            : 'open the folder in Obsidian') +
          ' to edit &mdash; the studio picks the file up on reload.</p>'
      ));
    }
    const slot = findingsSlot(item.findings);
    if (slot) card.appendChild(slot);
    card.appendChild(actionButtons(item, revision));
    return card;
  }

  function looseFindingCard(item) {
    const card = S.el(
      '<article class="card review-card is-finding">' +
        '<header class="review-head">' +
          '<div class="review-badges">' + kindPill(item.kind) +
            '<span class="pill pill-provenance">' + S.esc(item.source || 'verification') + '</span>' +
          '</div>' +
          '<span class="review-advisory">advisory &mdash; the DM decides</span>' +
        '</header>' +
        '<h4 class="review-title">' + S.esc(item.title || 'finding') + '</h4>' +
        '<div class="review-body"></div>' +
      '</article>'
    );
    card.querySelector('.review-body').textContent = String(item.body == null ? '' : item.body);
    return card;
  }

  function groupPanel(group, meta, obsidian) {
    const prov = [];
    if (meta && meta.week != null && meta.week !== '') prov.push('week ' + meta.week);
    if (meta && meta.focus) prov.push('focus: ' + meta.focus);
    if (meta && meta.created) prov.push(String(meta.created));

    const panel = S.el(
      '<section class="panel review-group">' +
        '<header class="review-group-head">' +
          '<div class="review-group-meta">' +
            '<code class="review-file">' + S.esc(group.file) + '</code>' +
            (meta && meta.legacy
              ? '<span class="pill pill-provenance" title="The old implementation already committed this week; approving only writes timeline entries.">legacy &mdash; timeline-only</span>'
              : '') +
            (prov.length
              ? '<span class="review-group-prov">' + S.esc(prov.join(' · ')) + '</span>'
              : '') +
          '</div>' +
          '<div class="review-group-actions">' +
            '<button type="button" class="btn review-approve-all">Approve all (' +
              group.entries.length + ')</button>' +
            '<button type="button" class="btn btn-danger review-delete-all">Delete all</button>' +
          '</div>' +
        '</header>' +
        '<div class="review-list"></div>' +
      '</section>'
    );

    const approveAll = panel.querySelector('.review-approve-all');
    const deleteAll = panel.querySelector('.review-delete-all');
    const inlineErr = S.el('<span class="review-inline-error" role="alert"></span>');
    panel.querySelector('.review-group-actions').appendChild(inlineErr);

    if (!group.entries.length) {
      approveAll.classList.add('hidden');
      deleteAll.classList.add('hidden');
    }

    async function runAll(path, btn) {
      if (btn.disabled) return;
      approveAll.disabled = true;
      deleteAll.disabled = true;
      inlineErr.textContent = '';
      const body = { file: group.file, all: true };
      if (meta && meta.revision) body.expected_revision = String(meta.revision);
      try {
        await act(path, body, inlineErr);
      } catch (err) {
        inlineErr.textContent = (err && err.error) ? ('Failed: ' + err.error) : 'Failed.';
        S.toast('Review action failed' + (err && err.error ? ': ' + err.error : ''), 'error');
        approveAll.disabled = false;
        deleteAll.disabled = false;
      }
    }
    approveAll.addEventListener('click', () => runAll('approve', approveAll));
    deleteAll.addEventListener('click', () => {
      if (!armed(deleteAll)) return;
      runAll('delete', deleteAll);
    });

    const list = panel.querySelector('.review-list');
    const revision = meta && meta.revision ? String(meta.revision) : null;
    group.entries.forEach((it) => list.appendChild(entryCard(it, revision, obsidian)));
    group.findings.forEach((it) => list.appendChild(looseFindingCard(it)));
    return panel;
  }

  S.tabs.review = async function (container) {
    container.innerHTML = '';
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }

    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);
    root.appendChild(S.loading('Loading the queue…'));

    let data;
    try {
      data = await S.api(S.campUrl('/review'));
    } catch (err) {
      root.innerHTML = '';
      root.appendChild(S.errorPanel(err, 'The review queue could not be loaded.'));
      return;
    }
    root.innerHTML = '';

    // recovery surfaces instead of curl: a failed history snapshot means
    // published-but-untracked (POST .../snapshot), a conflict-parked
    // transaction means publication writes are 409-blocked until decided
    // (POST .../publication/resolve)
    const banner = await S.snapshotBanner();
    if (banner) root.appendChild(banner);

    const items = (data && Array.isArray(data.items)) ? data.items : [];
    const notes = (data && Array.isArray(data.notes)) ? data.notes : [];
    if (!items.length) {
      root.appendChild(S.emptyState('Nothing awaits your judgment.'));
      return;
    }

    // page-draft cards carry the Obsidian pointer — one shared fetch, and
    // advisory only: a failed lookup degrades to the pointer without the link
    const obsidian = items.some((it) => it && it.kind === 'lore-page')
      ? await S.api(S.campUrl('/lore/path')).catch(() => null)
      : null;

    // grouping preserves queue order
    const meta = {};
    (data && Array.isArray(data.files) ? data.files : []).forEach((f) => {
      if (f && f.file) meta[f.file] = f;
    });
    const order = [];
    const byFile = {};
    items.forEach((it) => {
      const file = String(it.file || '(unknown)');
      if (!byFile[file]) {
        byFile[file] = { file: file, entries: [], findings: [] };
        order.push(byFile[file]);
      }
      if (it.kind === 'finding') byFile[file].findings.push(it);
      else byFile[file].entries.push(it);
    });

    const nEntries = order.reduce((n, g) => n + g.entries.length, 0);
    root.appendChild(S.el(
      '<p class="panel-note">' + S.esc(String(nEntries)) + ' pending ' +
      (nEntries === 1 ? 'entry' : 'entries') + ' across ' + order.length +
      (order.length === 1 ? ' file' : ' files') +
      '. Approval writes an entry into lore/timeline.yaml with its source kept; delete discards it.</p>'
    ));
    notes.forEach((n) => {
      root.appendChild(S.el('<p class="panel-note review-note">' + S.esc(String(n)) + '</p>'));
    });

    order.forEach((g) => root.appendChild(groupPanel(g, meta[g.file] || {}, obsidian)));
  };
}());
