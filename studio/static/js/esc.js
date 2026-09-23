/* esc.js — the one HTML-escaping rule for everything the server (or an LLM)
   sent. Loaded before core.js; core.js and every tab file use StudioEsc.esc.
   Doubles as a CommonJS module so studio/tests/js/test_esc.cjs can test it
   from Node without a browser. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.StudioEsc = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }
  return { esc: esc };
});
