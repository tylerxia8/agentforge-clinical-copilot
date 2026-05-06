import { Card, EmptyState, ErrorState } from "@/components/Card";
import { fhirGet, safeFetch, unwrap } from "@/lib/fhir";
import type { Encounter, FhirBundle, FhirCodeableConcept } from "@/lib/fhir-types";

const MAX_VISIBLE = 10;

/**
 * Encounter history — the "one optional section" we picked off the
 * spec list. Reasonable choice because every chart has one and the
 * data shape is forgiving (a date and a reason are usually enough).
 */
export async function EncountersCard({ patientId }: { patientId: string }) {
  const { data, error } = await safeFetch(() =>
    fhirGet<FhirBundle<Encounter>>("/Encounter", {
      patient: patientId,
      _count: "50",
    })
  );

  if (error) {
    return (
      <Card title="Encounter History" compact>
        <ErrorState status={error.status} resource="Encounter" />
      </Card>
    );
  }

  const items = unwrap(data!).sort(byMostRecent).slice(0, MAX_VISIBLE);
  const total = data!.total ?? unwrap(data!).length;

  return (
    <Card title="Encounter History" count={total}>
      {items.length === 0 ? (
        <EmptyState message="No encounters recorded." />
      ) : (
        <ul className="divide-y divide-clinical-border">
          {items.map((e) => (
            <li key={e.id} className="py-2 text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-medium">{labelOf(e.type?.[0]) || "Visit"}</span>
                <span className="shrink-0 text-xs text-clinical-muted tabular-nums">
                  {e.period?.start ? formatDate(e.period.start) : "—"}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-clinical-muted">
                {[e.class?.display ?? e.class?.code, e.status, reasonText(e)]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </li>
          ))}
        </ul>
      )}
      {total > items.length && (
        <p className="mt-2 text-xs text-clinical-muted">
          Showing {items.length} most-recent of {total}.
        </p>
      )}
    </Card>
  );
}

function reasonText(e: Encounter): string {
  const r = e.reasonCode?.[0];
  return r?.text ?? r?.coding?.[0]?.display ?? "";
}

function byMostRecent(a: Encounter, b: Encounter): number {
  return (b.period?.start ?? "").localeCompare(a.period?.start ?? "");
}

function labelOf(c?: FhirCodeableConcept): string {
  return c?.text ?? c?.coding?.[0]?.display ?? "";
}

function formatDate(s: string): string {
  return s.slice(0, 10);
}
