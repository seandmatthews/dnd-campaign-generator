/* Campaign Studio — places.js
   Places tab: the place-centered campaign view. One page per place answers
   "what is here, what changed, and what needs my attention?" — identity and
   provenance first (explicit / suggested / provisional), then maps, people,
   factions, projects, canon events, facts with visibility badges, pending
   proposals (PROPOSED, NOT CANON), forecasts (HYPOTHETICAL), and mentions.
   Everything is a deterministic server read — no LLM anywhere here. */
(function () {
  'use strict';
  const S = window.Studio;

  // Set by Studio.openPlace (contextual "Open place" links on other tabs);
  // consumed by the next render of this tab.
  let pendingDetail = null;

  S.openPlace = function (placeId) {
    pendingDetail = String(placeId || '');
    S.showTab('places');
  };

  function pill(cls, text, title) {
    return '<span class="pill ' + cls + '"' +
      (title ? ' title="' + S.esc(title) + '"' : '') + '>' + S.esc(text) +
      '</span>';
  }

  function statusPills(p) {
    if (p.conflict) return pill('pill-stale', 'ID conflict');
    if (p.explicit) return pill('pill-ok', 'linked');
    if (p.association === 'suggested') return pill('pill-when', 'suggested join');
    return pill('pill-muted', 'provisional');
  }

  /* ================================ list ================================ */

  async function listPanel(root) {
    const panel = S.el('<section class="panel places-list-panel"></section>');
    panel.appendChild(S.el('<h3>Places</h3>'));
    let doc;
    try {
      doc = await S.api(S.campUrl('/places'));
    } catch (err) {
      panel.appendChild(S.errorPanel(err));
      root.appendChild(panel);
      return;
    }
    const intro = S.el('<p class="muted">Every place this campaign knows: ' +
      'lore pages, simulated places, and map attachments. "Provisional" ' +
      'records have no explicit identity yet — linking one makes events, ' +
      'facts, and project joins precise. Nothing is merged by name alone.</p>');
    panel.appendChild(intro);

    const table = S.el('<div class="place-rows"></div>');
    if (!doc.places.length) {
      table.appendChild(S.emptyState('No places yet — create a lore page ' +
        'with kind: place, or a simulation sandbox with a city.'));
    }
    for (const p of doc.places) {
      const row = S.el(
        '<div class="place-row card">' +
          '<div class="place-row-main">' +
            '<button type="button" class="linklike place-open"><strong>' +
              S.esc(p.name) + '</strong></button> ' +
            '<span class="muted place-id">' + S.esc(p.id) + '</span> ' +
            statusPills(p) +
            (p.maps ? ' ' + pill('pill-muted', p.maps + ' map' + (p.maps > 1 ? 's' : '')) : '') +
          '</div>' +
          '<div class="place-row-side"></div>' +
        '</div>');
      row.querySelector('.place-open').addEventListener('click',
        () => S.openPlace(p.id));
      const side = row.querySelector('.place-row-side');
      if (!p.explicit && !p.conflict && p.lore_file) {
        const btn = S.el('<button type="button" class="btn place-link">Link place records</button>');
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          try {
            const res = await S.api(S.campUrl('/places/' +
              encodeURIComponent(p.id) + '/link'), { method: 'POST', body: {} });
            S.toast('Linked as ' + (res.linked || p.id) +
              (res.minted ? ' (new durable id minted)' : '') + '.', 'ok');
            S.showTab('places');
          } catch (err) {
            side.textContent = '';
            side.appendChild(S.errorPanel(err));
            btn.disabled = false;
          }
        });
        side.appendChild(btn);
      }
      table.appendChild(row);
    }
    panel.appendChild(table);

    if (doc.suggested.length) {
      const sug = S.el('<section class="panel-inner"><h4>Suggested joins</h4>' +
        '<p class="muted">Same name on a lore page and a simulated place. ' +
        'Confirming writes the id onto the page — one small reviewed write; ' +
        'nothing else changes.</p></section>');
      for (const s of doc.suggested) {
        const row = S.el('<div class="suggest-row">' +
          '<code>' + S.esc(s.lore_file) + '</code> ↔ <code>' + S.esc(s.sim_id) +
          '</code></div>');
        const btn = S.el('<button type="button" class="btn suggest-link">Link</button>');
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          try {
            await S.api(S.campUrl('/places/' + encodeURIComponent(s.id) +
              '/link'), { method: 'POST', body: {} });
            S.toast('Linked ' + s.name + '.', 'ok');
            S.showTab('places');
          } catch (err) {
            row.appendChild(S.errorPanel(err));
            btn.disabled = false;
          }
        });
        row.appendChild(btn);
        sug.appendChild(row);
      }
      panel.appendChild(sug);
    }
    if (doc.conflicts.length) {
      const conf = S.el('<section class="panel-inner"><h4 class="error-text">Identity conflicts</h4></section>');
      for (const c of doc.conflicts) {
        conf.appendChild(S.el('<p class="muted">place_id <code>' +
          S.esc(c.id) + '</code> is claimed by ' + c.files.length +
          ' pages (' + c.files.map((f) => '<code>' + S.esc(f) + '</code>')
            .join(', ') + ') — fix the duplicate before this page renders.</p>'));
      }
      panel.appendChild(conf);
    }
    if (doc.findings.length) {
      const fin = S.el('<details class="panel-inner place-findings"><summary>Findings (' +
        doc.findings.length + ')</summary></details>');
      for (const f of doc.findings) {
        fin.appendChild(S.el('<p class="muted">[' + S.esc(f.level) + '] ' +
          S.esc(f.message) + '</p>'));
      }
      panel.appendChild(fin);
    }
    root.appendChild(panel);
  }

  /* =============================== detail =============================== */

  function sectionList(title, items, emptyText) {
    const sec = S.el('<section class="panel-inner"><h4>' + S.esc(title) +
      '</h4></section>');
    if (!items.length) {
      sec.appendChild(S.el('<p class="muted">' + S.esc(emptyText) + '</p>'));
      return sec;
    }
    const ul = S.el('<ul class="place-items"></ul>');
    for (const li of items) ul.appendChild(S.el('<li>' + li + '</li>'));
    sec.appendChild(ul);
    return sec;
  }

  function eventLine(ev) {
    return '<strong>week ' + S.esc(ev.week == null ? '?' : ev.week) +
      '</strong> [' + S.esc(ev.actor || 'world') + ']: ' + S.esc(ev.text || '');
  }

  async function detailPanel(root, placeId) {
    const panel = S.el('<section class="panel place-detail"></section>');
    let v;
    try {
      v = await S.api(S.campUrl('/places/' + encodeURIComponent(placeId)));
    } catch (err) {
      panel.appendChild(S.errorPanel(err));
      const back = S.el('<button type="button" class="btn">← All places</button>');
      back.addEventListener('click', () => { pendingDetail = null; S.showTab('places'); });
      panel.prepend(back);
      root.appendChild(panel);
      return;
    }
    const p = v.place;

    const head = S.el(
      '<header class="place-head">' +
        '<button type="button" class="btn place-back">← All places</button>' +
        '<h3 class="place-title">' + S.esc(p.name) + '</h3>' +
        '<div class="place-meta">' +
          '<code>' + S.esc(p.id) + '</code> ' +
          (p.explicit ? pill('pill-ok', 'linked') :
            (p.association === 'suggested' ? pill('pill-when', 'suggested join') :
              pill('pill-muted', 'provisional'))) +
          (p.parent_place_id ? ' <span class="muted">child of ' +
            S.esc(p.parent_place_id) + '</span>' : '') +
        '</div>' +
      '</header>');
    head.querySelector('.place-back').addEventListener('click',
      () => { pendingDetail = null; S.showTab('places'); });
    panel.appendChild(head);

    if (p.lore && p.lore.body) {
      const lore = S.el('<div class="panel-inner place-lore"></div>');
      lore.appendChild(S.el('<p>' + S.esc(p.lore.body) + '</p>'));
      panel.appendChild(lore);
    }

    const mapsSec = sectionList('Maps', v.maps.map((m) =>
      (m.preferred ? '<strong>' : '') + S.esc(m.scale) + ' — <code>' +
      S.esc(m.dir) + '</code>' + (m.preferred ? '</strong> (preferred)' : '') +
      (m.place_id ? '' : ' <span class="muted">(no explicit id)</span>')),
      'No maps attached yet — generate one on the Maps tab with this place ' +
      'as the attach-to-place value, or attach an existing map.');
    if (v.maps.length > 1 && p.lore) {
      const sel = S.el('<label class="field"><span class="field-label">Preferred map</span>' +
        '<select class="input place-pref-map"><option value="">(none)</option></select></label>');
      const select = sel.querySelector('select');
      for (const m of v.maps) {
        const opt = S.el('<option value="' + S.esc(m.dir) + '">' +
          S.esc(m.slug) + ' (' + S.esc(m.scale) + ')</option>');
        if (m.preferred) opt.selected = true;
        select.appendChild(opt);
      }
      select.addEventListener('change', async () => {
        try {
          await S.api(S.campUrl('/places/' + encodeURIComponent(p.id)), {
            method: 'PATCH',
            body: { preferred_map: select.value || null }
          });
          S.toast('Preferred map saved.', 'ok');
          S.openPlace(p.id);
        } catch (err) { S.toast(err.error || 'Save failed.', 'error'); }
      });
      mapsSec.appendChild(sel);
    }
    panel.appendChild(mapsSec);

    const fsec = sectionList('Factions', v.factions.map((f) =>
      S.esc(f.name || f.id) + ' — ' + S.esc(f.relationship) +
      (f.explicit ? '' : ' <span class="muted">(default: the primary place)</span>')),
      'No factions recorded here.');
    panel.appendChild(fsec);

    panel.appendChild(sectionList('People & organizations', v.entities.map((e) =>
      S.esc(e.name) + ' <span class="muted">(' + S.esc(e.file) + ')</span> — ' +
      S.esc(e.relationship)),
      'No residents or present parties marked. Add resides_at / present_at ' +
      'frontmatter on an entity page (or via the API) to attach one.'));

    panel.appendChild(sectionList('Projects', v.projects.map((pr) =>
      '<strong>' + S.esc(pr.title || pr.id) + '</strong> (' +
      S.esc(pr.faction_id || '?') + '): ' + S.esc(pr.progress) + '/' +
      S.esc(pr.target) + ' — ' + S.esc(pr.status)),
      'No projects operate here.'));

    // events: recent by default, all-history on demand
    const evSec = S.el('<section class="panel-inner"><h4>Recent events <span class="muted">(canon — explicitly located here)</span></h4></section>');
    if (!v.events.length && !v.counts.events.total) {
      evSec.appendChild(S.el('<p class="muted">No events carry this place\'s ' +
        'id yet. Events are never located by inference — record them with ' +
        'place_ids (session events) or attach a locator via an association.</p>'));
    }
    for (const ev of v.events) {
      evSec.appendChild(S.el('<p class="place-event">' + eventLine(ev) + '</p>'));
    }
    if (v.counts.events.more) {
      const more = S.el('<button type="button" class="btn place-all-events">Show all history (' +
        v.counts.events.total + ' total)</button>');
      more.addEventListener('click', async () => {
        more.disabled = true;
        try {
          const full = await S.api(S.campUrl('/places/' +
            encodeURIComponent(p.id) + '/events?window=all&limit=200'));
          evSec.querySelectorAll('.place-event').forEach((n) => n.remove());
          for (const ev of full.events) {
            evSec.appendChild(S.el('<p class="place-event">' + eventLine(ev) + '</p>'));
          }
          more.remove();
        } catch (err) { S.toast(err.error || 'Load failed.', 'error'); more.disabled = false; }
      });
      evSec.appendChild(more);
    }
    panel.appendChild(evSec);

    panel.appendChild(sectionList('Facts', v.knowledge.map((f) => {
      let tag = f.visibility === 'public' ? 'public in-world' :
        (Array.isArray(f.visibility) ? 'visible to: ' + f.visibility.join(', ')
          : 'PRIVATE (DM only)');
      if (f.disclosed) tag += ' — DISCLOSED to party';
      return S.esc(f.text) + ' ' + pill('pill-vis-dm', tag);
    }), 'No facts about this place yet.'));

    if (v.pending.length) {
      panel.appendChild(sectionList('Pending — PROPOSED, NOT CANON', v.pending.map((it) =>
        S.esc(it.text || it.title || '') + ' <span class="muted">(proposal ' +
        S.esc(it.proposal_id) + ', ' + S.esc(it.proposal_kind) + ')</span>'),
        ''));
    }
    if (v.forecasts.length) {
      panel.appendChild(sectionList('Forecasts — HYPOTHETICAL, never canon', v.forecasts.map((it) =>
        'week ' + S.esc(it.week == null ? '?' : it.week) + ' [' +
        S.esc(it.actor || 'world') + ']: ' + S.esc(it.text || '')), ''));
    }
    if (!v.pending.length && !v.forecasts.length) {
      panel.appendChild(S.el('<p class="muted place-clean">No pending proposals ' +
        'or active forecasts touch this place.</p>'));
    }

    panel.appendChild(sectionList('Mentions', v.mentions.map((m) =>
      '<code>' + S.esc(m.source.file) + ':' + S.esc(m.source.line) + '</code> — ' +
      S.esc(m.excerpt || m.source_name || '')),
      'No wiki-links point at this place\'s page yet.'));

    if (v.findings.length) {
      const fin = S.el('<details class="panel-inner place-findings" open><summary>Findings (' +
        v.findings.length + ')</summary></details>');
      for (const f of v.findings) {
        fin.appendChild(S.el('<p class="muted">[' + S.esc(f.level) + '] ' +
          S.esc(f.message) + '</p>'));
      }
      panel.appendChild(fin);
    }
    root.appendChild(panel);
  }

  /* ============================ tab renderer ============================ */

  S.tabs.places = async function (container) {
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }
    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);
    if (pendingDetail) {
      const id = pendingDetail;
      pendingDetail = null;
      await detailPanel(root, id);
      return;
    }
    await listPanel(root);
  };
}());
