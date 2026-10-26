/* Campaign Studio — knowledge.js
   Knowledge tab (phases 4+6): approved facts with explicit visibility, the
   deterministic DM briefing, and the party-disclosure record + handout.
   Nothing here is implicit: a fact is dm_only until the DM says otherwise,
   and public-in-world is not party-known until a reveal happens. */
(function () {
  'use strict';
  const S = window.Studio;

  function visPill(vis) {
    if (vis === 'public') {
      return '<span class="pill pill-vis-public">public</span>';
    }
    if (Array.isArray(vis)) {
      return '<span class="pill pill-vis-factions" title="visible only to these factions">' +
        S.esc(vis.join(', ')) + '</span>';
    }
    return '<span class="pill pill-vis-dm">DM only</span>';
  }

  function statusPill(fact) {
    const bits = [];
    if (fact.status === 'superseded') {
      bits.push('<span class="pill pill-superseded" title="kept for provenance, never compiled again">superseded</span>');
    }
    if (fact.stale) {
      bits.push('<span class="pill pill-stale" title="the source file changed since this fact was approved">stale</span>');
    }
    return bits.join(' ');
  }

  function factActions(fact, revealedIds, host, factions) {
    const foot = S.el('<footer class="review-actions"></footer>');
    const inlineErr = S.el('<span class="review-inline-error" role="alert"></span>');

    const current = fact.status !== 'superseded';

    if (current && fact.visibility === 'public') {
      const revealed = revealedIds.has(String(fact.id));
      const btn = S.el('<button type="button" class="btn fact-reveal">' +
        (revealed ? 'Re-reveal (no-op)' : 'Reveal to party') + '</button>');
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        inlineErr.textContent = '';
        try {
          await S.api(S.campUrl('/knowledge/' + encodeURIComponent(fact.id) +
                                '/reveal'), { method: 'POST', body: {} });
          S.toast('Revealed to the party — it is in the handout now.', 'ok');
          S.showTab('knowledge');
        } catch (err) {
          inlineErr.textContent = (err && err.error) ? err.error : 'Reveal failed.';
          btn.disabled = false;
        }
      });
      foot.appendChild(btn);
    }
    if (current && revealedIds.has(String(fact.id))) {
      const btn = S.el('<button type="button" class="btn btn-danger fact-revoke">Revoke</button>');
      btn.addEventListener('click', () => {
        if (!armed(btn)) return;
        btn.disabled = true;
        inlineErr.textContent = '';
        S.api(S.campUrl('/knowledge/' + encodeURIComponent(fact.id) + '/revoke'),
              { method: 'POST' })
          .then(() => {
            S.toast('Revoked — the handout no longer contains it.', 'ok');
            S.showTab('knowledge');
          })
          .catch((err) => {
            inlineErr.textContent = (err && err.error) ? err.error : 'Revoke failed.';
            btn.disabled = false;
          });
      });
      foot.appendChild(btn);
    }
    if (fact.stale && fact.source_file) {
      const btn = S.el('<button type="button" class="btn fact-reconcile">Reconcile (I read the new source)</button>');
      btn.addEventListener('click', () => {
        btn.disabled = true;
        inlineErr.textContent = '';
        S.api(S.campUrl('/knowledge/' + encodeURIComponent(fact.id) + '/reconcile'),
              { method: 'POST' })
          .then(() => {
            S.toast('Reconciled — staleness cleared, text unchanged.', 'ok');
            S.showTab('knowledge');
          })
          .catch((err) => {
            inlineErr.textContent = (err && err.error) ? err.error : 'Reconcile failed.';
            btn.disabled = false;
          });
      });
      foot.appendChild(btn);
    }
    if (current) {
      const btn = S.el('<button type="button" class="btn fact-supersede">Supersede&hellip;</button>');
      btn.addEventListener('click', () => {
        // one form at a time: the form lives just after this card
        if (host.nextElementSibling &&
            host.nextElementSibling.classList.contains('supersede-form')) {
          return;
        }
        const form = supersedeForm(fact, factions);
        btn.disabled = true;
        const cancel = () => { form.remove(); btn.disabled = false; };
        form.querySelector('.supersede-cancel').addEventListener('click', cancel);
        form.addEventListener('submit', async (ev) => {
          ev.preventDefault();
          const body = {
            text: form.querySelector('.supersede-text').value,
            visibility: readVisibility(form),
            source_file: form.querySelector('.supersede-source').value.trim()
          };
          const errBox = form.querySelector('.form-error');
          errBox.innerHTML = '';
          try {
            await S.api(S.campUrl('/knowledge/' + encodeURIComponent(fact.id) +
                                  '/supersede'), { method: 'POST', body: body });
            S.toast('Superseded — the old fact is kept for provenance.', 'ok');
            S.showTab('knowledge');
          } catch (err) {
            errBox.appendChild(S.errorPanel(err));
          }
        });
        host.insertAdjacentElement('afterend', form);
      });
      foot.appendChild(btn);
    }
    foot.appendChild(inlineErr);
    return foot;
  }

  /* Two-step confirm for the destructive revoke button (same pattern as
     review.js's delete: first click arms, second within 4 s fires). */
  function armed(btn) {
    if (btn.dataset.armed) return true;
    btn.dataset.armed = '1';
    btn.dataset.label = btn.textContent;
    btn.textContent = 'Really revoke?';
    btn.classList.add('btn-armed');
    btn.setAttribute('aria-live', 'assertive');
    setTimeout(() => {
      if (!btn.isConnected) return;
      delete btn.dataset.armed;
      btn.textContent = btn.dataset.label;
      btn.classList.remove('btn-armed');
    }, 4000);
    return false;
  }

  function readVisibility(scope) {
    const mode = scope.querySelector('.vis-mode').value;
    if (mode === 'public') return 'public';
    if (mode === 'dm_only') return 'dm_only';
    const ids = [];
    scope.querySelectorAll('.vis-faction:checked').forEach((cb) => {
      ids.push(cb.value);
    });
    return ids; // the server refuses an empty list — say what you mean
  }

  function visibilityFields(scope, factions, current, readError) {
    const mode = (current === 'public') ? 'public'
      : Array.isArray(current) ? 'factions' : 'dm_only';
    // the empty answer differs by WHY it is empty (ticket 63): a failed
    // /simulation read knows nothing about factions, and "create the
    // simulation first" stated that wrong diagnosis as fact
    const empty = readError
      ? '<p class="muted" role="alert">The simulation could not be read (' +
        S.esc(String((readError && readError.error) || 'unknown error')) +
        ') — whether factions exist is unknown, not "none". The visibility ' +
        'stays DM-only until it loads.</p>'
      : '<p class="muted">No factions — create the simulation first.</p>';
    scope.innerHTML =
      '<label class="field"><span class="field-label">Visibility</span>' +
        '<select class="input vis-mode">' +
          '<option value="dm_only"' + (mode === 'dm_only' ? ' selected' : '') + '>DM only (never compiled)</option>' +
          '<option value="public"' + (mode === 'public' ? ' selected' : '') + '>public in world</option>' +
          '<option value="factions"' + (mode === 'factions' ? ' selected' : '') + '>specific factions</option>' +
        '</select></label>' +
      '<div class="vis-factions' + (mode === 'factions' ? '' : ' hidden') + '">' +
        (factions.length
          ? factions.map((f) =>
              '<label class="vis-faction-item"><input type="checkbox" class="vis-faction" value="' +
              S.esc(f.id) + '"' +
              (Array.isArray(current) && current.indexOf(f.id) >= 0 ? ' checked' : '') +
              '> ' + S.esc(f.name || f.id) + '</label>').join('')
          : empty) +
      '</div>';
    scope.querySelector('.vis-mode').addEventListener('change', (ev) => {
      scope.querySelector('.vis-factions').classList.toggle(
        'hidden', ev.target.value !== 'factions');
    });
  }

  function supersedeForm(fact, factions) {
    const form = S.el(
      '<form class="panel-card supersede-form">' +
        '<h4 class="panel-title">Supersede fact ' + S.esc(fact.id) + '</h4>' +
        '<p class="panel-note">The corrected text replaces it; the old fact is ' +
          'kept for provenance and never compiled again.</p>' +
        '<div class="form-block">' +
          '<label class="field"><span class="field-label">Corrected fact</span>' +
            '<textarea class="input supersede-text" rows="3" required></textarea></label>' +
          '<div class="supersede-vis"></div>' +
          '<label class="field"><span class="field-label">Source file (optional, e.g. lore/entities/x.md)</span>' +
            '<input type="text" class="input supersede-source" autocomplete="off" spellcheck="false"></label>' +
          '<div class="inline-form">' +
            '<button type="submit" class="btn btn-primary">Supersede</button>' +
            '<button type="button" class="btn supersede-cancel">Cancel</button>' +
          '</div>' +
          '<div class="form-error"></div>' +
        '</div>' +
      '</form>');
    form.querySelector('.supersede-text').value = String(fact.text || '');
    visibilityFields(form.querySelector('.supersede-vis'), factions, fact.visibility);
    return form;
  }

  function factCard(fact, revealedIds, factions) {
    const card = S.el(
      '<article class="card fact-card">' +
        '<header class="review-head"><div class="review-badges">' +
          visPill(fact.visibility) + statusPill(fact) +
        '</div></header>' +
        // not class="fact-text": that collides with the record form's
        // textarea and every panel-scoped query used to match this div first
        '<div class="fact-display-text"></div>' +
        '<div class="fact-meta"></div>' +
      '</article>');
    card.querySelector('.fact-display-text').textContent = String(fact.text || '');
    const meta = [];
    if (fact.week != null) {
      meta.push('week ' + String(fact.week));
    }
    if (Array.isArray(fact.subjects) && fact.subjects.length) {
      meta.push('subjects: ' + fact.subjects.join(', '));
    }
    if (fact.source_file) {
      meta.push('source: ' + fact.source_file);
    }
    if (fact.approved) {
      meta.push('approved ' + String(fact.approved).slice(0, 10));
    }
    card.querySelector('.fact-meta').textContent = meta.join(' · ');
    card.appendChild(factActions(fact, revealedIds, card, factions));
    return card;
  }

  /* ---------- panels ---------- */

  /* A "Tell the party" click on the Simulation tab parks a prefill payload
     ({text, visibility, source_file, week} — see js/handoff.js) and switches
     here. Fill the record form from it, say what happened AND what did not
     (recording is not revealing), then consume it: a later plain visit must
     not find a stale prefill. Submission stays the unchanged record flow. */
  function applyFactPrefill(panel, visScope) {
    const pre = S.state.factPrefill;
    if (!pre) return;
    delete S.state.factPrefill;
    const form = panel.querySelector('.fact-form');
    if (!form) return;
    form.querySelector('.fact-text').value = String(pre.text || '');
    if (pre.visibility) {
      // set + fire: visibilityFields's change listener keeps the faction
      // checkbox box in step with the mode
      const mode = visScope.querySelector('.vis-mode');
      mode.value = String(pre.visibility);
      mode.dispatchEvent(new Event('change'));
    }
    form.querySelector('.fact-source').value = String(pre.source_file || '');
    form.querySelector('.fact-week').value =
      (pre.week == null || pre.week === '') ? '' : String(pre.week);
    const note = S.el('<p class="panel-note fact-prefill-note"></p>');
    note.textContent = 'Prefilled from a rumor' +
      (pre.week != null ? ' (week ' + String(pre.week) + ')' : '') +
      ' — edit the wording if you want, then Record. Revealing it to the ' +
      'party is still its own step.';
    form.insertAdjacentElement('beforebegin', note);
  }

  async function factsPanel(factions, simReadError) {
    const panel = S.el(
      '<section class="panel panel-card">' +
        '<h3 class="panel-title">Approved facts</h3>' +
        '<p class="panel-note">What is true, and who may know it. Visibility ' +
          'is explicit or nothing ships: <strong>DM only</strong> facts never ' +
          'reach the engine; <strong>public</strong> facts compile into the ' +
          'world; faction-restricted facts reach only the factions named.</p>' +
        '<div class="fact-list"><p class="loading">Loading facts&hellip;</p></div>' +
        '<h4 class="panel-title">Record a fact</h4>' +
        '<form class="fact-form form-block">' +
          '<label class="field"><span class="field-label">The fact (one claim, in-world wording)</span>' +
            '<textarea class="input fact-text" rows="2" required></textarea></label>' +
          '<div class="fact-vis"></div>' +
          '<label class="field"><span class="field-label">Source file (optional — changes make it stale)</span>' +
            '<input type="text" class="input fact-source" autocomplete="off" spellcheck="false" ' +
              'placeholder="e.g. lore/entities/the-harbor-master.md"></label>' +
          '<label class="field field-narrow"><span class="field-label">Week (optional — e.g. the week whose chronicle a rumor came from)</span>' +
            '<input type="number" class="input fact-week" min="1" autocomplete="off"></label>' +
          '<label class="field"><span class="field-label">Subjects (optional, comma-separated)</span>' +
            '<input type="text" class="input fact-subjects" autocomplete="off" spellcheck="false"></label>' +
          '<div class="inline-form">' +
            '<button type="submit" class="btn btn-primary fact-record">Record fact</button>' +
          '</div>' +
          '<div class="form-error"></div>' +
        '</form>' +
      '</section>');

    const list = panel.querySelector('.fact-list');
    const visScope = panel.querySelector('.fact-vis');
    visibilityFields(visScope, factions, 'dm_only', simReadError);
    if (!factions.length && !simReadError) {
      visScope.insertAdjacentElement('afterend', S.el(
        '<p class="panel-note muted">Facts compile into the simulation state — ' +
          'create a sandbox on the Agent Simulation tab to record new ones.</p>'));
    }

    // bound BEFORE the data fetch: the empty-campaign path returns early
    // below, and a form whose submit handler was never attached
    // native-submits — reloading the page and losing the typed fact
    panel.querySelector('.fact-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      // query INSIDE the form: card text used to share a class with the
      // textarea, and a panel-scoped query silently dropped it (422
      // "Field required")
      const form = ev.currentTarget;
      const btn = form.querySelector('.fact-record');
      const errBox = form.querySelector('.form-error');
      if (btn.disabled) return; // one POST per click (ticket 63)
      errBox.innerHTML = '';
      const subjects = form.querySelector('.fact-subjects').value
        .split(',').map((s) => s.trim()).filter(Boolean);
      const weekRaw = form.querySelector('.fact-week').value.trim();
      const weekNum = Number(weekRaw);
      btn.disabled = true;
      try {
        await S.api(S.campUrl('/knowledge'), {
          method: 'POST',
          body: {
            text: form.querySelector('.fact-text').value,
            visibility: readVisibility(visScope),
            source_file: form.querySelector('.fact-source').value.trim(),
            week: (weekRaw !== '' && Number.isFinite(weekNum)) ? weekNum : null,
            subjects: subjects.length ? subjects : null
          }
        });
        S.toast('Fact recorded and compiled where its visibility allows.', 'ok');
        S.showTab('knowledge');
      } catch (err) {
        errBox.appendChild(S.errorPanel(err));
        btn.disabled = false; // a failed record can be retried
      }
    });

    applyFactPrefill(panel, visScope);

    let data;
    let disclosure;
    try {
      [data, disclosure] = await Promise.all([
        S.api(S.campUrl('/knowledge')),
        S.api(S.campUrl('/disclosure'))
      ]);
    } catch (err) {
      list.innerHTML = '';
      list.appendChild(S.errorPanel(err, 'The knowledge records could not be loaded.'));
      return panel;
    }
    const facts = (data && Array.isArray(data.facts)) ? data.facts : [];
    const revealedIds = new Set(((disclosure && disclosure.revealed) || [])
      .map((r) => String(r.fact_id)));
    list.innerHTML = '';
    if (!facts.length) {
      list.appendChild(S.emptyState(
        'No facts recorded yet. Anything the simulation should respect — ' +
        'an alliance, a secret, a lie the party believes — starts here.'));
      return panel;
    }
    const current = facts.filter((f) => f.status !== 'superseded');
    const old = facts.filter((f) => f.status === 'superseded');
    current.forEach((f) => list.appendChild(factCard(f, revealedIds, factions)));
    if (old.length) {
      list.appendChild(S.el('<p class="panel-note muted">' +
        old.length + ' superseded fact' + (old.length === 1 ? '' : 's') +
        ' kept for provenance:</p>'));
      old.forEach((f) => list.appendChild(factCard(f, revealedIds, factions)));
    }

    return panel;
  }

  /* ---------- next-session prep feedback ----------
     The write used to answer with ONE 4.2 s toast, after a multi-second
     git snapshot — miss the toast and the only trace was a new file in
     the vault (found 2026-10-03). The write now also lands as a
     persistent line in the panel, linked to the written file, and the
     line survives tab re-renders via sessionStorage keyed by campaign. */

  const PREP_STORE_KEY = 'studio-prep-last';

  function readPrepStore() {
    try {
      const rec = JSON.parse(sessionStorage.getItem(PREP_STORE_KEY));
      return (rec && rec.camp === S.state.campaign) ? rec : null;
    } catch (_) { return null; }
  }

  /* Pure — the persistent confirmation line for one prep write record
     {camp, file, committed, at}. The file path sits verbatim right after
     "Prep written: " so the panel render can split the line around it and
     link just the path. Pinned by studio/tests/js/test_prep_feedback.cjs. */
  S.prepStoredLine = function (rec) {
    if (!rec || !rec.file) return '';
    const when = (typeof rec.at === 'number')
      ? ' at ' + new Date(rec.at).toTimeString().slice(0, 5) : '';
    return 'Prep written: ' + rec.file +
      (rec.committed ? ' — snapshot committed' : ' — snapshot NOT committed') +
      when + '. A re-run takes the next free -N name, never overwrites.';
  };

  function statusMark(status) {
    if (status === 'pending') {
      return ' <span class="pill pill-stale">proposed, NOT canon</span>';
    }
    if (status === 'needs_attention') {
      return ' <span class="pill pill-superseded">needs attention</span>';
    }
    return '';
  }

  function briefingItem(item) {
    const li = S.el('<li class="briefing-item"></li>');
    const head = S.el('<div class="briefing-head"></div>');
    head.appendChild(S.el('<span class="pill pill-provenance">' +
      S.esc(item.kind || 'item') + '</span>'));
    if (item.week != null) {
      head.appendChild(S.el('<span class="pill pill-when">week ' +
        S.esc(item.week) + '</span>'));
    }
    if (item.actor) {
      head.appendChild(S.el('<span class="muted">[' + S.esc(item.actor) + ']</span>'));
    }
    head.insertAdjacentHTML('beforeend', statusMark(item.status));
    li.appendChild(head);
    li.appendChild(S.el('<div class="briefing-text"></div>'));
    li.querySelector('.briefing-text').textContent = String(item.title || '');
    (item.refs || []).forEach((ref) => {
      li.appendChild(S.el('<div class="briefing-ref muted">see ' +
        S.esc(ref.type) + ': ' + S.esc(ref.label) + '</div>'));
    });
    if (item.detail) {
      li.appendChild(S.el('<div class="briefing-ref muted">' +
        S.esc(item.detail) + '</div>'));
    }
    return li;
  }

  async function briefingPanel(root) {
    const panel = S.el(
      '<section class="panel panel-card">' +
        '<h3 class="panel-title">DM briefing</h3>' +
        '<p class="panel-note">Deterministic: same canon, same briefing. ' +
          'Capped at the first ten items — pending proposals are labeled, ' +
          'never canon.</p>' +
        '<div class="inline-form briefing-controls">' +
          '<label class="field"><span class="field-label">Changes after week</span>' +
            '<input type="number" class="input briefing-since" min="0" value="0"></label>' +
          '<label class="field"><span class="field-label">Focus (comma-separated names)</span>' +
            '<input type="text" class="input briefing-focus" autocomplete="off" spellcheck="false"></label>' +
          '<button type="button" class="btn briefing-refresh">Refresh</button>' +
          '<a class="btn briefing-markdown" target="_blank" rel="noopener">Markdown</a>' +
          '<button type="button" class="btn btn-primary briefing-prep">Next session prep</button>' +
        '</div>' +
        '<div class="prep-status"></div>' +
        '<div class="briefing-body"><p class="loading">Building&hellip;</p></div>' +
      '</section>');
    const body = panel.querySelector('.briefing-body');
    const since = panel.querySelector('.briefing-since');
    const focus = panel.querySelector('.briefing-focus');
    const mdLink = panel.querySelector('.briefing-markdown');
    const prepStatus = panel.querySelector('.prep-status');

    // the persistent confirmation: prepStoredLine's copy with the file
    // path split out and linked (opens the written page from the vault)
    function renderPrepLine(rec) {
      prepStatus.innerHTML = '';
      const line = S.prepStoredLine(rec);
      if (!line) return;
      const p = S.el('<p class="panel-note prep-written"></p>');
      const at = line.indexOf(rec.file);
      p.appendChild(document.createTextNode(line.slice(0, at)));
      const a = document.createElement('a');
      a.href = S.fileUrl(rec.file);
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = rec.file;
      p.appendChild(a);
      p.appendChild(document.createTextNode(line.slice(at + rec.file.length)));
      prepStatus.appendChild(p);
    }
    renderPrepLine(readPrepStore());

    async function load() {
      body.innerHTML = '';
      body.appendChild(S.loading('Building…'));
      const q = '?since_week=' + encodeURIComponent(since.value || '0') +
        (focus.value.trim() ? '&focus=' + encodeURIComponent(focus.value.trim()) : '');
      mdLink.setAttribute('href', S.campUrl('/briefing/markdown' + q));
      let doc;
      try {
        doc = await S.api(S.campUrl('/briefing' + q));
      } catch (err) {
        body.innerHTML = '';
        body.appendChild(S.errorPanel(err, 'The briefing could not be built.'));
        return;
      }
      body.innerHTML = '';
      const note = (doc.total) + ' item' + (doc.total === 1 ? '' : 's') +
        ((doc.omitted > 0) ? ' — showing ' + doc.shown + ', ' + doc.omitted +
          ' more not shown (narrow the range or the focus)' : '');
      body.appendChild(S.el('<p class="panel-note">' + S.esc(note) + '</p>'));
      const ul = S.el('<ul class="briefing-list"></ul>');
      (doc.items || []).forEach((item) => ul.appendChild(briefingItem(item)));
      body.appendChild(ul);
    }
    panel.querySelector('.briefing-refresh').addEventListener('click', load);

    /* Next session prep (tickets/07): one click compiles the briefing's
       events, the freshest rumors, the open quests, the unrevealed public
       facts, and the open decisions into ONE vault page —
       lore/prep/<date>-next-session.md. A compile, not a generation: the
       same canon gives the same bytes, and a re-run takes the next free
       -N name instead of overwriting. */
    panel.querySelector('.briefing-prep').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      if (btn.disabled) return;
      btn.disabled = true;
      const idleLabel = btn.textContent;
      btn.textContent = 'Writing prep…';
      prepStatus.innerHTML = '';
      // immediate state: the server takes the lock, writes, then commits
      // the git snapshot — seconds of dead air without this line
      prepStatus.appendChild(S.el(
        '<p class="panel-note prep-writing">Writing the prep page — ' +
        'compiling the briefing into one vault file, then committing the ' +
        'history snapshot. A few seconds.</p>'));
      try {
        const out = await S.api(S.campUrl('/prep?since_week=' +
          encodeURIComponent(since.value || '0')), { method: 'POST', body: {} });
        const snap = out.snapshot || {};
        const rec = {
          camp: S.state.campaign,
          file: out.file,
          committed: snap.state === 'committed',
          at: Date.now()
        };
        try {
          sessionStorage.setItem(PREP_STORE_KEY, JSON.stringify(rec));
        } catch (_) {}
        renderPrepLine(rec);
        S.toast('Wrote ' + out.file + ' into the vault' +
          (rec.committed ? ' — snapshot ' + snap.commit + ' committed.' : '.') +
          ' A re-run never overwrites it.', 'ok');
      } catch (err) {
        prepStatus.innerHTML = '';
        prepStatus.appendChild(S.errorPanel(err, 'The prep write failed — ' +
          'check the vault (lore/prep/) before re-clicking.'));
        S.toast('Prep could not be written' +
          (err && err.error ? ': ' + err.error : '') + '.', 'error');
      } finally {
        btn.disabled = false;
        btn.textContent = idleLabel;
      }
    });

    await load();
    return panel;
  }

  async function disclosurePanel() {
    const panel = S.el(
      '<section class="panel panel-card">' +
        '<h3 class="panel-title">Party disclosure</h3>' +
        '<p class="panel-note">Public-in-world is not party-known: the reveal ' +
          'IS the discovery. The handout contains ONLY what was revealed — ' +
          'never DM notes, mechanics, or faction-only knowledge.</p>' +
        '<div class="disclosure-body"><p class="loading">Loading&hellip;</p></div>' +
        '<p><a class="btn btn-primary disclosure-handout" target="_blank" rel="noopener">Open the player handout</a></p>' +
      '</section>');
    const body = panel.querySelector('.disclosure-body');
    panel.querySelector('.disclosure-handout')
      .setAttribute('href', S.campUrl('/disclosure/handout'));
    try {
      const doc = await S.api(S.campUrl('/disclosure'));
      const revealed = (doc && Array.isArray(doc.revealed)) ? doc.revealed : [];
      body.innerHTML = '';
      if (!revealed.length) {
        body.appendChild(S.emptyState(
          'Nothing revealed to the party yet. Reveal a public fact above and ' +
          'it appears in the handout.'));
        return panel;
      }
      const ul = S.el('<ul class="briefing-list"></ul>');
      revealed.forEach((r) => {
        const li = S.el('<li class="briefing-item"><div class="briefing-text"></div>' +
          '<div class="briefing-ref muted"></div></li>');
        li.querySelector('.briefing-text').textContent = String(r.text || r.fact_id);
        li.querySelector('.briefing-ref').textContent =
          'revealed' + (r.revealed_week != null ? ' week ' + r.revealed_week : '') +
          (r.revealed ? ' · ' + String(r.revealed).slice(0, 10) : '');
        ul.appendChild(li);
      });
      body.appendChild(ul);
    } catch (err) {
      body.innerHTML = '';
      body.appendChild(S.errorPanel(err, 'The disclosure record could not be loaded.'));
    }
    return panel;
  }

  S.tabs.knowledge = async function (container) {
    container.innerHTML = '';
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }
    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);

    // faction ids for visibility checkboxes; a missing sandbox just means
    // the faction option is empty — facts still list and reconcile. A
    // FAILED read is a different thing again (ticket 63): it must render
    // as a failed read, not as the confident "no factions" diagnosis.
    let factions = [];
    let simReadError = null;
    try {
      const sim = await S.api(S.campUrl('/simulation'));
      factions = (sim && Array.isArray(sim.factions)) ? sim.factions : [];
    } catch (err) {
      simReadError = err;
    }

    root.appendChild(await factsPanel(factions, simReadError));
    root.appendChild(await briefingPanel());
    root.appendChild(await disclosurePanel());
  };
}());
