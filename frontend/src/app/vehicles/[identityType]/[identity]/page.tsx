import { notFound, redirect } from "next/navigation";
import { VehicleWorkspace } from "@/components/vehicle-workspace";
import type { IdentityType } from "@/lib/domain";
import { currentPortalUserSafely } from "@/server/auth-session";
import { currentVehicleContext, matchesVehicleContext } from "@/server/vehicle-session";
export default async function VehiclePage({ params }: { params: Promise<{ identityType: string; identity: string }> }) {
  if (!await currentPortalUserSafely()) redirect("/login");
  const { identityType, identity } = await params; if (identityType !== "plate" && identityType !== "vin") notFound();
  const decodedIdentity = decodeURIComponent(identity);
  const context = await currentVehicleContext();
  if (!context) redirect("/");
  if (!matchesVehicleContext(context, identityType as IdentityType, decodedIdentity)) redirect("/verify-vehicle");
  return <VehicleWorkspace identityType={identityType as IdentityType} identity={decodedIdentity} />;
}
