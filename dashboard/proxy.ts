/**
 * Next.js Proxy (formerly Middleware) — authentication gate.
 *
 * Runs on every request before the page is rendered.
 * If the `dashboard_token` cookie is absent, redirects to /login.
 * The /login page itself and all Next.js internals are excluded from the matcher.
 */

import { NextRequest, NextResponse } from "next/server";

export function proxy(req: NextRequest) {
  const token = req.cookies.get("dashboard_token")?.value;

  if (!token) {
    const loginUrl = new URL("/login", req.url);
    // Preserve the intended destination so we can redirect back after login
    loginUrl.searchParams.set("next", req.nextUrl.pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

export const config = {
  /**
   * Protect all routes except:
   *   /login          — the login page itself
   *   /api/*          — Next.js route handlers (login action lives here)
   *   /_next/*        — Next.js static assets
   *   /favicon.ico    — browser default request
   */
  matcher: ["/((?!login|api|_next|favicon.ico).*)"],
};
