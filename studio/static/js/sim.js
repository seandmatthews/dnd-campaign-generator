/* Campaign Studio — sim.js
   Agent Simulation tab: week clock, faction cards, rumors board, events,
   chronicle reader, views inspector, why inspector, "run week" button. */
(function () {
  'use strict';
  const S = window.Studio;

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

  function factionCard(f) {
    const name = f.name || f.id || '(faction)';
    const kind = f.kind || '';
    const secret = f.secret
      ? '<details class="secret"><summary>Secret</summary><p>' + S.esc(f.secret) + '</p></details>'
      : '';
    return S.el(
      '<article class="card faction-card">' +
        '<header class="faction-head">' +
          '<h4 class="faction-name">' + S.esc(name) + '</h4>' +
          (kind ? '<span class="pill pill-kind">' + S.esc(kind) + '</span>' : '') +
        '</header>' +
        '<div class="chip-row">' + factionChips(f) + '</div>' +
        dispositionTable(f.disposition) +
        secret +
        knowsList(f.knows) +
      '</article>'
    );
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

  /* the ledger suffix: week actions are recorded as
     "<action> — succeeded (contested vs mint), margin +0" — the outcome is
     bookkeeping about the week, not part of the rumor people repeat. Strip
     it for DISPLAY only; the raw state keeps the full text. */
  function displayClaim(text) {
    return String(text == null ? '' : text).replace(/ — (succeeded|failed)\b.*$/, '');
  }

  function rumorItem(r) {
    const rawClaim = r.claim || r.text || r.rumor || '(no claim)';
    const claim = displayClaim(rawClaim) || String(rawClaim);
    const rawFid = r.fidelity || r.grade || r.state || '';
    const grade = fidelityGrade(rawFid);
    const born = (r.born_week != null) ? r.born_week
      : ((r.born != null) ? r.born : ((r.week != null) ? r.week : null));
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
    return S.el(
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

  /* ----- tab renderer ----- */

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

  function createSimulationPanel(root) {
    const panel = S.el(
      '<section class="panel">' +
        '<h3 class="panel-title">Create a simulation</h3>' +
        '<p class="panel-note">A sandbox is a premise and 2-5 factions that want ' +
          'things from each other. Nothing here calls the LLM or costs credits — ' +
          'this is the DM&rsquo;s setup; a week run is where generation happens.</p>' +
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
          '<div class="sand-factions"></div>' +
          '<div class="inline-form">' +
            '<button type="button" class="btn sand-add">+ Add a faction</button>' +
            '<button type="button" class="btn btn-primary sand-create">Create simulation</button>' +
          '</div>' +
          '<div class="form-error"></div>' +
          '<p class="muted form-note">Prefer hand-editing? This form writes ' +
            '<code>simulation/state.yaml</code> — you can edit that file directly instead.</p>' +
        '</div>' +
      '</section>'
    );
    const host = panel.querySelector('.sand-factions');
    const errBox = panel.querySelector('.form-error');
    const addBtn = panel.querySelector('.sand-add');
    const createBtn = panel.querySelector('.sand-create');

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
        return {
          name: row.querySelector('.f-name').value.trim(),
          kind: row.querySelector('.f-kind').value.trim(),
          goal: row.querySelector('.f-goal').value.trim(),
          resources: resources
        };
      });
      const body = {
        premise: panel.querySelector('.sand-premise').value.trim(),
        season: panel.querySelector('.sand-season').value.trim() || 'early spring',
        factions: factions,
        force: false
      };
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
        S.toast('Simulation created — factions are live below. Run week 1 when ready.', 'ok');
        S.showTab('sim'); // re-render: the real sim view with the run button
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

  function proposalPanel(root, pid) {
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
      const title = isSession
        ? 'Session changes — ' + S.esc(String((p.run_record && p.run_record.source) || 'session'))
        : 'Proposed week ' + S.esc(String(p.proposed_week)) + ' &mdash; not canon yet';
      const subtitle = isSession
        ? '<strong>The world has not changed yet.</strong> These are the recorded ' +
          'session facts and the reviewed effects they justify. Accepting applies ' +
          'them exactly once; discarding changes nothing.'
        : '<strong>The world has not advanced.</strong> ' +
          'This is a proposal generated from the current state (' +
          'base week ' + S.esc(String(p.base_week)) + '). Accepting publishes ' +
          'the whole week exactly once; discarding changes nothing.';

      panel.innerHTML = '';
      panel.appendChild(S.el(
        '<h3 class="panel-title">' + title + '</h3>' +
        '<p class="panel-note">' + subtitle + '</p>' +
        '<table class="mini-table proposal-diff"><tbody>' + diffRows(diff) + '</tbody></table>' +
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
              rums.map((r) => '<li class="rumor-entry"><span class="rumor-claim">' + S.esc(r.claim || '') + '</span></li>').join('') +
            '</ul></details>'
          : '') +
        (chron
          ? '<div class="proposal-chronicle"><h5 class="findings-title">Chronicle (proposed)</h5><article class="chronicle"></article></div>'
          : '') +
        '<div class="inline-form proposal-actions">' +
          '<button type="button" class="btn btn-primary proposal-accept">' +
            (isSession ? 'Accept session changes — apply exactly once'
                       : 'Accept week ' + S.esc(String(p.proposed_week)) + ' — publish everything') + '</button>' +
          '<button type="button" class="btn btn-danger proposal-discard">Discard</button>' +
          '<span class="muted form-note">Accept publishes as one step; discard keeps the audit receipt only.</span>' +
        '</div>' +
        '<div class="proposal-error"></div>'
      ));
      if (chron) panel.querySelector('.chronicle').innerHTML = S.md(chron.markdown || '');

      const errBox = panel.querySelector('.proposal-error');
      const acceptBtn = panel.querySelector('.proposal-accept');
      const discardBtn = panel.querySelector('.proposal-discard');

      acceptBtn.addEventListener('click', async () => {
        if (acceptBtn.disabled) return;
        const ok = await S.confirmRun(
          isSession
            ? 'Accepting applies the recorded session history and its reviewed ' +
              'effects to the live state — exactly once.'
            : 'Accepting publishes the whole proposed week into the campaign — ' +
              'clock, resources, chronicle, timeline — exactly once.'
        );
        if (!ok) return;
        acceptBtn.disabled = true; discardBtn.disabled = true;
        errBox.innerHTML = '';
        try {
          const r = await S.api(S.campUrl('/simulation/proposal/' +
            encodeURIComponent(p.proposal_id) + '/accept'), { method: 'POST' });
          S.toast(isSession
            ? 'Session changes are canon — applied exactly once.'
            : 'Week ' + (r.week != null ? r.week : p.proposed_week) +
              ' is canon — accepted exactly once.', 'ok');
          if (r.snapshot && (r.snapshot.state === 'failed' ||
              r.snapshot.state === 'unavailable')) {
            S.toast('Published, but the history snapshot ' + r.snapshot.state +
              ' — retry from the campaign summary.', 'error');
          }
          S.showTab('sim');
        } catch (err) {
          errBox.innerHTML = '';
          errBox.appendChild(S.errorPanel(err));
          acceptBtn.disabled = false; discardBtn.disabled = false;
        }
      });

      discardBtn.addEventListener('click', async () => {
        if (discardBtn.disabled) return;
        const ok = await S.confirmRun(
          'Discard this proposed week? Nothing was ever written to the ' +
          'campaign; the proposal stays as an audit receipt.'
        );
        if (!ok) return;
        acceptBtn.disabled = true; discardBtn.disabled = true;
        errBox.innerHTML = '';
        try {
          await S.api(S.campUrl('/simulation/proposal/' +
            encodeURIComponent(p.proposal_id) + '/discard'), { method: 'POST' });
          S.toast('Proposal discarded — the campaign never changed.', 'ok');
          S.showTab('sim');
        } catch (err) {
          errBox.innerHTML = '';
          errBox.appendChild(S.errorPanel(err));
          acceptBtn.disabled = false; discardBtn.disabled = false;
        }
      });
    }).catch(() => { panel.remove(); });
  }

  /* ----- record-a-session form (Phase 3: history + structured effects) ----- */

  function effectRow(factions) {
    const opts = factions.map((f) =>
      '<option value="' + S.esc(String(f.id)) + '">' + S.esc(f.name || f.id) + '</option>').join('');
    return S.el(
      '<div class="sand-faction session-effect">' +
        '<div class="inline-form">' +
          '<label class="field field-narrow"><span class="field-label">Faction</span>' +
            '<select class="input e-faction">' + opts + '</select></label>' +
          '<label class="field field-narrow"><span class="field-label">Kind of change</span>' +
            '<select class="input e-type"><option value="set_resource">resource</option>' +
              '<option value="set_field">fact (goal/kind/secret)</option></select></label>' +
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

  function sessionPanel(root, factions) {
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
      const row = effectRow(factions);
      row.querySelector('.e-remove').addEventListener('click', () => row.remove());
      const typeSel = row.querySelector('.e-type');
      const resWrap = row.querySelector('.e-res-wrap');
      const fieldWrap = row.querySelector('.e-field-wrap');
      const LEVELS = ['none', 'low', 'medium', 'high'];
      function sync() {
        const isField = typeSel.value === 'set_field';
        resWrap.classList.toggle('hidden', isField);
        fieldWrap.classList.toggle('hidden', !isField);
        const afterEl = row.querySelector('.e-after');
        if (isField && afterEl.tagName === 'SELECT') {
          const input = document.createElement('input');
          input.type = 'text'; input.className = 'input e-after';
          input.placeholder = 'the new value';
          afterEl.replaceWith(input);
        } else if (!isField && afterEl.tagName === 'INPUT') {
          const sel = document.createElement('select');
          sel.className = 'input e-after';
          sel.innerHTML = LEVELS.map((l) => '<option>' + l + '</option>').join('');
          afterEl.replaceWith(sel);
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
      const effects = Array.from(host.querySelectorAll('.session-effect')).map((row) => {
        const isField = row.querySelector('.e-type').value === 'set_field';
        const base = {
          type: isField ? 'set_field' : 'set_resource',
          faction_id: row.querySelector('.e-faction').value,
          after: isField ? row.querySelector('.e-after').value
                         : row.querySelector('.e-after').value,
          reason: row.querySelector('.e-reason').value.trim()
        };
        if (isField) base.field = row.querySelector('.e-field').value;
        else base.resource = row.querySelector('.e-res').value;
        const was = row.querySelector('.e-was').value.trim();
        if (was) base.expected_before = was;
        return base;
      });
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
        S.showTab('sim');
      } catch (err) {
        errBox.innerHTML = '';
        errBox.appendChild(S.errorPanel(err));
        btn.disabled = false;
      }
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
            'faction sandbox below — then <em>Run week</em> simulates what happens ' +
            'next, one week at a time, and writes the chronicle.</p>' +
          '</section>'
        ));
        createSimulationPanel(root);
        return;
      }
      root.appendChild(S.errorPanel(err, 'The simulation state could not be loaded.'));
      return;
    }
    root.innerHTML = '';

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
    const rumors = (sim && Array.isArray(sim.rumors)) ? sim.rumors : [];
    const events = (sim && Array.isArray(sim.events)) ? sim.events : [];
    const chronicles = (sim && Array.isArray(sim.chronicles)) ? sim.chronicles : [];
    const viewIds = (sim && Array.isArray(sim.views_available) && sim.views_available.length)
      ? sim.views_available
      : factions.map((f) => f.id).filter(Boolean);

    /* the pending proposal leads the tab: nothing else has moved */
    if (sim && sim.pending_proposal) {
      proposalPanel(root, sim.pending_proposal);
    }

    /* header strip: week clock + season + premise */
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
      const gridEl = S.el('<div class="faction-grid"></div>');
      factions.forEach((f) => gridEl.appendChild(factionCard(f)));
      factionBody.appendChild(gridEl);
    }
    root.appendChild(factionPanel);

    /* rumors board */
    const rumorPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Rumors</h3><div class="panel-body"></div></section>'
    );
    const rumorBody = rumorPanel.querySelector('.panel-body');
    if (!rumors.length) {
      rumorBody.appendChild(S.emptyState('No rumors in circulation.'));
    } else {
      const list = S.el('<ul class="rumor-list"></ul>');
      rumors.forEach((r) => list.appendChild(rumorItem(r)));
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
          '<span class="muted form-note">Runs the live scheduler, factions and dice against scratch data and stages a proposal — the campaign changes only when you accept it.</span>' +
        '</div>' +
        '<pre class="log-tail hidden run-log"></pre>' +
        '<div class="run-error"></div>' +
      '</section>'
    );
    const runBtn = runPanel.querySelector('.run-week-btn');
    const runLog = runPanel.querySelector('.run-log');
    const runErr = runPanel.querySelector('.run-error');
    runBtn.addEventListener('click', async () => {
      if (runBtn.disabled) return;
      const ok = await S.confirmRun(
        'This runs a real week against scratch data — it spends API credits. ' +
        'The campaign changes only if you accept the proposal afterwards.'
      );
      if (!ok) return;
      runBtn.disabled = true;
      runErr.innerHTML = '';
      runLog.classList.remove('hidden');
      runLog.textContent = '';
      (async () => {
        const resp = await S.api(S.campUrl('/simulation/week'), {
          method: 'POST',
          body: { confirm: true }
        });
        if (!resp || !resp.job_id) throw { status: 0, error: 'Server returned no job_id' };
        return S.pollJob(resp.job_id, (line) => {
          runLog.textContent += String(line == null ? '' : line) + '\n';
          runLog.scrollTop = runLog.scrollHeight;
        });
      })().then((result) => {
        const r = result || {};
        const held = Array.isArray(r.held) ? r.held : [];
        if (held.length) S.state.lastHeld = held; // banner on the re-render
        S.toast(
          'Week ' + (r.week != null ? r.week : nextWeek) + ' is PROPOSED — review it above' +
          (held.length ? ' (' + held.length + ' faction' + (held.length === 1 ? '' : 's') + ' held)' : ''),
          'ok'
        );
        S.showTab('sim'); // re-render: the proposal panel leads the tab
      }).catch((err) => {
        runErr.innerHTML = '';
        runErr.appendChild(S.errorPanel(err));
        S.toast('Week run failed' + (err && err.error ? ': ' + err.error : ''), 'error');
        runBtn.disabled = false;
      });
    });
    root.appendChild(runPanel);

    /* record-a-session: history + reviewed structured effects (Phase 3) */
    if (factions.length) sessionPanel(root, factions);
  };
}());
