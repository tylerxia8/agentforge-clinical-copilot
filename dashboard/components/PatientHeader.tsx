import type { Patient } from "@/lib/fhir-types";

interface Props {
  patient: Patient;
  /** Optional MRN if the FHIR identifier set doesn't surface it. */
  mrnOverride?: string;
}

/** The persistent identity bar — fixed at the top of every patient
 * route so the clinician always knows whose chart they're in. The
 * spec calls out: name, DOB, sex, MRN, active status. */
export function PatientHeader({ patient, mrnOverride }: Props) {
  const name = formatName(patient);
  const dob = patient.birthDate ?? "—";
  const age = patient.birthDate ? calcAge(patient.birthDate) : null;
  const sex = patient.gender ? capitalize(patient.gender) : "—";
  const mrn = mrnOverride ?? findMrn(patient) ?? patient.id;
  const active = patient.active !== false;

  // Cross-app deep-link to the same patient in OpenEMR's PHP chart.
  // OpenEMR's per-patient URL needs the integer pid, not the FHIR
  // uuid we have here — the closest deep link without a pid lookup
  // is the demographics search page; the user picks their patient
  // from there. Skip the link if OPENEMR_BASE_URL isn't configured.
  const openemrBase = process.env.NEXT_PUBLIC_OPENEMR_BASE_URL ?? process.env.OPENEMR_BASE_URL ?? "";
  const openInOpenEMR = openemrBase
    ? `${openemrBase.replace(/\/$/, "")}/interface/main/main_screen.php`
    : "";

  return (
    <header className="border-b border-clinical-border bg-clinical-surface">
      <div className="mx-auto flex max-w-screen-2xl flex-wrap items-baseline gap-x-6 gap-y-2 px-6 py-4">
        <h1 className="text-2xl font-semibold tracking-tight">{name}</h1>
        <Field label="DOB" value={age !== null ? `${dob} (${age}y)` : dob} />
        <Field label="Sex" value={sex} />
        <Field label="MRN" value={mrn} mono />
        <div className="ml-auto flex items-center gap-3">
          {openInOpenEMR && (
            <a
              href={openInOpenEMR}
              target="_blank"
              rel="noopener"
              className="rounded border border-clinical-border px-2.5 py-1 text-xs text-clinical-text hover:bg-slate-50"
              title="Open this patient in the OpenEMR PHP chart (scheduling, billing, full chart history)"
            >
              Open in OpenEMR ↗
            </a>
          )}
          {active ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-green-50 px-2.5 py-0.5 text-xs font-medium text-clinical-success">
              <span className="h-1.5 w-1.5 rounded-full bg-clinical-success" />
              Active
            </span>
          ) : (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-50 px-2.5 py-0.5 text-xs font-medium text-clinical-muted">
              Inactive
            </span>
          )}
        </div>
      </div>
    </header>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-1.5 text-sm">
      <span className="text-clinical-muted">{label}:</span>
      <span className={mono ? "font-mono tabular-nums" : ""}>{value}</span>
    </div>
  );
}

function formatName(p: Patient): string {
  const n = p.name?.find((x) => x.use === "official") ?? p.name?.[0];
  if (!n) return "Unknown patient";
  const family = n.family ?? "";
  const given = (n.given ?? []).join(" ");
  const prefix = (n.prefix ?? []).join(" ");
  const suffix = (n.suffix ?? []).join(" ");
  return [prefix, given, family, suffix].filter(Boolean).join(" ").trim() || "Unknown patient";
}

function findMrn(p: Patient): string | undefined {
  // FHIR MRN convention: type.coding includes http://terminology.hl7.org/CodeSystem/v2-0203 with code "MR".
  const mr = p.identifier?.find((id) =>
    id.type?.coding?.some((c) => c.code === "MR") || id.use === "official"
  );
  return mr?.value;
}

function calcAge(dob: string): number {
  const birth = new Date(dob);
  const now = new Date();
  let age = now.getFullYear() - birth.getFullYear();
  const m = now.getMonth() - birth.getMonth();
  if (m < 0 || (m === 0 && now.getDate() < birth.getDate())) age--;
  return age;
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
