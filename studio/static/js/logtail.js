/* logtail.js — consuming the server's rotating job-log window.
   jobs.py keeps only the last LOG_CAP (40) lines: once the window fills,
   its length pins at the cap while content keeps rotating. An index alone
   cannot tell "nothing new" from "rotated under me" — pollJob froze at
   line 40 for the rest of a 30-minute week run (found 2026-09-23). The
   merge anchors on the last emitted TEXT: when the anchor slid, resume
   just past it; when it slid out entirely (more than LOG_CAP lines between
   polls) replay the window — the window is all-new then anyway.
   Loaded after esc.js, before core.js. Doubles as a CommonJS module so
   studio/tests/js/test_logtail.cjs can test it from Node without a browser. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.StudioLogTail = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  // cursor: {seen: lines already consumed from the current window,
  //          last:  text of the last consumed line, or null}
  // returns {lines: the newly arrived texts, cursor: the updated cursor}
  function mergeLogTail(tail, cursor) {
    var lines = [];
    var seen = (cursor && cursor.seen) || 0;
    var last = cursor ? cursor.last : null;
    if (!Array.isArray(tail)) tail = [];
    if (seen > tail.length ||
        (last !== null && seen > 0 && tail[seen - 1] !== last)) {
      var idx = last !== null ? tail.indexOf(last) : -1;
      seen = idx === -1 ? 0 : idx + 1;
    }
    while (seen < tail.length) {
      last = tail[seen];
      lines.push(last);
      seen += 1;
    }
    return { lines: lines, cursor: { seen: seen, last: last } };
  }
  return { mergeLogTail: mergeLogTail };
});
