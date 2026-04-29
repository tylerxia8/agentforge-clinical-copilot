<?php

namespace OpenEMR\Modules\ClinicalCopilot\Services;

use OpenEMR\Modules\ClinicalCopilot\Auth\AgentTokenMinter;
use Psr\Log\LoggerInterface;
use RuntimeException;

/**
 * HTTP client to the Python agent service.
 *
 * Using cURL directly to avoid pulling another HTTP-client dependency
 * into the module — Guzzle is in the OpenEMR root composer, but custom
 * modules shouldn't assume it's available.
 *
 * All requests are short-timeout. The chat turn waits on the LLM and
 * legitimately needs ~5-15s; everything else (warm, healthz) should
 * complete in <1s and any longer indicates a problem.
 */
final class AgentClient
{
    private const CHAT_TIMEOUT_SEC = 30;
    private const FIRE_AND_FORGET_TIMEOUT_SEC = 2;

    public function __construct(
        private readonly string $baseUrl,
        private readonly AgentTokenMinter $minter,
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * Synchronous chat turn. Caller blocks on the LLM round trip.
     *
     * @param  array<string, mixed>  $body  The JSON body to POST.
     * @return array<string, mixed>  Decoded JSON response.
     */
    public function chat(int $userId, string $patientUuid, array $body): array
    {
        $token = $this->minter->mint($userId, $patientUuid);
        return $this->post('/agent/chat', $body, $token, self::CHAT_TIMEOUT_SEC);
    }

    /**
     * Fire-and-forget warm trigger. We don't block on the result and we
     * don't surface failures to the user — the chat turn will fall
     * through to a cold cache if warming fails.
     */
    public function warm(int $userId, string $patientUuid): void
    {
        try {
            $token = $this->minter->mint($userId, $patientUuid);
            $this->post(
                "/agent/warm/{$patientUuid}",
                [],
                $token,
                self::FIRE_AND_FORGET_TIMEOUT_SEC,
            );
        } catch (\Throwable $e) {
            // Swallow — warm is optional.
            $this->logger->info(
                'copilot warm failed (non-fatal): ' . $e->getMessage()
            );
        }
    }

    /**
     * @param  array<string, mixed>  $body
     * @return array<string, mixed>
     */
    private function post(string $path, array $body, string $bearerToken, int $timeoutSec): array
    {
        $url = rtrim($this->baseUrl, '/') . $path;
        $payload = json_encode($body, JSON_UNESCAPED_SLASHES);
        if ($payload === false) {
            throw new RuntimeException('failed to encode request body');
        }

        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_POST => true,
            CURLOPT_POSTFIELDS => $payload,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT => $timeoutSec,
            CURLOPT_HTTPHEADER => [
                'Content-Type: application/json',
                'Accept: application/json',
                'Authorization: Bearer ' . $bearerToken,
            ],
        ]);
        $rawResponse = curl_exec($ch);
        $errno = curl_errno($ch);
        $err = curl_error($ch);
        $status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
        curl_close($ch);

        if ($errno !== 0) {
            throw new RuntimeException("agent service unreachable: {$err}");
        }
        if ($status < 200 || $status >= 300) {
            throw new RuntimeException(
                "agent service returned HTTP {$status}: " . substr((string) $rawResponse, 0, 500)
            );
        }

        $decoded = json_decode((string) $rawResponse, true);
        if (!is_array($decoded)) {
            throw new RuntimeException('agent service returned non-JSON response');
        }
        return $decoded;
    }
}
