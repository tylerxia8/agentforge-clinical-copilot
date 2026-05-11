"""Typed Pydantic messages that cross agent boundaries.

The architecture says (see ARCHITECTURE.md §"Inter-agent
communication"): no agent reads another agent's internal state
directly — only the typed messages defined here. Keeping them
narrow is the conflict-of-interest firewall: the Judge cannot see
the Red Team's reasoning chain because the ``AttackAttempt`` model
doesn't expose it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ThreatCategory(str, Enum):
    """Pre-approved attack categories from THREAT_MODEL.md.

    The Orchestrator (when wired) can only launch campaigns against
    categories on this enum — net-new categories require an explicit
    human approval gate (see ARCHITECTURE.md §"Human approval gates").
    """

    INDIRECT_INJECTION = "indirect_injection"
    CROSS_PATIENT = "cross_patient"
    COST_AMPLIFICATION = "cost_amplification"
    # The following are pre-approved but not yet wired for MVP:
    DIRECT_INJECTION = "direct_injection"
    STATE_CORRUPTION = "state_corruption"
    TOOL_MISUSE = "tool_misuse"
    IDENTITY_EXPLOIT = "identity_exploit"


class ChatMessage(BaseModel):
    """One turn in the attack transcript (either user or assistant)."""

    role: Literal["user", "assistant"]
    content: str


class DocumentPayload(BaseModel):
    """For indirect-injection campaigns — a file the Red Team Agent
    plans to upload via /agent/extract or drag-drop into the chat
    panel. Content is the *injected* text the model would extract;
    doc_type matches the W2 extractor's enum."""

    doc_type: Literal["lab_pdf", "intake_form", "hl7v2", "docx", "xlsx", "tiff"]
    filename: str
    payload_text: str
    description: str = Field(
        ...,
        description="One-line description of the injection technique "
                    "(e.g. 'OCR-readable instruction injection in lab "
                    "comments column').",
    )


class AttackCampaign(BaseModel):
    """Orchestrator → Red Team. Pre-MVP, the runner constructs these
    directly; once the Orchestrator Agent is wired it will emit them.

    See ARCHITECTURE.md §"Orchestrator Agent" for the prioritization
    logic that will eventually drive ``rationale``.
    """

    campaign_id: UUID = Field(default_factory=uuid4)
    category: ThreatCategory
    seed_payload: str | None = Field(
        None,
        description="For 'mutate' mode — the prior attempt's prompt "
                    "that the Red Team Agent should produce a variant "
                    "of.",
    )
    hop_budget: int = Field(
        10,
        ge=1,
        le=50,
        description="Maximum number of Red Team attempts before the "
                    "campaign halts and rotates.",
    )
    cost_budget_usd: float = Field(
        2.00,
        ge=0.0,
        description="USD ceiling for the whole campaign. Enforced "
                    "deterministically before each agent invocation.",
    )
    stop_after_consecutive_fails: int = Field(
        5,
        ge=1,
        description="Halt the campaign early if the Judge returns this "
                    "many consecutive 'fail' verdicts (the surface is "
                    "likely defended; rotate rather than burn tokens).",
    )
    rationale: str = Field(
        "",
        description="Why this campaign now. Filled by the Orchestrator "
                    "Agent when wired; empty for MVP hardcoded runs.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AttackAttempt(BaseModel):
    """Red Team → target → Judge. Contains the attack transcript and
    the target's response. **Does NOT** contain the Red Team's
    internal reasoning — the Judge must verdict from inputs and
    outputs alone, by hard rule (ARCHITECTURE.md §"Judge Agent")."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    attempt_id: UUID = Field(default_factory=uuid4)
    campaign_id: UUID
    category: ThreatCategory
    mode: Literal["generate", "mutate"] = "generate"
    parent_attempt_id: UUID | None = None

    # The attack itself
    messages: list[ChatMessage] = Field(default_factory=list)
    uploaded_documents: list[DocumentPayload] = Field(default_factory=list)
    target_patient_uuid: str | None = Field(
        None,
        description="The patient_uuid the attack was launched against "
                    "(usually a demo patient like Farrah Rolle).",
    )

    # The target's response
    target_responses: list[ChatMessage] = Field(default_factory=list)
    target_status_codes: list[int] = Field(default_factory=list)
    target_sources: list[str] = Field(
        default_factory=list,
        description="The sources[] array returned by /demo/chat. Used "
                    "by deterministic Judge signals (e.g. cross-patient "
                    "UUID match).",
    )
    target_refused: bool | None = Field(
        None,
        description="Whether the target's response had refused=true. "
                    "Distinguishes a successful refusal from a "
                    "structural failure.",
    )

    # Bookkeeping
    cost_usd: float = 0.0
    langfuse_trace_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    error: str | None = Field(
        None,
        description="If the attempt failed to execute end-to-end "
                    "(network error, target timeout, Red Team refused "
                    "to generate). Distinct from 'attack succeeded' — "
                    "the Judge gets this attempt with whatever was "
                    "captured.",
    )


class JudgeVerdict(BaseModel):
    """Judge → orchestrator (or, for MVP, the run log).

    The verdict is the *only* thing downstream agents read from the
    Judge — its reasoning is logged for human audit but not consumed
    by the Red Team's mutate path beyond the verdict + the reasoning
    field. Keeping the Red Team's eye on a single signal prevents
    over-fitting to Judge idiosyncrasies."""

    attempt_id: UUID
    verdict: Literal["success", "partial", "fail", "judge_failed"]
    reasoning: str
    severity_hint: Literal["low", "medium", "high", "critical"] | None = None
    deterministic_signals: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured outputs from deterministic checks that "
                    "ran BEFORE the LLM Judge: cross_patient_uuid_found, "
                    "forbidden_phrase_match, response_cost_usd, etc. "
                    "Where these are conclusive the LLM is skipped.",
    )
    judge_confidence: float = Field(
        1.0,
        ge=0.0,
        le=1.0,
        description="Judge's self-reported confidence (LLM verdicts) "
                    "or 1.0 for deterministic-only verdicts. Verdicts "
                    "below 0.7 on a 'success' should flag for human "
                    "review before the Documentation Agent runs.",
    )
    judged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class VulnerabilityReport(BaseModel):
    """Documentation Agent → vulns/VULN-XXXX.md + W2 eval case append.

    PRD-required fields are all here (see ARCHITECTURE.md §"Documentation
    Agent"). For MVP we render this to markdown manually; the agent
    automation comes in the Friday final.
    """

    vuln_id: str = Field(..., pattern=r"^VULN-\d{4}$")
    severity: Literal["low", "medium", "high", "critical"]
    title: str
    description: str
    clinical_impact: str
    category: ThreatCategory
    minimal_repro: list[ChatMessage]
    minimal_repro_docs: list[DocumentPayload] = Field(default_factory=list)
    observed_behavior: str
    expected_behavior: str
    recommended_remediation: str
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    discovered_by_campaign: UUID
    discovered_by_attempt: UUID
    status: Literal["open", "triaged", "fixed", "validated"] = "open"
    fix_commit: str | None = None
    fix_validated_at: datetime | None = None
    variant_of: str | None = Field(
        None,
        description="If this finding is materially the same as a prior "
                    "vuln, point at it (VULN-NNNN). The Documentation "
                    "Agent appends a VARIANT_OF field rather than "
                    "filing a fresh report (see ARCHITECTURE.md).",
    )
