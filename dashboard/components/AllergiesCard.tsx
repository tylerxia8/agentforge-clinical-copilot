import { Card, EmptyState, ErrorState } from "@/components/Card";
import { fhirGet, safeFetch, unwrap } from "@/lib/fhir";
import type { AllergyIntolerance, FhirBundle, FhirCodeableConcept } from "@/lib/fhir-types";

export async function AllergiesCard({ patientId }: { patientId: string }) {
  const { data, error } = await safeFetch(() =>
    fhirGet<FhirBundle<AllergyIntolerance>>("/AllergyIntolerance", { patient: patientId })
  );

  if (error) {
    return (
      <Card title="Allergies" compact>
        <ErrorState status={error.status} resource="AllergyIntolerance" />
      </Card>
    );
  }

  const items = unwrap(data!).filter(isActive);
  const hasHigh = items.some((a) => a.criticality === "high");

  return (
    <Card
      title="Allergies"
      count={items.length}
      badge={hasHigh ? { text: "High criticality", tone: "danger" } : undefined}
    >
      {items.length === 0 ? (
        <EmptyState message="No known allergies on file (NKDA)." />
      ) : (
        <ul className="divide-y divide-clinical-border">
          {items.map((a) => (
            <li key={a.id} className="py-2 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{labelOf(a.code)}</span>
                <span className="shrink-0 text-xs text-clinical-muted">
                  {a.verificationStatus?.coding?.[0]?.code ?? ""}
                </span>
              </div>
              {a.reaction?.[0] && (
                <p className="mt-0.5 text-xs text-clinical-muted">
                  {a.reaction[0].manifestation
                    ?.map((m) => m.text ?? m.coding?.[0]?.display)
                    .filter(Boolean)
                    .join(", ")}
                  {a.reaction[0].severity ? ` · ${a.reaction[0].severity}` : ""}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function isActive(a: AllergyIntolerance): boolean {
  // FHIR: clinicalStatus coding `active` (or absent) is on-list.
  const code = a.clinicalStatus?.coding?.[0]?.code;
  return !code || code === "active";
}

function labelOf(c?: FhirCodeableConcept): string {
  return c?.text ?? c?.coding?.[0]?.display ?? c?.coding?.[0]?.code ?? "Unknown substance";
}
