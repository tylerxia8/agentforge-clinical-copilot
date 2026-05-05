/**
 * Clinical Co-Pilot — W2 PDF bounding-box overlay.
 *
 * Loads pdf.js from CDN on first use, renders the requested page of
 * an uploaded PDF in a full-screen modal, and draws a yellow
 * highlight at the citation bbox the agent's matcher attached.
 *
 * Public API:
 *
 *   window.CopilotPdfOverlay.open({
 *     pdfUrl,        // URL to GET the PDF from (pdf.php?id=...)
 *     page,          // 1-indexed page number
 *     bbox,          // {x0, y0, x1, y1}  in pdfplumber's top-down PDF points
 *     quote,         // human-readable text to show in the modal header
 *   });
 *
 * pdf.js is loaded from cdnjs.cloudflare.com on first use — Mozilla
 * is the upstream maintainer; cdnjs is a reputable mirror. For
 * production deployments outside this Gauntlet sprint we'd self-host
 * the worker bundle.
 */

(() => {
  "use strict";

  const PDFJS_VERSION = "4.7.76";
  const PDFJS_BASE = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${PDFJS_VERSION}`;
  const PDFJS_LIB_URL = `${PDFJS_BASE}/pdf.min.mjs`;
  const PDFJS_WORKER_URL = `${PDFJS_BASE}/pdf.worker.min.mjs`;

  let pdfjsPromise = null;

  function loadPdfJs() {
    if (pdfjsPromise) return pdfjsPromise;
    pdfjsPromise = (async () => {
      // dynamic ESM import — pdf.js v4 ships an ES module entrypoint
      const mod = await import(/* webpackIgnore: true */ PDFJS_LIB_URL);
      const pdfjs = mod.default || mod;
      pdfjs.GlobalWorkerOptions.workerSrc = PDFJS_WORKER_URL;
      return pdfjs;
    })().catch((err) => {
      pdfjsPromise = null; // allow retry
      throw err;
    });
    return pdfjsPromise;
  }

  async function open({ pdfUrl, page, bbox, quote }) {
    const modal = buildModal(quote);
    document.body.appendChild(modal);
    const status = modal.querySelector(".copilot-pdf-status");
    const canvas = modal.querySelector(".copilot-pdf-canvas");

    try {
      status.textContent = "Loading PDF reader…";
      const pdfjs = await loadPdfJs();

      status.textContent = "Fetching document…";
      const pdf = await pdfjs.getDocument(pdfUrl).promise;
      if (page < 1 || page > pdf.numPages) {
        status.textContent = `Page ${page} not in document (has ${pdf.numPages}).`;
        return;
      }

      status.textContent = `Rendering page ${page}…`;
      const pdfPage = await pdf.getPage(page);

      // Fit the page width to the modal's available area but cap scale
      // so very small PDFs don't render gigantic.
      const baseViewport = pdfPage.getViewport({ scale: 1 });
      const wrapper = modal.querySelector(".copilot-pdf-canvas-wrapper");
      const availableWidth = Math.max(wrapper.clientWidth - 16, 320);
      const scale = Math.min(2.0, availableWidth / baseViewport.width);
      const viewport = pdfPage.getViewport({ scale });

      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);
      const ctx = canvas.getContext("2d");

      await pdfPage.render({ canvasContext: ctx, viewport }).promise;
      status.textContent = "";

      if (bbox && typeof bbox.x0 === "number") {
        drawHighlight(ctx, bbox, scale);
      }
    } catch (err) {
      console.error("[copilot pdf-overlay]", err);
      status.textContent = `Could not render PDF: ${err.message || err}`;
    }
  }

  /**
   * pdfplumber emits top-down PDF-point coordinates (origin top-left,
   * Y grows downward). pdf.js's canvas after .render() also has
   * (0,0) at the top-left of the rendered page, so the conversion is
   * a straight scale by viewport.scale.
   */
  function drawHighlight(ctx, bbox, scale) {
    const x = bbox.x0 * scale;
    const y = bbox.y0 * scale;
    const w = (bbox.x1 - bbox.x0) * scale;
    const h = (bbox.y1 - bbox.y0) * scale;
    // Pad by a few pixels so the highlight extends beyond the glyphs
    // — easier to see than a bbox that hugs the text exactly.
    const pad = 3;
    ctx.fillStyle = "rgba(255, 215, 0, 0.35)";
    ctx.strokeStyle = "rgba(217, 119, 6, 0.95)";
    ctx.lineWidth = 2;
    ctx.fillRect(x - pad, y - pad, w + 2 * pad, h + 2 * pad);
    ctx.strokeRect(x - pad, y - pad, w + 2 * pad, h + 2 * pad);
  }

  function buildModal(quote) {
    const root = document.createElement("div");
    root.className = "copilot-pdf-modal";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", "Source document preview");

    root.innerHTML = `
      <div class="copilot-pdf-modal-backdrop"></div>
      <div class="copilot-pdf-modal-card">
        <header class="copilot-pdf-modal-header">
          <span class="copilot-pdf-quote"></span>
          <button type="button" class="copilot-pdf-close" aria-label="Close">×</button>
        </header>
        <div class="copilot-pdf-canvas-wrapper">
          <div class="copilot-pdf-status">Loading…</div>
          <canvas class="copilot-pdf-canvas"></canvas>
        </div>
      </div>
    `;

    root.querySelector(".copilot-pdf-quote").textContent = quote || "Source";

    const close = () => {
      document.removeEventListener("keydown", onKey);
      root.remove();
    };
    const onKey = (e) => {
      if (e.key === "Escape") close();
    };
    root.querySelector(".copilot-pdf-close").addEventListener("click", close);
    root.querySelector(".copilot-pdf-modal-backdrop").addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return root;
  }

  window.CopilotPdfOverlay = { open };
})();
