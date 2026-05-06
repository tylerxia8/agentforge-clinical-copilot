import { signOut } from "@/lib/auth";
import Link from "next/link";

interface Props {
  user?: { name?: string | null };
}

export function AppHeader({ user }: Props) {
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
