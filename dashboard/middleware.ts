/**
 * Auth middleware. Anything under /patient/* requires a valid
 * session; missing or expired sessions get redirected to /login.
 *
 * Auth.js v5 idiom: export `auth` as the middleware. It reads the
 * session cookie, hydrates the request, and lets the handler decide
 * whether to redirect.
 */

import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";

export default auth((req) => {
  const isAuthRoute = req.nextUrl.pathname.startsWith("/api/auth");
  const isLoginPage = req.nextUrl.pathname === "/login";
  const isAsset = req.nextUrl.pathname.startsWith("/_next") || req.nextUrl.pathname.includes(".");

  if (isAuthRoute || isLoginPage || isAsset) return;

  if (!req.auth) {
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("callbackUrl", req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(loginUrl);
  }

  if (req.auth.error === "RefreshAccessTokenError" || req.auth.error === "RefreshTokenMissing") {
    const loginUrl = new URL("/login", req.url);
    loginUrl.searchParams.set("error", "session_expired");
    return NextResponse.redirect(loginUrl);
  }
});

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
