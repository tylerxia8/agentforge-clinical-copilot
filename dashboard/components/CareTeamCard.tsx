import { Card, EmptyState, ErrorState } from "@/components/Card";
import { fhirGet, safeFetch, unwrap } from "@/lib/fhir";
import type { CareTeam, FhirBundle, FhirCodeableConcept } from "@/lib/fhir-types";

export async function CareTeamCard({ patientId }: { patientId: string }) {
  const { data, error } = await safeFetch(() =>
    fhirGet<FhirBundle<CareTeam>>("/CareTeam", { patient: patientId })
  );

  if (error) {
    return (
      <Card title="Care Team" compact>
        <ErrorState status={error.status} resource="CareTeam" />
      </Card>
    );
  }

  const teams = unwrap(data!).filter((t) => t.status !== "inactive" && t.status !== "entered-in-error");
  // Flatten participants across all active teams. Most charts have one
  // care team with several members; UI doesn't need the team grouping.
  const members = teams.flatMap((t) => t.participant ?? []);

  return (
    <Card title="Care Team" count={members.length}>
      {members.length === 0 ? (
        <EmptyState message="No care team members assigned." />
      ) : (
        <ul className="divide-y divide-clinical-border">
          {members.map((p, i) => (
            <li key={`${p.member?.reference ?? i}`} className="py-2 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">
                  {p.member?.display ?? "Unnamed practitioner"}
                </span>
                <span className="shrink-0 text-xs text-clinical-muted">
                  {labelOf(p.role?.[0])}
                </span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function labelOf(c?: FhirCodeableConcept): string {
  return c?.text ?? c?.coding?.[0]?.display ?? c?.coding?.[0]?.code ?? "";
}
