"""XLSX patient-workbook ingestion.

The cohort test pack ships per-patient workbooks with sheets like
"Patient" (demographics), "Medications", "Allergies", and "Family
History" — exactly the shape an :class:`IntakeFormExtraction`
consumes. We render every sheet as a CSV-flavoured text block,
concatenate them, and send to Claude with the intake-form schema.

No openpyxl dependency: the format is a zipped XML bundle and we
need a small subset (sheet enumeration + cell values for the cell
types our cohort actually uses). Hand-rolled to keep the deploy
footprint small.

Cell-value handling supports the three types we see in the cohort
fixtures:
- ``t="inlineStr"`` — value in ``<is><t>...``
- ``t="s"``        — value is a sharedStrings index
- absent / ``t="n"`` — numeric, value in ``<v>...``

Other types (``b`` bool, ``d`` date, formulas) are surfaced as
their raw string representation rather than crashing — accuracy can
be tightened in a follow-up if a real test asset breaks.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from typing import Any

from copilot.extraction.vision import _call_text_tool, _hydrate_and_validate
from copilot.schemas import IntakeFormExtraction

logger = logging.getLogger(__name__)


_SS_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def xlsx_to_text(xlsx_bytes: bytes) -> str:
    """Extract every sheet of an XLSX as CSV-shaped text. Each sheet
    is preceded by a ``# <sheet name>`` heading. Returns one string
    suitable for feeding to a text-only LLM call.

    Tag matching is local-name based (``_local(tag) == "row"``) so the
    parser doesn't break if a workbook declares a different default
    namespace or a sheet uses a `mc:AlternateContent` wrapper. Tested
    against the cohort 5 W2 asset pack (LibreOffice-style XLSX with
    inlineStr cells and no shared strings)."""
    import xml.etree.ElementTree as ET

    with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
        names = set(z.namelist())

        # 1. sharedStrings.xml (optional). Some writers (LibreOffice
        # with strict-mode export off) embed strings inline instead.
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in tree.iter():
                if _local(si.tag) == "si":
                    shared.append(_collect_text(si))

        # 2. workbook.xml — sheet names + relationship ids
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        rels_path = "xl/_rels/workbook.xml.rels"
        rels = (
            ET.fromstring(z.read(rels_path)) if rels_path in names else None
        )
        rel_target: dict[str, str] = {}
        if rels is not None:
            for rel in rels.iter():
                if _local(rel.tag) == "Relationship":
                    rel_target[rel.attrib.get("Id", "")] = rel.attrib.get("Target", "")

        sheets: list[tuple[str, str]] = []
        for sheet in wb.iter():
            if _local(sheet.tag) != "sheet":
                continue
            name = sheet.attrib.get("name", "Sheet")
            # r:id attribute — try every namespace key since different
            # writers prefix the relationships namespace differently.
            rid = ""
            for k, v in sheet.attrib.items():
                if k.endswith("}id") or k == "id":
                    rid = v
                    break
            target = rel_target.get(rid, "")
            sheets.append((name, _resolve_relationship_target(target)))

        # 3. Each sheet → CSV-ish lines
        chunks: list[str] = []
        for name, sheet_path in sheets:
            if not sheet_path or sheet_path not in names:
                continue
            chunks.append(f"# {name}")
            sheet_xml = ET.fromstring(z.read(sheet_path))
            for row in sheet_xml.iter():
                if _local(row.tag) != "row":
                    continue
                cells: list[str] = []
                for c in row.iter():
                    if _local(c.tag) != "c":
                        continue
                    cells.append(_cell_value(c, shared))
                if cells:
                    chunks.append(",".join(cells))
            chunks.append("")  # blank line between sheets

    return re.sub(r"\n{3,}", "\n\n", "\n".join(chunks)).strip()


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix off an ElementTree tag."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _resolve_relationship_target(target: str) -> str:
    """Normalize a workbook.xml.rels Target into a zip-relative path.

    Writers vary: LibreOffice and Excel emit ``/xl/worksheets/sheet1.xml``
    (absolute, leading slash); some writers emit ``worksheets/sheet1.xml``
    (relative to the workbook's directory). Either form must end up
    as ``xl/worksheets/sheet1.xml`` for our zip.namelist() lookup."""
    if not target:
        return ""
    # Strip leading slash, then ensure xl/ prefix.
    target = target.lstrip("/")
    if not target.startswith("xl/"):
        target = "xl/" + target
    return target


def _collect_text(el) -> str:
    """Concatenate every ``<t>`` descendant (any namespace) into one
    string."""
    return "".join(t.text or "" for t in el.iter() if _local(t.tag) == "t")


def _cell_value(c, shared: list[str]) -> str:
    cell_type = c.attrib.get("t", "n")
    if cell_type == "inlineStr":
        return _csv_escape(_collect_text(c))
    if cell_type == "s":
        v = _find_local(c, "v")
        try:
            idx = int(v.text or "0") if v is not None else 0
        except ValueError:
            return ""
        return _csv_escape(shared[idx]) if 0 <= idx < len(shared) else ""
    # numeric / bool / date — fall back to raw <v>
    v = _find_local(c, "v")
    return _csv_escape(v.text or "") if v is not None else ""


def _find_local(parent, local_name: str):
    """Find the first descendant with this local-name (any namespace)."""
    for el in parent.iter():
        if _local(el.tag) == local_name:
            return el
    return None


def _csv_escape(value: str) -> str:
    """Quote-escape a CSV cell. Plays nice with naive readers."""
    if not value:
        return ""
    if any(ch in value for ch in (",", "\"", "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value


_WORKBOOK_SYSTEM_PROMPT = """\
You are a medical-document parser specialized in patient workbooks
exported from upstream EHR systems.

You will be shown the contents of a multi-sheet spreadsheet, with
each sheet preceded by a ``# <sheet name>`` heading and rows
serialized as CSV. Common sheet names include ``Patient``,
``Medications``, ``Allergies``, ``Family History``, ``Vitals``,
``Conditions``. Extract all clinically actionable facts using the
emit_intake_extraction tool.

Sheet → schema mapping (apply when the sheet exists, even if
column names differ slightly from the canonical names below):

- ``Patient`` (or ``Demographics``) → ``demographics`` block.
  These sheets are usually two-column key/value: rows like
  ``Name, Margaret Chen``, ``DOB, 1968-03-12``, ``Sex, F``,
  ``MRN, BHS-2847163``. Map them onto demographics fields. Even a
  single recognizable identifier (name OR DOB OR MRN) is enough to
  emit the demographics object.
- ``Medications`` → ``medications[]`` (one entry per row, skipping
  the header row).
- ``Allergies`` → ``allergies[]``.
- ``Family History`` (or ``FamilyHistory``) → ``family_history[]``.
- Anything else (vitals, conditions, lab results) → surface as a
  warning string; the schema doesn't yet have those slots.

Rules:

1. For every extracted fact, set ``quote_or_value`` to the LITERAL
cell content (or row, joined with commas) you read it from.
2. Set ``field_or_chunk_id`` to ``<sheet>!<row>`` or
``<sheet>!<col><row>`` (e.g. ``Patient!B2``, ``Medications!3``).
3. ``intake_date``, when emitted, MUST be a bare ISO date string —
e.g. ``2026-05-06``, NOT ``"2026-05-06"`` (no embedded quotes).
4. If the workbook is empty or unreadable, return an empty
extraction with a warning. Do not guess.
"""


async def extract_xlsx_workbook(
    xlsx_bytes: bytes, document_reference_id: str
) -> IntakeFormExtraction:
    """Extract a patient XLSX workbook into the intake-form schema."""
    text = xlsx_to_text(xlsx_bytes)
    if not text:
        return IntakeFormExtraction(
            document_reference_id=document_reference_id,
            warnings=["XLSX contained no readable cells"],
        )
    raw = await _call_text_tool(
        prose=text,
        system_prompt=_WORKBOOK_SYSTEM_PROMPT,
        tool_name="emit_intake_extraction",
        tool_description="Emit the structured intake-form extraction for this patient workbook.",
        schema_cls=IntakeFormExtraction,
    )
    return _hydrate_and_validate(raw, document_reference_id, IntakeFormExtraction)
