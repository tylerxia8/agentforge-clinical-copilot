"""Settings loaded from environment. See agent-service/README.md for required vars."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM ---
    anthropic_api_key: str = Field(..., alias="ANTHROPIC_API_KEY")
    model_id: str = Field("claude-sonnet-4-6", alias="CLAUDE_MODEL_ID")
    judge_model_id: str = Field("claude-opus-4-7", alias="CLAUDE_JUDGE_MODEL_ID")
    max_tokens: int = 2048
    max_tool_rounds: int = 5
    max_verification_retries: int = 2

    # --- OpenEMR REST bridge ---
    # Auth: OAuth2 Password Grant for v0/v1 (the OpenEMR client_credentials
    # path requires private-key JWT signing with JWKS registration — out of
    # scope for the early submission deadline). This means a service-account
    # OpenEMR user that the agent runs as. Documented as a v2 swap target
    # in ARCHITECTURE.md.
    openemr_base_url: str = Field(..., alias="OPENEMR_BASE_URL")
    openemr_oauth_client_id: str = Field(..., alias="OPENEMR_OAUTH_CLIENT_ID")
    openemr_oauth_client_secret: str = Field(..., alias="OPENEMR_OAUTH_CLIENT_SECRET")
    openemr_service_username: str = Field(..., alias="OPENEMR_SERVICE_USERNAME")
    openemr_service_password: str = Field(..., alias="OPENEMR_SERVICE_PASSWORD")
    openemr_oauth_token_path: str = "/oauth2/default/token"
    # user/* scopes work with Password Grant; system/* would require JWT.
    openemr_oauth_scope: str = (
        "openid offline_access api:fhir "
        "user/Patient.read user/Encounter.read user/Observation.read "
        "user/MedicationRequest.read user/Condition.read "
        "user/AllergyIntolerance.read user/Immunization.read"
    )

    # --- Auth between PHP module and this service ---
    # The PHP module mints a short-lived HMAC token containing user_id and
    # patient_uuid; this service verifies it. Shared secret only.
    agent_shared_secret: str = Field(..., alias="AGENT_SHARED_SECRET")
    agent_token_ttl_seconds: int = 300

    # --- Cache ---
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")
    context_cache_ttl_seconds: int = 300

    # --- Observability ---
    langfuse_public_key: str | None = Field(None, alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str | None = Field(None, alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str | None = Field(None, alias="LANGFUSE_HOST")


settings = Settings()  # type: ignore[call-arg]
