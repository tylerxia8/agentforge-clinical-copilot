/**
 * FHIR R4 resource types — narrow to the fields the dashboard
 * actually renders. Full FHIR resources have ~80 fields each;
 * surfacing only what we display keeps callers honest about what
 * the UI depends on.
 *
 * Unknown/extension fields pass through as `unknown` rather than
 * `any` — forces callers to narrow if they want to read them.
 */

export interface FhirCoding {
  system?: string;
  code?: string;
  display?: string;
}

export interface FhirCodeableConcept {
  coding?: FhirCoding[];
  text?: string;
}

export interface FhirReference {
  reference?: string;
  display?: string;
}

export interface FhirHumanName {
  use?: string;
  family?: string;
  given?: string[];
  prefix?: string[];
  suffix?: string[];
}

export interface FhirIdentifier {
  use?: string;
  type?: FhirCodeableConcept;
  system?: string;
  value?: string;
}

export interface Patient {
  resourceType: "Patient";
  id: string;
  active?: boolean;
  name?: FhirHumanName[];
  gender?: "male" | "female" | "other" | "unknown";
  birthDate?: string;
  identifier?: FhirIdentifier[];
  telecom?: { system?: string; value?: string; use?: string }[];
  address?: { line?: string[]; city?: string; state?: string; postalCode?: string }[];
}

export interface AllergyIntolerance {
  resourceType: "AllergyIntolerance";
  id: string;
  clinicalStatus?: FhirCodeableConcept;
  verificationStatus?: FhirCodeableConcept;
  type?: "allergy" | "intolerance";
  category?: ("food" | "medication" | "environment" | "biologic")[];
  criticality?: "low" | "high" | "unable-to-assess";
  code?: FhirCodeableConcept;
  patient?: FhirReference;
  recordedDate?: string;
  reaction?: {
    manifestation?: FhirCodeableConcept[];
    severity?: "mild" | "moderate" | "severe";
    description?: string;
  }[];
}

export interface Condition {
  resourceType: "Condition";
  id: string;
  clinicalStatus?: FhirCodeableConcept;
  verificationStatus?: FhirCodeableConcept;
  category?: FhirCodeableConcept[];
  severity?: FhirCodeableConcept;
  code?: FhirCodeableConcept;
  subject?: FhirReference;
  onsetDateTime?: string;
  recordedDate?: string;
}

export interface MedicationRequest {
  resourceType: "MedicationRequest";
  id: string;
  status?: "active" | "on-hold" | "cancelled" | "completed" | "entered-in-error" | "stopped" | "draft" | "unknown";
  intent?: "proposal" | "plan" | "order" | "original-order" | "reflex-order" | "filler-order" | "instance-order" | "option";
  medicationCodeableConcept?: FhirCodeableConcept;
  medicationReference?: FhirReference;
  subject?: FhirReference;
  authoredOn?: string;
  requester?: FhirReference;
  dosageInstruction?: {
    text?: string;
    timing?: { repeat?: { frequency?: number; period?: number; periodUnit?: string } };
    doseAndRate?: { doseQuantity?: { value?: number; unit?: string } }[];
    route?: FhirCodeableConcept;
  }[];
}

export interface CareTeam {
  resourceType: "CareTeam";
  id: string;
  status?: "proposed" | "active" | "suspended" | "inactive" | "entered-in-error";
  category?: FhirCodeableConcept[];
  name?: string;
  subject?: FhirReference;
  participant?: {
    role?: FhirCodeableConcept[];
    member?: FhirReference;
    onBehalfOf?: FhirReference;
  }[];
}

export interface Encounter {
  resourceType: "Encounter";
  id: string;
  status?: "planned" | "arrived" | "triaged" | "in-progress" | "onleave" | "finished" | "cancelled";
  class?: FhirCoding;
  type?: FhirCodeableConcept[];
  subject?: FhirReference;
  period?: { start?: string; end?: string };
  reasonCode?: FhirCodeableConcept[];
  participant?: { individual?: FhirReference }[];
}

export interface FhirBundle<T> {
  resourceType: "Bundle";
  type?: string;
  total?: number;
  entry?: { resource?: T }[];
}
