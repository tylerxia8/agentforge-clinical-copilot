import { Card, EmptyState, ErrorState } from "@/components/Card";
import { fhirGet, safeFetch, unwrap } from "@/lib/fhir";
import type { Condition, FhirBundle, FhirCodeableConcept } from "@/lib/fhir-types";

export async function ProblemsCard({ patientId }: { patientId: string }) {
  const { data, error } = await safeFetch(() =>
    fhirGet<FhirBundle<Condition>>("/Condition", { patient: patientId })
  );

  if (error) {
    return (
      <Card title="Problem List" compact>
        <ErrorState status={error.status} resource="Condition" />
      </Card>
    );
  }

  const items = unwrap(data!).filter(isActive);

  return (
    <Card title="Problem List" count={items.length}>
      {items.length === 0 ? (
        <EmptyState message="No active problems on the chart." />
      ) : (
        <ul className="divide-y divide-clinical-border">
          {items.map((c) => (
            <li key={c.id} className="py-2 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{labelOf(c.code)}</span>
                <span className="shrink-0 font-mono text-xs text-clinical-muted tabular-nums">
                  {c.code?.coding?.[0]?.code ?? ""}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-clinical-muted">
                {c.verificationStatus?.coding?.[0]?.code ?? "unverified"}
                {c.onsetDateTime ? ` · onset ${formatDate(c.onsetDateTime)}` : ""}
                {c.recordedDate ? ` · recorded ${formatDate(c.recordedDate)}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function isActive(c: Condition): boolean {
  const code = c.clinicalStatus?.coding?.[0]?.code;
  return !code || code === "active" || code === "recurrence" || code === "relapse";
}

function labelOf(c?: FhirCodeableConcept): string {
  return c?.text ?? c?.coding?.[0]?.display ?? "Unknown condition";
}

function formatDate(s: string): string {
  return s.slice(0, 10);
}
