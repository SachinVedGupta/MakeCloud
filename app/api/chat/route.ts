import { NextResponse } from "next/server";
export async function POST() {
  return NextResponse.json(
    { detail: "Use /api/cloud/sessions for the infrastructure workflow." },
    { status: 410 },
  );
}
