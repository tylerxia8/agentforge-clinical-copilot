/**
 * FHIR client. One function: `fhirGet`.
 *
 * Server-component-only. The bearer token reads from the active
 * Auth.js session, which lives in an HTTP-only cookie. Calling this
 * from a client component would expose the access token in the
 * browser network tab — by design, that's a typecheck error
 * (`auth()` is async + uses `headers()`, both of which throw if
 * called outside a server context).
 *
 * Returns parsed JSON or throws. The caller decides how to render
 * the error — usually as a per-card empty state, not a full-page
 * crash, so an Allergies fetch failure doesn't blank the whole
 * dashboard.
 */

import { auth } from "@/lib/auth";
import type { FhirBundle } from "@/lib/fhir-types";

export class FhirError extends Error {
  constructor(public status: number, public resource: string, message: string) {
    super(`FHIR ${resource} ${status}: ${message}`);
    this.name = "FhirError";
  }
}

export async function fhirGet<T>(
  path: string,
  params: Record<string, string> = {},
): Promise<T> {
  const session = await auth();
  if (!session?.accessToken) {
    throw new FhirError(401, path, "no access token in session");
  }

  const base = process.env.OPENEMR_BASE_URL;
  if (!base) {
    throw new FhirError(500, path, "OPENEMR_BASE_URL not set");
  }

  const url = new URL(`${base}/apis/default/fhir${path}`);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);

  const resp = await fetch(url.toString(), {
    headers: {
      Authorization: `Bearer ${session.accessToken}`,
      Accept: "application/fhir+json",
    },
    // FHIR data is patient-state-of-the-moment; never cache between
    // requests. Stale meds in a chart is a clinical hazard.
    cache: "no-store",
  });

  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new FhirError(resp.status, path, body.slice(0, 200));
  }
  return (await resp.json()) as T;
}

/** Pull `.resource` out of every entry in a FHIR Bundle. */
export function unwrap<T>(bundle: FhirBundle<T>): T[] {
  return (bundle.entry ?? []).map((e) => e.resource).filter((r): r is T => Boolean(r));
}

/**
 * Wrap a card's data fetch so a 401/403/404/500 produces an empty
 * state instead of crashing the whole dashboard render. Returns
 * `{ data: T, error: null }` or `{ data: null, error: FhirError }`.
 */
export async function safeFetch<T>(
  fn: () => Promise<T>,
): Promise<{ data: T | null; error: FhirError | null }> {
  try {
    return { data: await fn(), error: null };
  } catch (e) {
    if (e instanceof FhirError) return { data: null, error: e };
    throw e; // genuine bug — let Next.js error boundary handle it
  }
}
