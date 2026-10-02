/* Campaign Studio — sessions.js
   Sessions tab (session-notes-to-changes): paste rough notes after a
   session, draft suggested consequences (manually or via the metered
   extractor), review each card beside its supporting quotation, and
   prepare the selected subset as ONE proposal. The proposal itself is
   reviewed and accepted on the Simulation tab — the same single
   pending-proposal slot every other flow uses. */
(function () {
  'use strict';
  const S = window.Studio;

  const KIND_LABEL = {
    history_event: 'history',
    resource_change: 'resource',
    field_change: 'fact',
    knowledge_fact: 'knowledge',
    party_disclosure: 'disclosure'
  };
  const GRADES = ['none', 'low', 'medium', 'high'];
  const RESOURCES = ['coin', 'muscle', 'legitimacy'];
  const FIELDS = ['goal', 'kind', 'secret', 'species'];

  // "campaign/sid" -> last extraction error: a failure must not read as
  // "no suggestions yet" across re-renders. Cleared on success. A page
  // reload forgets it (the server job log is the durable record).
  const extractFailures = new Map();

  function camp() { return S.campUrl('/'); }

  /* The prepare selection, resolved AFTER the draft save (ticket 11):
     saveDraft serializes the cards in DOM order and the server mints ids
     for the new ones, so POSITION correlates a card with its saved
     suggestion. Cards is [{checked, id}] in panel order (id null for a
     card the DM just added); a checked card resolves to the minted id
     when there is one, its own id otherwise. Pure — pinned by
     studio/tests/js/test_sessions_prepare.cjs. */
  S.prepareSelection = function (cards, savedSuggestions) {
    const saved = Array.isArray(savedSuggestions) ? savedSuggestions : [];
    const out = [];
    (cards || []).forEach(function (c, i) {
      if (!c || !c.checked) return;
      const minted = saved[i] && saved[i].id;
      const id = minted || c.id;
      if (id) out.push(String(id));
    });
    return out;
  };

  /* Pure — plan the draft PUT from the panel's cards BEFORE anything is
     sent (ticket 29): every card serializes in DOM order, because the
     server mints ids for new suggestions positionally and
     prepareSelection resolves checked cards by that same position — the
     body and the minting stay aligned only while one body entry maps to
     one card. A card whose data-id the draft no longer knows is FOREIGN
     (a stale multi-tab render): the plan comes back {foreign: [ids],
     suggestions: null} and the save must abort with a visible error.
     The old silent skip dropped the card and shifted every later card's
     position against the minted ids. Cards look like
     {id, selected, fresh, edits: {field: value}, confirmed: {…},
     visibility} — exactly what the DOM query builds. Pinned by
     studio/tests/js/test_sessions_staging.cjs. */
  S.draftSavePlan = function (cards, suggestions) {
    const known = {};
    (suggestions || []).forEach(function (s) {
      if (s && s.id) known[s.id] = s;
    });
    const foreign = [];
    (cards || []).forEach(function (c) {
      if (c && c.id && !known[c.id] && foreign.indexOf(c.id) === -1) {
        foreign.push(c.id);
      }
    });
    if (foreign.length) return { foreign: foreign, suggestions: null };
    return {
      foreign: [],
      suggestions: (cards || []).map(function (c) {
        c = c || {};
        // an existing card keeps its stored shape; a card without an id
        // is new material (the server mints the id) starting from the
        // blank suggestion it was authored from
        const upd = JSON.parse(JSON.stringify(known[c.id] || c.fresh || {}));
        upd.id = c.id || null;
        upd.selected = !!c.selected;
        Object.keys(c.edits || {}).forEach(function (f) {
          upd[f] = c.edits[f];
        });
        if (c.confirmed && Object.keys(c.confirmed).length) {
          upd.confirmed = Object.assign({}, upd.confirmed, c.confirmed);
        }
        if (c.visibility !== undefined) upd.visibility = c.visibility;
        return upd;
      })
    };
  };

  function pill(text, cls) {
    return '<span class="pill ' + (cls || 'pill-kind') + '">' + S.esc(text) + '</span>';
  }

  function kindPill(kind) {
    return pill(KIND_LABEL[kind] || kind, 'pill-kind');
  }

  /* ---------- suggestion cards ---------- */

  function card(sug, draft, sim, editMode) {
    const factions = (sim.factions || []);
    const places = (sim.places || []);
    const val = sug.validation || [];
    const findings = sug.findings || [];
    const confirmed = sug.confirmed || {};
    const isRes = sug.kind === 'resource_change';
    const isField = sug.kind === 'field_change';
    const isFact = sug.kind === 'knowledge_fact';
    const isDisc = sug.kind === 'party_disclosure';
    const isHist = sug.kind === 'history_event';
    const accepted = sug.status === 'accepted';
    const prepared = sug.status === 'prepared';

    let head = '<div class="ssn-card-head">' +
      '<label class="ssn-select"><input type="checkbox" class="ssn-check" ' +
      'data-id="' + S.esc(sug.id || '') + '" ' +
      ((sug.selected && !accepted) ? 'checked' : '') +
      (accepted ? ' disabled' : '') + '> <code>' + S.esc(sug.id || 'new') + '</code></label>' +
      kindPill(sug.kind) +
      (accepted ? pill('accepted', 'pill-vis-public') : '') +
      (prepared ? pill('in proposal', 'pill-vis-factions') : '') +
      '</div>';

    let body = '';
    if (isHist) {
      body += '<div class="ssn-field"><label>actor</label>' +
        actorSelect(sug, factions) + '</div>' +
        '<div class="ssn-field ssn-grow"><label>text</label>' +
        '<input type="text" class="input ssn-edit" data-field="text" value="' +
        S.esc(sug.text || '') + '"></div>';
    } else if (isRes) {
      body += '<div class="ssn-field"><label>faction</label>' +
        factionSelect(sug, factions) + '</div>' +
        '<div class="ssn-field"><label>resource</label>' +
        simpleSelect('resource', RESOURCES, sug.resource) + '</div>' +
        '<div class="ssn-field"><label>after</label>' +
        simpleSelect('after', GRADES, sug.after) + '</div>' +
        '<div class="ssn-field"><label>reason</label>' +
        '<input type="text" class="input ssn-edit" data-field="reason" value="' +
        S.esc(sug.reason || '') + '"></div>';
      body += '<div class="ssn-confirm">' +
        '<label><input type="checkbox" class="ssn-confirm-grade" data-id="' + S.esc(sug.id) + '" ' +
        (confirmed.resource_grade ? 'checked' : '') + '> I confirm the exact grade' +
        (sug.expected_before ? ' (was: ' + S.esc(String(sug.expected_before)) + ')' : '') +
        '</label></div>';
    } else if (isField) {
      body += '<div class="ssn-field"><label>faction</label>' +
        factionSelect(sug, factions) + '</div>' +
        '<div class="ssn-field"><label>field</label>' +
        simpleSelect('field', FIELDS, sug.field) + '</div>' +
        '<div class="ssn-field ssn-grow"><label>after</label>' +
        '<input type="text" class="input ssn-edit" data-field="after" value="' +
        S.esc(sug.after || '') + '"></div>' +
        '<div class="ssn-field ssn-grow"><label>reason</label>' +
        '<input type="text" class="input ssn-edit" data-field="reason" value="' +
        S.esc(sug.reason || '') + '"></div>';
    } else if (isFact) {
      body += '<div class="ssn-field ssn-grow"><label>fact text</label>' +
        '<input type="text" class="input ssn-edit" data-field="text" value="' +
        S.esc(sug.text || '') + '"></div>' +
        '<div class="ssn-field"><label>visibility</label>' +
        '<select class="input ssn-vis">' +
        '<option value="dm_only"' + (sug.visibility === 'dm_only' || !sug.visibility ? ' selected' : '') + '>DM only</option>' +
        '<option value="public"' + (sug.visibility === 'public' ? ' selected' : '') + '>public in world</option>' +
        '</select></div>';
      body += '<div class="ssn-confirm">' +
        '<label><input type="checkbox" class="ssn-confirm-vis" data-id="' + S.esc(sug.id) + '" ' +
        (confirmed.knowledge_visibility ? 'checked' : '') + '> I confirm a visibility beyond DM-only</label></div>';
    } else if (isDisc) {
      const target = sug.fact_id
        ? 'existing fact <code>' + S.esc(sug.fact_id) + '</code>'
        : sug.fact_suggestion
          ? 'new fact from <code>' + S.esc(sug.fact_suggestion) + '</code>'
          : '<strong>unresolved — pick a fact below</strong>';
      body += '<div class="ssn-field ssn-grow"><label>discloses</label><span>' + target + '</span></div>';
    }

    // evidence + validation, always visible — the quotation is the point
    const ev = (sug.evidence || [])[0];
    if (ev) {
      body += '<div class="ssn-evidence"><blockquote>' + S.esc(ev.quote || '') +
        '</blockquote><span class="muted">' + S.esc(ev.notes_path || '') +
        ':' + S.esc(String(ev.line_start)) + '-' + S.esc(String(ev.line_end)) + '</span></div>';
    }
    if (val.length) {
      body += S.stringList(val, 'ssn-blockers');
    }
    if (findings.length) {
      body += S.stringList(findings, 'muted');
    }
    if (accepted) {
      body += '<p class="muted">Already canon — corrections go into a new suggestion.</p>';
    }

    return '<article class="panel ssn-card' + (val.length ? ' ssn-card-blocked' : '') +
      (accepted ? ' ssn-card-accepted' : '') + '">' + head + '<div class="ssn-card-body">' + body + '</div></article>';
  }

  function factionSelect(sug, factions) {
    let out = '<select class="input ssn-edit" data-field="faction_id"><option value="">— unresolved —</option>';
    factions.forEach((f) => {
      out += '<option value="' + S.esc(f.id || f) + '"' +
        (sug.faction_id === (f.id || f) ? ' selected' : '') + '>' +
        S.esc(f.name || f.id || f) + '</option>';
    });
    return out + '</select>';
  }

  function actorSelect(sug, factions) {
    let out = '<select class="input ssn-edit" data-field="actor"><option value="">— unresolved —</option>' +
      '<option value="world"' + (sug.actor === 'world' ? ' selected' : '') + '>world</option>';
    factions.forEach((f) => {
      out += '<option value="' + S.esc(f.id || f) + '"' +
        (sug.actor === (f.id || f) ? ' selected' : '') + '>' +
        S.esc(f.name || f.id || f) + '</option>';
    });
    return out + '</select>';
  }

  function simpleSelect(field, options, current) {
    let out = '<select class="input ssn-edit" data-field="' + S.esc(field) + '">';
    options.forEach((o) => {
      out += '<option value="' + S.esc(o) + '"' +
        (String(current) === o ? ' selected' : '') + '>' + S.esc(o) + '</option>';
    });
    return out + '</select>';
  }

  /* ---------- manual drafting (the no-LLM path) ---------- */

  function blankSuggestion(kind) {
    // a fresh card authored by hand: no id yet (the server mints one at
    // save), blockers show until the fields are filled
    const base = { id: null, kind: kind, selected: false, status: 'open',
      confirmed: {}, findings: [], validation: [] };
    if (kind === 'history_event') { base.actor = ''; base.text = ''; base.week = null; }
    if (kind === 'resource_change') {
      base.faction_id = ''; base.resource = ''; base.after = ''; base.reason = '';
    }
    if (kind === 'field_change') {
      base.faction_id = ''; base.field = ''; base.after = ''; base.reason = '';
    }
    if (kind === 'knowledge_fact') { base.text = ''; base.visibility = 'dm_only'; }
    if (kind === 'party_disclosure') { base.fact_id = null; base.fact_suggestion = null; }
    return base;
  }

  function kindPicker(cls, btnLabel, btnCls) {
    let out = '<div class="inline-form ssn-authoring">' +
      '<select class="input ' + cls + '">';
    Object.keys(KIND_LABEL).forEach((k) => {
      out += '<option value="' + S.esc(k) + '">' + S.esc(KIND_LABEL[k]) + '</option>';
    });
    out += '</select>' +
      '<button type="button" class="btn ' + (btnCls || '') + ' ' + cls + '-btn">' +
      S.esc(btnLabel) + '</button></div>';
    return out;
  }

  /* ---------- one session's detail view ---------- */

  async function sessionDetail(root, sid, sim) {
    const detail = await S.api(camp() + 'sessions/' + encodeURIComponent(sid));
    const sess = detail.session;
    const notes = detail.notes || [];
    const drafts = detail.drafts || [];
    const latest = notes[notes.length - 1];
    const notesEl = latest && latest.text
      ? '<pre class="ssn-notes">' +
        latest.text.split('\n').map((ln, i) =>
          '<span class="ssn-line"><span class="ssn-lineno">' + (i + 1) +
          '</span>' + S.esc(ln) + '</span>').join('\n') + '</pre>'
      : '<p class="muted">No notes revision readable.</p>';

    const draft = drafts.length ? drafts[drafts.length - 1] : null;
    let cards = '';
    if (draft) {
      cards = (draft.suggestions || []).map((s) => card(s, draft, sim)).join('');
    }

    const panel = S.el(
      '<div class="tab-root ssn-detail">' +
        '<button type="button" class="btn ssn-back">&larr; All sessions</button>' +
        '<h3 class="panel-title">' + S.esc(sess.title || sid) + '</h3>' +
        '<p class="panel-note muted">session <code>' + S.esc(sid) + '</code> · event week ' +
          S.esc(String(sess.event_week)) +
          (sess.future_reason ? ' · future-dated: ' + S.esc(sess.future_reason) : '') +
          ' · ' + (draft ? 'draft <code>' + S.esc(draft.draft_id) + '</code> (revision ' +
            S.esc(String(draft.revision)) + ')' : 'no draft yet') + '</p>' +
        (draft && draft.extraction_findings && draft.extraction_findings.length
          ? '<details class="proposal-findings"><summary>Extraction notes (' +
            draft.extraction_findings.length + ')</summary>' +
            S.stringList(draft.extraction_findings) + '</details>'
          : '') +
        '<div class="ssn-columns">' +
          '<section class="panel"><h4 class="panel-title">Notes (' +
            S.esc(String(notes.length)) + ' revision' + (notes.length === 1 ? '' : 's') +
            ', immutable)</h4>' + notesEl +
            '<div class="ssn-append"><textarea class="input ssn-append-text" rows="3" ' +
            'placeholder="Append a new notes revision (never rewrites the old one)…"></textarea>' +
            '<button type="button" class="btn ssn-append-btn">Append revision</button></div>' +
          '</section>' +
          '<section class="panel ssn-review"><h4 class="panel-title">Suggested changes</h4>' +
            '<div class="ssn-extract-failure"></div>' +
            '<div class="ssn-cards">' +
              (cards ||
                (draft
                  ? '<p class="muted">No suggestions yet — extract from the notes ' +
                    '(costs one metered LLM call) or add cards by hand below.</p>'
                  : '<p class="muted">No draft yet — extract from the notes (one ' +
                    'metered LLM call) or start a manual draft:</p>' +
                    kindPicker('ssn-new-kind', 'Start a manual draft'))) +
            '</div>' +
            (draft ? kindPicker('ssn-add-kind', 'Add suggestion') : '') +
            '<div class="inline-form proposal-actions">' +
              '<button type="button" class="btn ssn-extract"' + (latest ? '' : ' disabled') +
              '>Extract changes (metered LLM call)</button>' +
              '<button type="button" class="btn btn-primary ssn-prepare">Prepare selected changes</button>' +
              '<span class="muted form-note">Prepare stages ONE proposal on the ' +
              'Simulation tab — nothing in the campaign changes until you accept it there.</span>' +
            '</div>' +
            '<div class="proposal-error"></div>' +
          '</section>' +
        '</div>' +
      '</div>'
    );
    root.appendChild(panel);

    const errBox = panel.querySelector('.proposal-error');

    // a remembered failed extraction must not read as "no suggestions yet"
    const failBox = panel.querySelector('.ssn-extract-failure');
    const lastFailure = extractFailures.get(
      (S.state.campaign || '') + '/' + sid);
    if (lastFailure && failBox) failBox.appendChild(S.errorPanel(lastFailure));

    panel.querySelector('.ssn-back').addEventListener('click', () => S.showTab('sessions'));

    // manual authoring: "Add suggestion" appends a blank card to the
    // existing draft (saved with the next save/prepare); with no draft at
    // all, "Start a manual draft" creates the session's first one
    const addSel = panel.querySelector('.ssn-add-kind');
    if (addSel) {
      panel.querySelector('.ssn-add-kind-btn').addEventListener('click', () => {
        const sug = blankSuggestion(addSel.value);
        const cardEl = S.el(card(sug, draft, sim));
        cardEl.__new = sug;
        panel.querySelector('.ssn-cards').appendChild(cardEl);
      });
    }
    const newSel = panel.querySelector('.ssn-new-kind');
    if (newSel) {
      panel.querySelector('.ssn-new-kind-btn').addEventListener('click', async () => {
        try {
          await S.api(camp() + 'sessions/' + encodeURIComponent(sid) +
            '/drafts', { method: 'POST',
            body: { suggestions: [blankSuggestion(newSel.value)] } });
          S.toast('Manual draft started — fill in the card, then prepare.', 'ok');
          const host = panel.parentElement;
          host.innerHTML = '';
          await sessionDetail(host, sid, sim);
        } catch (err) {
          errBox.textContent = (err && err.error) || 'Could not create the draft.';
        }
      });
    }

    // append an immutable notes revision
    panel.querySelector('.ssn-append-btn').addEventListener('click', async () => {
      const ta = panel.querySelector('.ssn-append-text');
      const text = ta.value.trim();
      if (!text) { S.toast('Write the new notes first.', 'info'); return; }
      try {
        await S.api(camp() + 'sessions/' + encodeURIComponent(sid) + '/notes', {
          method: 'POST',
          body: { notes: text, expected_revision: sess.revision }
        });
        S.toast('Notes revision appended — the old one is untouched.', 'ok');
        S.showTab('sessions');
      } catch (err) {
        errBox.textContent = (err && err.error) || 'Append failed.';
      }
    });

    // extract (explicit, metered)
    panel.querySelector('.ssn-extract').addEventListener('click', async () => {
      const ok = await S.confirmRun(
        'Extraction asks the configured model once to map the notes onto ' +
        'suggestions. It spends provider credits (at most two completion ' +
        'attempts, retries metered). The result is a DRAFT — nothing is ' +
        'applied without your review.');
      if (!ok) return;
      const failKey = (S.state.campaign || '') + '/' + sid;
      try {
        const r = await S.api(camp() + 'sessions/' + encodeURIComponent(sid) +
          '/extract', { method: 'POST', body: { confirm: true } });
        S.toast('Extracting — watch the job log on the Simulation tab.', 'ok');
        // pollJob resolves with the job's RESULT itself (already unwrapped)
        const result = await S.pollJob(r.job_id);
        extractFailures.delete(failKey);
        S.toast('Extraction staged ' + (result.suggestions || 0) +
          ' suggestion(s) — review the cards.', 'ok');
        S.showTab('sessions');
      } catch (err) {
        extractFailures.set(failKey, err);
        if (failBox) { failBox.innerHTML = ''; failBox.appendChild(S.errorPanel(err)); }
        errBox.textContent = (err && err.error) || 'Extraction failed.';
      }
    });

    // save edits + selections (and any NEW cards) as one draft revision
    async function saveDraft() {
      if (!draft) throw { error: 'no draft to save' };
      const cards = [];
      panel.querySelectorAll('.ssn-card').forEach((cardEl) => {
        const check = cardEl.querySelector('.ssn-check');
        const card = {
          id: check.getAttribute('data-id') || null,
          selected: check.checked,
          // a card without an id is new material: the server mints the id
          fresh: cardEl.__new || null,
          edits: {},
          confirmed: {},
          visibility: undefined
        };
        cardEl.querySelectorAll('.ssn-edit').forEach((inp) => {
          card.edits[inp.getAttribute('data-field')] = inp.value;
        });
        const grade = cardEl.querySelector('.ssn-confirm-grade');
        if (grade) card.confirmed.resource_grade = grade.checked;
        const vis = cardEl.querySelector('.ssn-confirm-vis');
        if (vis) card.confirmed.knowledge_visibility = vis.checked;
        const visSel = cardEl.querySelector('.ssn-vis');
        if (visSel) card.visibility = visSel.value;
        cards.push(card);
      });
      const plan = S.draftSavePlan(cards, draft.suggestions);
      if (plan.foreign.length) {
        // a card this draft no longer knows (stale multi-tab render):
        // the PUT must not go out around it — the silent skip shifted
        // every later card's position against the server's minted ids
        // (ticket 29). Flag the cards, tell the DM, save nothing.
        panel.querySelectorAll('.ssn-card').forEach((cardEl) => {
          const id = cardEl.querySelector('.ssn-check').getAttribute('data-id');
          if (id && plan.foreign.indexOf(id) !== -1) {
            cardEl.classList.add('ssn-card-foreign');
          }
        });
        const msg = 'Card ' + plan.foreign.join(', ') + ' is no longer in ' +
          'this draft — it changed in another tab. Reopen the session to ' +
          'resync; nothing was saved.';
        S.toast(msg, 'error');
        // keepView: the prepare handler must not re-render over the
        // highlighted cards
        throw { error: msg, keepView: true };
      }
      return S.api(camp() + 'sessions/' + encodeURIComponent(sid) +
        '/drafts/' + encodeURIComponent(draft.draft_id), {
        method: 'PUT',
        body: { suggestions: plan.suggestions, expected_revision: draft.revision }
      });
    }

    // prepare: save the editor state first, then stage with the revision
    // saveDraft returned — a concurrent edit between the two calls is a
    // 409, never a silently staged older selection. The checked cards are
    // collected BEFORE the save as positions, and their ids resolved from
    // the save's response: a card the DM just added has no id until the
    // save mints one, and it MUST stage with everything else (ticket 11)
    panel.querySelector('.ssn-prepare').addEventListener('click', async () => {
      const cards = Array.from(panel.querySelectorAll('.ssn-card')).map(
        (cardEl) => {
          const check = cardEl.querySelector('.ssn-check');
          return {
            checked: !!(check && check.checked && !check.disabled),
            id: check ? (check.getAttribute('data-id') || null) : null
          };
        });
      if (!cards.some((c) => c.checked)) {
        errBox.textContent = 'Select at least one suggestion first.';
        return;
      }
      try {
        const saved = await saveDraft();
        const checked = S.prepareSelection(cards, saved.suggestions);
        if (!checked.length) {
          errBox.textContent =
            'The save returned no ids for the checked cards — nothing staged.';
          return;
        }
        const r = await S.api(camp() + 'sessions/' + encodeURIComponent(sid) +
          '/prepare', {
          method: 'POST',
          body: {
            selected: checked,
            draft_id: saved.draft_id || draft.draft_id,
            expected_draft_revision: saved.revision
          }
        });
        S.toast('Staged proposal ' + (r.proposal_id || '—') +
          ' — review and accept it on the Simulation tab.', 'ok');
        S.showTab('sim');
      } catch (err) {
        errBox.textContent = (err && err.error) || 'Prepare failed.';
        if (err && err.keepView) {
          // already surfaced (toast + highlighted cards) — a re-render
          // would wipe the rows the DM needs to see
        } else if (err && err.status === 409 && /awaiting review/.test(err.error || '')) {
          S.toast('A proposal is already pending — review it on the Simulation tab.', 'info');
        } else {
          S.showTab('sessions'); // re-render: validations changed
        }
      }
    });

    // any edit/confirm/check just marks dirty; the save happens at prepare
  }

  /* ---------- session list + create ---------- */

  async function sessionList(root, sessions) {
    const panel = S.el(
      '<section class="panel">' +
        '<h3 class="panel-title">Sessions</h3>' +
        '<p class="panel-note muted">After play: paste your rough notes, review the ' +
        'suggested consequences beside their quotations, and publish the chosen ' +
        'changes once. Notes are immutable; nothing applies without your review.</p>' +
        '<div class="ssn-new">' +
          '<label class="field"><span class="field-label">Title</span>' +
            '<input type="text" class="input ssn-new-title" placeholder="e.g. The treasury fire"></label>' +
          '<label class="field ssn-new-notes-field"><span class="field-label">Notes (rough is fine)</span>' +
            '<textarea class="input ssn-new-notes" rows="5" ' +
            'placeholder="The party burned the Mint&rsquo;s warehouse at the east quay…"></textarea></label>' +
          '<div class="inline-form">' +
            '<button type="button" class="btn btn-primary ssn-new-btn">Save session</button>' +
            '<span class="muted form-note">Saving needs no API key. The event week defaults to the current simulation week.</span>' +
          '</div>' +
        '</div>' +
        '<ul class="ssn-list"></ul>' +
        '<div class="proposal-error"></div>' +
      '</section>'
    );
    root.appendChild(panel);

    const ul = panel.querySelector('.ssn-list');
    if (!sessions.length) {
      ul.innerHTML = '<li class="muted">No sessions yet.</li>';
    }
    sessions.forEach((s) => {
      const li = S.el('<li class="ssn-item"><button type="button" class="btn ssn-open">' +
        S.esc(s.title || s.session_id) + '</button> <span class="muted">week ' +
        S.esc(String(s.event_week)) + ' · ' +
        S.esc(String(s.drafts.length)) + ' draft(s) · ' +
        S.esc(String(s.decisions.length)) + ' decision(s)</span></li>');
      li.querySelector('.ssn-open').addEventListener('click', async () => {
        const root2 = li.closest('.tab-root').parentElement;
        root2.innerHTML = '';
        const wrap = S.el('<div class="tab-root"></div>');
        root2.appendChild(wrap);
        const sim = await S.api(camp() + 'simulation');
        await sessionDetail(wrap, s.session_id, sim);
      });
      ul.appendChild(li);
    });

    const errBox = panel.querySelector('.proposal-error');
    panel.querySelector('.ssn-new-btn').addEventListener('click', async () => {
      const title = panel.querySelector('.ssn-new-title').value.trim();
      const notes = panel.querySelector('.ssn-new-notes').value;
      if (!title || !notes.trim()) {
        errBox.textContent = 'A session needs a title and notes — even rough ones.';
        return;
      }
      try {
        const r = await S.api(camp() + 'sessions', {
          method: 'POST', body: { title: title, notes: notes }
        });
        S.toast('Session saved' + (r.session_id ? ' (' + r.session_id + ')' : '') +
          ' — open it to review changes.', 'ok');
        S.showTab('sessions');
      } catch (err) {
        errBox.textContent = (err && err.error) || 'Could not save the session.';
      }
    });
  }

  S.tabs.sessions = async function (container) {
    container.innerHTML = '';
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }
    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);
    const data = await S.api(camp() + 'sessions');
    await sessionList(root, data.sessions || []);
  };
}());
