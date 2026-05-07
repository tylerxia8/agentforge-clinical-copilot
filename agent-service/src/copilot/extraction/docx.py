"""DOCX referral-letter ingestion.

Strategy: extract plain text from the DOCX zip (no python-docx
dependency — we only need the prose, not styling), then send the
text to Claude with the existing intake-form schema. Output is an
:class:`IntakeFormExtraction` so the writeback path is shared with
the PDF intake-form ingestion.

Why reuse IntakeFormExtraction: a referral letter and a patient
intake form carry the same clinically-actionable fields —
demographics, chief concern, current meds, allergies, family
history. Wrapping referrals in their own schema would split the
writeback code in two for no added expressiveness.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile

from copilot.extraction.vision import _call_text_tool, _hydrate_and_validate
from copilot.schemas import IntakeFormExtraction

logger = logging.getLogger(__name__)


_DOCX_TEXT_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PARA_TAG = f"{_DOCX_TEXT_NS}p"
_TEXT_TAG = f"{_DOCX_TEXT_NS}t"
_TAB_TAG = f"{_DOCX_TEXT_NS}tab"
_BREAK_TAG = f"{_DOCX_TEXT_NS}br"


def docx_to_text(docx_bytes: bytes) -> str:
    """Pull all body text out of a DOCX. Paragraphs become newlines.
    Tables are flattened (one cell per line); tabs and explicit
    breaks are preserved as whitespace.

    No python-docx dependency on purpose: a docx is a zip with a
    single XML file we care about, and the namespace traversal is
    five lines. Adds zero to the build."""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        try:
            with z.open("word/document.xml") as fh:
                xml_bytes = fh.read()
        except KeyError as exc:
            raise ValueError("not a DOCX (no word/document.xml inside zip)") from exc

    root = ET.fromstring(xml_bytes)
    out: list[str] = []
    for paragraph in root.iter(_PARA_TAG):
        parts: list[str] = []
        for el in paragraph.iter():
            if el.tag == _TEXT_TAG and el.text:
                parts.append(el.text)
            elif el.tag == _TAB_TAG:
                parts.append("\t")
            elif el.tag == _BREAK_TAG:
                parts.append("\n")
        line = "".join(parts).strip()
        if line:
            out.append(line)
    text = "\n".join(out)
    # Collapse runs of >2 blank lines — DOCX exports often have spacer
    # paragraphs that bloat the prompt without changing meaning.
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_REFERRAL_SYSTEM_PROMPT = """\
You are a medical-document parser specialized in clinical referral
letters between physicians.

You will be shown the body text of one referral letter (the letter a
primary-care physician sends to a specialist when handing off
context for a consultation). Extract the patient's clinically
relevant facts using the emit_intake_extraction tool.

Rules:

1. For every extracted fact, set quote_or_value to the LITERAL phrase
from the letter that supports it. Quote at least the line of text;
prefer a short phrase to a single word.
2. The letter's `field_or_chunk_id` should be a stable path into the
schema (e.g. ``demographics.first_name``, ``medications[0]``).
3. Treat the referring physician's prose as authoritative for the
patient's current state at the time of writing. If the letter
contradicts itself (e.g. lists a med in one paragraph and says
"discontinued" later), surface BOTH lines as separate medications,
mark the discontinued one's notes accordingly, and add a warning.
4. If the letter is unreadable or appears to be in a non-English
language we can't reliably extract from, return an empty extraction
with a warning explaining why. Do not guess.
"""


async def extract_docx_referral(
    docx_bytes: bytes, document_reference_id: str
) -> IntakeFormExtraction:
    """Extract a DOCX referral letter into the intake-form schema.

    Two-step pipeline:
      1. Pull plain text out of the DOCX with :func:`docx_to_text`.
      2. Send the text to Claude with the intake-form tool schema.

    Same hydrate-and-validate post-pass as the PDF path, so
    server-owned fields (document_reference_id, citation source_id)
    are added by the pipeline rather than trusted from the LLM.
    """
    text = docx_to_text(docx_bytes)
    if not text:
        # Empty document — return empty extraction with a warning so
        # the eval rubric can grade the "unreadable input" path.
        return IntakeFormExtraction(
            document_reference_id=document_reference_id,
            warnings=["DOCX contained no extractable body text"],
        )

    raw = await _call_text_tool(
        prose=text,
        system_prompt=_REFERRAL_SYSTEM_PROMPT,
        tool_name="emit_intake_extraction",
        tool_description="Emit the structured intake-form extraction for this referral letter.",
        schema_cls=IntakeFormExtraction,
    )
    return _hydrate_and_validate(raw, document_reference_id, IntakeFormExtraction)


