// Highlight → Anki/Zettel content script: select text while armed to pop a
// panel (yellow → Anki cards, blue → Zettel notes, ✎ → a note that steers
// generation), click a highlight to edit its note / recolor / remove it, and
// answer the service worker's payload requests.
"use strict";

const HL_ATTR = "data-hlanki-id";
const HL_COLOR_ATTR = "data-hlanki-color";
const HL_NOTED_ATTR = "data-hlanki-noted"; // presence only: the note itself never enters the DOM
const HL_COLORS = { yellow: "#ffe95c", blue: "#a6d4ff" };
const NOTE_UNDERLINE = "2px dotted rgba(0,0,0,.55)";
// viewer.html (our PDF.js page) marks itself: there the text is a transparent
// text layer over a rendered canvas, so highlights tint it instead of
// painting over the glyphs, and the reported URL is the PDF's own.
const PDF_VIEWER = document.documentElement.hasAttribute("data-hlanki-pdf");
const HL_TINTS = { yellow: "rgba(255,233,92,.55)", blue: "rgba(166,212,255,.6)" };
let armed = false;
let nextId = 1;
// The only source of truth for what was highlighted, which color, and what
// note the reader typed. It lives here, in the content script's isolated
// world, where page scripts cannot reach it. The data-hlanki-* attributes on
// the spans are for styling and hit-testing only: a page can forge or edit
// them, and a forged one is simply not in this map, so it is never sent —
// which matters because the server treats notes as the reader's own trusted
// instructions. Every page-level handler below also ignores synthetic
// (page-dispatched) events.
const registry = new Map(); // id -> { color, comment, spans: [span, ...] }

function ownerId(span) {
  for (const [id, h] of registry) if (h.spans.includes(span)) return id;
  return null;
}

// ---- arming ----------------------------------------------------------------

chrome.runtime.sendMessage({ type: "getArmed" }).then(
  (res) => { armed = !!(res && res.armed); },
  () => {},
);

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "setArmed") {
    armed = !!msg.armed;
    if (!armed) hidePicker();
    sendResponse({ ok: true });
  } else if (msg.type === "getPayload") {
    sendResponse(collectPayload());
  } else if (msg.type === "toast") {
    showToast(msg.text, msg.kind || "info");
    sendResponse({ ok: true });
  } else if (msg.type === "trackJob") {
    trackJob(msg.job, msg.kind);
    sendResponse({ ok: true });
  } else if (msg.type === "panelEvent") {
    if (msg.panelId === PANEL_ID) onPanelEvent(msg);
    sendResponse({ ok: true });
  }
  return false;
});

// ---- job tracking ------------------------------------------------------------
// The service worker cannot wait out a long generation (Chrome kills it when a
// fetch takes > 30 s), so this script — alive as long as the tab — drives the
// polling and the worker only answers each short status request.

const JOB_POLL_MS = 2000;
let jobTimer = null;

function trackJob(job, kind) {
  clearInterval(jobTimer);
  jobTimer = setInterval(async () => {
    let res;
    try {
      res = await chrome.runtime.sendMessage({ type: "pollJob", job, kind });
    } catch (e) {
      // extension reloaded/disabled under us: this script cannot ask anymore
      clearInterval(jobTimer);
      showToast("Extension reloaded while generating — reload this page; the "
        + "server still finishes the job.", "err");
      return;
    }
    if (!res || !res.done) return;
    clearInterval(jobTimer);
    showToast(res.text, res.kind);
  }, JOB_POLL_MS);
}

// ---- highlighting ----------------------------------------------------------

function textPiecesIn(range) {
  // Snapshot [{node, start, end}] before mutating the DOM: wrapping one text
  // node never disturbs the others, but it does invalidate the live Range.
  const root = range.commonAncestorContainer;
  const scope = root.nodeType === Node.TEXT_NODE ? root.parentNode : root;
  const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
  const pieces = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!range.intersectsNode(n)) continue;
    if (!n.data.trim()) continue;
    const parent = n.parentElement;
    if (parent && (parent.closest("script,style,noscript,textarea") ||
                   parent.closest(`[${HL_ATTR}]`))) continue;
    const start = n === range.startContainer ? range.startOffset : 0;
    const end = n === range.endContainer ? range.endOffset : n.data.length;
    if (start >= end || !n.data.slice(start, end).trim()) continue;
    pieces.push({ node: n, start, end });
  }
  return pieces;
}

function paintSpan(span, color, hasNote) {
  span.setAttribute(HL_COLOR_ATTR, color);
  span.style.setProperty(
    "background-color", (PDF_VIEWER ? HL_TINTS : HL_COLORS)[color], "important");
  // In the PDF viewer the glyphs come from the canvas underneath: forcing a
  // text color here would print them a second time, out of register.
  if (!PDF_VIEWER) span.style.setProperty("color", "#1a1a1a", "important");
  span.style.setProperty("cursor", "pointer", "important");
  // A commented highlight is underlined, so it stays findable on the page.
  // Only the fact that a note exists is marked; its text stays in the
  // registry, where page scripts cannot read it.
  if (hasNote) {
    span.setAttribute(HL_NOTED_ATTR, "");
    span.style.setProperty("border-bottom", NOTE_UNDERLINE, "important");
  } else {
    span.removeAttribute(HL_NOTED_ATTR);
    span.style.removeProperty("border-bottom");
  }
}

function spansOf(id) {
  const h = registry.get(id);
  return h ? h.spans.filter((s) => s.isConnected) : [];
}

function wrapPiece(piece, id, color, comment) {
  const span = document.createElement("span");
  span.setAttribute(HL_ATTR, String(id));
  paintSpan(span, color, !!comment);
  const r = document.createRange();
  r.setStart(piece.node, piece.start);
  r.setEnd(piece.node, piece.end);
  r.surroundContents(span);
  return span;
}

function highlightRange(range, color, comment) {
  const pieces = textPiecesIn(range);
  if (!pieces.length) return false;
  const id = nextId++;
  const spans = pieces.map((piece) => wrapPiece(piece, id, color, comment));
  registry.set(id, { color, comment, spans });
  return true;
}

function unwrap(span) {
  const parent = span.parentNode;
  while (span.firstChild) parent.insertBefore(span.firstChild, span);
  parent.removeChild(span);
  parent.normalize();
}

// ---- picker panel (Web Highlighter-style) ----------------------------------
// The panel itself is panel.html, an extension page in an iframe: the page
// being read cannot see the note typed there or forge the panel's actions
// (see panel.js). This side only positions the frame and applies what the
// panel reports. Inbound to the panel: chrome.runtime.sendMessage
// {type: "panelOpen", panelId}; the panel only obeys its own id. Outbound
// from the panel: {type: "panelEvent", panelId} relayed by the service
// worker into this tab's top frame — page scripts can do neither.

// crypto.randomUUID is absent on plain http:// pages (not a secure context).
const PANEL_ID = crypto.randomUUID
  ? crypto.randomUUID()
  : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
let frameEl = null;
let panelOpen = false;
let pendingRange = null;    // create mode: the selection waiting for a color
let pendingRect = null;     // where the panel stays anchored
let editingId = null;       // edit mode: the highlight being edited

function ensurePanel() {
  if (frameEl) return frameEl;
  // The iframe sits in a closed shadow root: the page sees only an opaque
  // host element and cannot reach the frame to swap its src for a lookalike.
  const host = document.createElement("div");
  host.style.cssText = "position:absolute;top:0;left:0;width:0;height:0;overflow:visible;z-index:2147483647";
  const shadow = host.attachShadow({ mode: "closed" });
  frameEl = document.createElement("iframe");
  frameEl.src = chrome.runtime.getURL(`panel.html#${PANEL_ID}`);
  frameEl.style.cssText = [
    "position:absolute", "display:none", "border:none",
    "width:120px", "height:40px", "background:transparent", "color-scheme:light",
  ].join(";");
  shadow.appendChild(frameEl);
  document.documentElement.appendChild(host);
  return frameEl;
}

function tellPanel(mode, comment) {
  chrome.runtime.sendMessage(
    { type: "panelOpen", panelId: PANEL_ID, mode, comment }).catch(() => {});
}

function positionPanel(rect) {
  // Above the target, below it when there is no room.
  const w = frameEl.offsetWidth;
  const h = frameEl.offsetHeight;
  const left = Math.max(
    window.scrollX + 4, window.scrollX + rect.left + rect.width / 2 - w / 2);
  let top = window.scrollY + rect.top - h - 8;
  if (rect.top < h + 12) top = window.scrollY + rect.bottom + 8;
  frameEl.style.left = `${left}px`;
  frameEl.style.top = `${top}px`;
}

function showPicker(range) {
  // Create mode: swatches paint, ✎ opens the note.
  const el = ensurePanel();
  editingId = null;
  tellPanel("create", "");
  el.style.display = "block";
  panelOpen = true;
  pendingRect = range.getBoundingClientRect();
  positionPanel(pendingRect);
}

function showEditor(id) {
  // Edit mode: the note is already open, swatches recolor, ✗ removes.
  const el = ensurePanel();
  const [span] = spansOf(id);
  if (!span) return;
  pendingRange = null;
  editingId = id;
  tellPanel("edit", registry.get(id).comment);
  el.style.display = "block";
  panelOpen = true;
  pendingRect = span.getBoundingClientRect();
  positionPanel(pendingRect);
  // We hold the user activation (a real click); the panel does not, so it
  // cannot pull keyboard focus across the frame boundary by itself. Once
  // the frame is focused, the panel's own noteBox.focus() lands inside it —
  // and keystrokes stop reaching the page.
  el.focus();
}

function hidePicker() {
  if (frameEl) frameEl.style.display = "none";
  panelOpen = false;
  pendingRange = null;
  pendingRect = null;
  editingId = null;
}

// Edit mode saves the note on every keystroke; a swatch also recolors and
// then closes the panel. Create mode waits for a swatch to have anything to
// paint at all.
function saveNote(comment, color) {
  if (editingId === null) return;
  const h = registry.get(editingId);
  if (!h) return;
  h.comment = comment;
  if (color) h.color = color;
  spansOf(editingId).forEach((s) => paintSpan(s, h.color, !!h.comment));
}

function commit(color, comment) {
  if (editingId !== null) {
    saveNote(comment, color);
    hidePicker();
    return;
  }
  const range = pendingRange;
  hidePicker();
  if (!range) return;
  if (highlightRange(range, color, comment)) {
    const sel = window.getSelection();
    if (sel) sel.removeAllRanges();
  }
}

function removeEditing() {
  const spans = spansOf(editingId);
  registry.delete(editingId);
  hidePicker();
  spans.forEach(unwrap);
}

function onPanelEvent(msg) {
  if (!panelOpen && msg.event !== "size") return;
  switch (msg.event) {
    case "size":
      if (!frameEl) return;
      frameEl.style.width = `${Math.ceil(Number(msg.width) || 0) + 2}px`;
      frameEl.style.height = `${Math.ceil(Number(msg.height) || 0) + 2}px`;
      if (panelOpen && pendingRect) positionPanel(pendingRect);
      return;
    case "commit":
      if (!(msg.color in HL_COLORS)) return;
      commit(msg.color, String(msg.comment || "").trim());
      return;
    case "note":
      saveNote(String(msg.comment || "").trim());
      return;
    case "remove":
      removeEditing();
      return;
    case "close":
      hidePicker();
      return;
    default:
  }
}

document.addEventListener("mouseup", (e) => {
  if (!armed || !e.isTrusted) return;
  // Let the browser finalize the selection first.
  setTimeout(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const range = sel.getRangeAt(0);
    if (!range.toString().trim()) return;
    showPicker(range);
    pendingRange = range.cloneRange();
  }, 0);
});

// Clicks inside the panel never reach this document, so any mousedown here
// is outside it.
document.addEventListener("mousedown", (e) => {
  if (e.isTrusted) hidePicker();
}, true);

document.addEventListener("keydown", (e) => {
  if (e.isTrusted && e.key === "Escape" && panelOpen) hidePicker();
}, true);

document.addEventListener("click", (e) => {
  if (!armed || !e.isTrusted) return;
  const span = e.target && e.target.closest && e.target.closest(`[${HL_ATTR}]`);
  const id = span ? ownerId(span) : null;
  if (id === null) return; // not ours: a page-forged span, or one already removed
  const sel = window.getSelection();
  if (sel && !sel.isCollapsed) return; // end of a drag-select, not a click
  e.preventDefault();
  e.stopPropagation();
  showEditor(id);
}, true);

// ---- payload ---------------------------------------------------------------

const CONTEXT_BLOCKS = "p,li,blockquote,dd,dt,td,th,pre,figcaption,h1,h2,h3,h4,h5,h6";
const CONTEXT_CAP = 1500;

function nearestHeadingText(el) {
  // Nearest heading at or above the block, scanning backwards in DOM order.
  for (let node = el; node && node !== document.body; node = node.parentElement) {
    for (let sib = node.previousElementSibling; sib; sib = sib.previousElementSibling) {
      if (/^H[1-6]$/.test(sib.tagName)) return sib.innerText.trim();
      const hs = sib.querySelectorAll ? sib.querySelectorAll("h1,h2,h3,h4,h5,h6") : [];
      if (hs.length) return hs[hs.length - 1].innerText.trim();
    }
  }
  return "";
}

function normText(s) {
  return (s || "").replace(/\s+/g, " ").trim();
}

function contextInTextLayer(span) {
  // A PDF.js text layer is a flat run of absolutely-positioned spans: nothing
  // encloses the highlight and there are no headings to find, so walk the
  // page's own spans outward from the highlighted one instead.
  const layer = span.closest(".textLayer");
  if (!layer) return null;
  let run = span;
  while (run.parentElement && run.parentElement !== layer) run = run.parentElement;
  const runs = Array.from(layer.children);
  const at = runs.indexOf(run);
  if (at < 0) return null;
  const half = CONTEXT_CAP / 2;
  let before = "";
  let after = "";
  for (let i = at - 1; i >= 0 && before.length < half; i--) {
    before = `${normText(runs[i].textContent)} ${before}`;
  }
  for (let i = at + 1; i < runs.length && after.length < half; i++) {
    after = `${after} ${normText(runs[i].textContent)}`;
  }
  return normText(
    `${before.slice(-half)} ${normText(run.textContent)} ${after.slice(0, half)}`);
}

function contextFor(span, highlightText) {
  if (PDF_VIEWER) {
    const flat = contextInTextLayer(span);
    if (flat !== null) return flat;
  }
  // Anchored excerpt: enclosing block plus its neighbors, capped with the
  // window centered on the highlight so its surroundings always survive.
  // The anchor is positional (a Range from block start to the first span,
  // measured in the same normalized-textContent metric as the excerpt), so
  // it cannot miss the way a string search across inline elements can.
  const block = span.closest(CONTEXT_BLOCKS) || span.parentElement;
  if (!block) return "";
  const prev = block.previousElementSibling;
  const next = block.nextElementSibling;
  const prevText = prev ? normText(prev.textContent) : "";
  const blockText = normText(block.textContent);
  const nextText = next ? normText(next.textContent) : "";
  let text = [prevText, blockText, nextText].filter(Boolean).join("\n");
  if (text.length > CONTEXT_CAP) {
    const pre = document.createRange();
    pre.selectNodeContents(block);
    pre.setEndBefore(span);
    let anchor = normText(pre.toString()).length;
    if (prevText) anchor += prevText.length + 1;
    const mid = anchor + Math.min(highlightText.length, CONTEXT_CAP / 2) / 2;
    const start = Math.max(
      0, Math.min(Math.round(mid - CONTEXT_CAP / 2), text.length - CONTEXT_CAP));
    text = text.slice(start, start + CONTEXT_CAP);
  }
  const heading = nearestHeadingText(block);
  return heading && !text.startsWith(heading) ? `${heading}\n${text}` : text;
}

function collectPayload() {
  // Registry only — never querySelectorAll: attributes on the page are not
  // evidence that the reader highlighted anything. Sorted into page order.
  const live = [];
  for (const { color, comment, spans } of registry.values()) {
    const connected = spans.filter((s) => s.isConnected);
    if (!connected.length) continue;
    live.push({ color, comment, parts: connected.map((s) => s.textContent),
                firstSpan: connected[0] });
  }
  live.sort((a, b) => (
    a.firstSpan.compareDocumentPosition(b.firstSpan) & Node.DOCUMENT_POSITION_FOLLOWING
      ? -1 : 1));
  const byId = { values: () => live };
  const highlights = { yellow: [], blue: [] };
  for (const { color, comment, parts, firstSpan } of byId.values()) {
    const text = parts.join(" ").replace(/\s+/g, " ").trim();
    if (!text) continue;
    const bucket = highlights[color] ? color : "yellow";
    highlights[bucket].push(
      { text, context: contextFor(firstSpan, text), comment });
  }
  return {
    title: document.title || location.hostname,
    url: (PDF_VIEWER && document.documentElement.dataset.hlankiUrl) || location.href,
    highlights,
    pageText: (document.body ? document.body.innerText : "").slice(0, 30000),
  };
}

// ---- toast -----------------------------------------------------------------
// Every toast stays until replaced or clicked: a Claude run can take well over
// a minute — auto-hiding the progress toast made long runs look like silent
// failures — and trackJob always ends it with an ok/err toast.

let toastEl = null;

function showToast(text, kind) {
  if (!toastEl) {
    toastEl = document.createElement("div");
    toastEl.style.cssText = [
      "position:fixed", "right:16px", "bottom:16px", "z-index:2147483647",
      "max-width:360px", "padding:10px 28px 10px 14px", "border-radius:8px",
      "font:13px/1.4 -apple-system,system-ui,sans-serif", "color:#fff",
      "box-shadow:0 2px 12px rgba(0,0,0,.35)", "white-space:pre-wrap",
      "cursor:pointer",
    ].join(";");
    toastEl.title = "Click to dismiss";
    toastEl.addEventListener("click", () => { toastEl.style.display = "none"; });
    document.documentElement.appendChild(toastEl);
  }
  toastEl.style.background =
    kind === "err" ? "#c0392b" : kind === "ok" ? "#1e824c" : "#333";
  toastEl.textContent = text;
  const x = document.createElement("span");
  x.textContent = "\u00d7";
  x.style.cssText = [
    "position:absolute", "top:4px", "right:9px",
    "font:15px/1 -apple-system,system-ui,sans-serif", "opacity:.8",
  ].join(";");
  toastEl.appendChild(x);
  toastEl.style.display = "block";
}
