// Extension-owned PDF viewer: background.js sends PDF navigations here as
// viewer.html?file=<encoded original url>, and this renders every page with
// the vendored PDF.js — canvas plus a real text layer, so selections and the
// highlight UI in content.js behave exactly as on any other page. Text layers
// are eager for the whole document (Cmd+F and pageText must see every page);
// only canvas rasterization waits for the viewport.
"use strict";

const SCALE = 1.35;              // fixed zoom; no toolbar by design
const RENDER_AHEAD = "1500px";   // rasterize pages this far outside the viewport
const MAX_DPR = 2;               // cap the canvas backing store

const fileUrl = new URLSearchParams(location.search).get("file") || "";
// content.js reports this as the page URL, never the chrome-extension:// one.
document.documentElement.dataset.hlankiUrl = fileUrl;

const status = document.getElementById("status");
const viewer = document.getElementById("viewer");

function fail(text) {
  status.textContent = text;
  status.style.color = "#ffb3b3";
  // A failure after the first page rendered has to put the banner back.
  if (!status.isConnected) document.body.appendChild(status);
}

function fileName(url) {
  try {
    const path = new URL(url).pathname;
    return decodeURIComponent(path.slice(path.lastIndexOf("/") + 1)) || url;
  } catch (e) {
    return url;
  }
}

// The text layer sizes itself off --scale-factor, so the page box has to carry
// it (and match it, rounded the same way PDF.js rounds).
function sizePage(div, viewport) {
  div.style.setProperty("--scale-factor", String(viewport.scale));
  div.style.width = `${Math.floor(viewport.width)}px`;
  div.style.height = `${Math.floor(viewport.height)}px`;
}

// One record per page carries its pdfjs page and each layer's render promise,
// so the eager text pass and the lazy canvas pass each run exactly once.
function pageProxy(pdf, ref) {
  if (!ref.page) ref.page = pdf.getPage(ref.num);
  return ref.page;
}

// Text layer: cheap getTextContent-backed DOM, so every page gets one whether
// it is visible or not — browser find and content.js's pageText need the whole
// document, not just what has been rasterized.
function renderText(pdfjs, pdf, ref) {
  if (!ref.text) {
    ref.text = (async () => {
      const page = await pageProxy(pdf, ref);
      const viewport = page.getViewport({ scale: SCALE });
      sizePage(ref.div, viewport); // whichever layer lands first sizes the box
      const layer = document.createElement("div");
      layer.className = "textLayer";
      ref.div.appendChild(layer);
      await new pdfjs.TextLayer({
        textContentSource: page.streamTextContent(),
        container: layer,
        viewport,
      }).render();
    })();
  }
  return ref.text;
}

// Rasterization: the expensive half, so it stays lazy behind the observer.
function renderCanvas(pdf, ref) {
  if (!ref.canvas) {
    ref.canvas = (async () => {
      const page = await pageProxy(pdf, ref);
      const viewport = page.getViewport({ scale: SCALE });
      sizePage(ref.div, viewport);

      const wrap = document.createElement("div");
      wrap.className = "canvasWrapper";
      const canvas = document.createElement("canvas");
      const dpr = Math.min(window.devicePixelRatio || 1, MAX_DPR);
      canvas.width = Math.floor(viewport.width * dpr);
      canvas.height = Math.floor(viewport.height * dpr);
      wrap.appendChild(canvas);
      // Ahead of the text layer even when that rendered first: a positioned
      // canvas later in the DOM would paint over the highlight spans.
      ref.div.prepend(wrap);

      await page.render({
        canvasContext: canvas.getContext("2d"),
        viewport,
        transform: dpr === 1 ? null : [dpr, 0, 0, dpr, 0, 0],
      }).promise;
    })();
  }
  return ref.canvas;
}

(async function main() {
  if (!fileUrl) return fail("No ?file= parameter.");
  document.title = fileName(fileUrl);

  const pdfjs = await import("./pdfjs/pdf.min.mjs");
  pdfjs.GlobalWorkerOptions.workerSrc = "./pdfjs/pdf.worker.min.mjs";

  let pdf;
  try {
    pdf = await pdfjs.getDocument({
      url: fileUrl,
      withCredentials: true, // PDFs behind a login are the normal case
      cMapUrl: "./pdfjs/cmaps/",
      cMapPacked: true,
      standardFontDataUrl: "./pdfjs/standard_fonts/",
    }).promise;
  } catch (e) {
    // file:// reads need the per-extension toggle; nothing else explains it.
    const hint = fileUrl.startsWith("file:")
      ? 'Local PDFs need chrome://extensions → "Highlight → Anki / Zettel" → '
        + '"Allow access to file URLs".'
      : String((e && e.message) || e);
    return fail(`Could not load ${fileUrl}\n\n${hint}`);
  }

  const meta = await pdf.getMetadata().catch(() => null);
  const title = meta && meta.info && (meta.info.Title || "").trim();
  if (title) document.title = title;
  status.remove();

  // Lay every page out up front so scrolling is stable, but only rasterize
  // near the viewport: a few hundred eager canvases would eat the tab.
  const first = await pdf.getPage(1);
  const base = first.getViewport({ scale: SCALE });
  const refs = [];
  const pending = new Map(); // page div -> ref, until its canvas is queued
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      const ref = pending.get(entry.target);
      if (!entry.isIntersecting || !ref) continue;
      pending.delete(entry.target);
      observer.unobserve(entry.target);
      renderCanvas(pdf, ref).catch(
        (e) => console.error(`hlanki: page ${ref.num} canvas failed`, e));
    }
  }, { rootMargin: RENDER_AHEAD });

  for (let num = 1; num <= pdf.numPages; num++) {
    const div = document.createElement("div");
    div.className = "page";
    div.dataset.pageNumber = String(num);
    sizePage(div, base);
    viewer.appendChild(div);
    // Page 1's proxy is in hand already; the rest load on first render.
    const ref = {
      num, div, text: null, canvas: null,
      page: num === 1 ? Promise.resolve(first) : null,
    };
    refs.push(ref);
    pending.set(div, ref);
    observer.observe(div);
  }

  // Now the whole document's text, one page at a time and yielding to the task
  // queue in between, so the visible pages' canvases and input keep the thread.
  for (const ref of refs) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await renderText(pdfjs, pdf, ref).catch(
      (e) => console.error(`hlanki: page ${ref.num} text failed`, e));
  }
})().catch((e) => fail(`PDF viewer failed: ${(e && e.message) || e}`));
