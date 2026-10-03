import { NextRequest, NextResponse } from "next/server";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const route = path.join("/");
  if (
    !/^(health|sessions|sessions\/[0-9a-f-]{36}(\/(messages|code|plan|apply))?)$/.test(
      route,
    )
  ) {
    return NextResponse.json({ detail: "Unknown endpoint." }, { status: 404 });
  }
  if (!["GET", "HEAD"].includes(request.method)) {
    const origin = request.headers.get("origin");
    if (origin && new URL(origin).host !== request.headers.get("host")) {
      return NextResponse.json(
        { detail: "Cross-origin requests are not allowed." },
        { status: 403 },
      );
    }
  }
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (process.env.MAKECLOUD_API_TOKEN)
    headers.Authorization = `Bearer ${process.env.MAKECLOUD_API_TOKEN}`;
  try {
    const body = request.method === "GET" ? undefined : await request.text();
    if (body && body.length > 150000)
      return NextResponse.json(
        { detail: "Request too large." },
        { status: 413 },
      );
    const response = await fetch(
      `${process.env.MAKECLOUD_BACKEND_URL || "http://127.0.0.1:5000"}/${route}`,
      {
        method: request.method,
        headers,
        body,
        cache: "no-store",
        signal: AbortSignal.timeout(1250000),
      },
    );
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return NextResponse.json(
      {
        detail:
          "Backend unavailable or timed out. Check that it is running, then reload the conversation to inspect operation status.",
      },
      { status: 503 },
    );
  }
}
export { proxy as GET, proxy as POST, proxy as PUT };
