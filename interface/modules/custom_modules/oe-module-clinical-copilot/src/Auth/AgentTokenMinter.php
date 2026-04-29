<?php

namespace OpenEMR\Modules\ClinicalCopilot\Auth;

use RuntimeException;

/**
 * Mints HMAC-signed tokens that the Python agent service verifies.
 *
 * Format MUST match agent-service/src/copilot/context/patient.py:
 *   token = base64url(json_payload) + "." + hex(hmac_sha256(payload, secret))
 *
 * Payload shape:
 *   {
 *     "user_id": int,
 *     "patient_uuid": str,
 *     "encounter_uuid": str | null,
 *     "issued_at": int (unix seconds),
 *     "nonce": str (16 hex chars)
 *   }
 *
 * The Python side enforces a 300-second TTL and verifies the signature
 * before trusting any field. We never put data from the request body
 * into the payload — only fields derived server-side from the validated
 * OpenEMR session.
 */
final class AgentTokenMinter
{
    public function __construct(private readonly string $sharedSecret)
    {
        if ($sharedSecret === '') {
            throw new RuntimeException(
                'copilot_agent_shared_secret global is empty. Set it in '
                . 'Admin → Globals → Clinical Co-Pilot.'
            );
        }
    }

    public function mint(int $userId, string $patientUuid, ?string $encounterUuid = null): string
    {
        $payload = [
            'user_id' => $userId,
            'patient_uuid' => $patientUuid,
            'encounter_uuid' => $encounterUuid,
            'issued_at' => time(),
            'nonce' => bin2hex(random_bytes(8)),
        ];
        // JSON_UNESCAPED_SLASHES so PHP and Python produce byte-identical
        // payloads (Python's json.dumps doesn't escape slashes by default).
        $json = json_encode($payload, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        if ($json === false) {
            throw new RuntimeException('failed to encode token payload');
        }

        $payloadB64 = self::base64UrlEncode($json);
        $sig = hash_hmac('sha256', $payloadB64, $this->sharedSecret);
        return $payloadB64 . '.' . $sig;
    }

    /**
     * Match Python's `base64.urlsafe_b64encode(...).rstrip(b"=")`.
     */
    private static function base64UrlEncode(string $data): string
    {
        return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
    }
}
