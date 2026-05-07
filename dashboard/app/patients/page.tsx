import Link from "next/link";
import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";
import { fhirGet, FhirError, unwrap } from "@/lib/fhir";
import { AppHeader } from "@/components/AppHeader";
import type { FhirBundle, Patient } from "@/lib/fhir-types";

/**
 * Patient picker. Lists patients the signed-in user can see (via
 * the OAuth scopes on their session). Click-through goes to the
 * full dashboard at /patient/[uuid].
 *
 * NOT a search — for an MVP this is fine; production would add a
 * Patient name/MRN search box backed by FHIR's `_filter` or the
 * REST `/api/patient?search=` endpoint. Out of scope for the W2
 * port — the spec is "modernize the dashboard," not "build a new
 * search experience."
 */
export default async function PatientsPage() {
  const session = await auth();
  // Same 401-bounce pattern as /patient/[uuid] — bounce to /login
  // with a session_expired marker rather than crashing the SSR.
  let bundle: FhirBundle<Patient>;
  try {
    bundle = await fhirGet<FhirBundle<Patient>>("/Patient", { _count: "50" });
  } catch (e) {
    if (e instanceof FhirError && (e.status === 401 || e.status === 403)) {
      redirect("/login?error=session_expired&callbackUrl=/patients");
    }
    throw e;
  }
  const patients = unwrap(bundle);

  return (
    <div className="min-h-screen">
      <AppHeader user={session?.user ?? undefined} />
      <main className="mx-auto max-w-screen-2xl px-6 py-6">
        <h1 className="text-lg font-semibold tracking-tight">Patients</h1>
        <p className="mt-1 text-sm text-clinical-muted">
          {patients.length} patient{patients.length === 1 ? "" : "s"} in your scope.
        </p>

        <ul className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {patients.map((p) => (
            <li key={p.id}>
              <Link
                href={`/patient/${p.id}`}
                className="block rounded-lg border border-clinical-border bg-clinical-surface p-4 shadow-sm hover:border-clinical-accent hover:shadow"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-medium">{formatName(p)}</span>
                  <span className="text-xs text-clinical-muted">
                    {p.gender ? p.gender.charAt(0).toUpperCase() : "?"}
                    {" · "}
                    {p.birthDate ?? "—"}
                  </span>
                </div>
                <p className="mt-1 font-mono text-xs text-clinical-muted tabular-nums">
                  MRN {findMrn(p) ?? p.id}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}

function formatName(p: Patient): string {
  const n = p.name?.find((x) => x.use === "official") ?? p.name?.[0];
  if (!n) return "Unknown patient";
  return [(n.given ?? []).join(" "), n.family].filter(Boolean).join(" ");
}

function findMrn(p: Patient): string | undefined {
  return (
    p.identifier?.find((id) => id.type?.coding?.some((c) => c.code === "MR"))?.value ??
    p.identifier?.[0]?.value
  );
}
