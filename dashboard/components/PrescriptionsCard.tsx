import { Card, EmptyState, ErrorState } from "@/components/Card";
import { fhirGet, safeFetch, unwrap } from "@/lib/fhir";
import { drugLabel, dosageLine } from "@/components/MedicationsCard";
import type { FhirBundle, MedicationRequest } from "@/lib/fhir-types";

/**
 * "Prescriptions" in OpenEMR = orders the prescriber wrote, regardless
 * of whether they're still active. In FHIR: MedicationRequest where
 * intent=order. We surface up to 50 most-recent so a renewal-heavy
 * patient doesn't blow up the card.
 */
const MAX = 50;

export async function PrescriptionsCard({ patientId }: { patientId: string }) {
  const { data, error } = await safeFetch(() =>
    fhirGet<FhirBundle<MedicationRequest>>("/MedicationRequest", {
      patient: patientId,
      intent: "order",
      _count: String(MAX),
    })
  );

  if (error) {
    return (
      <Card title="Prescriptions" compact>
        <ErrorState status={error.status} resource="MedicationRequest" />
      </Card>
    );
  }

  const items = unwrap(data!).sort(byMostRecent);

  return (
    <Card title="Prescriptions" count={items.length}>
      {items.length === 0 ? (
        <EmptyState message="No prescriptions on file." />
      ) : (
        <ul className="divide-y divide-clinical-border">
          {items.slice(0, MAX).map((m) => (
            <li key={m.id} className="py-2 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{drugLabel(m)}</span>
                <span className="shrink-0 text-xs text-clinical-muted tabular-nums">
                  {m.authoredOn ? formatDate(m.authoredOn) : ""}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-clinical-muted">
                <StatusChip status={m.status} />
                {dosageLine(m) ? ` · ${dosageLine(m)}` : ""}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function StatusChip({ status }: { status?: string }) {
  if (!status) return <span className="text-clinical-muted">unknown</span>;
  const tone =
    status === "active"
      ? "text-clinical-success"
      : status === "stopped" || status === "cancelled" || status === "entered-in-error"
      ? "text-clinical-danger"
      : status === "completed"
      ? "text-clinical-muted"
      : "text-clinical-text";
  return <span className={tone}>{status}</span>;
}

function byMostRecent(a: MedicationRequest, b: MedicationRequest): number {
  return (b.authoredOn ?? "").localeCompare(a.authoredOn ?? "");
}

function formatDate(s: string): string {
  return s.slice(0, 10);
}
