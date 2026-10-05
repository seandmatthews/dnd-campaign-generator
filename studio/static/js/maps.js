/* Campaign Studio — maps.js
   Maps tab: world map hero, cities grid, "map a burg", battlemap scenes. */
(function () {
  'use strict';
  const S = window.Studio;

  /* ----- url helpers ----- */

  function citySvgUrl(city) {
    if (typeof city.svg === 'string' && city.svg &&
        (city.svg.indexOf('/') === 0 || city.svg.indexOf('data:') === 0)) {
      return city.svg;
    }
    const slug = city.slug || '';
    return S.fileUrl('maps/cities/' + encodeURIComponent(slug) + '/' + encodeURIComponent(slug) + '.svg');
  }

  function siteImgUrl(site) {
    if (typeof site.svg === 'string' && site.svg && site.svg.indexOf('/') === 0) return site.svg;
    if (typeof site.png === 'string' && site.png && site.png.indexOf('/') === 0) return site.png;
    const slug = site.slug || '';
    return S.fileUrl('maps/sites/' + encodeURIComponent(slug) + '/' + encodeURIComponent(slug) + '.png');
  }

  /* ----- cards ----- */

  function artifactCard(label, url) {
    const card = S.el(
      '<figure class="card artifact-card">' +
        '<div class="card-media"></div>' +
        '<figcaption class="card-title">' + S.esc(label) + '</figcaption>' +
      '</figure>'
    );
    const media = card.querySelector('.card-media');
    const img = new Image();
    img.alt = label;
    // NOTE: no loading='lazy' — a detached lazy image never intersects the
    // viewport, so it never loads, so it is never appended. Eager or nothing.
    img.onload = () => {
      media.innerHTML = '';
      media.appendChild(img);
    };
    img.onerror = () => {
      media.innerHTML = '';
      media.appendChild(S.emptyState('Map file missing'));
    };
    img.src = url;
    return card;
  }

  /* ----- log tail for forms ----- */

  function makeLogBox(wrap) {
    return S.makeLogBox(wrap.querySelector('.log-tail'));
  }

  /* ----- burg form ----- */

  function burgForm(burgs) {
    const wrap = S.el(
      '<div class="form-block">' +
        '<form class="inline-form">' +
          '<label class="field"><span class="field-label">Burg</span>' +
            '<select class="input burg-select"></select></label>' +
          '<label class="field"><span class="field-label">Attach to place</span>' +
            '<input type="text" class="input burg-place" ' +
              'placeholder="entity page the city map belongs to"></label>' +
          '<button type="submit" class="btn btn-primary">Map it</button>' +
        '</form>' +
        '<p class="muted form-note">Headless map generator: a few minutes, ' +
          'log streaming below. The place defaults to the burg itself; edit it to ' +
          'attach the map to another entity page. The map&rsquo;s companion note ' +
          'links the two (&ldquo;everything about this place&rdquo; finds it).</p>' +
        '<pre class="log-tail hidden"></pre>' +
        '<div class="form-error"></div>' +
      '</div>'
    );
    const select = wrap.querySelector('.burg-select');
    const placeIn = wrap.querySelector('.burg-place');
    const sorted = burgs.slice().sort((a, b) =>
      String(a.name || '').localeCompare(String(b.name || '')));
    // the option VALUE is the burg's dump id when geography carries one: a
    // generated world can hold two same-named burgs in different nations
    // (seed 6161: two Stormbergs), and a name submission is ambiguous
    // there — gen-city resolves by id exactly (ticket 41). The human
    // label stays the option TEXT. Id-less burgs (hand-written fixtures)
    // keep the name as the value.
    const nameOf = new Map();
    sorted.forEach((b) => {
      const bnation = b.nation || b.country || 'unknown';
      const bp = (b.population != null && b.population !== '') ? b.population
        : (b.population_thousands != null ? b.population_thousands * 1000 : null);
      const bpop = (bp != null && bp !== '') ? S.fmtInt(bp) : '?';
      const bport = b.port ? ' [port]' : '';
      const name = b.name || '';
      const value = (b.burg_id != null && b.burg_id !== '')
        ? String(b.burg_id) : name;
      nameOf.set(value, name);
      const opt = S.el(
        '<option value="' + S.esc(value) + '">' +
          S.esc(name || '(unnamed)') + ' — ' + S.esc(bnation) +
          ', pop ' + S.esc(bpop) + S.esc(bport) +
        '</option>'
      );
      select.appendChild(opt);
    });
    // the place follows the burg until the DM types their own
    let placeDirty = false;
    const syncPlace = () => {
      if (!placeDirty) placeIn.value = nameOf.get(select.value) || '';
    };
    select.addEventListener('change', syncPlace);
    placeIn.addEventListener('input', () => { placeDirty = true; });
    syncPlace();

    const form = wrap.querySelector('form');
    const btn = form.querySelector('button[type="submit"]');
    const errBox = wrap.querySelector('.form-error');
    const log = makeLogBox(wrap);

    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const burg = select.value;
      if (!burg || btn.disabled) return;
      const place = placeIn.value.trim();
      S.runJob({
        path: S.campUrl('/maps/cities'),
        body: place ? { burg: burg, place: place } : { burg: burg },
        btn: btn, errBox: errBox, log: log,
        doneMsg: (r) => 'City map ready: ' +
          ((r && r.slug) ? r.slug : (nameOf.get(burg) || burg)),
        failLabel: 'City mapping failed'
      }).then(() => refreshCities()).catch(() => refreshCities());
    });
    return wrap;
  }

  /* ----- sites form ----- */

  function siteForm() {
    const wrap = S.el(
      '<div class="form-block">' +
        '<form class="inline-form">' +
          '<label class="field"><span class="field-label">Kind</span>' +
            '<select class="input site-kind">' +
              '<option value="dungeon">dungeon</option>' +
              '<option value="cave">cave</option>' +
              '<option value="camp">camp</option>' +
            '</select></label>' +
          '<label class="field"><span class="field-label">Name</span>' +
            '<input type="text" class="input site-name" placeholder="e.g. The Drowned Crypt"></label>' +
          '<label class="field"><span class="field-label">Attach to place (optional)</span>' +
            '<input type="text" class="input site-place" ' +
              'placeholder="e.g. the Docks — a new name becomes a dangling link"></label>' +
          '<button type="submit" class="btn btn-primary">Generate scene</button>' +
        '</form>' +
        '<p class="muted form-note">battlemap gen &rarr; render &rarr; PNG (sharp) &rarr; UVTT wrap: ' +
          'a headless run, a few minutes. The .uvtt drops straight into Foundry (walls, doors and lights live).</p>' +
        '<div class="site-result"></div>' +
        '<pre class="log-tail hidden"></pre>' +
        '<div class="form-error"></div>' +
      '</div>'
    );
    const form = wrap.querySelector('form');
    const kindSel = wrap.querySelector('.site-kind');
    const nameIn = wrap.querySelector('.site-name');
    const btn = form.querySelector('button[type="submit"]');
    const errBox = wrap.querySelector('.form-error');
    const resultBox = wrap.querySelector('.site-result');
    const log = makeLogBox(wrap);

    // the fresh scene: preview + downloads for the artifact set
    function showSiteResult(result) {
      resultBox.innerHTML = '';
      if (!result || !result.svg) return;
      const label = result.label || result.slug || 'scene';
      const fig = S.el(
        '<figure class="card artifact-card site-result-card">' +
          '<div class="card-media"></div>' +
          '<figcaption class="card-title">' + S.esc(label) + '</figcaption>' +
          '<div class="site-result-links"></div>' +
        '</figure>'
      );
      const media = fig.querySelector('.card-media');
      const img = new Image();
      img.alt = label + ' scene';
      img.onload = () => { media.innerHTML = ''; media.appendChild(img); };
      img.onerror = () => { media.innerHTML = ''; media.appendChild(S.emptyState('scene.svg missing')); };
      img.src = result.svg;
      const links = fig.querySelector('.site-result-links');
      [['uvtt', ' (Foundry UVTT)'], ['png', ' (battlemap image)'], ['scene', ' (scene.json)']]
        .forEach(([key, suffix]) => {
          const url = result[key];
          if (!url) return;
          const fname = String(url).split('/').pop();
          links.appendChild(S.el(
            '<a class="result-link" href="' + S.esc(url) + '" download="' + S.esc(fname) + '">' +
              S.esc(fname) + S.esc(suffix) + '</a> '
          ));
        });
      resultBox.appendChild(fig);
    }

    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const kind = kindSel.value;
      const nm = nameIn.value.trim();
      if (!nm || btn.disabled) return;
      const place = wrap.querySelector('.site-place').value.trim();
      S.runJob({
        path: S.campUrl('/maps/sites'),
        body: place ? { kind: kind, name: nm, place: place }
                    : { kind: kind, name: nm },
        btn: btn, errBox: errBox, log: log,
        doneMsg: (r) => 'Scene ready: ' + ((r && r.slug) ? r.slug : nm),
        failLabel: 'Scene generation failed'
      }).then((result) => {
        showSiteResult(result);
        return refreshCities();
      }).catch(() => refreshCities());
    });
    return wrap;
  }

  /* ----- cities + sites grid ----- */

  let grid = null;
  let sitesGrid = null;
  let cityErrSlot = null;
  let siteErrSlot = null;

  function renderSites(data) {
    if (!sitesGrid) return;
    sitesGrid.innerHTML = '';
    const sites = (data && Array.isArray(data.sites)) ? data.sites : [];
    if (!sites.length) {
      sitesGrid.appendChild(S.emptyState('No battlemap scenes yet — generate one above.'));
      return;
    }
    sites.forEach((s) => {
      const label = s.label || s.name || s.slug || '(scene)';
      sitesGrid.appendChild(artifactCard(label, siteImgUrl(s)));
    });
  }

  // The Maps tab's durable failed-job surface (ticket 41): a failed map
  // job used to die with the page — a ~4 s toast and nothing else — so a
  // reload showed an empty grid with no hint anything had gone wrong.
  // The section error renders from the campaign-jobs snapshot's
  // last_finished record (newest finished job of the kind): status error
  // shows what failed and its error tail, any later finished run of that
  // kind replaces it. Advisory: the jobs read failing never blocks the
  // grids.
  function renderSectionError(slot, job, what) {
    if (!slot) return;
    slot.innerHTML = '';
    if (!job || job.status !== 'error') return;
    const detail = String(job.error || '').trim();
    slot.appendChild(S.el(
      '<div class="form-error section-error">' +
        '<strong>The last ' + S.esc(what) + ' failed.</strong> ' +
        (detail ? S.esc(detail) + ' ' : '') +
        'Fix what it names (a retry hint is often in the message) and run ' +
        'the form again — a finished run clears this note.' +
      '</div>'
    ));
  }

  async function refreshCities() {
    if (!grid) return;
    const myGrid = grid, mySites = sitesGrid;
    myGrid.innerHTML = '';
    myGrid.appendChild(S.loading('Loading city maps…'));
    let data;
    try {
      data = await S.api(S.campUrl('/maps'));
    } catch (err) {
      myGrid.innerHTML = '';
      myGrid.appendChild(S.errorPanel(err, 'The maps listing could not be loaded.'));
      if (mySites) {
        mySites.innerHTML = '';
        mySites.appendChild(S.emptyState('Sites unavailable — the maps listing failed.'));
      }
      return;
    }
    // the fetch outlived this view (campaign switched, tab re-rendered):
    // rendering the old campaign's world into the new grid is a wrong-world
    // map set — drop the stale response instead
    if (myGrid !== grid || mySites !== sitesGrid) return;
    myGrid.innerHTML = '';
    const cities = (data && Array.isArray(data.cities)) ? data.cities : [];
    if (!cities.length) {
      myGrid.appendChild(S.emptyState('No city maps yet — map a burg below and it will appear here.'));
    }
    cities.forEach((c) => {
      const label = c.label || c.slug || '(city)';
      myGrid.appendChild(artifactCard(label, citySvgUrl(c)));
    });
    renderSites(data);
    let lastFinished = null;
    try {
      lastFinished = (await S.api(S.campUrl('/jobs'))).last_finished;
    } catch (_) { /* advisory — the grids above still render */ }
    if (myGrid !== grid || mySites !== sitesGrid) return;
    renderSectionError(cityErrSlot,
      lastFinished && lastFinished['map-city'], 'city map');
    renderSectionError(siteErrSlot,
      lastFinished && lastFinished['map-site'], 'battlemap scene');
  }

  /* ----- generate a world (seed -> headless FMG -> import -> workspace) ----- */

  // Steering surface for one generation — mirrors studio/routers/maps.py.
  // Anything left blank is not sent: the seed keeps rolling it.
  const CULTURE_SETS = [
    ['', 'random (per seed)'],
    ['world', 'world — mixed, no theme'],
    ['european', 'european — historic name pool'],
    ['oriental', 'oriental — historic name pool'],
    ['english', 'english — historic name pool'],
    ['antique', 'antique — historic name pool'],
    ['highFantasy', 'highFantasy — elves, dwarves, giants (habitat-homed)'],
    ['darkFantasy', 'darkFantasy — grim variant (habitat-homed)'],
    ['random', 'random — surprise set'],
  ];
  const HEIGHTMAP_TEMPLATES = ['volcano', 'highIsland', 'lowIsland', 'continents',
    'archipelago', 'atoll', 'mediterranean', 'peninsula', 'pangea', 'isthmus',
    'shattered', 'taklamakan', 'oldWorld', 'fractious'];
  // [class, server key, label, hint, min] — min mirrors the server's
  // _OPTION_RANGES (maps.py) so a legal-looking form cannot 422 at submit
  const STEER_FIELDS = [
    ['opt-cultures', 'cultures', 'Cultures', '1-30', 1],
    ['opt-states', 'states', 'States', '1-60', 1],
    ['opt-burgs', 'burgs', 'Burgs', '50-5000', 50],
    ['opt-precip', 'precipitation', 'Precipitation', '5-500', 5],
    ['opt-lat', 'latitude', 'Latitude band', '0 far north .. 100 far south', 0],
  ];

  function steerControlsHtml() {
    const sets = CULTURE_SETS.map(([v, label]) =>
      `<option value="${v}">${label}</option>`).join('');
    const templates = ['<option value="">random (per seed)</option>']
      .concat(HEIGHTMAP_TEMPLATES.map((t) => `<option value="${t}">${t}</option>`))
      .join('');
    const nums = STEER_FIELDS.map(([cls, , label, range, min]) =>
      `<label class="field"><span class="field-label">${label}</span>` +
      `<input type="number" class="input ${cls}" min="${min}" placeholder="${range}"></label>`).join('');
    const temps = [['opt-equator', 'equator'], ['opt-north', 'north_pole'], ['opt-south', 'south_pole']]
      .map(([cls, label]) =>
        `<label class="field"><span class="field-label">${label} &deg;C</span>` +
        `<input type="number" class="input ${cls}" placeholder="&plusmn;60"></label>`).join('');
    return (
      `<div class="steer-grid">` +
        `<label class="field"><span class="field-label">Culture set</span>` +
          `<select class="input opt-culture-set">${sets}</select></label>` +
        `<label class="field"><span class="field-label">Heightmap</span>` +
          `<select class="input opt-template">${templates}</select></label>` +
        nums + temps +
      `</div>`);
  }

  function collectSteerOptions(wrap) {
    const val = (sel) => {
      const el = wrap.querySelector('.' + sel);
      return el ? String(el.value).trim() : '';
    };
    const num = (sel) => {
      const v = val(sel);
      if (v === '') return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    const opts = {};
    if (val('opt-culture-set')) opts.culture_set = val('opt-culture-set');
    if (val('opt-template')) opts.template = val('opt-template');
    STEER_FIELDS.forEach(([cls, key]) => {
      const v = num(cls);
      if (v !== null) opts[key] = v;
    });
    const temps = {};
    [['opt-equator', 'equator'], ['opt-north', 'north_pole'], ['opt-south', 'south_pole']]
      .forEach(([cls, key]) => {
        const v = num(cls);
        if (v !== null) temps[key] = v;
      });
    if (Object.keys(temps).length) opts.temperature_c = temps;
    return Object.keys(opts).length ? opts : null;
  }

  function fillSteerOptions(wrap, options) {
    const o = options || {};
    const setVal = (sel, v) => {
      const el = wrap.querySelector('.' + sel);
      if (el) el.value = v == null ? '' : String(v);
    };
    setVal('opt-culture-set', o.culture_set);
    setVal('opt-template', o.template);
    STEER_FIELDS.forEach(([cls, key]) => setVal(cls, o[key]));
    const temps = o.temperature_c || {};
    setVal('opt-equator', temps.equator);
    setVal('opt-north', temps.north_pole);
    setVal('opt-south', temps.south_pole);
  }

  function worldGenForm(geoData) {
    const hasGeography = !!geoData;
    // the seed that made the CURRENT world — geography.yaml records it,
    // but nothing surfaced it after the input cleared on reload (39e)
    const currentSeed = (geoData && geoData.meta && geoData.meta.seed)
      ? '<p class="muted form-note current-seed-note">This world came from ' +
        'seed <strong>' + S.esc(String(geoData.meta.seed)) + '</strong> &mdash; ' +
        'the seed drove every roll; the same seed regenerates the same map.</p>'
      : '';
    const wrap = S.el(
      '<div class="form-block">' +
        '<form class="inline-form">' +
          '<label class="field"><span class="field-label">Seed</span>' +
            '<input type="text" class="input world-seed" placeholder="e.g. 12345"></label>' +
          '<button type="submit" class="btn btn-primary world-gen-btn"' +
            (hasGeography ? ' disabled' : '') + '>Generate world</button>' +
          (hasGeography
            ? '<label class="field field-narrow force-field">' +
              '<input type="checkbox" class="force-toggle"> ' +
              '<span class="field-label">force (replace the world)</span></label>'
            : '') +
        '</form>' +
        '<details class="world-steer">' +
          '<summary>World character (optional &mdash; steers this generation only)</summary>' +
          '<div class="steer-premise">' +
            '<label class="field"><span class="field-label">Describe the world you want ' +
              '&mdash; the model fills the options below</span>' +
              '<textarea class="input world-premise" rows="2" ' +
              'placeholder="e.g. A frozen northern world: dwarven holds in the icy mountains, troll warbands across the glacier, warmer kingdoms in the south"></textarea></label>' +
            '<button type="button" class="btn world-suggest-btn">Suggest options</button>' +
          '</div>' +
          steerControlsHtml() +
          '<p class="muted form-note">Blank fields stay random per seed. highFantasy/' +
          'darkFantasy culture sets home each race to its natural habitat &mdash; pair one with a ' +
          'cold north pole and a low latitude band for dwarven mountains and troll glaciers.</p>' +
        '</details>' +
        (hasGeography
          ? '<p class="muted form-note">This campaign already has a generated world. Regenerating ' +
            'replaces <code>world/source.*</code> and <code>world/geography.yaml</code> &mdash; existing ' +
            'city maps and lore keep naming the old world. Without force, the server refuses (409).</p>' +
            currentSeed
          : '<p class="muted form-note">Type a seed, get a world: this runs a ' +
            'headless map generator — expect a few minutes, its log streaming ' +
            'below. FMG runs the genesis pipeline, ' +
            'import_fmg distills nations and settlements, and the world map renders from the dump.</p>') +
        '<pre class="log-tail hidden"></pre>' +
        '<div class="form-error"></div>' +
      '</div>'
    );
    const form = wrap.querySelector('form');
    const seedIn = wrap.querySelector('.world-seed');
    const btn = wrap.querySelector('.world-gen-btn');
    const errBox = wrap.querySelector('.form-error');
    const log = makeLogBox(wrap);
    const forceTgl = wrap.querySelector('.force-toggle');
    if (forceTgl) {
      forceTgl.addEventListener('change', () => {
        btn.disabled = !forceTgl.checked;
      });
    }

    const suggestBtn = wrap.querySelector('.world-suggest-btn');
    suggestBtn.addEventListener('click', async () => {
      const premise = wrap.querySelector('.world-premise').value.trim();
      if (!premise) {
        S.toast('Describe the world you want first', 'error');
        return;
      }
      suggestBtn.disabled = true;
      try {
        const resp = await S.api(S.campUrl('/world-options'), {
          method: 'POST',
          body: { premise: premise }
        });
        fillSteerOptions(wrap, resp && resp.options);
        S.toast('Options suggested — review them, then generate', 'ok');
      } catch (err) {
        S.toast('Suggest failed' + (err && err.error ? ': ' + err.error : ''), 'error');
      } finally {
        suggestBtn.disabled = false;
      }
    });

    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      if (btn.disabled) return; // force-gated regen: the checkbox note explains it
      const seed = seedIn.value.trim();
      if (!seed) {
        // an empty seed used to return silently — the click read exactly
        // like a dead button (the silent-failure class, 2026-10-03)
        errBox.textContent = 'Type a seed first — the seed drives every roll '
          + 'of the generator, so an empty field has nothing to run.';
        S.toast('Type a seed first — the form needs a seed.', 'error');
        return;
      }
      const body = { seed: seed, force: forceTgl ? !!forceTgl.checked : false };
      const options = collectSteerOptions(wrap);
      if (options) body.options = options;
      // reenable: false — without force checked the button must stay dead
      // after one run (the replace-the-world escape hatch is the checkbox)
      S.runJob({
        path: S.campUrl('/world'),
        body: body,
        btn: btn, errBox: errBox, log: log, reenable: false,
        doneMsg: (r) => {
          r = r || {};
          return 'World generated: ' + (r.nations != null ? r.nations + ' nations, ' : '') +
            (r.settlements != null ? r.settlements + ' settlements' : 'done');
        },
        failLabel: 'World generation failed'
      }).then(() => {
        S.showTab('maps'); // full re-render: hero, burg list, geography
      }).catch(() => {
        btn.disabled = forceTgl ? !forceTgl.checked : false;
      });
    });
    return wrap;
  }

  /* ----- render world map (only when map.svg is missing) ----- */

  function renderMapPanel() {
    const wrap = S.el(
      '<div class="form-block">' +
        '<div class="inline-form">' +
          '<button type="button" class="btn render-map-btn">Render world map</button>' +
          '<span class="muted form-note">Renders world/map.svg from the existing world dump ' +
          '(read-only &mdash; geography is never regenerated).</span>' +
        '</div>' +
        '<pre class="log-tail hidden"></pre>' +
        '<div class="form-error"></div>' +
      '</div>'
    );
    const btn = wrap.querySelector('.render-map-btn');
    const errBox = wrap.querySelector('.form-error');
    const log = makeLogBox(wrap);
    btn.addEventListener('click', async () => {
      if (btn.disabled) return;
      S.runJob({
        path: S.campUrl('/maps/world-map'),
        body: {}, btn: btn, errBox: errBox, log: log
      }).then(() => {
        S.toast('World map rendered', 'ok');
        S.showTab('maps'); // hero re-checks world/map.svg on render
      }).catch(() => {});
    });
    return wrap;
  }

  /* ----- tab renderer ----- */

  S.tabs.maps = async function (container) {
    container.innerHTML = '';
    if (!S.state.campaign) {
      container.appendChild(S.noCampaign());
      return;
    }

    const root = S.el('<div class="tab-root"></div>');
    container.appendChild(root);

    // Geography drives three things: the world card's state, the render
    // panel's visibility, and the burg picker. 404 = no geography yet.
    // Declared up front: the hero's onerror can fire during the awaits below.
    let geoData = null;

    // Hero world map — hidden gracefully if world/map.svg is missing (404);
    // a missing map + existing geography reveals the "Render world map" panel.
    let heroFailed = false;
    const hero = S.el(
      '<section class="panel panel-hero hidden">' +
        '<h3 class="panel-title">World map</h3>' +
        '<div class="worldmap-holder"></div>' +
      '</section>'
    );
    const holder = hero.querySelector('.worldmap-holder');
    const heroImg = new Image();
    heroImg.alt = 'World map';
    heroImg.className = 'worldmap-img';
    heroImg.onload = () => { hero.classList.remove('hidden'); };
    heroImg.onerror = () => {
      heroFailed = true;
      hero.classList.add('hidden');
      maybeShowRenderPanel();
    };
    heroImg.src = S.fileUrl('world/map.svg');
    holder.appendChild(heroImg);
    root.appendChild(hero);

    // "Render world map" — inserted between hero and cities only when the
    // hero image failed AND geography exists (no world -> nothing to render).
    let renderPanelInserted = false;
    function maybeShowRenderPanel() {
      if (renderPanelInserted || !heroFailed || !geoData) return;
      const panel = S.el(
        '<section class="panel"><h3 class="panel-title">Render world map</h3>' +
          '<div class="form-slot"></div></section>'
      );
      panel.querySelector('.form-slot').appendChild(renderMapPanel());
      root.insertBefore(panel, citiesPanel);
      renderPanelInserted = true;
    }

    // Generate a world card (adapts once geography presence is known).
    const worldPanel = S.el(
      '<section class="panel"><h3 class="panel-title">Generate a world</h3>' +
        '<div class="form-slot"><div class="loading">Checking the world&hellip;</div></div>' +
      '</section>'
    );
    const worldSlot = worldPanel.querySelector('.form-slot');
    root.appendChild(worldPanel);

    // Cities grid.
    const citiesPanel = S.el(
      '<section class="panel">' +
        '<h3 class="panel-title">Cities</h3>' +
        '<div class="section-error-slot"></div>' +
        '<div class="artifact-grid cities-grid"></div>' +
      '</section>'
    );
    grid = citiesPanel.querySelector('.cities-grid');
    cityErrSlot = citiesPanel.querySelector('.section-error-slot');
    root.appendChild(citiesPanel);

    // Battlemap scenes: grid + form (the route may 501 until a later wave).
    const sitesPanel = S.el(
      '<section class="panel">' +
        '<h3 class="panel-title">Battlemap scenes</h3>' +
        '<div class="form-slot"></div>' +
        '<div class="section-error-slot"></div>' +
        '<div class="artifact-grid sites-grid"></div>' +
      '</section>'
    );
    sitesGrid = sitesPanel.querySelector('.sites-grid');
    siteErrSlot = sitesPanel.querySelector('.section-error-slot');
    sitesPanel.querySelector('.form-slot').appendChild(siteForm());
    root.appendChild(sitesPanel);

    // Map a burg form (burgs come from geography).
    const burgPanel = S.el(
      '<section class="panel">' +
        '<h3 class="panel-title">Map a burg</h3>' +
        '<p class="panel-note">A burg is a town on the world map — this turns it ' +
          'into a street-level city map.</p>' +
        '<div class="form-slot"><div class="loading">Loading burgs&hellip;</div></div>' +
      '</section>'
    );
    const burgSlot = burgPanel.querySelector('.form-slot');
    root.appendChild(burgPanel);

    await refreshCities();

    try {
      geoData = await S.api(S.campUrl('/geography'));
    } catch (err) {
      if (!err || err.status !== 404) {
        burgSlot.innerHTML = '';
        burgSlot.appendChild(S.errorPanel(err, "Burgs come from geography; it could not be loaded."));
      }
    }

    worldSlot.innerHTML = '';
    worldSlot.appendChild(worldGenForm(geoData));

    burgSlot.innerHTML = '';
    const burgs = geoData
      ? (((Array.isArray(geoData.burgs)) ? geoData.burgs
          : (Array.isArray(geoData.major_settlements)) ? geoData.major_settlements : []))
      : [];
    if (!burgs.length) {
      burgSlot.appendChild(S.emptyState(geoData
        ? "No burgs found in this campaign's geography."
        : 'No geography yet — generate a world above and burgs appear here.'));
    } else {
      burgSlot.appendChild(burgForm(burgs));
    }

    maybeShowRenderPanel();
  };
}());
