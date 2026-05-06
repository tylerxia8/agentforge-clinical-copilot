import { signIn } from "@/lib/auth";

interface PageProps {
  searchParams: Promise<{ callbackUrl?: string; error?: string }>;
}

export default async function LoginPage({ searchParams }: PageProps) {
  const params = await searchParams;
  const callbackUrl = params.callbackUrl ?? "/patients";
  const error = params.error;

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-md rounded-lg border border-clinical-border bg-clinical-surface p-8 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight">OpenEMR Patient Dashboard</h1>
        <p className="mt-2 text-sm text-clinical-muted">
          Sign in with your OpenEMR account to access patient charts.
        </p>

        {error === "session_expired" && (
          <div className="mt-4 rounded border border-clinical-warning/40 bg-amber-50 px-3 py-2 text-sm text-clinical-warning">
            Your session expired. Please sign in again.
          </div>
        )}

        <form
          action={async () => {
            "use server";
            await signIn("openemr", { redirectTo: callbackUrl });
          }}
          className="mt-6"
        >
          <button
            type="submit"
            className="w-full rounded-md bg-clinical-accent px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-clinical-accent focus:ring-offset-2"
          >
            Sign in with OpenEMR
          </button>
        </form>

        <p className="mt-6 text-xs text-clinical-muted">
          Authentication uses OpenEMR&rsquo;s OAuth2 / OIDC server. Your access token
          is stored in an encrypted, HTTP-only session cookie and never reaches the
          browser.
        </p>
      </div>
    </main>
  );
}
