import { redirect } from "next/navigation";
import { VehicleVerification } from "@/components/vehicle-verification";
import { currentPortalUserSafely } from "@/server/auth-session";
import { currentVehicleContext } from "@/server/vehicle-session";

export default async function IndexPage() {
  if (!await currentPortalUserSafely()) redirect("/login");
  const context = await currentVehicleContext();
  if (context) redirect(context.canonicalPath);
  return <VehicleVerification />;
}
