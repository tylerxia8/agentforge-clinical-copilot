# Cohort 5 — Week 2 Asset Pack

7 patients × 4 source formats. Provided by the cohort to test the
ingestion pipeline end-to-end.

| Format | Path | Status | Maps to |
|---|---|---|---|
| HL7 v2 ORU_R01 | `hl7v2/p*-oru-r01.hl7` | ✅ supported | `LabPdfExtraction` (one OBX per `LabResult`) |
| HL7 v2 ADT_A08 | `hl7v2/p*-adt-a08.hl7` | ✅ supported | demographics dict |
| DOCX referral letter | `docx/p*-referral.docx` | ✅ supported (W2 Day 1) | `IntakeFormExtraction` |
| XLSX workbook | `xlsx/p*-workbook.xlsx` | ✅ supported (W2 Day 1) | structured tabular dict |
| TIFF fax packet | `tiff/p*-fax-packet.tiff` | ✅ supported (W2 Day 1) | `LabPdfExtraction` (via PDF conversion + vision) |

## Smoke runner

Run end-to-end against the deployed agent service:

```bash
cd agent-service
python -m fixtures.cohort_smoke
```

The runner:

1. Picks up `AGENT_URL` + `AGENT_SHARED_SECRET` from the environment
   (same vars the W2 eval suite uses).
2. POSTs each file in this directory to `/agent/extract` with the
   appropriate `doc_type`.
3. Prints per-file pass/fail and any extraction warnings.
4. Exits non-zero if any file failed to parse cleanly.

## Patients

The asset pack uses its own synthetic cohort (chen, whitaker, reyes,
kowalski, patel, johnson, nguyen) rather than the AgentForge demo
seed. The smoke runner picks an existing seed patient UUID and sends
the cohort messages on behalf of that patient — the OpenEMR-side
patient identity in the message is informational; what gets written
back is keyed by the agent's authenticated chart context.
