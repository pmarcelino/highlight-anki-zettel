// Highlight → Anki/Zettel service worker: per-tab arming (toolbar icon), two
// context-menu actions (yellow → Anki cards, blue → Zettel notes), and the
// job submission / status polls against the local companion server.
"use strict";

const SERVER = "http://127.0.0.1:8766";
const SERVER_DOWN =
  "Companion server not running? Start it with ./run.sh in the highlight-anki-zettel folder (port 8766).";

const KINDS = {
  anki: {
    menuId: "hlanki-anki",
    menuTitle: "Generate Anki cards from yellow highlights",
    color: "yellow",
    url: `${SERVER}/cards`,
    empty: "No yellow highlights on this page.",
    progress: (n) => `Generating Anki cards from ${n} yellow highlight(s)…`,
    failed: "Card generation failed",
    success: (out) => {
      let msg = `${out.count} card(s) → ${out.path}`;
      if (out.anki) {
        msg += out.anki.failed
          ? ` — imported ${out.anki.imported} into Anki, ${out.anki.failed} failed (duplicates?)`
          : ` — imported ${out.anki.imported} into Anki`;
      } else {
        msg += " — Anki not running; import the file manually";
      }
      return msg;
    },
  },
  zettel: {
    menuId: "hlanki-zettel",
    menuTitle: "Create Zettel notes from blue highlights",
    color: "blue",
    url: `${SERVER}/zettel`,
    empty: "No blue highlights on this page.",
    progress: (n) => `Creating Zettel notes from ${n} blue highlight(s)…`,
    failed: "Zettel note creation failed",
    success: (out) => `${out.count} note(s) → ${out.dir}`,
  },
};

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    for (const kind of Object.values(KINDS)) {
      chrome.contextMenus.create({
        id: kind.menuId,
        title: kind.menuTitle,
        contexts: ["all"],
      });
    }
  });
});

// ---- PDFs ------------------------------------------------------------------

// Chrome's built-in PDF viewer never gets a content script, so PDF
// navigations in an armed tab are handed to viewer.html (our PDF.js page)
// instead; unarmed tabs keep Chrome's viewer. http(s) is decided by the
// response type — arxiv.org/pdf/1706.03762 and friends carry no .pdf extension
// — while file:// has no headers to look at, only a name.
const VIEWER_URL = chrome.runtime.getURL("viewer.html");
const sentToViewer = new Map(); // tabId -> pdf url already redirected

function resHeader(details, name) {
  const hit = (details.responseHeaders || [])
    .find((h) => h.name.toLowerCase() === name);
  return (hit && hit.value) || "";
}

// PDFs left in Chrome's viewer because the tab was not armed, so arming it
// later can still switch them over. Session storage, not memory: the worker
// is long gone by the time someone decides to highlight a PDF they've been
// reading for a while. Writes are queued: the headers and the tab-url events
// for one navigation both touch this and must land in order.
async function getNativePdfs() {
  const { nativePdfs = {} } = await chrome.storage.session.get("nativePdfs");
  return nativePdfs;
}

let nativePdfsQueue = Promise.resolve();
function updateNativePdfs(mutate) {
  nativePdfsQueue = nativePdfsQueue.then(async () => {
    const nativePdfs = await getNativePdfs();
    mutate(nativePdfs);
    await chrome.storage.session.set({ nativePdfs });
  });
  return nativePdfsQueue;
}

function toViewer(tabId, url) {
  if (sentToViewer.get(tabId) === url) return; // no bouncing
  sentToViewer.set(tabId, url);
  updateNativePdfs((m) => { delete m[tabId]; });
  chrome.tabs.update(tabId, { url: `${VIEWER_URL}?file=${encodeURIComponent(url)}` })
    .catch(() => sentToViewer.delete(tabId));
}

async function onPdf(tabId, url) {
  if (tabId < 0) return;
  const armedTabs = await getArmedMap();
  if (armedTabs[tabId]) toViewer(tabId, url);
  else await updateNativePdfs((m) => { m[tabId] = url; });
}

chrome.webRequest.onHeadersReceived.addListener(
  (details) => {
    if (!/^application\/pdf\b/i.test(resHeader(details, "content-type"))) return;
    // An attachment is a download, not something to render.
    if (/^\s*attachment\b/i.test(resHeader(details, "content-disposition"))) return;
    onPdf(details.tabId, details.url);
  },
  { urls: ["http://*/*", "https://*/*"], types: ["main_frame"] },
  ["responseHeaders"],
);

// file:// PDFs only reach us when "Allow access to file URLs" is on; without
// it the URL is withheld here and Chrome's own viewer keeps the tab.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  const url = changeInfo.url;
  if (!url) return;
  if (url.startsWith("file:") && /\.pdf$/i.test(url.split(/[?#]/, 1)[0])) {
    onPdf(tabId, url);
  } else if (!url.startsWith(VIEWER_URL)) {
    sentToViewer.delete(tabId); // navigated away: the same PDF may come again
    updateNativePdfs((m) => { if (m[tabId] !== url) delete m[tabId]; });
  }
});

async function getArmedMap() {
  const { armedTabs = {} } = await chrome.storage.session.get("armedTabs");
  return armedTabs;
}

async function toggleArmed(tabId) {
  const armedTabs = await getArmedMap();
  const armed = !armedTabs[tabId];
  if (armed) armedTabs[tabId] = true;
  else delete armedTabs[tabId];
  await chrome.storage.session.set({ armedTabs });
  await chrome.action.setBadgeBackgroundColor({ tabId, color: "#f4b400" });
  await chrome.action.setBadgeText({ tabId, text: armed ? "ON" : "" });
  try {
    await chrome.tabs.sendMessage(tabId, { type: "setArmed", armed });
  } catch (e) {
    // No content script here (chrome:// pages etc.) — badge still reflects it.
  }
  // Arming a tab that is sitting in Chrome's PDF viewer moves it to ours so
  // there is something to highlight; disarming leaves ours in place.
  if (armed) {
    await nativePdfsQueue;
    const nativeUrl = (await getNativePdfs())[tabId];
    if (nativeUrl) toViewer(tabId, nativeUrl);
  }
  return armed;
}

chrome.action.onClicked.addListener((tab) => {
  if (tab && tab.id != null) toggleArmed(tab.id);
});

chrome.commands.onCommand.addListener(async (command) => {
  if (command !== "toggle-highlight-mode") return;
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (tab && tab.id != null) toggleArmed(tab.id);
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  sentToViewer.delete(tabId);
  updateNativePdfs((m) => { delete m[tabId]; });
  const armedTabs = await getArmedMap();
  if (armedTabs[tabId]) {
    delete armedTabs[tabId];
    await chrome.storage.session.set({ armedTabs });
  }
});

// The highlight panel runs in an extension-origin iframe (panel.html) and
// reports its actions here; we relay them into the top frame of its own tab.
// Page scripts cannot send runtime messages, and the sender URL check keeps
// a content script from posing as the panel.
const PANEL_URL = chrome.runtime.getURL("panel.html");

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "getArmed") {
    getArmedMap().then((m) =>
      sendResponse({ armed: !!(sender.tab && m[sender.tab.id]) }));
    return true; // async response
  }
  if (msg.type === "pollJob") {
    pollJob(msg.job, msg.kind).then(sendResponse);
    return true;
  }
  if (msg.type === "panelEvent") {
    if (sender.tab && sender.tab.id != null && sender.url &&
        sender.url.startsWith(PANEL_URL)) {
      chrome.tabs.sendMessage(sender.tab.id, msg, { frameId: 0 }).catch(() => {});
    }
    sendResponse({ ok: true });
    return false;
  }
  return false;
});

async function toast(tabId, text, kind) {
  try {
    await chrome.tabs.sendMessage(tabId, { type: "toast", text, kind });
  } catch (e) { /* tab gone or no content script */ }
}

async function fetchJson(url, init) {
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    throw new Error(SERVER_DOWN);
  }
  if (!res.ok) {
    let detail = `server error ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* keep */ }
    throw new Error(detail);
  }
  return res.json();
}

// Generation takes longer than the 30 s Chrome allows a service-worker fetch
// to wait for a response, so the server runs it as a job: POST returns the id
// at once and the content script — which lives as long as the tab — asks us
// to poll it. This worker may die and be revived between polls; nothing here
// depends on surviving.
async function pollJob(job, kindName) {
  const kind = KINDS[kindName];
  let status;
  try {
    status = await fetchJson(`${SERVER}/jobs/${job}`);
  } catch (e) {
    return { done: true, text: `${kind.failed}: ${e.message}`, kind: "err" };
  }
  if (status.state === "running") return { done: false };
  return status.state === "done"
    ? { done: true, text: kind.success(status.result), kind: "ok" }
    : { done: true, text: `${kind.failed}: ${status.detail}`, kind: "err" };
}

async function generateForTab(tabId, kindName = "anki") {
  const kind = KINDS[kindName];
  let payload;
  try {
    payload = await chrome.tabs.sendMessage(tabId, { type: "getPayload" });
  } catch (e) {
    return { error: "no content script in tab" };
  }
  const highlights =
    (payload && payload.highlights && payload.highlights[kind.color]) || [];
  if (!highlights.length) {
    await toast(tabId, kind.empty, "err");
    return { error: "no highlights" };
  }
  await toast(tabId, kind.progress(highlights.length));
  let out;
  try {
    out = await fetchJson(kind.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: payload.title,
        url: payload.url,
        highlights,
        page_text: payload.pageText,
      }),
    });
  } catch (e) {
    await toast(tabId, `${kind.failed}: ${e.message}`, "err");
    return { error: e.message };
  }
  try {
    await chrome.tabs.sendMessage(
      tabId, { type: "trackJob", job: out.job, kind: kindName });
  } catch (e) { /* tab gone; the server finishes the job regardless */ }
  return out;
}

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (!tab || tab.id == null) return;
  const entry = Object.entries(KINDS)
    .find(([, k]) => k.menuId === info.menuItemId);
  if (entry) generateForTab(tab.id, entry[0]);
});
