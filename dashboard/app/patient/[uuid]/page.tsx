import { Suspense } from "react";
import { auth } from "@/lib/auth";
import { fhirGet } from "@/lib/fhir";
import type { Patient } from "@/lib/fhir-types";
import { AppHeader } from "@/components/AppHeader";
import { PatientHeader } from "@/components/PatientHeader";
import { Card } from "@/components/Card";
import { AllergiesCard } from "@/components/AllergiesCard";
import { ProblemsCard } from "@/components/ProblemsCard";
import { MedicationsCard } from "@/components/MedicationsCard";
import { PrescriptionsCard } from "@/components/PrescriptionsCard";
import { CareTeamCard } from "@/components/CareTeamCard";
import { EncountersCard } from "@/components/EncountersCard";

interface Props {
  params: Promise<{ uuid: string }>;
}

/**
 * Patient dashboard route — the main deliverable. Each card is its
 * own server component wrapped in Suspense so they stream
 * independently: a slow Encounter query doesn't block Allergies
 * from rendering. Original PHP dashboard was synchronous + blocked
 * on the slowest section; this is one of the gains called out in
 * PATIENT_DASHBOARD_MIGRATION.md §3.
 *
 * The header is awaited inline because the page can't render
 * without identity context — patient name in the H1 is a clinical
 * grounding requirement, not optional UI.
 */
export default async function PatientDashboard({ params }: Props) {
  const { uuid } = await params;
  const session = await auth();
  const patient = await fhirGet<Patient>(`/Patient/${uuid}`);

  return (
    <div className="min-h-screen">
      <AppHeader user={session?.user ?? undefined} />
      <PatientHeader patient={patient} />

      <main className="mx-auto max-w-screen-2xl px-6 py-6">
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Suspense fallback={<CardSkeleton title="Allergies" />}>
            <AllergiesCard patientId={uuid} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Problem List" />}>
            <ProblemsCard patientId={uuid} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Medications" />}>
            <MedicationsCard patientId={uuid} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Prescriptions" />}>
            <PrescriptionsCard patientId={uuid} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Care Team" />}>
            <CareTeamCard patientId={uuid} />
          </Suspense>
          <Suspense fallback={<CardSkeleton title="Encounter History" />}>
            <EncountersCard patientId={uuid} />
          </Suspense>
        </div>
      </main>
    </div>
  );
}

function CardSkeleton({ title }: { title: string }) {
  return (
    <Card title={title}>
      <div className="space-y-2 py-1">
        <div className="h-3 w-3/4 animate-pulse rounded bg-slate-100" />
        <div className="h-3 w-1/2 animate-pulse rounded bg-slate-100" />
        <div className="h-3 w-2/3 animate-pulse rounded bg-slate-100" />
      </div>
    </Card>
  );
}
