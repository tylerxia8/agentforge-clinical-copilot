import { redirect } from "next/navigation";
import { auth } from "@/lib/auth";

/**
 * Index landing. Authenticated users get redirected to the patient
 * picker; everyone else hits /login. Keeps the home URL useful as
 * an entrypoint without exposing app shell to the unauthenticated.
 */
export default async function HomePage() {
  const session = await auth();
  if (!session) redirect("/login");
  redirect("/patients");
}
