import { NextResponse } from "next/server";
import { clearVehicleContext } from "@/server/vehicle-session";

export function GET(request: Request) {
  void request;
  const response = new NextResponse(null, { status: 307, headers: { Location: "/" } });
  clearVehicleContext(response);
  response.headers.set("Cache-Control", "private, no-cache, no-store, must-revalidate");
  return response;
}
