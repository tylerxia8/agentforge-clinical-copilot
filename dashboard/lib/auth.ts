/**
 * Auth.js v5 configuration.
 *
 * Provider: OpenEMR's OAuth2/OIDC server at /oauth2/default. We use
 * the authorization-code flow with PKCE — that's the right grant
 * for an interactive web client where the user consents to scopes
 * in their browser. (The agent service uses password grant for
 * its own headless service identity; that's a different code path.)
 *
 * Token lifecycle:
 *   - access_token (1h default in OpenEMR) lives in the encrypted
 *     session cookie. Server components read it via `auth()` and
 *     call FHIR with it server-side. The browser never sees it.
 *   - refresh_token rolls the access_token transparently when it
 *     gets within 60s of expiry. If refresh fails (revoked,
 *     network), we mark the session error and the user is bounced
 *     back to /login on next request.
 *
 * Why NOT iron-session / cookie-only / DIY OAuth: Auth.js v5
 * handles PKCE, state CSRF, JWT validation, and refresh-token
 * rotation for us. Reimplementing those is the "rolled my own
 * crypto" story we don't want to defend in a clinical context.
 */

import NextAuth, { type DefaultSession } from "next-auth";

const issuer = process.env.OPENEMR_OAUTH_ISSUER;

export const { handlers, signIn, signOut, auth } = NextAuth({
  trustHost: true,
  session: { strategy: "jwt" },
  pages: { signIn: "/login" },
  providers: [
    {
      id: "openemr",
      name: "OpenEMR",
      type: "oauth",
      clientId: process.env.OPENEMR_OAUTH_CLIENT_ID,
      clientSecret: process.env.OPENEMR_OAUTH_CLIENT_SECRET,
      // OpenEMR exposes OIDC discovery; using `wellKnown` lets
      // Auth.js fetch authorization_endpoint, token_endpoint,
      // userinfo_endpoint, jwks_uri at startup so we don't hardcode
      // them and drift if OpenEMR changes paths.
      wellKnown: issuer
        ? `${issuer}/.well-known/openid-configuration`
        : undefined,
      authorization: {
        params: {
          // FHIR scopes mirror what the agent service was registered
          // with — Patient + Condition + AllergyIntolerance +
          // MedicationRequest + Encounter + CareTeam read. Add scopes
          // here if a future card needs more.
          scope: [
            "openid",
            "offline_access",
            "profile",
            "api:fhir",
            "user/Patient.read",
            "user/Condition.read",
            "user/AllergyIntolerance.read",
            "user/MedicationRequest.read",
            "user/Encounter.read",
            "user/CareTeam.read",
            "user/Observation.read",
            "user/Immunization.read",
          ].join(" "),
          // PKCE is on by default in Auth.js v5 for OIDC providers.
        },
      },
      idToken: true,
      checks: ["pkce", "state"],
      profile(profile) {
        // OpenEMR's id_token has `sub` (user uuid) and may include
        // `fname`/`lname`. Fall back to `preferred_username` which
        // OpenEMR sets to the OE login name.
        const fname = (profile as Record<string, unknown>).fname as string | undefined;
        const lname = (profile as Record<string, unknown>).lname as string | undefined;
        const name =
          [fname, lname].filter(Boolean).join(" ") ||
          (profile.preferred_username as string | undefined) ||
          (profile.name as string | undefined) ||
          (profile.sub as string);
        return {
          id: profile.sub as string,
          name,
          email: (profile.email as string | undefined) ?? null,
        };
      },
    },
  ],
  callbacks: {
    async jwt({ token, account }) {
      // First sign-in: stash the OAuth tokens on the JWT. Subsequent
      // requests reach this callback without an `account` (it's only
      // present right after the OAuth callback).
      if (account) {
        token.accessToken = account.access_token;
        token.refreshToken = account.refresh_token;
        token.expiresAt = account.expires_at;
        return token;
      }

      // Token is still valid — return as-is.
      const expiresAt = (token.expiresAt as number | undefined) ?? 0;
      if (expiresAt && Date.now() < expiresAt * 1000 - 60_000) {
        return token;
      }

      // Try refresh. If we don't have a refresh_token, force re-auth.
      if (!token.refreshToken) {
        return { ...token, error: "RefreshTokenMissing" };
      }
      try {
        const refreshed = await refreshAccessToken(token.refreshToken as string);
        return {
          ...token,
          accessToken: refreshed.access_token,
          refreshToken: refreshed.refresh_token ?? token.refreshToken,
          expiresAt: Math.floor(Date.now() / 1000) + (refreshed.expires_in ?? 3600),
          error: undefined,
        };
      } catch {
        return { ...token, error: "RefreshAccessTokenError" };
      }
    },
    async session({ session, token }) {
      session.accessToken = token.accessToken as string | undefined;
      session.error = token.error as string | undefined;
      return session;
    },
  },
});

async function refreshAccessToken(refreshToken: string): Promise<{
  access_token: string;
  refresh_token?: string;
  expires_in?: number;
}> {
  const url = `${issuer}/token`;
  const clientId = process.env.OPENEMR_OAUTH_CLIENT_ID ?? "";
  const clientSecret = process.env.OPENEMR_OAUTH_CLIENT_SECRET ?? "";
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: clientId,
      client_secret: clientSecret,
    }),
  });
  if (!resp.ok) throw new Error(`refresh failed: ${resp.status}`);
  return resp.json();
}

declare module "next-auth" {
  interface Session extends DefaultSession {
    accessToken?: string;
    error?: string;
  }
}
