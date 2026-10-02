/* Campaign Studio — world.js
   World Building tab: geography digest, timeline, entities, style editing,
   backstory generation, name helper. */
(function () {
  'use strict';
  const S = window.Studio;

  // Last failed backstory for this campaign: the transient error box dies on
  // tab switch, so re-render shows this. Cleared on success.
  let backstoryFailure = null; // { campaign, error }

  /* ----- source badges: authored=ink, session=blue, sim:week-N=red ----- */

  function sourcePill(source) {
    const src = String(source == null ? '' : source);
    let cls = 'pill-source-neutral';
    if (src === 'authored') cls = 'pill-source-authored';
    else if (src === 'session') cls = 'pill-source-session';
    else if (/^sim/.test(src)) cls = 'pill-source-sim';
    return '<span class="pill ' + cls + '">' + S.esc(src || 'unknown') + '</span>';
  }

  /* ----- entities: the store may be a list, keyed by name, or grouped by kind ----- */

  function normalizeEntity(e, fallbackKind) {
    e = e || {};
    return {
      name: e.name || e.id || '(unnamed)',
      kind: e.kind || fallbackKind || '',
      note: e.note || e.summary || e.text || e.description || '',
      aliases: Array.isArray(e.aliases) ? e.aliases : []
    };
  }

  function normalizeEntities(raw) {
    const out = [];
    if (Array.isArray(raw)) {
      raw.forEach((e) => out.push(normalizeEntity(e)));
      return out;
    }
    if (raw && typeof raw === 'object') {
      Object.keys(raw).forEach((key) => {
        const v = raw[key];
        if (Array.isArray(v)) {
          v.forEach((e) => out.push(normalizeEntity(e, key)));
        } else if (v && typeof v === 'object') {
          const merged = Object.assign({}, v);
          if (!merged.name) merged.name = key;
          out.push(normalizeEntity(merged));
        } else {
          out.push(normalizeEntity({ name: key, note: v }));
        }
      });
    }
    return out;
  }

  function townCount(n) {
    let v = n.towns;
    if (v == null) v = n.town_count;
    if (v == null) v = n.burgs;
    if (v == null) return '—';
    if (Array.isArray(v)) return String(v.length);
    return String(v);
  }

  /* ----- panels ----- */

  function geographyDigest(geo, host) {
    host.innerHTML = '';
    const nations = (geo && Array.isArray(geo.nations)) ? geo.nations : [];
    const burgs = (geo && Array.isArray(geo.burgs)) ? geo.burgs
      : (geo && Array.isArray(geo.major_settlements)) ? geo.major_settlements : [];

    const wrap = S.el('<div class="digest-grid"></div>');

    const natTable = S.el(
      '<div><h4 class="sub-title">Nations</h4><table class="data-table">' +
        '<thead><tr><th>Name</th><th>Culture</th><th>Towns</th></tr></thead><tbody></tbody></table></div>'
    );
    const natBody = natTable.querySelector('tbody');
    if (!nations.length) {
      natBody.appendChild(S.el('<tr><td colspan="3" class="muted">No nations in geography.</td></tr>'));
    }
    nations.forEach((n) => {
      natBody.appendChild(S.el(
        '<tr>' +
          '<td>' + S.esc(n.name || n.title || '(unnamed)') + '</td>' +
          '<td>' + S.esc(n.culture || n.culture_name || '—') + '</td>' +
          '<td>' + S.esc(townCount(n)) + '</td>' +
        '</tr>'
      ));
    });

    const burgTable = S.el(
      '<div><h4 class="sub-title">Burgs</h4><table class="data-table">' +
        '<thead><tr><th>Name</th><th>Nation</th><th>Population</th><th>Port</th></tr></thead><tbody></tbody></table></div>'
    );
    const burgBody = burgTable.querySelector('tbody');
    if (!burgs.length) {
      burgBody.appendChild(S.el('<tr><td colspan="4" class="muted">No burgs in geography.</td></tr>'));
    }
    burgs.forEach((b) => {
      const bp = (b.population != null && b.population !== '') ? b.population
        : (b.population_thousands != null ? b.population_thousands * 1000 : null);
      burgBody.appendChild(S.el(
        '<tr>' +
          '<td>' + S.esc(b.name || '(unnamed)') + '</td>' +
          '<td>' + S.esc(b.nation || b.country || '—') + '</td>' +
          '<td class="num">' + S.esc((bp != null && bp !== '') ? S.fmtInt(bp) : '—') + '</td>' +
          '<td>' + (b.port ? '<span class="pill pill-port">port</span>' : '<span class="muted">—</span>') + '</td>' +
        '</tr>'
      ));
    });

    wrap.appendChild(natTable);
    wrap.appendChild(burgTable);
    host.appendChild(wrap);
  }

  function timelineList(entries, host) {
    host.innerHTML = '';
    if (!entries.length) {
      host.appendChild(S.emptyState('No timeline entries yet.'));
      return;
    }
    const list = S.el('<ul class="timeline"></ul>');
    entries.forEach((e) => {
      const item = S.el(
        '<li class="timeline-entry">' +
          '<div class="entry-when">' +
            '<span class="pill pill-when">' + S.esc(e.when || '?') + '</span>' +
          '</div>' +
          '<div class="entry-main">' +
            '<div class="entry-head"><strong class="entry-title">' + S.esc(e.title || '(untitled)') + '</strong>' +
              sourcePill(e.source) + '</div>' +
            '<p class="entry-text">' + S.esc(e.text || '') + '</p>' +
          '</div>' +
        '</li>'
      );
      list.appendChild(item);
    });
    host.appendChild(list);
  }

  function entitiesTable(entries, host) {
    host.innerHTML = '';
    if (!entries.length) {
      host.appendChild(S.emptyState('No entities recorded yet.'));
      return;
    }
    const table = S.el(
      '<table class="data-table"><thead>' +
        '<tr><th>Name</th><th>Kind</th><th>Note</th><th>Aliases</th></tr>' +
      '</thead><tbody></tbody></table>'
    );
    const body = table.querySelector('tbody');
    entries.forEach((e) => {
      const row = S.el(
        '<tr' + (e.file ? ' class="entity-row" title="Open page"' : '') + '>' +
          '<td class="strong">' + S.esc(e.name) + '</td>' +
          '<td>' + S.esc(e.kind || '—') + '</td>' +
          '<td>' + S.esc(e.note || '—') + '</td>' +
          '<td class="muted">' + S.esc(e.aliases.length ? e.aliases.join(', ') : '—') + '</td>' +
        '</tr>'
      );
      if (e.file) {
        // page-backed: the stem is the URL piece (routes never touch a shell)
        const page = String(e.file).replace(/^lore\/entities\//, '').replace(/\.md$/, '');
        row.addEventListener('click', () => openEntityPage(page, host));
      }
      body.appendChild(row);
    });
    host.appendChild(table);
    const panel = S.el('<div class="entity-page-host"></div>');
    host.appendChild(panel);
  }

  /* ----- entity page view: the page + its computed backlinks ----- */

  function openEntityPage(page, host) {
    const panel = host.querySelector('.entity-page-host') || host;
    panel.innerHTML = '';
    const box = S.el(
      '<div class="entity-page">' +
        '<div class="entity-page-head">' +
          '<strong class="entity-page-title">' + S.esc(page) + '</strong>' +
          '<button type="button" class="btn entity-page-close">Close</button>' +
        '</div>' +
        '<div class="loading">Loading page…</div>' +
      '</div>'
    );
    box.querySelector('.entity-page-close').addEventListener('click', () => {
      box.remove();
    });
    panel.appendChild(box);
    const body = box.querySelector('.loading');
    S.api(S.campUrl('/lore/pages/' + encodeURIComponent(page))).then((p) => {
      box.innerHTML = '';
      const head = S.el(
        '<div class="entity-page-head">' +
          '<strong class="entity-page-title">' + S.esc(p.name || page) + '</strong>' +
          '<span class="pill pill-when">' + S.esc(p.kind || 'unknown') + '</span>' +
          '<button type="button" class="btn entity-page-close">Close</button>' +
        '</div>'
      );
      head.querySelector('.entity-page-close').addEventListener('click', () => {
        box.remove();
      });
      box.appendChild(head);
      if (p.aliases && p.aliases.length) {
        box.appendChild(S.el('<p class="muted">also: ' +
          S.esc(p.aliases.join(', ')) + '</p>'));
      }
      const article = S.el('<div class="entity-page-body"></div>');
      article.innerHTML = S.md(p.body || p.note || '(empty page)');
      box.appendChild(article);
      const bl = Array.isArray(p.backlinks) ? p.backlinks : [];
      const blBox = S.el('<div class="entity-backlinks"></div>');
      blBox.appendChild(S.el('<h4 class="sub-title">Mentioned in</h4>'));
      if (!bl.length) {
        blBox.appendChild(S.emptyState('Nothing links here yet.'));
      } else {
        const list = S.el('<ul class="backlink-list"></ul>');
        bl.forEach((b) => {
          const li = S.el(
            '<li class="backlink-item">' +
              '<span class="pill search-kind search-kind-' +
                S.esc(b.source_kind) + '">' + S.esc(b.source_kind) + '</span> ' +
              S.esc(b.source_name || b.source_file) +
            '</li>'
          );
          if (b.source_kind === 'chronicle' && S.tabs.sim) {
            // the tab is registered as "sim" — "simulation" never existed,
            // so this click was dead (found 2026-09-25)
            li.style.cursor = 'pointer';
            li.title = 'Read in the Simulation tab';
            li.addEventListener('click', () => S.showTab('sim'));
          }
          list.appendChild(li);
        });
        blBox.appendChild(list);
      }
      box.appendChild(blBox);
    }).catch((err) => {
      body.innerHTML = '';
      body.className = '';
      body.appendChild(S.errorPanel(err));
    });
  }

  /* ----- style panel ----- */

  function stylePanel(style, host) {
    host.innerHTML = '';
    const files = (style && typeof style === 'object') ? Object.keys(style) : [];
    if (!files.length) {
      host.appendChild(S.emptyState('No style files in the lore store.'));
      return;
    }
    const wrap = S.el(
      '<div class="form-block">' +
        '<div class="inline-form">' +
          '<label class="field"><span class="field-label">File</span>' +
            '<select class="input style-select"></select></label>' +
        '</div>' +
        '<textarea class="input style-text" rows="10" spellcheck="false"></textarea>' +
        '<div class="form-actions">' +
          '<button type="button" class="btn btn-primary style-save">Save</button>' +
          '<span class="muted form-note">Saves straight into the campaign&rsquo;s files ' +
            '(<code>lore/style/</code>) &mdash; edit with care.</span>' +
        '</div>' +
        '<div class="form-error"></div>' +
      '</div>'
    );
    const select = wrap.querySelector('.style-select');
    const area = wrap.querySelector('.style-text');
    const saveBtn = wrap.querySelector('.style-save');
    const errBox = wrap.querySelector('.form-error');

    files.forEach((f, i) => {
      const opt = S.el('<option value="' + S.esc(f) + '">' + S.esc(f) + '</option>');
      if (i === 0) opt.selected = true;
      select.appendChild(opt);
    });
    area.value = String(style[files[0]] == null ? '' : style[files[0]]);

    select.addEventListener('change', () => {
      area.value = String(style[select.value] == null ? '' : style[select.value]);
    });

    saveBtn.addEventListener('click', async () => {
      const file = select.value;
      if (!file || saveBtn.disabled) return;
      saveBtn.disabled = true;
      errBox.innerHTML = '';
      try {
        await S.api(S.campUrl('/lore/style/' + encodeURIComponent(file)), {
          method: 'PUT',
          body: { text: area.value }
        });
        style[file] = area.value;
        S.toast('Saved ' + file, 'ok');
      } catch (err) {
        errBox.innerHTML = '';
        errBox.appendChild(S.errorPanel(err));
        S.toast('Save failed' + (err && err.error ? ': ' + err.error : ''), 'error');
      } finally {
        saveBtn.disabled = false;
      }
    });
    host.appendChild(wrap);
  }

  /* ----- generate backstory ----- */

  function backstoryCard(host) {
    const card = S.el(
      '<section class="panel panel-card">' +
        '<h3 class="panel-title">Generate backstory</h3>' +
        '<p class="panel-note">One metered LLM call: a few minutes, then the entries wait in Review. Constrained to geography + existing lore; output is staged as pending, never auto-applied.</p>' +
        '<div class="form-block">' +
          '<form class="inline-form">' +
            '<label class="field grow"><span class="field-label">Focus</span>' +
              '<input type="text" class="input focus-input" placeholder="e.g. the salt tax riots of the Docks"></label>' +
            '<button type="submit" class="btn btn-primary">Generate</button>' +
          '</form>' +
          '<pre class="log-tail hidden"></pre>' +
          '<div class="form-error"></div>' +
          '<div class="gen-result hidden"></div>' +
        '</div>' +
      '</section>'
    );
    const form = card.querySelector('form');
    const input = card.querySelector('.focus-input');
    const btn = form.querySelector('button[type="submit"]');
    const logBox = card.querySelector('.log-tail');
    const errBox = card.querySelector('.form-error');
    const resultBox = card.querySelector('.gen-result');

    // show a remembered failure from before a tab switch (see above)
    if (backstoryFailure && backstoryFailure.campaign === S.state.campaign) {
      errBox.appendChild(S.errorPanel(backstoryFailure.error));
    }

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      if (btn.disabled) return;
      const ok = await S.confirmRun(
        'Generate pending backstory entries with the LLM? This spends API ' +
        'credits and runs a few minutes; the entries land in Review — ' +
        'nothing is applied directly.'
      );
      if (!ok) return;
      resultBox.classList.add('hidden');
      const log = S.makeLogBox(logBox);
      S.runJob({
        path: S.campUrl('/lore/entries'),
        body: input.value.trim() ? { focus: input.value.trim() } : {},
        btn: btn, errBox: errBox, log: log,
        failLabel: 'Generation failed'
      }).then((result) => {
        backstoryFailure = null;
        const r = result || {};
        const count = (r.entries != null) ? r.entries : '?';
        const findings = Array.isArray(r.findings) ? r.findings.length : 0;
        resultBox.innerHTML = '';
        resultBox.appendChild(S.el(
          '<div class="success-panel"><strong>' + S.esc(String(count)) + '</strong> entr' +
          (String(count) === '1' ? 'y' : 'ies') + ' staged to Review — ' +
          S.esc(String(findings)) + ' verification finding' + (findings === 1 ? '' : 's') + ' attached.' +
          '</div>'
        ));
        const go = S.el('<button type="button" class="btn">Go to Review</button>');
        go.addEventListener('click', () => S.showTab('review'));
        resultBox.appendChild(go);
        resultBox.classList.remove('hidden');
        S.toast('Backstory staged to Review', 'ok');
      }).catch((err) => {
        backstoryFailure = { campaign: S.state.campaign, error: err };
      });
    });
    host.appendChild(card);
  }

  /* ----- name helper ----- */

  function nameHelperCard(host) {
    const card = S.el(
      '<section class="panel panel-card">' +
        '<h3 class="panel-title">Name helper</h3>' +
        '<p class="panel-note">Names are drawn from the culture&rsquo;s seed list ' +
          '(<code>lore/style/names-&lt;culture&gt;.txt</code>) and voice excerpts — in-voice, not guaranteed.</p>' +
        '<div class="form-block">' +
          '<form class="inline-form">' +
            '<label class="field"><span class="field-label">Culture</span>' +
              '<input type="text" class="input culture-input" placeholder="e.g. Shadian"></label>' +
            '<label class="field field-narrow"><span class="field-label">Count</span>' +
              '<input type="number" class="input count-input" min="1" max="50" value="8"></label>' +
            '<button type="submit" class="btn btn-primary">Suggest names</button>' +
          '</form>' +
          '<div class="form-error"></div>' +
          '<div class="name-results hidden"></div>' +
          '<div class="names-stage hidden">' +
            '<p class="panel-note"><strong>No seed list for this culture yet.</strong> ' +
              'The helper refuses to invent names from nothing (fed-or-refused) — ' +
              'give it 3+ names to draw from, one per line:</p>' +
            '<textarea class="input names-seeds" rows="5" ' +
              'placeholder="e.g. Kestra&#10;Olvane&#10;Dross&#10;Margan"></textarea>' +
            '<div class="inline-form">' +
              '<button type="button" class="btn btn-primary names-stage-btn">Stage seed list</button>' +
            '</div>' +
            '<div class="stage-error"></div>' +
          '</div>' +
        '</div>' +
      '</section>'
    );
    const form = card.querySelector('form');
    const cultureIn = card.querySelector('.culture-input');
    const countIn = card.querySelector('.count-input');
    const btn = form.querySelector('button[type="submit"]');
    const errBox = card.querySelector('.form-error');
    const out = card.querySelector('.name-results');
    const stageBox = card.querySelector('.names-stage');
    const seedsArea = card.querySelector('.names-seeds');
    const stageBtn = card.querySelector('.names-stage-btn');
    const stageErr = card.querySelector('.stage-error');

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const culture = cultureIn.value.trim();
      if (!culture || btn.disabled) return;
      const count = Math.max(1, Math.min(50, Number(countIn.value) || 8));
      const ok = await S.confirmRun(
        'Suggest names with the LLM? This spends a few API credits.'
      );
      if (!ok) return;
      btn.disabled = true;
      errBox.innerHTML = '';
      (async () => {
        return S.api(S.campUrl('/lore/name'), {
          method: 'POST',
          body: { culture: culture, count: count }
        });
      })().then((resp) => {
        const names = (resp && Array.isArray(resp.names)) ? resp.names : [];
        out.innerHTML = '';
        if (!names.length) {
          out.appendChild(S.emptyState('No names came back — the culture list may be empty.'));
        } else {
          names.forEach((n) => {
            out.appendChild(S.el('<span class="pill pill-name">' + S.esc(n) + '</span>'));
          });
        }
        out.classList.remove('hidden');
      }).catch((err) => {
        errBox.innerHTML = '';
        errBox.appendChild(S.errorPanel(err));
        // the fed-or-refused 422 is actionable: offer to stage the seed list
        if (err && err.status === 422 && /names-/.test(err.error || '')) {
          stageBox.classList.remove('hidden');
          if (!seedsArea.value.trim()) seedsArea.focus();
        }
        S.toast('Naming failed' + (err && err.error ? ': ' + err.error : ''), 'error');
      }).finally(() => { btn.disabled = false; });
    });

    stageBtn.addEventListener('click', async () => {
      if (stageBtn.disabled) return;
      const culture = cultureIn.value.trim();
      if (!culture) {
        S.toast('Type the culture name above first.', 'info');
        return;
      }
      const file = 'names-' + culture.toLowerCase() + '.txt';
      stageBtn.disabled = true;
      stageErr.innerHTML = '';
      try {
        await S.api(S.campUrl('/lore/style/' + encodeURIComponent(file)), {
          method: 'POST',
          body: { text: seedsArea.value }
        });
        S.toast('Seed list staged (' + file + ') — Suggest names is ready.', 'ok');
        stageBox.classList.add('hidden');
        errBox.innerHTML = '';
      } catch (err) {
        stageErr.innerHTML = '';
        stageErr.appendChild(S.errorPanel(err));
      } finally {
        stageBtn.disabled = false;
      }
    });
    host.appendChild(card);
  }

  /* ----- write the missing pages (dangling links -> pending drafts) ----- */

  function missingPagesCard(host) {
    const card = S.el(
      '<section class="panel panel-card">' +
        '<h3 class="panel-title">Write the missing pages</h3>' +
        '<p class="panel-note">Dangling wiki-links are generation targets: the ' +
          'pages that mention the name are the grounding, and drafts land in ' +
          'Review &mdash; never applied directly. One metered call per page: ' +
          'a few minutes for a typical run.</p>' +
        '<div class="form-block">' +
          '<div class="missing-targets"><p class="muted">Checking the lore&hellip;</p></div>' +
          '<div class="inline-form">' +
            '<label class="field missing-limit">' +
              '<span class="field-label">Max pages this run</span>' +
              '<input type="number" class="input missing-limit-n" min="1" step="1" value="8" ' +
                'aria-label="Maximum pages to generate this run"></label>' +
            '<button type="button" class="btn btn-primary missing-write" disabled>Write the missing pages</button>' +
          '</div>' +
          '<pre class="log-tail hidden"></pre>' +
          '<div class="form-error"></div>' +
          '<div class="gen-result hidden"></div>' +
        '</div>' +
      '</section>'
    );
    const targetsBox = card.querySelector('.missing-targets');
    const limitInput = card.querySelector('.missing-limit-n');
    const btn = card.querySelector('.missing-write');
    const logBox = card.querySelector('.log-tail');
    const errBox = card.querySelector('.form-error');
    const resultBox = card.querySelector('.gen-result');
    let targets = [];

    S.api(S.campUrl('/lore/pages/missing')).then((resp) => {
      targets = (resp && Array.isArray(resp.targets)) ? resp.targets : [];
      targetsBox.innerHTML = '';
      if (!targets.length) {
        targetsBox.appendChild(S.emptyState(
          'No dangling links — type [[Something New]] on an entity page ' +
          '(or in Obsidian) and it shows up here.'));
        return;
      }
      targets.forEach((t) => {
        targetsBox.appendChild(S.el(
          '<span class="pill pill-name" title="' +
            S.esc(String(t.sources.length) + ' page(s) mention it') + '">' +
            S.esc(t.target) + '</span> '));
      });
      btn.disabled = false;
    }).catch((err) => {
      targetsBox.innerHTML = '';
      targetsBox.appendChild(S.errorPanel(err, 'The link index could not be loaded.'));
    });

    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      // the pre-spend throttle: the server accepts {"limit": n} and stops
      // there — without it one click on a 30-dangling-link vault burns the
      // full LLM call ceiling in one go
      const n = Math.max(1, parseInt(limitInput.value, 10) || 1);
      const capped = Math.min(targets.length, n);
      const ok = await S.confirmRun(
        'Generate draft pages for up to ' + capped + ' dangling link' +
        (capped === 1 ? '' : 's') + ' with the LLM? This spends API ' +
        'credits; the drafts land in Review — nothing is applied directly.'
      );
      if (!ok) return;
      resultBox.classList.add('hidden');
      const log = S.makeLogBox(logBox);
      S.runJob({
        path: S.campUrl('/lore/pages/missing'),
        body: { targets: targets.map((t) => t.target), limit: n },
        btn: btn, errBox: errBox, log: log,
        failLabel: 'Generation failed'
      }).then((result) => {
        const r = result || {};
        const count = (r.pages != null) ? r.pages : '?';
        const findings = Array.isArray(r.findings) ? r.findings.length : 0;
        resultBox.innerHTML = '';
        resultBox.appendChild(S.el(
          '<div class="success-panel"><strong>' + S.esc(String(count)) +
          '</strong> page draft' + (String(count) === '1' ? '' : 's') +
          ' staged to Review — ' + S.esc(String(findings)) +
          ' verification finding' + (findings === 1 ? '' : 's') + ' attached.</div>'
        ));
        const go = S.el('<button type="button" class="btn">Go to Review</button>');
        go.addEventListener('click', () => S.showTab('review'));
        resultBox.appendChild(go);
        resultBox.classList.remove('hidden');
        S.toast('Page drafts staged to Review', 'ok');
      }).catch(() => {});
    });
    host.appendChild(card);
  }

  /* ----- authoring lore: the outside-editor pointer (no in-app editor,
     by ruling — this card only points at the folder) ----- */

  function authoringKey(campaign) {
    return 'campaign-studio.authoring-dismissed.' + campaign;
  }

  function authoringDismissed(campaign) {
    try { return localStorage.getItem(authoringKey(campaign)) === '1'; }
    catch (_) { return false; }
  }

  function authoringCard() {
    const card = S.el(
      '<section class="panel panel-card authoring-card">' +
        '<div class="authoring-head">' +
          '<h3 class="panel-title">Authoring lore</h3>' +
          '<button type="button" class="btn authoring-dismiss">Dismiss</button>' +
        '</div>' +
        '<p class="panel-note">This is now an <strong>Obsidian household</strong>. ' +
          'Every page you see here &mdash; entities, chronicles, chronicle ' +
          'links, the works &mdash; is a plain markdown file under ' +
          '<code>campaigns/' + S.esc(S.state.campaign || '') + '/lore/</code>. ' +
          'Write in Obsidian; the studio picks it up on reload.</p>' +
        '<div class="form-block">' +
          '<div class="inline-form">' +
            '<code class="lore-path">campaigns/' + S.esc(S.state.campaign || '') +
              '/lore/</code>' +
            '<button type="button" class="btn lore-copy">Copy path</button>' +
            '<a class="btn btn-primary lore-open hidden" ' +
              'rel="noopener">Open in Obsidian</a>' +
            '<a class="btn lore-get" target="_blank" rel="noopener" ' +
              'href="https://obsidian.md/download">Get Obsidian</a>' +
          '</div>' +
          '<p class="muted form-note">The copy button gives the full folder ' +
            'path &mdash; point any editor at it. With Obsidian installed, ' +
            'Open focuses it (the first click on a new folder asks Obsidian ' +
            'to add the vault; that dialog is Obsidian&rsquo;s own).</p>' +
          '<div class="form-error"></div>' +
        '</div>' +
      '</section>'
    );
    const pathEl = card.querySelector('.lore-path');
    const openLink = card.querySelector('.lore-open');
    const errBox = card.querySelector('.form-error');

    card.querySelector('.authoring-dismiss').addEventListener('click', () => {
      try { localStorage.setItem(authoringKey(S.state.campaign), '1'); }
      catch (_) {}
      card.remove();
    });

    card.querySelector('.lore-copy').addEventListener('click', async () => {
      const text = pathEl.textContent || '';
      try {
        await navigator.clipboard.writeText(text);
        S.toast('Lore folder path copied.', 'ok');
      } catch (_) {
        S.toast('Copy failed — select the path and copy it by hand.', 'error');
      }
    });

    // the absolute folder + its obsidian:// URI are server-resolved — the
    // browser only knows the campaign name
    S.api(S.campUrl('/lore/path')).then((p) => {
      if (!card.isConnected) return;
      if (p && p.path) pathEl.textContent = String(p.path);
      if (p && p.obsidian_uri) {
        openLink.href = String(p.obsidian_uri); // property set: never parsed as markup
        openLink.classList.remove('hidden');
      }
    }).catch((err) => {
      if (!card.isConnected) return;
      errBox.innerHTML = '';
      errBox.appendChild(S.errorPanel(err,
        'The resolved folder path could not be loaded — the relative ' +
        'path above still holds under the studio checkout.'));
    });
    return card;
  }

  /* ----- tab renderer ----- */

  S.tabs.world = async function (container) {
    container.innerHTML = '';
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }

    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);

    // The permanent 🔗: dismissal hides the card, never the way back in.
    const head = S.el(
      '<div class="world-head">' +
        '<button type="button" class="btn world-authoring-link" ' +
          'aria-label="Authoring lore" ' +
          'title="Authoring lore — where the files live">🔗</button>' +
      '</div>'
    );
    head.querySelector('.world-authoring-link').addEventListener('click', () => {
      try { localStorage.removeItem(authoringKey(S.state.campaign)); }
      catch (_) {}
      S.showTab('world'); // re-render: the card reflects the cleared flag
    });
    root.appendChild(head);
    if (!authoringDismissed(S.state.campaign)) root.appendChild(authoringCard());

    // Geography digest.
    const geoPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Geography digest</h3>' +
        '<div class="panel-body"><div class="loading">Loading geography…</div></div></section>'
    );
    const geoBody = geoPanel.querySelector('.panel-body');
    root.appendChild(geoPanel);

    // Timeline.
    const timelinePanel = S.el(
      '<section class="panel"><h3 class="panel-title">Timeline</h3>' +
        '<div class="panel-body"><div class="loading">Loading lore…</div></div></section>'
    );
    const timelineBody = timelinePanel.querySelector('.panel-body');
    root.appendChild(timelinePanel);

    // Entities.
    const entitiesPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Entities</h3>' +
        '<div class="panel-body"><div class="loading">Loading entities…</div></div></section>'
    );
    const entitiesBody = entitiesPanel.querySelector('.panel-body');
    root.appendChild(entitiesPanel);

    // Style panel.
    const styleHost = S.el(
      '<section class="panel"><h3 class="panel-title">Style &amp; voice</h3>' +
        '<div class="panel-body"><div class="loading">Loading style files…</div></div></section>'
    );
    const styleBody = styleHost.querySelector('.panel-body');
    root.appendChild(styleHost);

    // Generation row.
    const genRow = S.el('<div class="digest-grid"></div>');
    root.appendChild(genRow);
    backstoryCard(genRow);
    nameHelperCard(genRow);
    missingPagesCard(genRow);

    // Geography is optional-ish for this tab; lore is the core. Load both,
    // each failing into its own panel so one broken route doesn't kill the tab.
    S.api(S.campUrl('/geography')).then((geo) => {
      geographyDigest(geo, geoBody);
    }).catch((err) => {
      geoBody.innerHTML = '';
      geoBody.appendChild(S.errorPanel(err, 'World geography could not be loaded.'));
    });

    try {
      const lore = await S.api(S.campUrl('/lore'));
      const timeline = (lore && Array.isArray(lore.timeline)) ? lore.timeline.slice() : [];
      timeline.sort((a, b) => String(a.id || '').localeCompare(String(b.id || '')));
      timelineList(timeline, timelineBody);
      entitiesTable(normalizeEntities(lore && lore.entities), entitiesBody);
      stylePanel(lore && lore.style, styleBody);
    } catch (err) {
      timelineBody.innerHTML = '';
      timelineBody.appendChild(S.errorPanel(err, 'The lore store could not be loaded.'));
      entitiesBody.innerHTML = '';
      entitiesBody.appendChild(S.errorPanel(err));
      styleBody.innerHTML = '';
      styleBody.appendChild(S.errorPanel(err));
    }
  };
}());
