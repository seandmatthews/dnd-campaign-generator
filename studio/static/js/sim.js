/* Campaign Studio — sim.js
   Agent Simulation tab: week clock, faction cards, rumors board, events,
   chronicle reader, views inspector, why inspector, "propose week" button. */
(function () {
  'use strict';
  const S = window.Studio;
  const Handoff = window.StudioHandoff; // rumor → knowledge-form prefill

  /* ----- project card state for the delegated action handlers -----
     Every re-render (accepts, staging, job polls) rebuilds the faction and
     project cards, and listeners bound to the old nodes died with them —
     a Pause click after a week accept silently did nothing. The buttons
     are now handled by ONE document-level listener (bottom of the file),
     which looks the live project up here. */
  const projectIndex = new Map();   // project id -> project object
  let simFactionsCache = [];        // last rendered factions (for Edit terms)

  /* ----- resource chips: color by level none/low/medium/high ----- */

  function resLevel(v) {
    if (typeof v === 'number') {
      if (v <= 0) return 'none';
      if (v <= 2) return 'low';
      if (v <= 4) return 'medium';
      return 'high';
    }
    const s = String(v == null ? '' : v).toLowerCase().trim();
    if (s === 'none' || s === 'low' || s === 'medium' || s === 'high') return s;
    const n = Number(s);
    if (s !== '' && !Number.isNaN(n)) return resLevel(n);
    return 'low';
  }

  function factionChips(f) {
    const res = (f.resources && typeof f.resources === 'object' && !Array.isArray(f.resources))
      ? f.resources : {};
    const order = ['coin', 'muscle', 'legitimacy'];
    const keys = order.filter((k) => res[k] != null)
      .concat(Object.keys(res).filter((k) => order.indexOf(k) === -1));
    return keys.map((k) => {
      const lvl = resLevel(res[k]);
      return '<span class="pill chip chip-' + lvl + '" title="' + S.esc(k + ': ' + res[k] + ' (' + lvl + ')') + '">' +
        S.esc(k) + ': ' + S.esc(String(res[k])) + '</span>';
    }).join(' ');
  }

  /* ----- disposition mini-table (shape varies: object, list, or strings) ----- */

  function dispositionTable(disp) {
    if (!disp) return '';
    let rows = [];
    if (Array.isArray(disp)) {
      disp.forEach((d) => {
        if (typeof d === 'string') {
          rows.push('<tr><td colspan="2">' + S.esc(d) + '</td></tr>');
        } else if (d && typeof d === 'object') {
          const who = d.faction || d.target || d.of || d.who || d.id || '?';
          const val = (d.value != null) ? d.value : (d.opinion != null ? d.opinion : (d.score != null ? d.score : '?'));
          rows.push('<tr><td>' + S.esc(String(who)) + '</td><td class="num">' + S.esc(String(val)) + '</td></tr>');
        }
      });
    } else if (typeof disp === 'object') {
      Object.keys(disp).forEach((k) => {
        rows.push('<tr><td>' + S.esc(k) + '</td><td class="num">' + S.esc(String(disp[k])) + '</td></tr>');
      });
    } else {
      rows.push('<tr><td colspan="2">' + S.esc(String(disp)) + '</td></tr>');
    }
    if (!rows.length) return '';
    return '<table class="mini-table"><thead><tr><th colspan="2">Disposition</th></tr></thead><tbody>' +
      rows.join('') + '</tbody></table>';
  }

  function knowsList(knows) {
    if (!knows) return '';
    const items = Array.isArray(knows) ? knows : [knows];
    if (!items.length) return '';
    const lis = items.map((k) => {
      const text = (typeof k === 'string') ? k : JSON.stringify(k);
      return '<li>' + S.esc(text == null ? '' : text) + '</li>';
    }).join('');
    return '<details class="knows"><summary>Knows (' + items.length + ')</summary><ul>' + lis + '</ul></details>';
  }

  function factionCard(f, placeNames) {
    const name = f.name || f.id || '(faction)';
    const kind = f.kind || '';
    const placeName = (f.place && placeNames && placeNames[f.place]) || '';
    const secret = f.secret
      ? '<details class="secret"><summary>Secret</summary><p>' + S.esc(f.secret) + '</p></details>'
      : '';
    const cardEl = S.el(
      '<article class="card faction-card">' +
        '<header class="faction-head">' +
          '<h4 class="faction-name">' + S.esc(name) + '</h4>' +
          (kind ? '<span class="pill pill-kind">' + S.esc(kind) + '</span>' : '') +
          (placeName ? '<button type="button" class="pill pill-when faction-place-link" title="Open this place (Places tab)" data-place="' +
            S.esc(f.place) + '">' + S.esc(placeName) + '</button>' : '') +
        '</header>' +
        '<div class="chip-row">' + factionChips(f) + '</div>' +
        dispositionTable(f.disposition) +
        secret +
        knowsList(f.knows) +
      '</article>'
    );
    return cardEl;
  }

  /* ----- rumors: map raw fidelity strings onto fresh/reliable/hearsay/dead ----- */

  function fidelityGrade(raw) {
    const s = String(raw == null ? '' : raw).toLowerCase().trim();
    if (/fresh/.test(s)) return 'fresh';
    if (/eyewitness|firsthand|first-hand/.test(s)) return 'fresh';
    if (/reliable|confirmed|solid/.test(s)) return 'reliable';
    if (/dead|stale|expired|forgotten/.test(s)) return 'dead';
    if (/hearsay|secondhand|second-hand|rumou?r|word of mouth/.test(s)) return 'hearsay';
    return 'hearsay'; // conservative default: never overstate reliability
  }

  /* the ledger suffix is stripped for display (and for the "Tell the party"
     handoff) by StudioHandoff.displayClaim — see js/handoff.js: the outcome
     is bookkeeping about the week, not part of the rumor people repeat. The
     raw state keeps the full text. */

  function tellTheParty(prefill) {
    if (!prefill) {
      S.toast('This rumor has no claim to tell.', 'error');
      return;
    }
    S.state.factPrefill = prefill;
    S.toast('Knowledge form prefilled — edit the wording, record it, and ' +
      'revealing it to the party stays its own step.', 'info');
    S.showTab('knowledge');
  }

  function rumorItem(r, chronicles) {
    const rawClaim = r.claim || r.text || r.rumor || '(no claim)';
    const claim = Handoff.displayClaim(rawClaim) || String(rawClaim);
    const rawFid = r.fidelity || r.grade || r.state || '';
    const grade = fidelityGrade(rawFid);
    const born = Handoff.rumorWeek(r);
    const hb = r.heard_by;
    let heard = null;
    if (Array.isArray(hb)) heard = hb.length;
    else if (typeof hb === 'number') heard = hb;
    else if (r.heard_by_count != null) heard = r.heard_by_count;
    else if (r.heardCount != null) heard = r.heardCount;

    let meta = [];
    if (born != null && born !== '') meta.push('born week ' + born);
    if (heard != null) meta.push('heard by ' + heard);
    const metaHtml = meta.length
      ? '<span class="rumor-meta">' + S.esc(meta.join(' · ')) + '</span>' : '';

    // the chain line: how the rumor has moved (spread() records tellings
    // in state; a telling drops fidelity one grade)
    const tellings = Array.isArray(r.transmissions) ? r.transmissions.length
      : (typeof r.transmissions === 'number' ? r.transmissions : null);
    const lastSpread = (r.last_spread_week != null) ? r.last_spread_week
      : ((r.lastSpreadWeek != null) ? r.lastSpreadWeek : null);
    let chain = [];
    if (born != null && born !== '') chain.push('born week ' + born);
    if (tellings != null) chain.push(tellings + ' telling' + (tellings === 1 ? '' : 's'));
    if (lastSpread != null && lastSpread !== '') chain.push('last spread week ' + lastSpread);
    const chainHtml = chain.length
      ? '<div class="rumor-chain">' + S.esc(chain.join(' · ')) + '</div>' : '';

    const badgeText = String(rawFid || grade);
    const stripped = claim !== String(rawClaim);
    const li = S.el(
      '<li class="rumor-entry">' +
        '<span class="pill fidelity fidelity-' + grade + '" title="grade: ' + grade + '">' +
          S.esc(badgeText) + '</span>' +
        '<span class="rumor-claim"' +
          (stripped ? ' title="as recorded: ' + S.esc(rawClaim) + '"' : '') + '>' +
          S.esc(claim) + '</span>' +
        metaHtml +
        chainHtml +
      '</li>'
    );
    // "Tell the party": park the prefill payload and let the Knowledge
    // tab's record form consume it — recording and revealing stay two steps
    const tell = S.el('<button type="button" class="btn rumor-tell" ' +
      'title="Pre-fill the Knowledge form from this rumor (text, public, ' +
      'chronicle source, week)">Tell the party</button>');
    tell.addEventListener('click', () => tellTheParty(Handoff.rumorHandoff(r, chronicles)));
    li.appendChild(tell);
    return li;
  }

  /* ----- events ----- */

  function eventItems(events, host) {
    host.innerHTML = '';
    if (!events.length) {
      host.appendChild(S.emptyState('No events on record.'));
      return;
    }
    const list = S.el('<ul class="event-list"></ul>');
    const norm = events.map((e) => {
      if (typeof e === 'string') return { week: null, text: e };
      return {
        week: (e.week != null) ? e.week : ((e.turn != null) ? e.turn : null),
        text: e.text || e.event || e.description || e.summary || ''
      };
    }).sort((a, b) => (Number(a.week) || 0) - (Number(b.week) || 0));
    norm.forEach((e) => {
      const wk = (e.week != null && e.week !== '')
        ? '<span class="pill pill-when">week ' + S.esc(String(e.week)) + '</span>' : '';
      list.appendChild(S.el('<li>' + wk + '<span class="event-text">' + S.esc(e.text) + '</span></li>'));
    });
    host.appendChild(list);
  }

  /* ----- create-a-simulation form (the fresh-campaign path) ----- */

  const RESOURCES = ['coin', 'muscle', 'legitimacy'];

  function factionRow() {
    return S.el(
      '<div class="sand-faction">' +
        '<div class="inline-form">' +
          '<label class="field grow"><span class="field-label">Faction name</span>' +
            '<input type="text" class="input f-name" placeholder="e.g. The Bridge Wardens"></label>' +
          '<label class="field field-narrow"><span class="field-label">Kind (optional)</span>' +
            '<input type="text" class="input f-kind" placeholder="e.g. toll Brotherhood"></label>' +
          '<label class="field field-narrow"><span class="field-label">Place (optional)</span>' +
            '<input type="text" class="input f-place" placeholder="blank = the city"></label>' +
        '</div>' +
        '<div class="inline-form">' +
          '<label class="field grow"><span class="field-label">Goal — what it wants, one line</span>' +
            '<input type="text" class="input f-goal" placeholder="e.g. rebuild the bridge and the toll with it"></label>' +
          RESOURCES.map((r) =>
            '<label class="field field-narrow"><span class="field-label">' + r + '</span>' +
              '<select class="input f-res" data-res="' + r + '">' +
                ['none', 'low', 'medium', 'high'].map((lvl) =>
                  '<option value="' + lvl + '"' + (lvl === 'low' ? ' selected' : '') + '>' + lvl + '</option>'
                ).join('') +
              '</select></label>'
          ).join('') +
          '<button type="button" class="btn f-remove" title="Remove this faction">&times;</button>' +
        '</div>' +
      '</div>'
    );
  }

  /* ----- premise-to-sandbox design (one metered LLM pass; prefill only) --
     The design fills the create form below; NOTHING is created or staged
     until Create simulation is clicked (the unchanged free path), and the
     secret drafts stay out of the faction rows — they are proposed DM-only
     knowledge facts for the Knowledge tab, after the sandbox exists. */

  function copyHint(text) {
    const done = () => S.toast('Copied — paste it where you want it.', 'ok');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, () =>
        S.toast('Copy failed — select the text manually.', 'error'));
    } else {
      S.toast('Copy is not available here — select the text manually.', 'info');
    }
  }

  function designExtras(design) {
    /* suggested projects (copyable hints) + secret drafts (DM-only) — two
       S.el calls, one root would drop the second (see el() in core.js) */
    const wrap = document.createElement('div');
    const projects = Array.isArray(design.projects) ? design.projects : [];
    if (projects.length) {
      const hints = S.el(
        '<details class="proposal-events" open><summary>Suggested projects ' +
          '(copyable hints — stage them from the Projects panel after creating)</summary>' +
          projects.map((p) =>
            '<div class="design-hint"><span class="event-text"><strong>' +
              S.esc(String(p.title || '')) + '</strong> — ' +
              S.esc(String(p.faction || '')) + ': ' +
              S.esc(String(p.objective || '')) +
              ' (target ' + S.esc(String(p.target)) + ')</span>' +
              '<button type="button" class="btn design-copy" title="Copy this hint">' +
              'Copy</button></div>').join('') + '</details>');
      hints.querySelectorAll('.design-copy').forEach((btn, i) => {
        const p = projects[i];
        btn.addEventListener('click', () => copyHint(
          (p.title || '') + ' — ' + (p.objective || '') +
          ' (target ' + p.target + ', owner ' + (p.faction || '') + ')'));
      });
      wrap.appendChild(hints);
    }
    const secrets = Array.isArray(design.secrets) ? design.secrets : [];
    if (secrets.length) {
      const drafts = S.el(
        '<details class="secret" open><summary>Proposed DM-only knowledge ' +
          'facts (drafts — add them in the Knowledge tab after creating; ' +
          'they never enter the sandbox state)</summary>' +
          secrets.map((s) =>
            '<div class="design-hint"><span class="event-text">' +
              S.esc(String(s.faction || '')) + ': ' +
              S.esc(String(s.text || '')) + '</span>' +
              '<button type="button" class="btn design-copy" title="Copy this draft">' +
              'Copy</button></div>').join('') + '</details>');
      drafts.querySelectorAll('.design-copy').forEach((btn, i) => {
        const s = secrets[i];
        btn.addEventListener('click', () => copyHint(
          (s.faction || '') + ': ' + (s.text || '')));
      });
      wrap.appendChild(drafts);
    }
    return wrap;
  }

  function createSimulationPanel(root) {
    const panel = S.el(
      '<section class="panel">' +
        '<h3 class="panel-title">Create a simulation</h3>' +
        '<div class="form-block design-block">' +
          '<p class="panel-note">No concept yet? Say the tension in a paragraph ' +
            'and one LLM pass drafts the factions below — you edit anything, ' +
            'then Create. Nothing is written until Create simulation.</p>' +
          '<label class="field"><span class="field-label">The premise — the tension, one paragraph</span>' +
            '<textarea class="input design-premise" rows="3" ' +
              'placeholder="e.g. The toll bridge burned the night the wardens raised the toll. Provisions councils blame the ferry league; the ferry league blames the wardens; the charters say both owe the crown."></textarea></label>' +
          '<label class="field"><span class="field-label">Places it happens (optional, comma-separated) — grounds the design</span>' +
            '<input type="text" class="input design-places" placeholder="e.g. Provis, Dunmore"></label>' +
          '<div class="inline-form">' +
            '<button type="button" class="btn btn-primary design-run">Design the sandbox</button>' +
            '<button type="button" class="btn design-regenerate hidden">Regenerate</button>' +
            '<span class="muted form-note">One metered LLM pass — it spends a ' +
              'little API credit and fills the form below.</span>' +
          '</div>' +
          '<div class="design-error"></div>' +
          '<div class="design-result"></div>' +
        '</div>' +
        '<div class="form-block">' +
          '<label class="field"><span class="field-label">Premise — the tension, one line</span>' +
            '<textarea class="input sand-premise" rows="2" ' +
              'placeholder="e.g. The toll bridge burned the night the wardens raised the toll."></textarea></label>' +
          '<div class="inline-form">' +
            '<label class="field"><span class="field-label">City or place</span>' +
              '<input type="text" class="input sand-city" placeholder="e.g. Provis"></label>' +
            '<label class="field field-narrow"><span class="field-label">Population (optional)</span>' +
              '<input type="number" class="input sand-pop" min="1" placeholder="12300"></label>' +
            '<label class="field field-narrow"><span class="field-label">Season</span>' +
              '<input type="text" class="input sand-season" value="early spring"></label>' +
          '</div>' +
          '<label class="field"><span class="field-label">Other places (optional, comma-separated) — factions can live here too</span>' +
            '<input type="text" class="input sand-places" placeholder="e.g. Harmouth, the Kingdom of Sweth"></label>' +
          '<div class="sand-factions"></div>' +
          '<div class="inline-form">' +
            '<button type="button" class="btn sand-add">+ Add a faction</button>' +
            '<button type="button" class="btn btn-primary sand-create">Create simulation</button>' +
          '</div>' +
          '<div class="form-error"></div>' +
          '<p class="muted form-note">Creating is free — no LLM anywhere in ' +
            'this form. Prefer hand-editing? It writes ' +
            '<code>simulation/state.yaml</code> — you can edit that file directly instead.</p>' +
        '</div>' +
      '</section>'
    );
    const host = panel.querySelector('.sand-factions');
    const errBox = panel.querySelector('.form-error');
    const addBtn = panel.querySelector('.sand-add');
    const createBtn = panel.querySelector('.sand-create');
    const designBtn = panel.querySelector('.design-run');
    const regenBtn = panel.querySelector('.design-regenerate');
    const designErr = panel.querySelector('.design-error');
    const designResult = panel.querySelector('.design-result');

    function setFactionRows(factions) {
      host.innerHTML = '';
      (factions || []).slice(0, 5).forEach((f) => {
        const row = factionRow();
        row.querySelector('.f-name').value = f.name || '';
        row.querySelector('.f-kind').value = f.kind || '';
        row.querySelector('.f-goal').value = f.goal || '';
        row.querySelector('.f-place').value = f.place || '';
        const res = (f.resources && typeof f.resources === 'object') ? f.resources : {};
        row.querySelectorAll('.f-res').forEach((sel) => {
          const lvl = resLevel(res[sel.getAttribute('data-res')]);
          sel.value = ['none', 'low', 'medium', 'high'].indexOf(lvl) >= 0 ? lvl : 'low';
        });
        row.querySelector('.f-remove').addEventListener('click', () => {
          if (host.children.length <= 2) {
            S.toast('Keep at least two factions — the tension needs two sides.', 'info');
            return;
          }
          row.remove();
        });
        host.appendChild(row);
      });
    }

    function applyDesign(r, namedPlaces) {
      const d = (r && r.design) || {};
      if (d.premise) panel.querySelector('.sand-premise').value = d.premise;
      setFactionRows(d.factions);
      // the sandbox's own places are the ones the DM named: the first leads
      // as the city, the rest stay "other places" (factions may live there)
      if (namedPlaces.length) {
        panel.querySelector('.sand-city').value = namedPlaces[0];
        panel.querySelector('.sand-places').value =
          namedPlaces.slice(1).join(', ');
      }
      designResult.innerHTML = '';
      designResult.appendChild(designExtras(d));
      ((r && r.notes) || []).forEach((n) => S.toast(n, 'info'));
      S.toast('Design drafted below — edit anything, then Create simulation.', 'ok');
    }

    async function runDesign() {
      if (designBtn.disabled) return;
      const premise = panel.querySelector('.design-premise').value.trim();
      if (!premise) {
        S.toast('Describe the tension first — one paragraph is enough.', 'error');
        return;
      }
      designBtn.disabled = true;
      regenBtn.disabled = true;
      designErr.innerHTML = '';
      const places = panel.querySelector('.design-places').value.split(',')
        .map((s) => s.trim()).filter(Boolean);
      try {
        const r = await S.api(S.campUrl('/simulation/design'), {
          method: 'POST', body: { premise: premise, places: places }
        });
        applyDesign(r, places);
        regenBtn.classList.remove('hidden');
      } catch (err) {
        designErr.innerHTML = '';
        designErr.appendChild(S.errorPanel(err));
      } finally {
        designBtn.disabled = false;
        regenBtn.disabled = false;
      }
    }
    designBtn.addEventListener('click', runDesign);
    regenBtn.addEventListener('click', runDesign); // Regenerate re-runs the pass

    function addRow() {
      const row = factionRow();
      row.querySelector('.f-remove').addEventListener('click', () => {
        if (host.children.length <= 2) {
          S.toast('Keep at least two factions — the tension needs two sides.', 'info');
          return;
        }
        row.remove();
      });
      host.appendChild(row);
    }
    addRow();
    addRow();
    addBtn.addEventListener('click', () => {
      if (host.children.length >= 5) {
        S.toast('Five factions is plenty — more than that and weeks get muddy.', 'info');
        return;
      }
      addRow();
    });

    createBtn.addEventListener('click', async () => {
      if (createBtn.disabled) return;
      const factions = Array.from(host.querySelectorAll('.sand-faction')).map((row) => {
        const resources = {};
        row.querySelectorAll('.f-res').forEach((sel) => { resources[sel.getAttribute('data-res')] = sel.value; });
        const f = {
          name: row.querySelector('.f-name').value.trim(),
          kind: row.querySelector('.f-kind').value.trim(),
          goal: row.querySelector('.f-goal').value.trim(),
          resources: resources
        };
        const place = row.querySelector('.f-place').value.trim();
        if (place) f.place = place;
        return f;
      });
      const body = {
        premise: panel.querySelector('.sand-premise').value.trim(),
        season: panel.querySelector('.sand-season').value.trim() || 'early spring',
        factions: factions,
        force: false
      };
      const places = panel.querySelector('.sand-places').value.split(',')
        .map((s) => s.trim()).filter(Boolean)
        .map((name) => ({ name: name }));
      if (places.length) body.places = places;
      const city = panel.querySelector('.sand-city').value.trim();
      const pop = Number(panel.querySelector('.sand-pop').value);
      if (!city) {
        S.toast('The sandbox needs a city or place — weeks are chronicled there.', 'error');
        return;
      }
      body.city = { name: city };
      if (pop > 0) body.city.population = pop;
      createBtn.disabled = true;
      errBox.innerHTML = '';
      try {
        await S.api(S.campUrl('/simulation/state'), { method: 'POST', body: body });
        S.toast('Simulation created — factions are live below. Propose week 1 when ready.', 'ok');
        S.rerenderIfCurrent('sim'); // re-render: the real sim view with the run button
      } catch (err) {
        errBox.innerHTML = '';
        errBox.appendChild(S.errorPanel(err));
        createBtn.disabled = false;
      }
    });

    root.appendChild(panel);
  }

  /* ----- proposed week panel (Phase 2: generate -> review -> accept) ----- */

  function diffRows(diff) {
    const rows = [];
    const show = (v) => S.esc(
      v === null || v === undefined || v === '' ? '(unset)' : String(v));
    const clock = diff && diff.clock;
    if (clock) {
      rows.push('<tr><td>Clock</td><td class="num">week ' +
        S.esc(String(clock.before)) + ' &rarr; week ' +
        S.esc(String(clock.after)) + '</td></tr>');
    }
    const res = (diff && diff.resources) || {};
    Object.keys(res).forEach((fid) => {
      Object.keys(res[fid]).forEach((k) => {
        const ch = res[fid][k];
        rows.push('<tr><td>' + S.esc(fid) + ' &middot; ' + S.esc(k) +
          '</td><td class="num">' + S.esc(String(ch.before)) + ' &rarr; ' +
          S.esc(String(ch.after)) + '</td></tr>');
      });
    });
    // set_field changes (goal/kind/secret) — a session that moved a fact
    // must show the before/after, not just the history lines that justify it
    const flds = (diff && diff.fields) || {};
    Object.keys(flds).forEach((fid) => {
      Object.keys(flds[fid]).forEach((k) => {
        const ch = flds[fid][k] || {};
        rows.push('<tr><td>' + S.esc(fid) + ' &middot; ' + S.esc(k) +
          '</td><td class="num">' + show(ch.before) + ' &rarr; ' +
          show(ch.after) + '</td></tr>');
      });
    });
    // projects: additions + per-project movement
    const projAdded = (diff && diff.projects_added) || [];
    if (projAdded.length) {
      rows.push('<tr><td>Projects added</td><td class="num">' +
        projAdded.length + '</td></tr>');
    }
    const projCh = (diff && diff.projects_changed) || {};
    Object.keys(projCh).forEach((pid) => {
      const ch = projCh[pid] || {};
      Object.keys(ch).forEach((k) => {
        if (k === 'faction_id' || k === 'title') return;
        const v = ch[k] || {};
        rows.push('<tr><td>' + S.esc(String(ch.faction_id || pid)) +
          ' &middot; ' + S.esc(String(ch.title || pid)) + ' &middot; ' +
          S.esc(k) + '</td><td class="num">' + show(v.before) + ' &rarr; ' +
          show(v.after) + '</td></tr>');
      });
    });
    const evs = (diff && diff.events_added) || [];
    if (evs.length) {
      rows.push('<tr><td>Events added</td><td class="num">' + evs.length + '</td></tr>');
    }
    const rums = (diff && diff.rumors_added) || [];
    if (rums.length) {
      rows.push('<tr><td>Rumors added</td><td class="num">' + rums.length + '</td></tr>');
    }
    if (diff && diff.timeline_entries) {
      rows.push('<tr><td>Timeline entries</td><td class="num">' +
        S.esc(String(diff.timeline_entries)) + '</td></tr>');
    }
    return rows.join('');
  }

  function proposalPanel(root, pid, chronicles) {
    const panel = S.el(
      '<section class="panel proposal-panel">' +
        '<h3 class="panel-title">Loading the proposed week…</h3>' +
      '</section>'
    );
    root.appendChild(panel);

    S.api(S.campUrl('/simulation/proposal')).then((data) => {
      const p = data && data.proposal;
      if (!p) { panel.remove(); return; }
      const diff = data.diff || {};
      const evs = diff.events_added || [];
      const rums = diff.rumors_added || [];
      const chron = (data.chronicles && data.chronicles.length)
        ? data.chronicles[data.chronicles.length - 1] : null;
      const isSession = p.kind === 'session_effects';
      const isGoals = p.kind === 'goal_updates';
      const isProjects = p.kind === 'project_changes';
      const isSsnChanges = p.kind === 'session_changes';
      const updates = Array.isArray(data.updates) ? data.updates : [];
      const changes = Array.isArray(data.changes) ? data.changes : [];
      const findings = Array.isArray(data.findings) ? data.findings : [];
      const title = isGoals
        ? 'Proposed goal updates — not canon yet'
        : isSsnChanges
          ? 'Session changes — session ' + S.esc(String((p.run_record && p.run_record.session_id) || '')) + ' — not canon yet'
          : isSession
            ? 'Session changes — ' + S.esc(String((p.run_record && p.run_record.source) || 'session'))
            : isProjects
              ? 'Proposed project changes — not canon yet'
              : 'Proposed week ' + S.esc(String(p.proposed_week)) + ' &mdash; not canon yet';
      const subtitle = isGoals
        ? '<strong>The world has not changed yet.</strong> The engine read ' +
          'the ledger and proposes these goal changes for each faction. ' +
          'Accepting applies them exactly once; discarding changes nothing.'
        : isSsnChanges
          ? '<strong>The world has not changed yet.</strong> These are the selected ' +
            'consequences of one session: state effects, knowledge facts, and party ' +
            'disclosures staged together. Accepting publishes them exactly once; ' +
            'discarding changes nothing (and reopens the suggestions).'
          : isSession
          ? '<strong>The world has not changed yet.</strong> These are the recorded ' +
            'session facts and the reviewed effects they justify. Accepting applies ' +
            'them exactly once; discarding changes nothing.'
          : isProjects
            ? '<strong>The world has not changed yet.</strong> These are your staged ' +
              'project operations, validated by the engine against the state they ' +
              'were prepared on. Accepting applies them exactly once; discarding ' +
              'changes nothing.'
            : '<strong>The world has not advanced.</strong> ' +
              'This is a proposal generated from the current state (' +
              'base week ' + S.esc(String(p.base_week)) + '). Accepting publishes ' +
              'the whole week exactly once; discarding changes nothing.';

      const goalsBody = () => {
        const rows = updates.length ? updates : Object
          .entries((diff.fields) || {})
          .map(([fid, ch]) => ({ faction_id: fid, name: fid,
            current_goal: (ch.goal || {}).before,
            goal: (ch.goal || {}).after, reason: null }));
        if (!rows.length) {
          return '<p class="muted">No update details were recorded in this ' +
            'bundle — read the diff below.</p>';
        }
        return '<div class="goal-updates">' + rows.map((u) =>
          '<div class="goal-update">' +
            '<h5 class="goal-faction">' + S.esc(String(u.name || u.faction_id)) + '</h5>' +
            '<p class="goal-change"><span class="goal-old">' +
              S.esc(String(u.current_goal || '')) + '</span> &rarr; ' +
              '<span class="goal-new">' + S.esc(String(u.goal || '')) +
            '</span></p>' +
            (u.reason ? '<p class="goal-reason muted">' +
              S.esc(String(u.reason)) + '</p>' : '') +
          '</div>').join('') + '</div>';
      };

      // staged project operations, one block each: what will change and why
      const projectsBody = () => {
        if (!changes.length) {
          return '<p class="muted">No operation details were recorded in ' +
            'this bundle — read the diff below.</p>';
        }
        return '<div class="goal-updates">' + changes.map((c) =>
          '<div class="goal-update">' +
            '<h5 class="goal-faction">' + S.esc(String(c.op || '?')) +
              (c.project_id ? ' &middot; ' + S.esc(String(c.project_id)) : '') +
            '</h5>' +
            (c.reason ? '<p class="goal-reason muted">' +
              S.esc(String(c.reason)) + '</p>' : '') +
          '</div>').join('') + '</div>';
      };

      // session_changes: the knowledge facts and party disclosures this
      // batch writes beyond state (from the bundle's after-images)
      const ssnChangesBody = (data, diff) => {
        const extra = (data && data.extra) || {};
        let out = '';
        const facts = (diff && diff.facts_added) || [];
        const supd = (diff && diff.facts_superseded) || [];
        const disc = (diff && diff.disclosed) || [];
        if (facts.length) {
          out += '<details class="proposal-events" open><summary>Knowledge facts written (' +
            facts.length + ')</summary><ul class="event-list">' +
            facts.map((f) => '<li><span class="event-text">' + S.esc(f.text || '') + '</span> ' +
              (f.visibility === 'public'
                ? '<span class="pill pill-vis-public">public in world</span>'
                : '<span class="pill pill-vis-dm">DM only</span>') + '</li>').join('') +
            '</ul></details>';
        }
        if (supd.length) {
          out += '<p class="muted">Supersedes fact(s): ' +
            supd.map((x) => '<code>' + S.esc(String(x)) + '</code>').join(', ') + '</p>';
        }
        if (disc.length) {
          out += '<details class="proposal-events" open><summary>Disclosed to the party (' +
            disc.length + ')</summary><ul class="event-list">' +
            disc.map((d) => '<li><span class="event-text">' + S.esc(d.text || d.fact_id || '') +
              '</span> <span class="pill pill-when">party handout</span></li>').join('') +
            '</ul></details>';
        }
        if (!out) {
          out = '<p class="muted">State changes only — no knowledge or disclosure writes.</p>';
        }
        return out;
      };

      // proposals the engine cut during validation — why an update the
      // model proposed is not on the table (persisted in the bundle since
      // 2026-09-25; they used to live only in the ephemeral job log)
      const goalsFindings = () => findings.length
        ? '<details class="proposal-findings"><summary>Dropped during ' +
          'validation (' + findings.length + ')</summary>' +
          S.stringList(findings) + '</details>'
        : '';

      // innerHTML, not S.el(): multi-root content (see el() in core.js)
      panel.innerHTML =
        '<h3 class="panel-title">' + title + '</h3>' +
        '<p class="panel-note">' + subtitle + '</p>' +
        (isGoals
          ? goalsBody() + goalsFindings()
          : isProjects
            ? projectsBody() + goalsFindings()
            : '<table class="mini-table proposal-diff"><tbody>' + diffRows(diff) + '</tbody></table>' +
            (isSsnChanges ? ssnChangesBody(data, diff) : '') +
            (evs.length
              ? '<details class="proposal-events"' + (isSession ? ' open' : '') + '><summary>' +
                  (isSession ? 'Recorded history &amp; effects (' : 'Proposed events (') + evs.length + ')</summary><ul class="event-list">' +
                  evs.map((e) => '<li><span class="event-text">' + S.esc(e.text || '') + '</span>' +
                    (e.effect ? ' <span class="pill pill-when">effect</span>' : '') +
                    (e.effect == null && e.source ? ' <span class="pill pill-when">history only</span>' : '') +
                    '</li>').join('') +
                '</ul></details>'
              : '') +
            (rums.length
              ? '<details class="proposal-rumors"><summary>Proposed rumors (' + rums.length + ')</summary><ul class="rumor-list">' +
                  rums.map((r) => '<li class="rumor-entry"><span class="rumor-claim">' + S.esc(r.claim || '') + '</span>' +
                    '<button type="button" class="btn rumor-tell" ' +
                    'title="Pre-fill the Knowledge form from this rumor (text, public, chronicle source, week)">Tell the party</button></li>').join('') +
                '</ul></details>'
              : '') +
            (chron
              ? '<div class="proposal-chronicle"><h5 class="findings-title">Chronicle (proposed)</h5><article class="chronicle"></article></div>'
              : '')) +
        '<div class="inline-form proposal-actions">' +
          '<button type="button" class="btn btn-primary proposal-accept">' +
            (isGoals ? 'Accept the goal changes — apply exactly once'
                     : isSsnChanges ? 'Accept the session changes — publish state, knowledge, disclosure'
                     : isSession ? 'Accept session changes — apply exactly once'
                     : isProjects ? 'Accept the project changes — apply exactly once'
                                 : 'Accept week ' + S.esc(String(p.proposed_week)) + ' — publish everything') + '</button>' +
          '<button type="button" class="btn btn-danger proposal-discard">Discard</button>' +
          '<span class="muted form-note">Accept publishes as one step; discard keeps the audit receipt only.</span>' +
        '</div>' +
        '<div class="proposal-error"></div>';
      // the chronicle node exists only in the non-goals branch above; guard
      // the query so a future bundle kind carrying a chronicle degrades to
      // no chronicle block instead of dropping the whole panel into catch
      const chronNode = chron && panel.querySelector('.chronicle');
      if (chronNode) chronNode.innerHTML = S.md(chron.markdown || '');

      // proposed rumors can be told too: the week is the proposal's, and
      // source_file resolves against CANON chronicles only — until the week
      // is accepted the prefill simply carries no source (never a staged
      // file the record route would refuse)
      panel.querySelectorAll('.proposal-rumors .rumor-tell').forEach((btn, i) => {
        const r = rums[i];
        if (!r) return;
        btn.addEventListener('click', () => tellTheParty(
          Handoff.rumorHandoff(
            { claim: r.claim, born_week: (isGoals || isProjects) ? null : p.proposed_week },
            chronicles)));
      });

      const errBox = panel.querySelector('.proposal-error');
      const acceptBtn = panel.querySelector('.proposal-accept');
      const discardBtn = panel.querySelector('.proposal-discard');

      acceptBtn.addEventListener('click', async () => {
        if (acceptBtn.disabled) return;
        acceptBtn.disabled = true; discardBtn.disabled = true;
        const ok = await S.confirmRun(
          isGoals
            ? 'Accepting applies the proposed goal changes to the factions ' +
              '— exactly once. The audit events cite the ledger.'
            : isSsnChanges
              ? 'Accepting publishes this session in ONE step: the state ' +
                'effects, the knowledge facts, and the party disclosures. ' +
                'The decision record cites the notes revision. Exactly once.'
              : isSession
              ? 'Accepting applies the recorded session history and its reviewed ' +
                'effects to the live state — exactly once.'
              : isProjects
                ? 'Accepting applies the staged project operations to the live ' +
                  'state — exactly once. Completion counters reached this way ' +
                  'complete through the same rules a weekly tick uses.'
                : 'Accepting publishes the whole proposed week into the campaign — ' +
                  'clock, resources, chronicle, timeline — exactly once.'
        );
        if (!ok) { acceptBtn.disabled = false; discardBtn.disabled = false; return; }
        errBox.innerHTML = '';
        try {
          const r = await S.api(S.campUrl('/simulation/proposal/' +
            encodeURIComponent(p.proposal_id) + '/accept'), { method: 'POST' });
          S.toast(isGoals
            ? 'Goal changes are canon — applied exactly once.'
            : isSession
              ? 'Session changes are canon — applied exactly once.'
              : isProjects
                ? 'Project changes are canon — applied exactly once.'
                : 'Week ' + (r.week != null ? r.week : p.proposed_week) +
                  ' is canon — accepted exactly once.', 'ok');
          if (r.snapshot && (r.snapshot.state === 'failed' ||
              r.snapshot.state === 'unavailable')) {
            S.toast('Published, but the history snapshot ' + r.snapshot.state +
              ' — use the Retry snapshot button above.', 'error');
          }
          if (r.draft_update === 'failed') {
            S.toast('Published, but the session draft could not be updated — ' +
              'accepting again (it is idempotent) repairs it.', 'error');
          }
          S.rerenderIfCurrent('sim');
        } catch (err) {
          errBox.innerHTML = '';
          errBox.appendChild(S.errorPanel(err));
          acceptBtn.disabled = false; discardBtn.disabled = false;
        }
      });

      discardBtn.addEventListener('click', async () => {
        if (discardBtn.disabled) return;
        acceptBtn.disabled = true; discardBtn.disabled = true;
        const ok = await S.confirmRun(
          'Discard this proposal? Nothing was ever written to the ' +
          'campaign; the bundle stays as an audit receipt.'
        );
        if (!ok) { acceptBtn.disabled = false; discardBtn.disabled = false; return; }
        errBox.innerHTML = '';
        try {
          const r = await S.api(S.campUrl('/simulation/proposal/' +
            encodeURIComponent(p.proposal_id) + '/discard'), { method: 'POST' });
          S.toast('Proposal discarded — the campaign never changed.', 'ok');
          if (r && r.draft_update === 'failed') {
            S.toast('Discarded, but the session draft could not be reopened — ' +
              'the excluded cards may need re-preparing by hand.', 'error');
          }
          S.rerenderIfCurrent('sim');
        } catch (err) {
          errBox.innerHTML = '';
          errBox.appendChild(S.errorPanel(err));
          acceptBtn.disabled = false; discardBtn.disabled = false;
        }
      });
    }).catch((err) => {
      // never silent: a removed panel reads as "no proposal", which is a lie
      panel.innerHTML =
        '<h3 class="panel-title">The proposed week could not be loaded</h3>';
      panel.appendChild(S.errorPanel(err));
    });
  }

  /* ----- forecast panel (propose-and-discard: never canon) ----- */

  function forecastPanel(root) {
    const panel = S.el(
      '<section class="panel forecast-panel">' +
        '<h3 class="panel-title">Loading the forecast…</h3>' +
      '</section>'
    );
    root.appendChild(panel);

    S.api(S.campUrl('/simulation/forecast')).then((data) => {
      const f = data && data.forecast;
      const weeks = (data && Array.isArray(data.weeks)) ? data.weeks : [];
      if (!f || !weeks.length) { panel.remove(); return; }
      panel.innerHTML =
        '<h3 class="panel-title">Forecast — where this is heading ' +
          '(from week ' + S.esc(String(f.base_week)) + ', not canon)</h3>' +
        '<p class="panel-note"><strong>Projection only.</strong> The engine ' +
          'ran ' + weeks.length + ' real week' + (weeks.length === 1 ? '' : 's') +
          ' on scratch data from canon week ' + S.esc(String(f.base_week)) + '. ' +
          'Nothing here is canon and nothing can be accepted — steer now ' +
          '(record a session, add knowledge, change a goal) before the real ' +
          'week lands on any of this.</p>' +
        weeks.map((w) =>
          '<div class="forecast-week">' +
            '<h4 class="forecast-week-title"><span class="pill pill-when">forecast</span> Week ' +
              S.esc(String(w.week)) +
              (w.title ? ' — ' + S.esc(String(w.title)) : '') + '</h4>' +
            '<article class="chronicle forecast-chronicle">' + S.md(w.markdown || '') + '</article>' +
            '<table class="mini-table forecast-diff"><tbody>' + diffRows(w.diff) + '</tbody></table>' +
          '</div>').join('') +
        '<div class="inline-form proposal-actions">' +
          '<button type="button" class="btn btn-danger forecast-discard">Discard the forecast</button>' +
          '<span class="muted form-note">Discard only — a forecast is never canon; ' +
          'the bundle stays as a receipt.</span>' +
        '</div>' +
        '<div class="forecast-error"></div>';

      const discardBtn = panel.querySelector('.forecast-discard');
      const errBox = panel.querySelector('.forecast-error');
      discardBtn.addEventListener('click', async () => {
        if (discardBtn.disabled) return;
        discardBtn.disabled = true;
        const ok = await S.confirmRun(
          'Discard this forecast? It was never canon — the campaign has ' +
          'not changed by one line because of it.');
        if (!ok) { discardBtn.disabled = false; return; }
        errBox.innerHTML = '';
        try {
          await S.api(S.campUrl('/simulation/forecast/' +
            encodeURIComponent(f.forecast_id) + '/discard'), { method: 'POST' });
          S.toast('Forecast discarded — the campaign never changed.', 'ok');
          S.rerenderIfCurrent('sim');
        } catch (err) {
          errBox.innerHTML = '';
          errBox.appendChild(S.errorPanel(err));
          discardBtn.disabled = false;
        }
      });
    }).catch((err) => {
      panel.innerHTML =
        '<h3 class="panel-title">The forecast could not be loaded</h3>';
      panel.appendChild(S.errorPanel(err));
    });
  }

  /* ----- goal review (the ledger moves, goals can too) ----- */

  function goalsCard(root) {
    const card = S.el(
      '<section class="panel goals-panel">' +
        '<h3 class="panel-title">Review the goals</h3>' +
        '<p class="panel-note">Goals are written once, at creation; the ledger ' +
          'keeps moving. One bounded LLM pass proposes goal updates grounded in ' +
          'the recorded weeks, rumors and canon deadlines — staged as a proposal ' +
          'above; the campaign changes only if you accept it.</p>' +
        '<div class="inline-form">' +
          '<button type="button" class="btn btn-primary goal-review-btn">Propose goal updates</button>' +
          '<span class="muted form-note">Spends API credits; most runs justify few or no changes.</span>' +
        '</div>' +
        '<label class="field auto-accept-field">' +
          '<input type="checkbox" class="input goal-auto-accept">' +
          '<span><strong>Auto-accept</strong> — apply the proposed goal updates the moment they stage ' +
          '(error-level findings leave the proposal pending instead). Default is off.</span>' +
        '</label>' +
        '<pre class="log-tail hidden goals-log"></pre>' +
        '<div class="goals-error"></div>' +
      '</section>'
    );
    const btn = card.querySelector('.goal-review-btn');
    const logBox = card.querySelector('.goals-log');
    const errBox = card.querySelector('.goals-error');
    const autoChk = card.querySelector('.goal-auto-accept');
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      const auto = autoChk && autoChk.checked;
      btn.disabled = true;
      const ok = await S.confirmRun(
        'Run a goal review? One LLM pass reads the ledger and proposes goal ' +
        'updates — it spends API credits. Nothing changes unless you accept ' +
        'the proposal afterwards.' +
        (auto ? ' AUTO-ACCEPT is on: the updates apply as soon as they stage.' : '')
      );
      if (!ok) { btn.disabled = false; return; }
      S.runJob({
        path: S.campUrl('/simulation/goal-review'),
        body: { auto_accept: !!auto },
        btn: btn, errBox: errBox, log: S.makeLogBox(logBox),
        // 50 min: the server's own watchdog for this job sits at 2800 s
        // (~47 min) — the default 31-min poll cap used to give up first
        timeoutMs: 50 * 60 * 1000,
        failLabel: 'Goal review failed'
      }).then((result) => {
        const r = result || {};
        if (r.auto_accept === 'approved') {
          S.toast(r.proposed + ' goal update' + (r.proposed === 1 ? '' : 's') +
            ' AUTO-ACCEPTED — the ledger moves on.', 'ok');
          S.rerenderIfCurrent('sim');
        } else if (r.staged) {
          S.toast(r.proposed + ' goal update' + (r.proposed === 1 ? '' : 's') +
            ' proposed — review it above, then accept or discard.', 'ok');
          S.rerenderIfCurrent('sim'); // re-render: the proposal panel leads the tab
        } else {
          const jobFindings = Array.isArray(r.findings) ? r.findings : [];
          // two nodes — one S.el() call would drop the second (see el() in core.js)
          errBox.innerHTML = '';
          errBox.appendChild(S.el(
            '<div class="success-panel">No goal updates proposed — the ' +
            'ledger does not justify any changes.</div>'));
          if (jobFindings.length) {
            errBox.appendChild(S.el(S.stringList(jobFindings)));
          }
        }
      }).catch(() => {});
    });
    root.appendChild(card);
  }

  /* ----- forecast (run the real engine forward on scratch) ----- */

  function forecastCard(root) {
    const card = S.el(
      '<section class="panel forecast-card-panel">' +
        '<h3 class="panel-title">Forecast the next weeks</h3>' +
        '<p class="panel-note">Runs the real engine forward 2&ndash;3 weeks ' +
          'on scratch data — dice, scheduler, consequences and all — and ' +
          'shows you where the current trajectory lands. A projection only: ' +
          'never canon, never accept-able; read it, steer, discard it.</p>' +
        '<div class="inline-form">' +
          '<label class="field field-narrow"><span class="field-label">Weeks</span>' +
            '<select class="input forecast-weeks"><option value="2">2</option>' +
              '<option value="3">3</option></select></label>' +
          '<button type="button" class="btn btn-primary forecast-run-btn">Run the forecast</button>' +
          '<span class="muted form-note">Costs one real week of API credits ' +
            'per projected week — and one week of engine time: ' +
            S.DURATIONS.band(2) + ' for a 2-week forecast, roster-dependent.</span>' +
        '</div>' +
        '<pre class="log-tail hidden forecast-log"></pre>' +
        '<div class="forecast-run-error"></div>' +
      '</section>'
    );
    const btn = card.querySelector('.forecast-run-btn');
    const weeksSel = card.querySelector('.forecast-weeks');
    const logBox = card.querySelector('.forecast-log');
    const errBox = card.querySelector('.forecast-run-error');
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      const n = parseInt(weeksSel.value, 10) || 2;
      btn.disabled = true;
      const ok = await S.confirmRun(
        'Run a ' + n + '-week forecast? The engine runs ' + n + ' real week' +
        (n === 1 ? '' : 's') + ' on scratch data — about ' + n + 'x a week' +
        '\'' + 's API spend and roughly ' + S.DURATIONS.approx(n) +
        ' at the keyboard. ' +
        'Nothing lands in canon; the projection is for ' +
        'your eyes, then you discard it.'
      );
      if (!ok) { btn.disabled = false; return; }
      S.runJob({
        path: S.campUrl('/simulation/forecast'),
        body: { confirm: true, weeks: n },
        btn: btn, errBox: errBox, log: S.makeLogBox(logBox),
        // the give-up must scale with the run: the per-week engine cap is
        // 30 min, so a 3-week forecast legally runs 90+ — the week-sized
        // 31-minute default reported a false failure mid-run
        timeoutMs: n * 31 * 60 * 1000 + 5 * 60 * 1000,
        failLabel: 'Forecast failed'
      }).then(() => {
        S.toast('Forecast staged — read it above, then discard it.', 'ok');
        S.rerenderIfCurrent('sim'); // re-render: the forecast panel leads with the weeks
      }).catch(() => {});
    });
    root.appendChild(card);
  }

  /* ----- record-a-session form (Phase 3: history + structured effects) ----- */

  const EFFECT_LEVELS = ['none', 'low', 'medium', 'high'];
  // what the "Now" (after) control offers for each kind, single-sourced:
  // a resource row picks from the four grades, a project-progress row from
  // the 0-8 counter, facts and project statuses are free text
  const EFFECT_AFTER = {
    set_resource: { tag: 'select', options: EFFECT_LEVELS, value: 'none' },
    set_project_progress: {
      tag: 'select',
      options: ['0', '1', '2', '3', '4', '5', '6', '7', '8'],
      value: '0'
    },
    set_field: { tag: 'input', options: null, value: '',
                 placeholder: 'the new value' },
    set_project_status: { tag: 'input', options: null, value: '',
                          placeholder: 'active | paused | abandoned' }
  };

  function sameOptions(a, b) {
    const x = Array.isArray(a) ? a : [];
    const y = Array.isArray(b) ? b : [];
    return x.length === y.length && x.every((o, i) => String(o) === String(y[i]));
  }

  /* Pure — the "Now" control a kind must render, given what the row shows
     now ({tag: 'select'|'input', options?: [...], value?, placeholder?},
     or null for a not-yet-rendered row). Returns {rebuild, control,
     reset}: rebuild is false when the kind's option set is already on
     screen (the DM's selection stays put); otherwise control is what to
     render ({tag, options, value, placeholder}) and reset flags that a
     stale user value was discarded — the row must show it changed, never
     carry a word from the old kind into the new one (ticket 29). Pinned
     by studio/tests/js/test_sessions_staging.cjs. */
  S.effectAfterControl = function (kind, current) {
    const spec = EFFECT_AFTER[kind];
    if (!spec) return { rebuild: false, control: current, reset: false };
    if (current && current.tag === spec.tag &&
        (spec.tag === 'input'
          ? String(current.placeholder || '') === String(spec.placeholder || '')
          : sameOptions(current.options, spec.options))) {
      return { rebuild: false, control: current, reset: false };
    }
    return {
      rebuild: true,
      control: {
        tag: spec.tag,
        options: spec.options ? spec.options.slice() : null,
        value: spec.value,
        placeholder: spec.placeholder
      },
      reset: !!(current && String(current.value || '') !== '' &&
                String(current.value) !== String(spec.value))
    };
  };

  /* Pure — the after value a kind may submit, or null when the value does
     not belong to the kind (a resource word on a project-progress row, a
     0-8 counter as a resource level). The submit gate blocks on null —
     coercion is how 'high' once became progress 0. Free-text kinds pass
     their string through as-is. Pinned by
     studio/tests/js/test_sessions_staging.cjs. */
  S.effectAfterValue = function (kind, value) {
    const spec = EFFECT_AFTER[kind];
    if (!spec) return null;
    const v = String(value == null ? '' : value);
    if (spec.tag === 'input') return v;
    return spec.options.indexOf(v) !== -1 ? v : null;
  };

  function effectRow(factions, projects) {
    const opts = factions.map((f) =>
      '<option value="' + S.esc(String(f.id)) + '">' + S.esc(f.name || f.id) + '</option>').join('');
    const projOpts = (projects || []).map((p) =>
      '<option value="' + S.esc(String(p.id)) + '">' +
        S.esc(String((p.title || p.id) + ' (' + String(p.progress) + '/' +
                      String(p.target) + ')')) + '</option>').join('');
    return S.el(
      '<div class="sand-faction session-effect">' +
        '<div class="inline-form">' +
          '<label class="field field-narrow"><span class="field-label">Kind of change</span>' +
            '<select class="input e-type"><option value="set_resource">resource</option>' +
              '<option value="set_field">fact (goal/kind/secret)</option>' +
              '<option value="set_project_progress">project progress</option>' +
              '<option value="set_project_status">project status</option></select></label>' +
          '<label class="field field-narrow e-faction-wrap"><span class="field-label">Faction</span>' +
            '<select class="input e-faction">' + opts + '</select></label>' +
          '<label class="field field-narrow hidden e-project-wrap"><span class="field-label">Project</span>' +
            '<select class="input e-project">' + (projOpts ||
              '<option value="">(no projects yet)</option>') + '</select></label>' +
          '<label class="field field-narrow e-res-wrap"><span class="field-label">Resource</span>' +
            '<select class="input e-res"><option>coin</option><option>muscle</option><option>legitimacy</option></select></label>' +
          '<label class="field field-narrow hidden e-field-wrap"><span class="field-label">Field</span>' +
            '<select class="input e-field"><option>goal</option><option>kind</option><option>secret</option></select></label>' +
          '<label class="field field-narrow"><span class="field-label">Was (optional)</span>' +
            '<input type="text" class="input e-was" placeholder="e.g. high"></label>' +
          '<label class="field field-narrow"><span class="field-label">Now</span>' +
            '<select class="input e-after"></select></label>' +
          '<button type="button" class="btn e-remove" title="Remove this effect">&times;</button>' +
        '</div>' +
        '<div class="inline-form">' +
          '<label class="field grow"><span class="field-label">Reason — what happened at the table</span>' +
            '<input type="text" class="input e-reason" placeholder="e.g. The party destroyed the treasury."></label>' +
        '</div>' +
      '</div>'
    );
  }

  function sessionPanel(root, factions, projects) {
    const simProjects = Array.isArray(projects) ? projects : [];
    const panel = S.el(
      '<section class="panel session-panel">' +
        '<h3 class="panel-title">Record a session</h3>' +
        '<p class="panel-note">Fold what happened at the table into the world. ' +
          'History lines are <strong>recorded as-is with no mechanical effect</strong> — ' +
          'a treasury destroyed in prose changes nothing until you add a resource ' +
          'effect. Everything stages as a proposal above; accept applies it once.</p>' +
        '<div class="form-block">' +
          '<div class="inline-form">' +
            '<label class="field"><span class="field-label">Source</span>' +
              '<input type="text" class="input s-source" placeholder="e.g. session:session-08"></label>' +
            '<label class="field field-narrow"><span class="field-label">In-world week (optional)</span>' +
              '<input type="number" class="input s-week" min="0" placeholder="current"></label>' +
          '</div>' +
          '<label class="field"><span class="field-label">What happened — one event per line, ' +
            '"actor: text" (actor defaults to world)</span>' +
            '<textarea class="input s-events" rows="3" ' +
              'placeholder="wardens: The party bullied the toll collector into silence.&#10;The new magistrate doubled the bridge guard."></textarea></label>' +
          '<div class="s-effects"></div>' +
          '<div class="inline-form">' +
            '<button type="button" class="btn s-add-effect">+ Add a resource/fact effect</button>' +
            '<button type="button" class="btn btn-primary s-submit">Stage session changes for review</button>' +
          '</div>' +
          '<div class="form-error"></div>' +
        '</div>' +
      '</section>'
    );
    const host = panel.querySelector('.s-effects');
    const errBox = panel.querySelector('.form-error');

    function addRow() {
      const row = effectRow(factions, simProjects);
      row.querySelector('.e-remove').addEventListener('click', () => row.remove());
      const typeSel = row.querySelector('.e-type');
      const resWrap = row.querySelector('.e-res-wrap');
      const fieldWrap = row.querySelector('.e-field-wrap');
      const factionWrap = row.querySelector('.e-faction-wrap');
      const projectWrap = row.querySelector('.e-project-wrap');
      function sync() {
        const t = typeSel.value;
        const isField = t === 'set_field';
        const isProject = t === 'set_project_progress' ||
                          t === 'set_project_status';
        resWrap.classList.toggle('hidden', isField || isProject);
        fieldWrap.classList.toggle('hidden', !isField);
        factionWrap.classList.toggle('hidden', isProject);
        projectWrap.classList.toggle('hidden', !isProject);
        const afterEl = row.querySelector('.e-after');
        // what the row shows now, in the state machine's shape: the
        // template ships an EMPTY select — a fresh row must get its
        // options here too, not only when toggling from another kind
        const current = (afterEl.tagName === 'SELECT')
          ? { tag: 'select',
              options: Array.prototype.map.call(afterEl.options, (o) => o.value),
              value: afterEl.value }
          : { tag: 'input', placeholder: afterEl.placeholder || '',
              value: afterEl.value };
        const plan = S.effectAfterControl(t, current);
        if (!plan.rebuild) return;
        let next;
        if (plan.control.tag === 'input') {
          next = document.createElement('input');
          next.type = 'text';
          next.placeholder = plan.control.placeholder || '';
        } else {
          next = document.createElement('select');
          next.innerHTML = plan.control.options.map((o) =>
            '<option value="' + S.esc(o) + '">' + S.esc(o) + '</option>').join('');
        }
        next.className = 'input e-after';
        next.value = plan.control.value;
        afterEl.replaceWith(next);
        if (plan.reset) {
          // the new kind's options displaced a value the DM had picked:
          // the reset to the kind's default must be visible, not silent
          row.classList.add('session-effect-changed');
          row.title = 'The kind of change switched — "Now" was reset to ' +
            'this kind\u2019s default. Re-check it.';
          next.addEventListener('change', () => {
            row.classList.remove('session-effect-changed');
            row.title = '';
          }, { once: true });
        }
      }
      typeSel.addEventListener('change', sync);
      sync();
      host.appendChild(row);
    }
    panel.querySelector('.s-add-effect').addEventListener('click', addRow);

    panel.querySelector('.s-submit').addEventListener('click', async () => {
      const btn = panel.querySelector('.s-submit');
      if (btn.disabled) return;
      const source = panel.querySelector('.s-source').value.trim();
      const weekRaw = panel.querySelector('.s-week').value.trim();
      const events = panel.querySelector('.s-events').value.split('\n')
        .map((ln) => ln.trim()).filter(Boolean)
        .map((ln) => {
          const m = ln.match(/^([A-Za-z0-9_]+)\s*:\s*(.+)$/);
          return m ? { actor: m[1], text: m[2].trim() } : { actor: 'world', text: ln };
        });
      const effectRows = Array.from(host.querySelectorAll('.session-effect'));
      const effects = effectRows.map((row) => {
        const t = row.querySelector('.e-type').value;
        const isField = t === 'set_field';
        const isProject = t === 'set_project_progress' ||
                          t === 'set_project_status';
        const after = S.effectAfterValue(t, row.querySelector('.e-after').value);
        if (after === null) return null; // value foreign to the kind
        const base = {
          type: t,
          after: after,
          reason: row.querySelector('.e-reason').value.trim()
        };
        if (isProject) {
          base.project_id = row.querySelector('.e-project').value;
          // the gate above guarantees a 0-8 option string, so the counter
          // converts exactly — never Number(word) || 0
          if (t === 'set_project_progress') base.after = Number(after);
        } else {
          base.faction_id = row.querySelector('.e-faction').value;
          if (isField) base.field = row.querySelector('.e-field').value;
          else base.resource = row.querySelector('.e-res').value;
        }
        const was = row.querySelector('.e-was').value.trim();
        if (was) base.expected_before = was;
        return base;
      });
      const badAt = effects.indexOf(null);
      if (badAt !== -1) {
        // a stale "Now" value must never ride into a staged effect: the
        // engine would take 'high' as progress 0 (engine-valid, silent)
        // or 422 on a number as a resource level
        effectRows[badAt].classList.add('session-effect-changed');
        S.toast('Effect ' + (badAt + 1) + ': the "Now" value does not fit ' +
          'that kind of change — pick one from its list first.', 'error');
        return;
      }
      if (!source) { S.toast('Name the source — e.g. session:session-08.', 'error'); return; }
      if (!events.length && !effects.length) {
        S.toast('Nothing to record: add history lines or an effect.', 'error');
        return;
      }
      const body = { source: source, events: events, effects: effects };
      if (weekRaw !== '') body.week = Number(weekRaw);
      btn.disabled = true;
      errBox.innerHTML = '';
      try {
        await S.api(S.campUrl('/simulation/session'), { method: 'POST', body: body });
        S.toast('Session staged — review the proposal above, then accept.', 'ok');
        S.rerenderIfCurrent('sim');
      } catch (err) {
        errBox.innerHTML = '';
        errBox.appendChild(S.errorPanel(err));
        btn.disabled = false;
      }
    });

    root.appendChild(panel);
  }

  /* ----- faction projects: concrete undertakings with persistent progress ----- */

  function requirementChips(project) {
    const req = (project.requirements && typeof project.requirements === 'object')
      ? project.requirements : {};
    const keys = Object.keys(req);
    if (!keys.length) return '<span class="muted">no requirements</span>';
    return keys.map((k) =>
      '<span class="pill chip chip-' + S.esc(String(req[k])) + '" ' +
        'title="minimum eligibility — work cannot progress below this">' +
        S.esc(k) + ' &ge; ' + S.esc(String(req[k])) + '</span>').join(' ') +
      ((project.requirements_met === false)
        ? ' <span class="pill fidelity fidelity-dead">unmet</span>' : '');
  }

  function projectHistoryItem(ev) {
    const wk = (ev.week != null) ? '<span class="pill pill-when">week ' +
      S.esc(String(ev.week)) + '</span>' : '';
    const moved = (ev.progress_before != null && ev.project_id)
      ? ' <span class="pill pill-kind">' + S.esc(String(ev.progress_before)) +
        ' &rarr; ' + S.esc(String(ev.progress_after)) + '</span>' : '';
    const text = ev.public_action && ev.public_action !== ev.action
      ? S.esc(String(ev.public_action)) +
        ' <span class="muted">(public line; DM: ' +
        S.esc(String(ev.action || ev.text || '')) + ')</span>'
      : S.esc(String(ev.text || ev.action || ''));
    return '<li>' + wk + moved + '<span class="event-text">' + text + '</span></li>';
  }

  function projectCard(project, factions) {
    const owner = factions.find((f) => f.id === project.faction_id);
    const ownerName = (owner && owner.name) || project.faction_id;
    const status = project.status || 'active';
    const atTarget = Number(project.progress) >= Number(project.target);
    const blocked = project.completion_blocked;
    const card = S.el(
      '<article class="card project-card" data-project-id="' +
        S.esc(String(project.id)) + '">' +
        '<header class="faction-head">' +
          '<h5 class="faction-name">' + S.esc(String(project.title || project.id)) + '</h5>' +
          '<span class="pill pill-kind">' + S.esc(status) + '</span>' +
          '<span class="pill pill-when" title="progress / target">' +
            S.esc(String(project.progress)) + '/' + S.esc(String(project.target)) + '</span>' +
        '</header>' +
        '<p class="muted">' + S.esc(String(project.objective || '')) + '</p>' +
        '<div class="chip-row">' + requirementChips(project) + '</div>' +
        ((project.place_ids || []).length
          ? '<div class="chip-row project-places">' + (project.place_ids || []).map((pid) =>
              '<button type="button" class="pill pill-when project-place-link" data-place="' +
              S.esc(pid) + '" title="Open this place (Places tab)">' + S.esc(pid) + '</button>').join('') + '</div>'
          : '') +
        (project.completion_text
          ? '<p class="muted project-completion"><strong>On completion:</strong> ' +
            S.esc(String(project.completion_text)) + '</p>' : '') +
        (blocked ? '<div class="success-panel" style="border-color: #b23">' +
          '<strong>Completion blocked:</strong> ' + S.esc(String(blocked)) + '</div>' : '') +
        '<details class="project-history"><summary>History &amp; movement</summary>' +
          '<div class="project-history-body"><p class="muted">Loading…</p></div>' +
        '</details>' +
        '<div class="inline-form project-actions">' +
          (status === 'active'
            ? '<button type="button" class="btn p-pause">Pause</button>' +
              (atTarget ? '' : '<button type="button" class="btn p-abandon">Abandon</button>')
            : status === 'paused'
              ? '<button type="button" class="btn p-resume">Resume</button>' +
                '<button type="button" class="btn p-abandon">Abandon</button>'
              : '') +
          (status !== 'completed' && status !== 'abandoned'
            ? '<button type="button" class="btn p-edit">Edit terms</button>' : '') +
        '</div>' +
      '</article>'
    );
    // No per-card listeners here: pause/resume/abandon/edit, the place
    // links and the history toggle are all handled by the document-level
    // delegated listeners at the bottom of this file, which survive the
    // re-renders that replace these cards.
    return card;
  }

  /* ----- delegated project-card actions (survive re-renders) ----- */

  function projectErrBox(card) {
    const panel = card.closest('.projects-panel');
    return panel ? panel.querySelector('.projects-error') : null;
  }

  async function prepareProjectOp(project, errBox, op) {
    const ok = await S.confirmRun(
      'Stage this project operation (' + op.op + ')? Nothing changes until ' +
      'you accept the proposal above.'
    );
    if (!ok) return;
    if (errBox) errBox.innerHTML = '';
    try {
      await S.api(S.campUrl('/simulation/projects/prepare'), {
        method: 'POST',
        body: { operations: [op] }
      });
      S.toast('Project changes staged — review the proposal above, then accept.', 'ok');
      S.rerenderIfCurrent('sim');
    } catch (err) {
      if (errBox) {
        errBox.innerHTML = '';
        errBox.appendChild(S.errorPanel(err));
      } else {
        S.toast((err && err.error) ? err.error : 'The project operation failed.', 'error');
      }
    }
  }

  document.addEventListener('click', (ev) => {
    if (!(ev.target instanceof Element)) return;
    const placeBtn = ev.target.closest('.faction-place-link, .project-place-link');
    if (placeBtn) {
      S.openPlace(placeBtn.getAttribute('data-place'));
      return;
    }
    const btn = ev.target.closest('.p-pause, .p-resume, .p-abandon, .p-edit');
    if (!btn) return;
    const card = btn.closest('.project-card');
    if (!card) return;
    const project = projectIndex.get(card.getAttribute('data-project-id'));
    if (!project) return;
    const errBox = projectErrBox(card);
    if (btn.classList.contains('p-pause')) {
      prepareProjectOp(project, errBox, { op: 'pause', project_id: project.id,
        reason: 'Paused from the Projects panel.' });
    } else if (btn.classList.contains('p-resume')) {
      prepareProjectOp(project, errBox, { op: 'resume', project_id: project.id,
        reason: 'Resumed from the Projects panel.' });
    } else if (btn.classList.contains('p-abandon')) {
      prepareProjectOp(project, errBox, { op: 'abandon', project_id: project.id,
        reason: 'Abandoned from the Projects panel.' });
    } else if (btn.classList.contains('p-edit')) {
      const panel = card.closest('.projects-panel');
      const formHost = panel ? panel.querySelector('.project-form-host') : null;
      if (formHost) {
        projectFormPanel(formHost, simFactionsCache, errBox, project,
          () => S.rerenderIfCurrent('sim'));
      }
    }
  });

  // 'toggle' does not bubble, but capture-phase ancestors still receive it
  document.addEventListener('toggle', (ev) => {
    const details = ev.target;
    if (!(details instanceof Element) ||
        !details.classList.contains('project-history') || !details.open) return;
    const card = details.closest('.project-card');
    const body = details.querySelector('.project-history-body');
    if (!card || !body || body.dataset.loaded) return;
    const project = projectIndex.get(card.getAttribute('data-project-id'));
    if (!project) return;
    S.api(S.campUrl('/simulation/projects/' + encodeURIComponent(project.id)))
      .then((data) => {
        const hist = Array.isArray(data.history) ? data.history : [];
        body.innerHTML = hist.length
          ? '<ul class="event-list">' + hist.map(projectHistoryItem).join('') + '</ul>'
          : '<p class="muted">No recorded movement yet — work weeks, sabotage, ' +
            'sessions and completions will appear here.</p>';
        body.dataset.loaded = '1';
      })
      .catch((err) => {
        body.innerHTML = '';
        body.appendChild(S.errorPanel(err));
      });
  }, true);

  function projectFormPanel(host, factions, errBox, existing, onStaged) {
    // one form for create AND edit (existing != null): the same fields, an
    // edit posts {op: 'edit', set: {...}} against the project's id
    const old = existing || {};
    host.innerHTML = '';
    const LEVELS = ['none', 'low', 'medium', 'high'];
    const reqSel = (key, val) =>
      '<label class="field field-narrow"><span class="field-label">req ' + key + '</span>' +
        '<select class="input pf-req" data-res="' + key + '">' +
          '<option value=""' + (!val ? ' selected' : '') + '>&mdash;</option>' +
          LEVELS.map((l) => '<option value="' + l + '"' +
            (val === l ? ' selected' : '') + '>' + l + '</option>').join('') +
        '</select></label>';
    const eff = ((old.completion || {}).effects || [])[0] || {};
    const panel = S.el(
      '<section class="panel project-form-panel">' +
        '<h3 class="panel-title">' + (existing ? 'Edit project terms — ' +
          S.esc(String(old.title || old.id)) : 'New project') + '</h3>' +
        '<p class="panel-note">A project is one concrete undertaking with a ' +
        'counter: successful work weeks move it one step, successful sabotage ' +
        'moves it back one, and reaching the target applies the preapproved ' +
        'completion exactly once. One active project per faction. Staging is ' +
        'free — no LLM anywhere.</p>' +
        '<div class="form-block">' +
          '<div class="inline-form">' +
            '<label class="field field-narrow"><span class="field-label">Faction (owner)</span>' +
              '<select class="input pf-owner"' + (existing ? ' disabled' : '') + '>' +
                factions.map((f) => '<option value="' + S.esc(String(f.id)) + '"' +
                  ((old.faction_id || '') === f.id ? ' selected' : '') + '>' +
                  S.esc(String(f.name || f.id)) + '</option>').join('') +
              '</select></label>' +
            '<label class="field grow"><span class="field-label">Title</span>' +
              '<input type="text" class="input pf-title" value="' +
              S.esc(String(old.title || '')) + '" placeholder="e.g. Buy the ferry concession"></label>' +
            '<label class="field field-narrow"><span class="field-label">Target steps</span>' +
              '<input type="number" class="input pf-target" min="2" max="8" value="' +
              S.esc(String(old.target || 4)) + '"></label>' +
          '</div>' +
          '<label class="field"><span class="field-label">Objective — what done looks like</span>' +
            '<input type="text" class="input pf-objective" value="' +
            S.esc(String(old.objective || '')) + '" placeholder="e.g. Secure the concession before the spring auctions."></label>' +
          '<div class="inline-form">' +
            '<label class="field grow"><span class="field-label">Places (optional, comma-separated ids)</span>' +
              '<input type="text" class="input pf-places" value="' +
              S.esc(String((old.place_ids || []).join(', '))) + '" placeholder="e.g. ferry-crossing"></label>' +
            '<label class="field field-narrow"><span class="field-label">Visibility</span>' +
              '<select class="input pf-visibility">' +
                '<option value="restricted"' + ((old.visibility || 'restricted') === 'restricted' ? ' selected' : '') + '>restricted (hidden)</option>' +
                '<option value="public"' + (old.visibility === 'public' ? ' selected' : '') + '>public</option>' +
              '</select></label>' +
            '<label class="field field-narrow"><span class="field-label">Known to (optional, comma-separated ids)</span>' +
              '<input type="text" class="input pf-known" value="' +
              S.esc(String((old.known_to || []).join(', '))) + '" placeholder="rivals told on purpose"></label>' +
          '</div>' +
          '<div class="inline-form">' +
            reqSel('coin', (old.requirements || {}).coin) +
            reqSel('muscle', (old.requirements || {}).muscle) +
            reqSel('legitimacy', (old.requirements || {}).legitimacy) +
          '</div>' +
          '<label class="field"><span class="field-label">Completion — what the world looks like when the counter fills</span>' +
            '<input type="text" class="input pf-completion" value="' +
            S.esc(String((old.completion || {}).text || '')) + '" placeholder="e.g. The Wardens acquired the ferry concession."></label>' +
          '<div class="inline-form">' +
            '<label class="field field-narrow"><span class="field-label">Completion effect</span>' +
              '<select class="input pf-eff-kind">' +
                '<option value="">none</option>' +
                '<option value="set_resource"' + (eff.type === 'set_resource' ? ' selected' : '') + '>resource</option>' +
                '<option value="set_field"' + (eff.type === 'set_field' ? ' selected' : '') + '>fact (kind/goal/secret/species)</option>' +
              '</select></label>' +
            '<label class="field field-narrow pf-eff-res-wrap"><span class="field-label">Resource</span>' +
              '<select class="input pf-eff-res"><option>coin</option><option>muscle</option><option>legitimacy</option></select></label>' +
            '<label class="field field-narrow pf-eff-field-wrap hidden"><span class="field-label">Field</span>' +
              '<select class="input pf-eff-field"><option>kind</option><option>goal</option><option>secret</option><option>species</option></select></label>' +
            '<label class="field field-narrow"><span class="field-label">Was (optional)</span>' +
              '<input type="text" class="input pf-eff-was" value="' + S.esc(String(eff.expected_before || '')) + '" placeholder="precondition"></label>' +
            '<label class="field field-narrow"><span class="field-label">After</span>' +
              '<input type="text" class="input pf-eff-after" value="' + S.esc(String(eff.after || '')) + '" placeholder="e.g. medium"></label>' +
            '<label class="field grow"><span class="field-label">Effect reason</span>' +
              '<input type="text" class="input pf-eff-reason" value="' + S.esc(String(eff.reason || '')) + '" placeholder="why completion means this"></label>' +
          '</div>' +
          '<div class="inline-form">' +
            '<button type="button" class="btn btn-primary pf-stage">' +
              (existing ? 'Stage the term edit for review' : 'Stage the new project for review') + '</button>' +
            '<button type="button" class="btn pf-cancel">Cancel</button>' +
            '<span class="muted form-note">Stages a proposal above — the campaign changes only if you accept it.</span>' +
          '</div>' +
          '<div class="form-error pf-error"></div>' +
        '</div>' +
      '</section>'
    );
    const kindSel = panel.querySelector('.pf-eff-kind');
    const resWrap = panel.querySelector('.pf-eff-res-wrap');
    const fieldWrap = panel.querySelector('.pf-eff-field-wrap');
    if (eff.resource) panel.querySelector('.pf-eff-res').value = eff.resource;
    if (eff.field) panel.querySelector('.pf-eff-field').value = eff.field;
    function syncKind() {
      const isRes = kindSel.value === 'set_resource';
      resWrap.classList.toggle('hidden', !isRes);
      fieldWrap.classList.toggle('hidden', isRes || kindSel.value === '');
    }
    kindSel.addEventListener('change', syncKind);
    syncKind();
    panel.querySelector('.pf-cancel').addEventListener('click', () => panel.remove());
    panel.querySelector('.pf-stage').addEventListener('click', async () => {
      const btn = panel.querySelector('.pf-stage');
      if (btn.disabled) return;
      const owner = existing ? old.faction_id
        : panel.querySelector('.pf-owner').value;
      const title = panel.querySelector('.pf-title').value.trim();
      const objective = panel.querySelector('.pf-objective').value.trim();
      if (!title || !objective) {
        S.toast('A project needs a title and an objective.', 'error');
        return;
      }
      const target = Number(panel.querySelector('.pf-target').value) || 4;
      const places = panel.querySelector('.pf-places').value.split(',')
        .map((s) => s.trim()).filter(Boolean);
      const visibility = panel.querySelector('.pf-visibility').value;
      const known = panel.querySelector('.pf-known').value.split(',')
        .map((s) => s.trim()).filter(Boolean);
      const requirements = {};
      panel.querySelectorAll('.pf-req').forEach((sel) => {
        if (sel.value) requirements[sel.getAttribute('data-res')] = sel.value;
      });
      const completion = { text: panel.querySelector('.pf-completion').value.trim() };
      if (kindSel.value) {
        const effRec = { type: kindSel.value, faction_id: owner,
                         after: panel.querySelector('.pf-eff-after').value.trim(),
                         reason: panel.querySelector('.pf-eff-reason').value.trim() ||
                                 completion.text || 'Completion of ' + title };
        if (kindSel.value === 'set_resource') effRec.resource = panel.querySelector('.pf-eff-res').value;
        else effRec.field = panel.querySelector('.pf-eff-field').value;
        const was = panel.querySelector('.pf-eff-was').value.trim();
        if (was) effRec.expected_before = was;
        completion.effects = [effRec];
      }
      const op = existing
        ? { op: 'edit', project_id: old.id,
            reason: 'Term edit from the Projects panel.',
            set: { title: title, objective: objective, target: target,
                   place_ids: places, visibility: visibility,
                   known_to: known, requirements: requirements,
                   completion: completion } }
        : { op: 'create', reason: 'New project from the Projects panel.',
            project: { faction_id: owner, title: title, objective: objective,
                       target: target, place_ids: places,
                       visibility: visibility, known_to: known,
                       requirements: requirements, completion: completion } };
      btn.disabled = true;
      const box = panel.querySelector('.pf-error');
      box.innerHTML = '';
      try {
        await S.api(S.campUrl('/simulation/projects/prepare'), {
          method: 'POST', body: { operations: [op] }
        });
        S.toast('Project changes staged — review the proposal above, then accept.', 'ok');
        if (onStaged) onStaged(); else S.rerenderIfCurrent('sim');
      } catch (err) {
        box.innerHTML = '';
        box.appendChild(S.errorPanel(err));
        btn.disabled = false;
      }
    });
    host.appendChild(panel);
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function projectsPanel(root, factions) {
    const panel = S.el(
      '<section class="panel projects-panel">' +
        '<h3 class="panel-title">Projects</h3>' +
        '<p class="panel-note">What each faction is actually trying to finish, ' +
        'and how far it has gotten. Owners see their own in full; rivals only ' +
        'see what is public or told to them — exact counters here are DM ' +
        'information.</p>' +
        '<div class="projects-by-owner panel-body"></div>' +
        '<div class="inline-form">' +
          '<button type="button" class="btn project-new">+ New project</button>' +
        '</div>' +
        '<div class="project-form-host"></div>' +
        '<div class="projects-error"></div>' +
      '</section>'
    );
    const host = panel.querySelector('.projects-by-owner');
    const formHost = panel.querySelector('.project-form-host');
    const errBox = panel.querySelector('.projects-error');
    panel.querySelector('.project-new').addEventListener('click', () =>
      projectFormPanel(formHost, factions, errBox, null));
    S.api(S.campUrl('/simulation/projects')).then((rep) => {
      const projects = (rep && Array.isArray(rep.projects)) ? rep.projects : [];
      if (!projects.length) {
        host.appendChild(S.emptyState(
          'No projects yet — a project gives a faction something concrete to ' +
          'finish, one step at a time.'));
        return;
      }
      const byOwner = {};
      projects.forEach((p) => {
        projectIndex.set(String(p.id), p); // for the delegated action handlers
        (byOwner[p.faction_id] = byOwner[p.faction_id] || []).push(p);
      });
      Object.keys(byOwner).forEach((fid) => {
        const f = factions.find((x) => x.id === fid);
        host.appendChild(S.el('<h4 class="project-owner">' +
          S.esc(String((f && f.name) || fid)) + '</h4>'));
        const grid = S.el('<div class="faction-grid"></div>');
        byOwner[fid].forEach((p) =>
          grid.appendChild(projectCard(p, factions)));
        host.appendChild(grid);
      });
      if (Array.isArray(rep.findings) && rep.findings.length) {
        errBox.appendChild(S.el(S.stringList(rep.findings)));
      }
    }).catch((err) => {
      host.innerHTML = '';
      host.appendChild(S.errorPanel(err));
    });
    root.appendChild(panel);
  }

  S.tabs.sim = async function (container) {
    container.innerHTML = '';
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }

    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);
    root.appendChild(S.loading('Loading simulation…'));

    let sim;
    try {
      sim = await S.api(S.campUrl('/simulation'));
    } catch (err) {
      root.innerHTML = '';
      if (err && err.status === 404) {
        // A campaign without simulation/state.yaml is normal (a fresh world
        // has no factions yet) — this is an empty state with a working
        // create form, not an error.
        root.appendChild(S.el(
          '<section class="panel sim-intro"><h3 class="panel-title">No simulation in this campaign yet</h3>' +
            '<p class="muted">Maps and World Building work without it. Create the ' +
            'faction sandbox below — then <em>Propose week 1</em> simulates what happens ' +
            'next, one week at a time; accepting the proposal writes the chronicle ' +
            'and advances the canon clock.</p>' +
          '</section>'
        ));
        createSimulationPanel(root);
        return;
      }
      root.appendChild(S.errorPanel(err, 'The simulation state could not be loaded.'));
      return;
    }
    root.innerHTML = '';

    // a failed history snapshot means published-but-untracked: surface the
    // one-click recovery (POST .../snapshot) where the accept happened
    const snapBanner = await S.snapshotBanner();
    if (snapBanner) root.appendChild(snapBanner);

    // scheduler holds from the week run that triggered this re-render:
    // shown once as a banner, then cleared (the design doc wants holds
    // visible as "held", not silent)
    const lastHeld = Array.isArray(S.state.lastHeld) ? S.state.lastHeld : [];
    S.state.lastHeld = null;
    if (lastHeld.length) {
      root.appendChild(S.el(
        '<section class="panel held-banner">' +
          '<strong>Held (scheduler):</strong> ' +
          S.esc(lastHeld.join(', ')) +
          ' <span class="muted">&mdash; these factions sat the week out.</span>' +
        '</section>'
      ));
    }

    const clock = (sim && sim.clock) ? sim.clock : {};
    const week = (clock.week != null) ? clock.week : null;
    const season = clock.season || '';
    const premise = (sim && sim.premise) || '';
    const factions = (sim && Array.isArray(sim.factions)) ? sim.factions : [];
    simFactionsCache = factions; // for the delegated Edit-terms handler
    const rumors = (sim && Array.isArray(sim.rumors)) ? sim.rumors : [];
    const events = (sim && Array.isArray(sim.events)) ? sim.events : [];
    const chronicles = (sim && Array.isArray(sim.chronicles)) ? sim.chronicles : [];
    const viewIds = (sim && Array.isArray(sim.views_available) && sim.views_available.length)
      ? sim.views_available
      : factions.map((f) => f.id).filter(Boolean);

    /* the pending proposal leads the tab: nothing else has moved */
    if (sim && sim.pending_proposal) {
      proposalPanel(root, sim.pending_proposal, chronicles);
    }

    /* an active forecast reads as "where this is heading": right below the
       proposal (it can coexist with one — it is never canon) */
    if (sim && sim.active_forecast) {
      forecastPanel(root);
    }

    const nextWeek = (week != null && !Number.isNaN(Number(week))) ? Number(week) + 1 : '?';
    root.appendChild(S.el(
      '<section class="panel sim-header">' +
        '<div class="sim-clock">' +
          '<span class="clock-week">' + (week != null ? 'Week ' + S.esc(String(week)) : 'Week ?') + '</span>' +
          (season ? '<span class="pill pill-season">' + S.esc(season) + '</span>' : '') +
        '</div>' +
        (premise ? '<p class="premise">' + S.esc(premise) + '</p>' : '') +
      '</section>'
    ));

    /* faction cards */
    const factionPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Factions</h3><div class="panel-body"></div></section>'
    );
    const factionBody = factionPanel.querySelector('.panel-body');
    if (!factions.length) {
      factionBody.appendChild(S.emptyState('No factions in the simulation state.'));
    } else {
      const placeNames = {};
      ((sim && Array.isArray(sim.places)) ? sim.places : []).forEach((p) => {
        if (p && p.id) placeNames[p.id] = p.name || p.id;
      });
      const gridEl = S.el('<div class="faction-grid"></div>');
      factions.forEach((f) => gridEl.appendChild(factionCard(f, placeNames)));
      factionBody.appendChild(gridEl);
    }
    root.appendChild(factionPanel);

    projectsPanel(root, factions);

    /* rumors board */
    const rumorPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Rumors</h3><div class="panel-body"></div></section>'
    );
    const rumorBody = rumorPanel.querySelector('.panel-body');
    if (!rumors.length) {
      rumorBody.appendChild(S.emptyState('No rumors in circulation.'));
    } else {
      const list = S.el('<ul class="rumor-list"></ul>');
      rumors.forEach((r) => list.appendChild(rumorItem(r, chronicles)));
      rumorBody.appendChild(list);
    }
    root.appendChild(rumorPanel);

    /* events list */
    const eventPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Events</h3><div class="panel-body"></div></section>'
    );
    eventItems(events, eventPanel.querySelector('.panel-body'));
    root.appendChild(eventPanel);

    /* inspectors row: chronicle reader / views / why */
    const inspRow = S.el('<div class="inspector-grid"></div>');
    root.appendChild(inspRow);

    /* chronicle reader */
    const chronPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Chronicle</h3>' +
        '<div class="inline-form"><label class="field grow"><span class="field-label">Week</span>' +
          '<select class="input chron-select"></select></label></div>' +
        '<article class="chronicle"></article>' +
      '</section>'
    );
    const chronSelect = chronPanel.querySelector('.chron-select');
    const chronArticle = chronPanel.querySelector('.chronicle');
    const sortedChron = chronicles.slice().sort((a, b) =>
      (Number(a.week) || 0) - (Number(b.week) || 0));
    sortedChron.forEach((c) => {
      const label = 'Week ' + c.week + (c.title ? ' — ' + c.title : '');
      chronSelect.appendChild(S.el(
        '<option value="' + S.esc(String(c.week)) + '">' + S.esc(label) + '</option>'
      ));
    });
    async function loadChronicle(wk) {
      chronArticle.innerHTML = '';
      chronArticle.appendChild(S.loading('Loading chronicle…'));
      try {
        const ch = await S.api(S.campUrl('/simulation/chronicle/' + encodeURIComponent(wk)));
        chronArticle.innerHTML = S.md((ch && ch.markdown) || '');
      } catch (err) {
        chronArticle.innerHTML = '';
        chronArticle.appendChild(S.errorPanel(err));
      }
    }
    if (sortedChron.length) {
      chronSelect.value = String(sortedChron[sortedChron.length - 1].week); // latest by default
      chronSelect.addEventListener('change', () => loadChronicle(chronSelect.value));
      loadChronicle(chronSelect.value);
    } else {
      chronSelect.appendChild(S.el('<option value="">No chronicles yet</option>'));
      chronSelect.disabled = true;
      chronArticle.appendChild(S.emptyState('Run a week to write the first chronicle.'));
    }
    inspRow.appendChild(chronPanel);

    /* views inspector */
    const viewsPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Views inspector</h3>' +
        '<p class="panel-note">What each faction believes — its fog of war.</p>' +
        '<div class="inline-form"><label class="field grow"><span class="field-label">Faction</span>' +
          '<select class="input views-select"></select></label>' +
          '<button type="button" class="btn views-load">Load view</button></div>' +
        '<pre class="code-view views-view hidden"></pre>' +
        '<div class="views-error"></div>' +
      '</section>'
    );
    const viewsSelect = viewsPanel.querySelector('.views-select');
    const viewsView = viewsPanel.querySelector('.views-view');
    const viewsErr = viewsPanel.querySelector('.views-error');
    const viewsBtn = viewsPanel.querySelector('.views-load');
    viewIds.forEach((id) => {
      const label = (() => {
        const f = factions.find((x) => x.id === id);
        return (f && f.name) ? f.name + ' (' + id + ')' : id;
      })();
      viewsSelect.appendChild(S.el(
        '<option value="' + S.esc(String(id)) + '">' + S.esc(label) + '</option>'
      ));
    });
    async function loadView() {
      const id = viewsSelect.value;
      if (!id) return;
      viewsBtn.disabled = true;
      viewsErr.innerHTML = '';
      viewsView.classList.remove('hidden');
      viewsView.textContent = 'Loading view…';
      try {
        const v = await S.api(S.campUrl('/simulation/views/' + encodeURIComponent(id)));
        viewsView.textContent = JSON.stringify(v, null, 2);
      } catch (err) {
        viewsView.classList.add('hidden');
        viewsErr.innerHTML = '';
        viewsErr.appendChild(S.errorPanel(err));
      } finally {
        viewsBtn.disabled = false;
      }
    }
    if (viewIds.length) viewsBtn.addEventListener('click', loadView);
    else viewsBtn.disabled = true;
    inspRow.appendChild(viewsPanel);

    /* why inspector */
    const whyPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Why inspector</h3>' +
        '<p class="panel-note">The causal chain behind a faction&rsquo;s week.</p>' +
        '<div class="inline-form">' +
          '<label class="field field-narrow"><span class="field-label">Week</span>' +
            '<input type="number" class="input why-week" min="1" value="' + S.esc(week != null ? String(week) : '1') + '"></label>' +
          '<label class="field grow"><span class="field-label">Faction</span>' +
            '<select class="input why-select"></select></label>' +
          '<button type="button" class="btn why-load">Ask why</button></div>' +
        '<pre class="code-view why-view hidden"></pre>' +
        '<div class="why-error"></div>' +
      '</section>'
    );
    const whyWeek = whyPanel.querySelector('.why-week');
    const whySelect = whyPanel.querySelector('.why-select');
    const whyView = whyPanel.querySelector('.why-view');
    const whyErr = whyPanel.querySelector('.why-error');
    const whyBtn = whyPanel.querySelector('.why-load');
    factions.forEach((f) => {
      if (!f.id) return;
      whySelect.appendChild(S.el(
        '<option value="' + S.esc(String(f.id)) + '">' + S.esc(f.name || f.id) + '</option>'
      ));
    });
    whyBtn.addEventListener('click', async () => {
      const fid = whySelect.value;
      if (!fid || whyBtn.disabled) return;
      whyBtn.disabled = true;
      whyErr.innerHTML = '';
      whyView.classList.remove('hidden');
      whyView.textContent = 'Asking why…';
      try {
        const resp = await S.api(S.campUrl('/simulation/why'), {
          method: 'POST',
          body: { week: Number(whyWeek.value) || 0, faction: fid }
        });
        const lines = (resp && Array.isArray(resp.lines)) ? resp.lines : [];
        // leading spaces are meaningful (indentation = causality depth): pre keeps them
        whyView.textContent = lines.join('\n') || '(no explanation returned)';
      } catch (err) {
        whyView.classList.add('hidden');
        whyErr.innerHTML = '';
        whyErr.appendChild(S.errorPanel(err));
      } finally {
        whyBtn.disabled = false;
      }
    });
    if (!factions.length) whyBtn.disabled = true;
    inspRow.appendChild(whyPanel);

    /* run week N — the expensive button: it GENERATES a proposal; only
       accepting the proposal moves the world */
    const runPanel = S.el(
      '<section class="panel run-panel">' +
        '<h3 class="panel-title">Generate week ' + S.esc(String(nextWeek)) + '</h3>' +
        '<div class="inline-form">' +
          '<button type="button" class="btn btn-primary run-week-btn">Propose week ' + S.esc(String(nextWeek)) + '</button>' +
          '<span class="muted form-note">Runs the live scheduler, factions and dice against scratch data and stages a proposal — the campaign changes only when you accept it. The week runs faction-by-faction: expect ' + S.DURATIONS.weekRun + ', roster-dependent.</span>' +
        '</div>' +
        '<label class="field auto-accept-field">' +
          '<input type="checkbox" class="input week-auto-accept">' +
          '<span><strong>Publish the week without reading it first.</strong> ' +
          'Auto-accept: the proposal is accepted the moment it stages — no chronicle review, no discard step. ' +
          'The engine still refuses invalid candidates and git keeps the undo. Default is off; reading the chronicle is the point.</span>' +
        '</label>' +
        '<pre class="log-tail hidden run-log"></pre>' +
        '<div class="run-error"></div>' +
      '</section>'
    );
    const runBtn = runPanel.querySelector('.run-week-btn');
    const runLog = runPanel.querySelector('.run-log');
    const runErr = runPanel.querySelector('.run-error');
    const autoChk = runPanel.querySelector('.week-auto-accept');
    runBtn.addEventListener('click', async () => {
      if (runBtn.disabled) return;
      const auto = autoChk && autoChk.checked;
      const ok = await S.confirmRun(
        'This runs a real week against scratch data — it spends API credits ' +
        'and takes ' + S.DURATIONS.weekRun + ' with a full roster. ' +
        'The campaign changes only if you accept the proposal afterwards.' +
        (auto ? ' AUTO-ACCEPT IS ON: the week publishes itself the moment it stages, without reading it first.' : '')
      );
      if (!ok) return;
      S.runJob({
        path: S.campUrl('/simulation/week'),
        body: { confirm: true, auto_accept: !!auto },
        btn: runBtn, errBox: runErr, log: S.makeLogBox(runLog),
        failLabel: 'Week run failed'
      }).then((result) => {
        const r = result || {};
        const held = Array.isArray(r.held) ? r.held : [];
        if (held.length) S.state.lastHeld = held; // banner on the re-render
        if (r.auto_accept === 'approved') {
          S.toast('Week ' + (r.week != null ? r.week : nextWeek) +
            ' was AUTO-ACCEPTED and published — read the chronicle above' +
            (held.length ? ' (' + held.length + ' faction' + (held.length === 1 ? '' : 's') + ' held)' : ''), 'ok');
        } else if (r.auto_accept === 'blocked' || r.auto_accept === 'error') {
          S.toast('Week ' + (r.week != null ? r.week : nextWeek) +
            ' staged but auto-accept did not run — review it above.', 'info');
        } else {
          S.toast(
            'Week ' + (r.week != null ? r.week : nextWeek) + ' is PROPOSED — review it above' +
            (held.length ? ' (' + held.length + ' faction' + (held.length === 1 ? '' : 's') + ' held)' : ''),
            'ok'
          );
        }
        S.rerenderIfCurrent('sim'); // re-render: the proposal panel leads the tab
      }).catch(() => {});
    });
    root.appendChild(runPanel);

    /* goal review: the sim proposes goal updates from the ledger */
    if (factions.length) goalsCard(root);

    /* forecast: the real engine projected forward on scratch */
    if (factions.length) forecastCard(root);

    /* record-a-session: history + reviewed structured effects (Phase 3) */
    if (factions.length) sessionPanel(root, factions, sim.projects);
  };
}());
