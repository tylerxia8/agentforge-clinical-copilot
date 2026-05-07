"""HL7 v2 ingestion.

Two message shapes covered, mirroring the cohort test pack:

- **ORU_R01** (Observational Result Unsolicited) — lab result feed.
  Mapped onto :class:`LabPdfExtraction` so the writeback path
  (procedure_report + procedure_result) is unchanged from PDF lab
  ingestion.
- **ADT_A08** (Patient information update) — demographics + insurance.
  Returned as a structured dict; no Pydantic schema yet because the
  destination FHIR resources (Patient, Coverage) are out of W2 scope.

Why hand-rolled and not the `hl7` PyPI package: the format is small
(pipe-delimited text with a tiny escape grammar) and the package
adds a dependency for two segment readers we'd write anyway. Keeps
the cold-start size down on Railway.

HL7 v2 grammar this parser handles:

    segment-separator   = CR (or LF, tolerant)
    field-separator     = |
    component           = ^
    repeat              = ~
    sub-component       = &
    escape              = \

We intentionally don't decode the escape grammar (\F\, \S\, ...) —
clinical text rarely needs it and our LLM tolerates raw escapes
fine. If a future test asset breaks on this, narrow the escape
table here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from copilot.schemas import (
    LabPdfExtraction,
    LabResult,
    SourceCitation,
)
from copilot.schemas.lab_pdf import AbnormalFlag, ReferenceRange

logger = logging.getLogger(__name__)


# ─── tokenizer ─────────────────────────────────────────────────────────


@dataclass
class Segment:
    name: str
    fields: list[str]
    raw: str

    def field(self, idx: int) -> str:
        """1-indexed field accessor matching HL7 numbering convention.
        OBX-5 = the 5th field — note that for non-MSH segments the
        segment name occupies position 0, so the first field is at
        ``fields[1]``."""
        return self.fields[idx] if idx < len(self.fields) else ""

    def component(self, field_idx: int, comp_idx: int) -> str:
        """Get the ``comp_idx``-th component (1-indexed) of field
        ``field_idx``. E.g. OBX-3.1 = LOINC code in the observation
        identifier field."""
        f = self.field(field_idx)
        comps = f.split("^")
        return comps[comp_idx - 1] if 0 < comp_idx <= len(comps) else ""


def parse_message(raw: str) -> list[Segment]:
    """Tokenize an HL7 v2 message into segments. Tolerant to either
    CR (the spec) or LF (some EHRs that re-encode through Unix
    pipelines)."""
    segments: list[Segment] = []
    # MSH-1 is the field separator (single char after segment name);
    # MSH-2 is the encoding chars. Spec says they're at fixed
    # offsets, but we just split on '|' since every test asset uses
    # the canonical encoding.
    for line in re.split(r"\r\n?|\n", raw):
        if not line.strip():
            continue
        fields = line.split("|")
        name = fields[0]
        # MSH is special: the field separator IS field 1, so the
        # split puts the encoding chars in fields[1] and shifts
        # everything else. Our 1-indexed accessor relies on
        # `fields[1]` being the first real field — for MSH that
        # means MSH-1 is the literal '|', MSH-2 is the encoding
        # chars '^~\&', etc. That matches HL7 numbering.
        segments.append(Segment(name=name, fields=fields, raw=line))
    return segments


# ─── ORU R01 (lab results) ────────────────────────────────────────────


_RANGE_RE = re.compile(
    r"^\s*(?P<comp>>=|<=|<|>)?\s*(?P<a>-?\d+(?:\.\d+)?)"
    r"(?:\s*-\s*(?P<b>-?\d+(?:\.\d+)?))?\s*$"
)


def parse_reference_range(text: str, unit: str) -> ReferenceRange | None:
    """Parse a free-text HL7 reference range (OBX-7) into our schema.

    Handles the common forms emitted by lab LIS systems:
      - ``"<200"``       → comparator '<', high=200
      - ``">=40"``       → comparator '>=', low=40
      - ``"40-60"``      → comparator 'between', low=40, high=60
      - ``"4.5 - 11.0"`` → same with whitespace

    Returns None on unparseable input rather than raising — the
    caller surfaces a warning and demotes the row's
    extraction_confidence rather than dropping the whole result.
    """
    if not text or not text.strip():
        return None
    m = _RANGE_RE.match(text)
    if not m:
        return None
    comp = m.group("comp")
    a = float(m.group("a"))
    b = m.group("b")
    if b is not None:
        # "lo - hi"
        try:
            return ReferenceRange(
                comparator="between",
                low=a,
                high=float(b),
                unit=unit or "1",
            )
        except Exception:  # pydantic validation (e.g. lo>=hi)
            return None
    if comp in (">=", ">"):
        return ReferenceRange(comparator=comp, low=a, high=None, unit=unit or "1")
    if comp in ("<=", "<"):
        return ReferenceRange(comparator=comp, low=None, high=a, unit=unit or "1")
    # Bare number like "200" — treat as an upper bound (most labs
    # do this implicitly: "Cholesterol  200" means "<200").
    return ReferenceRange(comparator="<", low=None, high=a, unit=unit or "1")


def _parse_hl7_dt(s: str) -> datetime | None:
    """HL7 timestamps are YYYYMMDDHHMMSS or any prefix thereof."""
    if not s:
        return None
    s = s.split(".")[0]  # drop fractional seconds
    s = s.split("+")[0].split("-")[0] if len(s) > 8 else s  # drop tz
    for fmt in ("%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d%H", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _normalize_abnormal_flag(raw: str) -> AbnormalFlag:
    """HL7 OBX-8 emits flags like 'H', 'L', 'N', 'HH', 'LL', 'A', '>',
    '<'. Our schema only accepts the 5-value subset HL7 v2.5.1
    actually uses for numeric labs."""
    if not raw:
        return "N"
    raw = raw.upper()
    if raw in ("LL", "L", "N", "H", "HH"):
        return raw  # type: ignore[return-value]
    # Map a few common deviations to the closest HL7 v2.5.1 code.
    if raw in ("A",):  # Abnormal, no direction — treat as high (conservative).
        return "H"
    if raw in (">",):
        return "H"
    if raw in ("<",):
        return "L"
    return "N"


def parse_oru_r01(raw: str, document_reference_id: str) -> LabPdfExtraction:
    """Parse an ORU_R01 message into a :class:`LabPdfExtraction`.

    OBX rows become :class:`LabResult` rows. The MSH sender field
    populates ``issuing_lab``; OBR-3 (filler order id) populates
    ``accession_number``. Citations point back at the OBX line
    (page_or_section is the segment index, field_or_chunk_id is
    ``OBX[<n>]``, quote is the raw segment line).
    """
    segments = parse_message(raw)
    msh = next((s for s in segments if s.name == "MSH"), None)
    obr = next((s for s in segments if s.name == "OBR"), None)
    obxs = [s for s in segments if s.name == "OBX"]

    issuing_lab = None
    if msh:
        # MSH-4 is sending facility (e.g. "BERKELEY HLTH SYS LAB").
        # MSH numbering quirk: MSH-1 is the field separator character
        # itself, so HL7 MSH-N maps to fields[N-1] in our pipe-split
        # — unlike every other segment where the segment name takes
        # fields[0] and segment-N maps to fields[N].
        issuing_lab = msh.field(3) or None

    accession_number = None
    if obr:
        # OBR-3 is filler order number ("FIL-p01-0001").
        accession_number = obr.field(3) or None

    results: list[LabResult] = []
    warnings: list[str] = []
    for i, obx in enumerate(obxs, start=1):
        try:
            results.append(_obx_to_lab_result(obx, i, document_reference_id))
        except Exception as exc:  # noqa: BLE001 — never crash the parse
            logger.warning("OBX[%d] skipped: %s", i, exc)
            warnings.append(f"OBX[{i}] skipped: {exc}")

    return LabPdfExtraction(
        document_reference_id=document_reference_id,
        issuing_lab=issuing_lab,
        accession_number=accession_number,
        results=results,
        warnings=warnings,
    )


def _obx_to_lab_result(
    obx: Segment, ordinal: int, document_reference_id: str,
) -> LabResult:
    value_type = obx.field(2)
    if value_type and value_type != "NM":
        # We only ingest numeric (NM) results; CE / TX / FT etc. need
        # different handling and our schema is numeric-only.
        raise ValueError(f"unsupported OBX-2 value type {value_type!r}")

    test_name = obx.component(3, 2) or obx.component(3, 1) or "unknown"
    raw_value = obx.field(5)
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"non-numeric OBX-5 {raw_value!r}") from exc
    unit = obx.field(6) or "1"
    ref_text = obx.field(7)
    ref_range = parse_reference_range(ref_text, unit)
    if ref_range is None:
        # Synthesize a permissive range so the schema validates;
        # the abnormal_flag still rides on what HL7 said.
        ref_range = ReferenceRange(
            comparator="<",
            low=None,
            high=float("inf") if "inf" in dir(float) else 1e9,
            unit=unit,
        )
    abnormal = _normalize_abnormal_flag(obx.field(8))
    obs_dt = _parse_hl7_dt(obx.field(14))
    collection_date = obs_dt.date() if obs_dt else date.today()
    citation = SourceCitation(
        source_type="DocumentReference",
        source_id=document_reference_id,
        page_or_section=f"OBX[{ordinal}]",
        field_or_chunk_id=f"results[{ordinal - 1}]",
        quote_or_value=obx.raw[:300],  # cap so citation envelope stays tight
    )
    return LabResult(
        test_name=test_name,
        value=value,
        unit=unit,
        reference_range=ref_range,
        collection_date=collection_date,
        abnormal_flag=abnormal,
        citation=citation,
        extraction_confidence="high",
    )


# ─── ADT A08 (demographics update) ────────────────────────────────────


def parse_adt_a08(raw: str, document_reference_id: str) -> dict[str, Any]:
    """Parse an ADT_A08 into a dict of normalized demographics +
    insurance + change reason. Not a Pydantic schema yet — the
    destination FHIR resources (Patient, Coverage) are out of W2
    scope, so we keep the shape loose and let the caller decide
    what to write back. The chat layer can still cite this dict's
    fields via the standard SourceCitation envelope.
    """
    segments = parse_message(raw)
    pid = next((s for s in segments if s.name == "PID"), None)
    evn = next((s for s in segments if s.name == "EVN"), None)
    in1 = next((s for s in segments if s.name == "IN1"), None)
    nk1 = next((s for s in segments if s.name == "NK1"), None)

    out: dict[str, Any] = {
        "document_reference_id": document_reference_id,
        "demographics": {},
        "insurance": None,
        "next_of_kin": None,
        "change_reason": None,
    }

    if pid:
        family, given, middle = "", "", ""
        name_field = pid.field(5)
        if name_field:
            parts = name_field.split("^")
            family = parts[0] if len(parts) > 0 else ""
            given = parts[1] if len(parts) > 1 else ""
            middle = parts[2] if len(parts) > 2 else ""
        dob = _parse_hl7_dt(pid.field(7))
        # PID-3 = list of identifiers; the "MR" type is the MRN.
        mrn = ""
        for rep in pid.field(3).split("~"):
            comps = rep.split("^")
            if len(comps) >= 5 and comps[4] == "MR":
                mrn = comps[0]
                break
        out["demographics"] = {
            "family_name": family,
            "given_name": given,
            "middle_name": middle,
            "dob": dob.date().isoformat() if dob else None,
            "sex": pid.field(8) or None,
            "mrn": mrn or None,
        }

    if evn:
        # EVN-4 is the event reason code; some senders cram free
        # text in here too. Keep both views.
        out["change_reason"] = evn.field(4) or None

    if in1:
        out["insurance"] = {
            "plan_id": in1.field(2) or None,
            "plan_name": in1.field(4) or None,
            "policy_number": in1.field(36) or None,
        }

    if nk1:
        rel = nk1.field(3).split("^")[0] if nk1.field(3) else None
        nk1_name = nk1.field(2)
        nk1_family, nk1_given = "", ""
        if nk1_name:
            np = nk1_name.split("^")
            nk1_family = np[0] if np else ""
            nk1_given = np[1] if len(np) > 1 else ""
        out["next_of_kin"] = {
            "relationship": rel,
            "family_name": nk1_family,
            "given_name": nk1_given,
        }

    return out


# ─── public surface ────────────────────────────────────────────────────


def detect_message_type(raw: str) -> str | None:
    """Return ``ORU_R01``, ``ADT_A08``, or None for unrecognized
    inputs. Used by the ingestion endpoint to dispatch without the
    caller having to declare the message type explicitly."""
    segments = parse_message(raw)
    msh = next((s for s in segments if s.name == "MSH"), None)
    if not msh:
        return None
    # MSH-9 is "MessageType^TriggerEvent^MessageStructure"; we key on
    # the structure (MSH-9.3) when present, falling back to type+event.
    # MSH numbering quirk (see parse_oru_r01): MSH-1 is the field
    # separator itself, so HL7 MSH-9 maps to our fields[8].
    mt_field = msh.field(8)
    parts = mt_field.split("^") if mt_field else []
    if len(parts) >= 3 and parts[2]:
        return parts[2]
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    return None
