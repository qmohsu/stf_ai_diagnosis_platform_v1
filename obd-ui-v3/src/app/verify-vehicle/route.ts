import { NextResponse } from "next/server";

export function GET(request: Request) {
  void request;
  const response = new NextResponse(null, { status: 307, headers: { Location: "/" } });
  response.cookies.set("portal_vehicle_context", "", { path: "/", maxAge: 0 });
  response.headers.set("Cache-Control", "private, no-cache, no-store, must-revalidate");
  return response;
}
