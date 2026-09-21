// The highlight panel, running inside its own extension-origin iframe.
//
// Why an iframe and not a div in the page: the note typed here is sent to
// the model as the reader's own trusted instructions, so the page being read
// must not be able to read it (keystroke listeners on the parent window see
// nothing — events never leave this document) nor forge it. All traffic with
// the content script goes over chrome.runtime messaging, which page scripts
// can neither send nor observe; window.postMessage would be visible to them.
// Inbound: {type: "panelOpen", panelId, mode, comment} sent by the content
// script; outbound: {type: "panelEvent", panelId, ...} relayed by the
// service worker to the content script of this tab.
"use strict";

const panelId = location.hash.slice(1);
const $ = (id) => document.getElementById(id);
const noteBox = $("note");
let mode = "create";

function send(event, fields = {}) {
  chrome.runtime.sendMessage({ type: "panelEvent", panelId, event, ...fields }).catch(() => {});
}

function open(newMode, comment) {
  mode = newMode;
  const editing = mode === "edit";
  noteBox.value = editing ? comment : "";
  noteBox.style.display = editing ? "block" : "none";
  $("pen").style.display = editing ? "none" : "flex";
  $("kill").style.display = editing ? "flex" : "none";
  if (editing) noteBox.focus();
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type !== "panelOpen" || msg.panelId !== panelId) return false;
  open(msg.mode === "edit" ? "edit" : "create", String(msg.comment || ""));
  sendResponse(true);
  return false;
});

for (const color of ["yellow", "blue"]) {
  $(color).addEventListener("click", () => {
    send("commit", { color, comment: noteBox.value.trim() });
  });
}
$("pen").addEventListener("click", () => {
  noteBox.style.display = "block";
  noteBox.focus();
});
$("kill").addEventListener("click", () => send("remove"));

// Editing an existing highlight saves as you type — no save button, and
// emptying the box is how a note is deleted.
noteBox.addEventListener("input", () => {
  if (mode === "edit") send("note", { comment: noteBox.value.trim() });
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" || (e.key === "Enter" && (e.metaKey || e.ctrlKey))) {
    send("close");
  }
});
// Edit mode: the content script focuses this frame from its click handler,
// but that focus arrives over IPC and may land after the panelOpen message
// above already tried noteBox.focus() in a still-unfocused frame. Either
// order works when the window's own focus event also moves into the box.
window.addEventListener("focus", () => { if (mode === "edit") noteBox.focus(); });

// The content script sizes the iframe to whatever the panel needs.
const panel = $("panel");
function reportSize() {
  send("size", { width: panel.offsetWidth, height: panel.offsetHeight });
}
new ResizeObserver(reportSize).observe(panel);
reportSize();
