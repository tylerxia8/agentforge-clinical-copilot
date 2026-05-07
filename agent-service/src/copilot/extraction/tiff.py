"""TIFF fax-packet ingestion.

Strategy: convert the TIFF to a PDF in-process (Pillow handles
multi-page bi-level fax TIFFs natively), then route through the
existing lab-PDF vision pipeline. Fax packets in the cohort test
pack are scanned lab reports / order requisitions, so the
LabPdfExtraction schema is the right destination.

Why convert to PDF rather than send TIFF directly:
- Anthropic's PDF document content block is the established
  cited-extraction path; reusing it keeps the bbox-attach pass
  applicable downstream (pdfplumber works on the converted PDF).
- TIFF as an image content block would split into one image per
  page for Claude, and we'd lose document-level cohesion.
- Cost/latency wash: a one-page-per-fax-image PDF is 100-300 KB
  total, well within the 32 MB Anthropic PDF cap.
"""

from __future__ import annotations

import io
import logging

from copilot.extraction.vision import extract_lab_pdf
from copilot.schemas import LabPdfExtraction

logger = logging.getLogger(__name__)


def tiff_to_pdf(tiff_bytes: bytes) -> bytes:
    """Convert a (potentially multi-page) TIFF to a single PDF.

    Pillow's Image API supports `seek()` across TIFF frames; we
    collect every frame and emit a multi-page PDF. Bi-level (1bpp)
    fax TIFFs convert cleanly — Pillow promotes them to 'L' for
    PDF output."""
    from PIL import Image, ImageSequence

    img = Image.open(io.BytesIO(tiff_bytes))
    frames: list[Image.Image] = []
    for frame in ImageSequence.Iterator(img):
        # PDF doesn't accept 1-bit images directly; promote to L (8-bit
        # grayscale). Same visual fidelity for B&W faxes, broader PDF
        # reader compatibility.
        if frame.mode == "1":
            frame = frame.convert("L")
        elif frame.mode not in ("L", "RGB"):
            frame = frame.convert("RGB")
        frames.append(frame.copy())

    if not frames:
        raise ValueError("TIFF contained no frames")

    out = io.BytesIO()
    head, *tail = frames
    head.save(
        out,
        format="PDF",
        save_all=True,
        append_images=tail,
        resolution=200.0,  # match typical fax DPI
    )
    return out.getvalue()


async def extract_tiff_fax(
    tiff_bytes: bytes, document_reference_id: str
) -> LabPdfExtraction:
    """Extract a TIFF fax packet via the lab-PDF vision pipeline.

    Round-trip: TIFF bytes → in-memory PDF → existing
    :func:`extract_lab_pdf`. Returns the same shape and goes through
    the same hydrate/validate/bbox-attach flow as a native PDF lab
    report."""
    pdf_bytes = tiff_to_pdf(tiff_bytes)
    return await extract_lab_pdf(pdf_bytes, document_reference_id)
