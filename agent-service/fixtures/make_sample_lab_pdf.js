#!/usr/bin/env node
/**
 * Generate a synthetic Quest-style lab report PDF for W2 testing.
 *
 * Output: agent-service/fixtures/sample_lab_report.pdf
 *
 * No npm dependencies — emits raw PDF 1.4 bytes using Helvetica, one
 * of the 14 standard fonts every PDF reader has built in. The
 * extraction pipeline reads this with pdfplumber + Claude vision; the
 * matcher must be able to find every result row's bbox cleanly.
 *
 * The patient is FAKE: name from our demo seed (Farrah Rolle), the
 * lab values are realistic for an uncontrolled T2DM follow-up but
 * carry no real-world identifier or PHI. PRD requires synthetic data
 * only.
 */

const fs = require("fs");
const path = require("path");

// ─── content ───────────────────────────────────────────────────────────

const HEADER = [
  "Quest Diagnostics",
  "1701 Trinity Bell Pkwy",
  "Austin TX 78758",
];

const META = [
  "Patient Name: Farrah Rolle",
  "Date of Birth: 06/14/1972    Sex: F",
  "Specimen Collected: 04/15/2026 09:14",
  "Accession #: QD-A1B2C3D4",
  "Ordered By: Dr. M. Chen, MD",
];

// [test_name, value, unit, reference, flag]
const RESULTS = [
  ["HDL Cholesterol",   "52",   "mg/dL",         "40 - 60",   "N"],
  ["LDL Cholesterol",   "132",  "mg/dL",         "< 100",     "H"],
  ["Total Cholesterol", "215",  "mg/dL",         "< 200",     "H"],
  ["Triglycerides",     "155",  "mg/dL",         "< 150",     "H"],
  ["Hemoglobin A1c",    "7.4",  "%",             "< 5.7",     "H"],
  ["Glucose, Fasting",  "158",  "mg/dL",         "70 - 99",   "H"],
  ["Creatinine",        "1.0",  "mg/dL",         "0.6 - 1.2", "N"],
  ["eGFR",              "78",   "mL/min/1.73m2", ">= 60",     "N"],
];

const FOOTER = [
  "Reference ranges are for adults unless otherwise noted.",
  "Critical values phoned to ordering provider per protocol.",
];

// ─── PDF builder ───────────────────────────────────────────────────────

const PAGE_WIDTH = 612;
const PAGE_HEIGHT = 792;
const LEFT = 56;
const TOP = 740;

// Build content stream operators. PDF coordinate origin is bottom-left,
// so y values run high → low as we move down the page.
const ops = [];
let y = TOP;

function line(text, { font = "F1", size = 11, dx = 0 } = {}) {
  // Escape ( ) \ in PDF strings.
  const safe = text.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
  ops.push(`BT /${font} ${size} Tf ${LEFT + dx} ${y} Td (${safe}) Tj ET`);
}

function blank(px = 14) {
  y -= px;
}

// Header — the lab name as a bold-ish larger font
line(HEADER[0], { font: "F2", size: 18 });
blank(20);
line(HEADER[1], { size: 10 });
blank(13);
line(HEADER[2], { size: 10 });
blank(24);

// Meta block
for (const m of META) {
  line(m, { size: 11 });
  blank(15);
}
blank(8);

// Results table — column header, then rows. We pad each column to a
// fixed pixel offset by drawing each cell as a separate Tj — pdfplumber
// will return one Word per cell, which is exactly what the matcher
// wants.
const COLS = [0, 200, 280, 350, 460]; // x-offsets from LEFT for the 5 cells

function row(cells, opts = {}) {
  for (let i = 0; i < cells.length; i++) {
    line(cells[i], { ...opts, dx: COLS[i] });
  }
  blank(opts.gap || 18);
}

// Header row in bold
row(["Test", "Value", "Unit", "Reference", "Flag"], { font: "F2", size: 11, gap: 22 });

// Result rows
for (const r of RESULTS) {
  row(r, { size: 11 });
}

blank(20);
line("Comments:", { font: "F2", size: 11 });
blank(16);
for (const f of FOOTER) {
  line(f, { size: 10 });
  blank(13);
}

const contentStream = ops.join("\n") + "\n";

// ─── Object table ──────────────────────────────────────────────────────

const objects = [];

function addObj(body) {
  const id = objects.length + 1;
  objects.push({ id, body: `${id} 0 obj\n${body}\nendobj\n` });
  return id;
}

const fontHelv = addObj(
  "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
);
const fontHelvBold = addObj(
  "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
);

const contentObjId = addObj(
  `<< /Length ${Buffer.byteLength(contentStream)} >>\nstream\n${contentStream}endstream`,
);

const pageObjId = addObj(
  `<< /Type /Page ` +
  `/Parent __PAGES_REF__ ` +
  `/MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] ` +
  `/Resources << /Font << /F1 ${fontHelv} 0 R /F2 ${fontHelvBold} 0 R >> >> ` +
  `/Contents ${contentObjId} 0 R >>`,
);

const pagesObjId = addObj(
  `<< /Type /Pages /Kids [${pageObjId} 0 R] /Count 1 >>`,
);

// Backfill the pages reference on the page object
objects[pageObjId - 1].body = objects[pageObjId - 1].body.replace(
  "__PAGES_REF__",
  `${pagesObjId} 0 R`,
);

const catalogObjId = addObj(
  `<< /Type /Catalog /Pages ${pagesObjId} 0 R >>`,
);

// ─── assemble + xref + trailer ─────────────────────────────────────────

let pdf = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";  // binary marker
const offsets = [0];
for (const o of objects) {
  offsets.push(Buffer.byteLength(pdf, "latin1"));
  pdf += o.body;
}

const xrefStart = Buffer.byteLength(pdf, "latin1");
pdf += `xref\n0 ${objects.length + 1}\n`;
pdf += "0000000000 65535 f \n";
for (let i = 1; i <= objects.length; i++) {
  pdf += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
}
pdf += `trailer\n<< /Size ${objects.length + 1} /Root ${catalogObjId} 0 R >>\nstartxref\n${xrefStart}\n%%EOF\n`;

const outPath = path.resolve(__dirname, "sample_lab_report.pdf");
fs.writeFileSync(outPath, pdf, { encoding: "latin1" });

const stats = fs.statSync(outPath);
console.log(`wrote ${outPath} (${stats.size} bytes)`);
