import { signOut } from "@/lib/auth";
import Link from "next/link";

interface Props {
  user?: { name?: string | null };
}

export function AppHeader({ user }: Props) {
  // Dashboard ↔ OpenEMR cross-app navigation: requirement clarification
  // says "a user can navigate from the new dashboard to existing OpenEMR
  // pages." OPENEMR_BASE_URL is server-side only (lib/fhir reads it); we
  // need it client-side too for this link, so it's set as a public env
  // var (NEXT_PUBLIC_…) on the dashboard service.
  const openemrUrl = process.env.NEXT_PUBLIC_OPENEMR_BASE_URL ?? process.env.OPENEMR_BASE_URL ?? "";
  return (
    <header className="border-b border-clinical-border bg-clinical-surface">
      <div className="mx-auto flex max-w-screen-2xl items-center justify-between px-6 py-3">
        <Link href="/patients" className="flex items-center gap-2 text-sm font-semibold tracking-tight">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="h-5 w-5 text-clinical-accent"
            aria-hidden="true"
          >
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
          OpenEMR Patient Dashboard
        </Link>
        <div className="flex items-center gap-3 text-sm">
          {openemrUrl && (
            <a
              href={openemrUrl}
              target="_blank"
              rel="noopener"
              className="rounded border border-clinical-border px-3 py-1 text-xs text-clinical-text hover:bg-slate-50"
              title="Open the rest of OpenEMR (scheduling, billing, full chart, etc.) in a new tab"
            >
              ← OpenEMR
            </a>
          )}
          {user?.name && <span className="text-clinical-muted">{user.name}</span>}
          <form
            action={async () => {
              "use server";
              await signOut({ redirectTo: "/login" });
            }}
          >
            <button
              type="submit"
              className="rounded border border-clinical-border px-3 py-1 text-xs hover:bg-slate-50"
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    </header>
  );
}
