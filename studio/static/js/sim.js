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
        // has no factions yet) — this is an empty state, not an error.
        root.appendChild(S.el(
          '<section class="panel"><h3 class="panel-title">No simulation in this campaign yet</h3>' +
            '<p class="muted">The faction sandbox needs <code>simulation/state.yaml</code> — ' +
            'a premise and the factions that want things from each other — before weeks can run. ' +
            'Maps and World Building above work without it. ' +
            'To bootstrap one, see <code>agentic-simulator/AGENTS.md</code>; ' +
            'a create-a-sandbox flow for fresh campaigns is a planned feature.</p>' +
          '</section>'
        ));
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

    /* run week N — the expensive button */
    const runPanel = S.el(
      '<section class="panel run-panel">' +
        '<h3 class="panel-title">Advance the world</h3>' +
        '<div class="inline-form">' +
          '<button type="button" class="btn btn-primary run-week-btn">Run week ' + S.esc(String(nextWeek)) + '</button>' +
          '<span class="muted form-note">Runs the live scheduler, factions, rumors and dice — and writes the chronicle.</span>' +
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
        'This runs a real week — it spends API credits and advances the canon clock.'
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
          'Week ' + (r.week != null ? r.week : nextWeek) + ' is done' +
          (held.length ? ' (' + held.length + ' faction' + (held.length === 1 ? '' : 's') + ' held)' : ''),
          'ok'
        );
        S.showTab('sim'); // re-render: new clock, new chronicle, held banner
      }).catch((err) => {
        runErr.innerHTML = '';
        runErr.appendChild(S.errorPanel(err));
        S.toast('Week run failed' + (err && err.error ? ': ' + err.error : ''), 'error');
        runBtn.disabled = false;
      });
    });
    root.appendChild(runPanel);
  };
}());
