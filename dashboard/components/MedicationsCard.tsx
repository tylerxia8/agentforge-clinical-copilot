import { Card, EmptyState, ErrorState } from "@/components/Card";
import { fhirGet, safeFetch, unwrap } from "@/lib/fhir";
import type { FhirBundle, FhirCodeableConcept, MedicationRequest } from "@/lib/fhir-types";

/**
 * "Medications" in the OpenEMR UI = the patient's current med list.
 * In FHIR: MedicationRequest with status=active. (The Prescriptions
 * card surfaces every order regardless of status — they're related
 * but distinct datasets.)
 */
export async function MedicationsCard({ patientId }: { patientId: string }) {
  const { data, error } = await safeFetch(() =>
    fhirGet<FhirBundle<MedicationRequest>>("/MedicationRequest", {
      patient: patientId,
      status: "active",
    })
  );

  if (error) {
    return (
      <Card title="Medications" compact>
        <ErrorState status={error.status} resource="MedicationRequest" />
      </Card>
    );
  }

  const items = unwrap(data!);

  return (
    <Card title="Medications" count={items.length}>
      {items.length === 0 ? (
        <EmptyState message="No active medications." />
      ) : (
        <ul className="divide-y divide-clinical-border">
          {items.map((m) => (
            <li key={m.id} className="py-2 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{drugLabel(m)}</span>
                <span className="shrink-0 text-xs text-clinical-muted tabular-nums">
                  {m.authoredOn ? formatDate(m.authoredOn) : ""}
                </span>
              </div>
              {dosageLine(m) && (
                <p className="mt-0.5 text-xs text-clinical-muted">{dosageLine(m)}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function drugLabel(m: MedicationRequest): string {
  const cc = m.medicationCodeableConcept;
  return cc?.text ?? cc?.coding?.[0]?.display ?? m.medicationReference?.display ?? "Unknown medication";
}

export function dosageLine(m: MedicationRequest): string {
  const d = m.dosageInstruction?.[0];
  if (!d) return "";
  if (d.text) return d.text;
  const dose = d.doseAndRate?.[0]?.doseQuantity;
  const route = labelOf(d.route);
  const parts = [
    dose?.value !== undefined ? `${dose.value} ${dose.unit ?? ""}`.trim() : "",
    route,
  ].filter(Boolean);
  return parts.join(" · ");
}

function labelOf(c?: FhirCodeableConcept): string {
  return c?.text ?? c?.coding?.[0]?.display ?? "";
}

function formatDate(s: string): string {
  return s.slice(0, 10);
}
