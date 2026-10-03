import { NextRequest, NextResponse } from "next/server";
export function middleware(request: NextRequest) {
  const password = process.env.MAKECLOUD_OPERATOR_PASSWORD;
  let host = "";
  try {
    host = new URL(`http://${request.headers.get("host") || ""}`).hostname;
  } catch {
    host = "";
  }
  const localOnly =
    process.env.NODE_ENV === "development" ||
    process.env.MAKECLOUD_LOCAL_ONLY === "true";
  if (
    !password &&
    localOnly &&
    ["localhost", "127.0.0.1", "[::1]"].includes(host)
  )
    return NextResponse.next();
  const auth = request.headers.get("authorization");
  let valid = false;
  if (password && auth?.startsWith("Basic ")) {
    try {
      valid = atob(auth.slice(6)) === `operator:${password}`;
    } catch {
      valid = false;
    }
  }
  if (valid) return NextResponse.next();
  return new NextResponse(
    "MakeCloud requires operator authentication for remote access. Configure MAKECLOUD_OPERATOR_PASSWORD and use username operator.",
    {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="MakeCloud"',
        "Cache-Control": "no-store",
      },
    },
  );
}
export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
