/* minidom.cjs — a zero-dependency DOM subset just wide enough to LOAD the
   real studio front-end (esc.js + core.js + a tab script) under node and
   drive renders and clicks against a stubbed fetch. It exists so the
   failure-surface behaviors (ticket 30) can be DOM-tested, not just
   source-scraped: what these tests assert is what the shipped IIFEs
   actually do to the document.

   Supported (everything the world/sessions/core flows touch):
     - stack HTML parser (nested tags, void elements, entities in text)
     - querySelector/querySelectorAll: tag, .class, #id, [attr="val"],
       compounds of those, and descendant chains of them
     - appendChild/remove/insertAdjacentElement/closest/isConnected
     - classList, get/setAttribute/removeAttribute, className
     - addEventListener + fire(), textContent/innerHTML, firstElementChild
     - document.createElement('template') with .content.firstElementChild
       (what S.el builds on), getElementById, the app's page skeleton

   Not a browser: no layout, no events bubbling, no <form> semantics.
   Call boot() once per process; newPage() rebuilds the document between
   test cases (the studio scripts resolve `document` per call, so swapping
   the global after load is safe). */

'use strict';
const path = require('path');

const JS_DIR = path.join(__dirname, '..', '..', 'static', 'js');

/* ------------------------------------------------------------- nodes --- */

const VOID = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img',
  'input', 'link', 'meta', 'param', 'source', 'track', 'wbr']);
const BOOL_ATTRS = new Set(['checked', 'disabled', 'selected', 'readonly',
  'multiple', 'hidden', 'open']);

const NAMED_ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  ndash: '\u2013', mdash: '\u2014', hellip: '\u2026', middot: '\u00b7',
  rarr: '\u2192', larr: '\u2190', uarr: '\u2191', darr: '\u2193',
  rsquo: '\u2019', lsquo: '\u2018', ldquo: '\u201c', rdquo: '\u201d',
  times: '\u00d7', bull: '\u2022', dagger: '\u2020', copy: '\u00a9',
  deg: '\u00b0', sect: '\u00a7', para: '\u00b6'
};

function decodeEntities(text) {
  return String(text)
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) =>
      String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10)))
    .replace(/&([a-zA-Z][a-zA-Z0-9]*);/g, (m, name) =>
      Object.prototype.hasOwnProperty.call(NAMED_ENTITIES, name)
        ? NAMED_ENTITIES[name] : m);
}

class TextNode {
  constructor(text) { this.text = String(text); this.parentNode = null; }
  get textContent() { return this.text; }
  set textContent(v) { this.text = String(v == null ? '' : v); }
}

class El {
  constructor(tagName, attrs) {
    this.tagName = String(tagName || '').toLowerCase();
    this.attrs = attrs || {};
    this.childNodes = [];
    this.parentNode = null;
    this.style = {};
    this._listeners = {};
    // boolean attributes reflect into properties, like a real DOM
    BOOL_ATTRS.forEach((a) => {
      if (this.attrs[a] !== undefined) this[a] = true;
    });
  }

  get children() { return this.childNodes.filter((n) => n instanceof El); }
  get firstChild() { return this.childNodes[0] || null; }
  get firstElementChild() { return this.children[0] || null; }
  get lastElementChild() {
    const c = this.children; return c[c.length - 1] || null;
  }
  get parentElement() { return this.parentNode instanceof El ? this.parentNode : null; }
  get isConnected() {
    let n = this;
    while (n) { if (n._isDocument) return true; n = n.parentNode; }
    return false;
  }

  get className() { return this.attrs.class || ''; }
  set className(v) { this.attrs.class = String(v); }

  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = String(v); }

  get textContent() {
    return this.childNodes.map((n) => n.textContent).join('');
  }
  set textContent(v) {
    this.childNodes.slice().forEach((n) => this.removeChild(n));
    if (v != null && String(v) !== '') this.appendChild(new TextNode(v));
  }

  get innerHTML() { // serialization, for the rare reader
    return this.childNodes.map((n) => {
      if (n instanceof TextNode) return n.text;
      const attrs = Object.keys(n.attrs).map((k) =>
        ' ' + k + '="' + String(n.attrs[k]).replace(/"/g, '&quot;') + '"').join('');
      return '<' + n.tagName + attrs + '>' + n.innerHTML + '</' + n.tagName + '>';
    }).join('');
  }
  set innerHTML(html) {
    this.childNodes.slice().forEach((n) => this.removeChild(n));
    if (String(html || '').trim() !== '') parseInto(this, String(html));
  }

  appendChild(node) {
    if (node.parentNode) node.parentNode.removeChild(node);
    node.parentNode = this;
    this.childNodes.push(node);
    return node;
  }
  removeChild(node) {
    const i = this.childNodes.indexOf(node);
    if (i !== -1) this.childNodes.splice(i, 1);
    node.parentNode = null;
    return node;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }

  insertAdjacentElement(where, node) {
    const p = this.parentNode;
    if (!p) return node;
    const i = p.childNodes.indexOf(this);
    if (where === 'afterbegin') { this.insertBefore(node, this.firstChild); return node; }
    if (where === 'beforeend') { this.appendChild(node); return node; }
    if (where === 'beforebegin') { p.insertBefore(node, this); return node; }
    p.childNodes.splice(i + 1, 0, node);
    node.parentNode = p;
    return node;
  }
  insertBefore(node, ref) {
    if (node.parentNode) node.parentNode.removeChild(node);
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i === -1) { this.childNodes.push(node); } else {
      this.childNodes.splice(i, 0, node);
    }
    node.parentNode = this;
    return node;
  }

  get classList() {
    const self = this;
    const list = () => self.className.split(/\s+/).filter(Boolean);
    return {
      add(...cls) {
        const cur = new Set(list());
        cls.forEach((c) => cur.add(c));
        self.className = Array.from(cur).join(' ');
      },
      remove(...cls) {
        const cur = list().filter((c) => cls.indexOf(c) === -1);
        self.className = cur.join(' ');
      },
      toggle(c, force) {
        const has = list().indexOf(c) !== -1;
        const want = force === undefined ? !has : !!force;
        if (want) this.add(c); else this.remove(c);
        return want;
      },
      contains(c) { return list().indexOf(c) !== -1; }
    };
  }

  getAttribute(name) {
    const v = this.attrs[name];
    return v === undefined ? null : v;
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; }
  hasAttribute(name) { return this.attrs[name] !== undefined; }

  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  removeEventListener(type, fn) {
    const l = this._listeners[type] || [];
    const i = l.indexOf(fn);
    if (i !== -1) l.splice(i, 1);
  }
  dispatchEvent(ev) {
    ev = ev || {};
    ev.target = ev.target || this;
    if (typeof ev.preventDefault !== 'function') {
      ev.preventDefault = function () {};
    }
    (this._listeners[ev.type] || []).slice().forEach((fn) => fn(ev));
    return true;
  }
  fire(type, ev) { return this.dispatchEvent(Object.assign({ type: type }, ev || {})); }
  click() { return this.fire('click'); }
  focus() {}
  blur() {}

  closest(selector) {
    let n = this;
    while (n) {
      if (n instanceof El && matchesCompound(n, selector)) return n;
      n = n.parentNode;
    }
    return null;
  }

  querySelector(selector) { return queryAll(this, selector)[0] || null; }
  querySelectorAll(selector) { return queryAll(this, selector); }
}

class TemplateEl extends El {
  constructor() {
    super('#template');
    this.content = new El('#fragment');
    this.content._isFragmentRoot = true;
  }
  set innerHTML(html) {
    this.content.childNodes = [];
    if (String(html || '').trim() !== '') parseInto(this.content, String(html));
  }
  get innerHTML() { return this.content.innerHTML; }
}

/* ------------------------------------------------------------ parser --- */

const TAG_RE = /<!--[\s\S]*?-->|<\/?([a-zA-Z][a-zA-Z0-9-]*)((?:[^>"']|"[^"]*"|'[^']*')*)>|([^<]+)/g;
const ATTR_RE = /([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:\s*=\s*("([^"]*)"|'([^']*)'|[^\s"'>]+))?/g;

function parseAttrs(rest) {
  const attrs = {};
  let m;
  ATTR_RE.lastIndex = 0;
  while ((m = ATTR_RE.exec(rest))) {
    const name = m[1].toLowerCase();
    const value = m[3] !== undefined ? m[3]
      : m[4] !== undefined ? m[4]
        : m[2] !== undefined ? m[2] : '';
    attrs[name] = decodeEntities(value);
  }
  return attrs;
}

function parseInto(parent, html) {
  const stack = [parent];
  TAG_RE.lastIndex = 0;
  let m;
  while ((m = TAG_RE.exec(html))) {
    const top = stack[stack.length - 1];
    if (m[1]) {
      const tag = m[1].toLowerCase();
      if (m[0][1] === '/') { // closing tag: pop to the matching opener
        for (let i = stack.length - 1; i > 0; i--) {
          if (stack[i].tagName === tag) { stack.length = i; break; }
        }
      } else {
        const node = new El(tag, parseAttrs(m[2] || ''));
        top.appendChild(node);
        if (!VOID.has(tag) && !/\/>$/.test(m[0])) stack.push(node);
      }
    } else if (m[3] != null && m[3] !== '') {
      top.appendChild(new TextNode(decodeEntities(m[3])));
    }
  }
}

/* ---------------------------------------------------------- selectors --- */

function parseCompound(sel) {
  // 'tag.class#id[attr="v"]' -> {tag?, classes[], id?, attrs{}}
  const out = { tag: null, classes: [], id: null, attrs: {} };
  const s = String(sel);
  const tm = s.match(/^([a-zA-Z][a-zA-Z0-9-]*)/);
  if (tm) out.tag = tm[1].toLowerCase();
  let rest = s.slice(tm ? tm[0].length : 0);
  let m;
  const attrRe = /\[([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:~="([^"]*)"|="([^"]*)")?\]/g;
  while ((m = attrRe.exec(rest))) {
    out.attrs[m[1].toLowerCase()] = m[2] !== undefined ? m[2]
      : m[3] !== undefined ? m[3] : null;
  }
  rest = rest.replace(attrRe, '');
  const clsRe = /\.([a-zA-Z0-9_-]+)/g;
  while ((m = clsRe.exec(rest))) out.classes.push(m[1]);
  rest = rest.replace(clsRe, '');
  const idm = rest.match(/#([a-zA-Z0-9_-]+)/);
  if (idm) out.id = idm[1];
  return out;
}

function matchesCompound(node, compound) {
  const c = typeof compound === 'string' ? parseCompound(compound) : compound;
  if (!(node instanceof El) || node._isFragmentRoot) return false;
  if (c.tag && node.tagName !== c.tag) return false;
  if (c.id && node.id !== c.id) return false;
  if (c.classes.length) {
    const have = node.className.split(/\s+/);
    for (const cls of c.classes) if (have.indexOf(cls) === -1) return false;
  }
  for (const key of Object.keys(c.attrs)) {
    const want = c.attrs[key];
    const got = node.attrs[key];
    if (want === null) { if (got === undefined) return false; }
    else if (String(got) !== want) return false;
  }
  return true;
}

function descendants(node) {
  const out = [];
  (node.childNodes || []).forEach((n) => {
    if (!(n instanceof El)) return;
    if (!n._isFragmentRoot) out.push(n);
    out.push(...descendants(n));
  });
  return out;
}

function queryAll(root, selector) {
  const parts = String(selector).trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return [];
  const last = parseCompound(parts[parts.length - 1]);
  return descendants(root).filter((node) => {
    if (!matchesCompound(node, last)) return false;
    // earlier parts must match some ancestor chain, in order
    let idx = parts.length - 2;
    let n = node.parentNode;
    while (idx >= 0 && n) {
      if (n instanceof El && matchesCompound(n, parts[idx])) idx -= 1;
      n = n.parentNode;
    }
    return idx < 0;
  });
}

/* ----------------------------------------------------------- document --- */

class Document extends El {
  constructor() {
    super('#document');
    this._isDocument = true;
    this.documentElement = new El('html');
    this.appendChild(this.documentElement);
    this.body = new El('body');
    this.documentElement.appendChild(this.body);
  }
  createElement(tag) {
    return tag.toLowerCase() === 'template' ? new TemplateEl() : new El(tag);
  }
  getElementById(id) {
    return descendants(this).filter((n) => n.id === id)[0] || null;
  }
  querySelector(selector) {
    if (/^[#.][\w-]+$/.test(selector)) {
      // id lookups must find nodes anywhere, including outside <body>
      if (selector[0] === '#') {
        const byId = this.getElementById(selector.slice(1));
        if (byId) return byId;
      }
    }
    return super.querySelector(selector);
  }
}

/* The app page skeleton the front-end expects (static/index.html): the
   header, the tab bar, one section per tab, the toast host, modal root. */
function newPage(tabNames) {
  const doc = new Document();
  const header = new El('header', { class: 'app-header' });
  doc.body.appendChild(header);
  const bar = new El('nav', { class: 'tab-bar' });
  (tabNames || ['maps', 'world', 'sim', 'review', 'sessions', 'knowledge'])
    .forEach((t) => {
      const b = new El('button', { class: 'tab', 'data-tab': t });
      b.textContent = t;
      bar.appendChild(b);
    });
  doc.body.appendChild(bar);
  (tabNames || ['maps', 'world', 'sim', 'review', 'sessions', 'knowledge'])
    .forEach((t) => {
      const sec = new El('section',
        { class: 'tab-section', 'data-tab': t });
      doc.body.appendChild(sec);
    });
  doc.body.appendChild(new El('div', { id: 'toasts' }));
  doc.body.appendChild(new El('div', { id: 'modal-root' }));
  return doc;
}

/* --------------------------------------------------------------- boot --- */

function boot(tabs) {
  global.window = {};
  global.document = newPage();
  global.localStorage = memoryStorage();
  global.sessionStorage = memoryStorage();
  try { global.navigator = {}; } catch (_) {
    // node 22+ ships a getter-only navigator global — leave it as is
    Object.defineProperty(global, 'navigator',
      { value: {}, configurable: true });
  }
  // core.js's toasts setTimeout 4200 ms — keep them from holding a test
  // process open after the assertions finish
  const realSetTimeout = global.setTimeout;
  global.setTimeout = (fn, ms, ...args) => {
    const t = realSetTimeout(fn, ms, ...args);
    if (t && typeof t.unref === 'function') t.unref();
    return t;
  };

  window.StudioEsc = require(path.join(JS_DIR, 'esc.js'));
  require(path.join(JS_DIR, 'core.js'));
  (tabs || []).forEach((name) => require(path.join(JS_DIR, name + '.js')));
  return { window: window, document: document, S: window.Studio };
}

function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); }
  };
}

/* ---------------------------------------------------------- fetch stub --- */

/* installFetch(routes): routes is [{method, match(url), reply(req)},
   evaluated in order; reply returns the JSON body, or {__status, error}
   for a failure, or a promise of either. Every request is logged. */
function installFetch(routes) {
  const log = [];
  global.fetch = (url, init) => {
    init = init || {};
    const entry = { url: String(url), method: init.method || 'GET',
      body: init.body ? JSON.parse(init.body) : null };
    log.push(entry);
    const route = (routes || []).find((r) =>
      (r.method || 'GET') === entry.method &&
      (typeof r.match === 'function' ? r.match(entry.url)
        : entry.url.indexOf(r.match) !== -1));
    // an unmatched request answers 404, loudly: a mis-routed fetch in a
    // test must not read as a silent empty success
    const reply = route ? route.reply(entry)
      : { __status: 404,
          error: 'no test route for ' + entry.method + ' ' + entry.url };
    return Promise.resolve(reply).then((r) => {
      if (r && r.__reject) {
        return Promise.reject(new Error(r.__reject));
      }
      const status = (r && r.__status) || 200;
      const ok = status >= 200 && status < 300;
      const body = (r && r.__status) ? JSON.stringify({ error: r.error || 'error' })
        : JSON.stringify(r === null ? {} : r);
      return { ok: ok, status: status,
        text: () => Promise.resolve(body) };
    });
  };
  return log;
}

/* let pending promise chains (renders, handlers) run out */
async function flush(rounds) {
  for (let i = 0; i < (rounds || 6); i++) {
    await new Promise((resolve) => realTimeout(resolve, 0));
  }
}
const realTimeout = setTimeout;

module.exports = { boot, newPage, installFetch, flush, El, TextNode };
