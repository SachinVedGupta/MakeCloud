import { NextRequest, NextResponse } from "next/server";
import { isIP } from "node:net";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;
const allowed = /^(health|catalog|connections(\/(begin|google\/callback|template\/[A-Za-z0-9_-]{40,100}|[0-9a-f-]{36}(\/(instructions|projects|verify))?))?|auth\/(me|login|register|logout|request-link|verify|reset)|sessions|sessions\/[0-9a-f-]{36}(\/(messages|requirements|code|proposal|plan|apply|cancel|recover|archive|connection|export|revisions\/[0-9]+(\/restore)?))?)$/;
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  if (!allowed.test(route)) return NextResponse.json({ detail: "Unknown endpoint." }, { status: 404 });
  if (!["GET", "HEAD"].includes(request.method)) {
    const origin = request.headers.get("origin");
    try {
      if (request.headers.get("sec-fetch-site") === "cross-site" || (origin && new URL(origin).host !== request.headers.get("host")))
        return NextResponse.json({ detail: "Cross-origin requests are not allowed." }, { status: 403 });
    } catch {
      return NextResponse.json({ detail: "Invalid request origin." }, { status: 403 });
    }
    if (Number(request.headers.get("content-length") || 0) > 160000)
      return NextResponse.json({ detail: "Request too large." }, { status: 413 });
    if (request.headers.get("content-length") !== "0" && request.headers.get("content-type") && !request.headers.get("content-type")?.startsWith("application/json"))
      return NextResponse.json({ detail: "Use JSON requests." }, { status: 415 });
  }
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (process.env.MAKECLOUD_API_TOKEN) headers.Authorization = `Bearer ${process.env.MAKECLOUD_API_TOKEN}`;
  // Vercel/Caddy must replace untrusted forwarded headers at the entrypoint.
  const clientIp = request.headers.get("x-forwarded-for")?.split(",")[0].trim();
  if (clientIp && isIP(clientIp)) headers["X-MakeCloud-Client-IP"] = clientIp;
  const cookie = request.cookies.get("makecloud_auth")?.value;
  if (cookie) headers.Cookie = `makecloud_auth=${encodeURIComponent(cookie)}`;
  const nonce = request.cookies.get("makecloud_oauth")?.value;
  if (nonce && route === "connections/google/callback") headers.Cookie = `${headers.Cookie || ""}; makecloud_oauth=${encodeURIComponent(nonce)}`;
  try {
    let body: string | undefined;
    if (!["GET", "HEAD"].includes(request.method)) {
      const reader = request.body?.getReader();
      const chunks: Uint8Array[] = [];
      let length = 0;
      if (reader) {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          length += chunk.value.byteLength;
          if (length > 160000) {
            await reader.cancel();
            return NextResponse.json({ detail: "Request too large." }, { status: 413 });
          }
          chunks.push(chunk.value);
        }
      }
      body = length ? Buffer.concat(chunks).toString("utf8") : undefined;
    }
    const queryParams = new URLSearchParams();
    for (const key of route === "connections/google/callback" ? ["state", "code", "error"] : ["archived"]) {
      const value = request.nextUrl.searchParams.get(key);
      if (value && value.length <= 4096) queryParams.set(key, value);
    }
    const query = queryParams.size ? "?" + queryParams.toString() : "";
    const response = await fetch(`${process.env.MAKECLOUD_BACKEND_URL || "http://127.0.0.1:5010"}/${route}${query}`, {
      method: request.method, headers, body, redirect: "manual", cache: "no-store", signal: AbortSignal.timeout(55000),
    });
    const result = new NextResponse(await response.text(), {
      status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });
    for (const value of response.headers.getSetCookie()) result.headers.append("Set-Cookie", value);
    if (route === "connections/google/callback" && response.status === 303) {
      const location = response.headers.get("location");
      const publicUrl = process.env.MAKECLOUD_PUBLIC_URL;
      if (!location || !publicUrl || new URL(location).origin !== new URL(publicUrl).origin) return NextResponse.json({ detail: "Invalid callback destination." }, { status: 502 });
      result.headers.set("Location", location);
      result.headers.set("Referrer-Policy", "no-referrer");
    }
    return result;
  } catch {
    return NextResponse.json({ detail: "Backend unavailable or timed out. Reload the project to inspect its saved status." }, { status: 503 });
  }
}
export { proxy as GET, proxy as POST, proxy as PUT, proxy as DELETE };
