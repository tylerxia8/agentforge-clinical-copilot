#!/usr/bin/env node
/**
 * Generate a synthetic patient-intake form PDF for W2 testing.
 *
 * Output: agent-service/fixtures/sample_intake_form.pdf
 *
 * Same shape as make_sample_lab_pdf.js but laid out as a labeled
 * intake form. Synthetic patient (Farrah Rolle from the demo seed).
 */

const fs = require("fs");
const path = require("path");

const HEADER = ["Austin Family Medicine", "New Patient Intake Form"];

const SECTIONS = [
  ["Patient Information",
   ["First Name: Farrah",
    "Last Name: Rolle",
    "Date of Birth: 06/14/1972",
    "Sex: Female",
    "Phone: (512) 555-0188",
    "Email: frolle.demo@example.org",
    "Address: 4912 Cherry Cv, Austin TX 78745"]],

  ["Reason for Visit",
   ["I'm here for my diabetes follow-up. Sugar has been higher than usual",
    "and my feet have been numb at night. Want to discuss meds."]],

  ["Current Medications",
   ["1. Lisinopril 20 mg daily (for blood pressure)",
    "2. Atorvastatin 40 mg at bedtime (for cholesterol)",
    "3. Metformin 1000 mg twice a day (for diabetes)"]],

  ["Allergies",
   ["Penicillin - rash, moderate, since childhood"]],

  ["Family History",
   ["Mother: Type 2 diabetes (age 58)",
    "Father: Heart attack (age 64), deceased",
    "Sibling: Hypertension"]],

  ["Signed",
   ["Patient signature: Farrah Rolle",
    "Date: 04/15/2026"]],
];

// ─── PDF builder (same skeleton as the lab version) ──────────────────

const PAGE_WIDTH = 612;
const PAGE_HEIGHT = 792;
const LEFT = 56;
const TOP = 740;

const ops = [];
let y = TOP;

function pdfEscape(text) {
  return text.replace(/\\/g, "\\\\").replace(/\(/g, "\\(").replace(/\)/g, "\\)");
}

function line(text, { font = "F1", size = 11, dx = 0 } = {}) {
  ops.push(`BT /${font} ${size} Tf ${LEFT + dx} ${y} Td (${pdfEscape(text)}) Tj ET`);
}

function blank(px = 14) {
  y -= px;
}

// Header
line(HEADER[0], { font: "F2", size: 16 });
blank(20);
line(HEADER[1], { font: "F2", size: 13 });
blank(28);

// Sections
for (const [heading, items] of SECTIONS) {
  line(heading, { font: "F2", size: 12 });
  blank(18);
  for (const item of items) {
    line(item, { size: 11 });
    blank(15);
  }
  blank(8);
}

const contentStream = ops.join("\n") + "\n";

// ─── Object table (identical scaffold to lab generator) ──────────────

const objects = [];
function addObj(body) {
  const id = objects.length + 1;
  objects.push({ id, body: `${id} 0 obj\n${body}\nendobj\n` });
  return id;
}

const fontHelv = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>");
const fontHelvBold = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>");
const contentObjId = addObj(
  `<< /Length ${Buffer.byteLength(contentStream)} >>\nstream\n${contentStream}endstream`,
);
const pageObjId = addObj(
  `<< /Type /Page /Parent __PAGES_REF__ ` +
  `/MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] ` +
  `/Resources << /Font << /F1 ${fontHelv} 0 R /F2 ${fontHelvBold} 0 R >> >> ` +
  `/Contents ${contentObjId} 0 R >>`,
);
const pagesObjId = addObj(`<< /Type /Pages /Kids [${pageObjId} 0 R] /Count 1 >>`);
objects[pageObjId - 1].body = objects[pageObjId - 1].body.replace(
  "__PAGES_REF__",
  `${pagesObjId} 0 R`,
);
const catalogObjId = addObj(`<< /Type /Catalog /Pages ${pagesObjId} 0 R >>`);

let pdf = "%PDF-1.4\n%\xE2\xE3\xCF\xD3\n";
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

const outPath = path.resolve(__dirname, "sample_intake_form.pdf");
fs.writeFileSync(outPath, pdf, { encoding: "latin1" });
console.log(`wrote ${outPath} (${fs.statSync(outPath).size} bytes)`);
