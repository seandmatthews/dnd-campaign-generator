/* handoff.js — the rumor → knowledge-form handoff ("Tell the party").
   A rumor row builds this payload, parks it on Studio.state.factPrefill and
   switches to the Knowledge tab, whose record form consumes it. The payload
   is data only: submission itself goes through the unchanged record → reveal
   flow (recording a public fact and revealing it stay separate decisions).
   Doubles as a CommonJS module so studio/tests/js/test_handoff.cjs can test
   the payload shape from Node without a browser. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.StudioHandoff = factory();
})(typeof self !== 'undefined' ? self : this, function () {

  /* The ledger suffix ("… — succeeded (contested vs mint), margin +0") is
     bookkeeping about the week, not part of the rumor people repeat — the
     same strip the rumors board uses for display. Everything else is the
     rumor verbatim; the DM shapes the wording before recording if they want. */
  function displayClaim(text) {
    return String(text == null ? '' : text).replace(/ — (succeeded|failed)\b.*$/, '');
  }

  /* A rumor's week: the engine names it born_week; hand-built rows may use
     born or week. Absent stays absent — nothing is invented. */
  function rumorWeek(r) {
    if (!r) return null;
    if (r.born_week != null) return r.born_week;
    if (r.born != null) return r.born;
    if (r.week != null) return r.week;
    return null;
  }

  /* The chronicle file a week's rumors carry as provenance — resolved from
     the campaign's real chronicle list ([{week, title, file}]), never
     fabricated: no chronicle for that week means no source_file (''), and a
     fact recorded then simply has no staleness trail. */
  function chronicleFileFor(chronicles, week) {
    if (!Array.isArray(chronicles) || week == null || week === '') return '';
    const hit = chronicles.find((c) => c && c.file &&
      Number(c.week) === Number(week));
    return hit ? String(hit.file) : '';
  }

  /* The prefill payload: {text, visibility, source_file, week}. text is the
     claim people repeat; visibility is public (the party-facing default —
     the reveal stays its own step); source_file points at the rumor's
     chronicle when one exists; week rides along for the record. Returns
     null when there is no claim to tell. */
  function rumorHandoff(r, chronicles) {
    const raw = r && (r.claim || r.text || r.rumor) || '';
    const text = displayClaim(raw).trim();
    if (!text) return null;
    const week = rumorWeek(r);
    const n = Number(week);
    return {
      text: text,
      visibility: 'public',
      source_file: chronicleFileFor(chronicles, week),
      week: (week != null && week !== '' && Number.isFinite(n)) ? n : null
    };
  }

  return {
    displayClaim: displayClaim,
    rumorWeek: rumorWeek,
    chronicleFileFor: chronicleFileFor,
    rumorHandoff: rumorHandoff
  };
});
