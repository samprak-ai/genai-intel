/**
 * POST /api/auth/login
 *
 * Validates the supplied token against the FastAPI backend (/api/auth/verify).
 * On success, sets an HttpOnly cookie so the browser sends it automatically on
 * every subsequent request.  The raw token is never exposed to client-side JS.
 *
 * Body: { token: string }
 * Returns: 200 { ok: true } | 401 { error: string }
 */

import { NextRequest, NextResponse } from "next/server";

// API_URL is a private server-side env var (set in Vercel/Railway settings).
// Falls back to NEXT_PUBLIC_API_URL for local dev compatibility.
const API_BASE =
  process.env.API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000";

export async function POST(req: NextRequest) {
  const { token } = (await req.json()) as { token?: string };

  if (!token) {
    return NextResponse.json({ error: "Token required" }, { status: 400 });
  }

  // Validate against the FastAPI token guard
  let backendOk = false;
  try {
    const res = await fetch(`${API_BASE}/api/auth/verify`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    });
    backendOk = res.ok;
  } catch {
    return NextResponse.json(
      { error: "Could not reach API server" },
      { status: 502 }
    );
  }

  if (!backendOk) {
    return NextResponse.json({ error: "Invalid token" }, { status: 401 });
  }

  // Token is valid — set it as an HttpOnly cookie (inaccessible to JS)
  const response = NextResponse.json({ ok: true });
  response.cookies.set("dashboard_token", token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    // No explicit maxAge → session cookie; cleared when browser closes.
    // Set maxAge: 60 * 60 * 24 * 30 for 30-day persistence if preferred.
    maxAge: 60 * 60 * 24 * 30, // 30 days — persistent across browser restarts
  });
  return response;
}
