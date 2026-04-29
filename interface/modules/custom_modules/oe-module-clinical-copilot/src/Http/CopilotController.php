<?php

namespace OpenEMR\Modules\ClinicalCopilot\Http;

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\ClinicalCopilot\Auth\AgentTokenMinter;
use OpenEMR\Modules\ClinicalCopilot\Services\AgentClient;
use Psr\Log\LoggerInterface;
use RuntimeException;

/**
 * Chat-turn endpoint logic.
 *
 * The actual HTTP entry is public/chat.php (which loads globals.php for
 * session + autoload). It instantiates this class and calls handleChat().
 *
 * Pre-conditions checked here (defense in depth — public/chat.php also
 * checks):
 *   1. Session is authenticated.
 *   2. User has the patients/demo ACL.
 *   3. There is an open patient in the session ($_SESSION['pid']).
 *
 * The patient_uuid sent to the agent service is derived FROM THE SESSION,
 * NEVER from the request body. This is the closure for AUDIT.md §1.2.
 */
final class CopilotController
{
    public function __construct(
        private readonly AgentClient $client,
        private readonly LoggerInterface $logger,
    ) {
    }

    /**
     * @param  array<string, mixed>  $sessionState
     * @param  array<string, mixed>  $requestBody
     * @return array<string, mixed>
     */
    public function handleChat(array $sessionState, array $requestBody): array
    {
        // 1. Auth + ACL — also enforced upstream, here for defense in depth.
        $userId = $this->requireSessionUser($sessionState);
        if (!AclMain::aclCheckCore('patients', 'demo')) {
            return $this->refuse('not authorized to read patient records');
        }

        // 2. Patient handle from SESSION — never from request body.
        $pid = (int) ($sessionState['pid'] ?? 0);
        if ($pid <= 0) {
            return $this->refuse('no open patient chart in session');
        }
        $patientUuid = $this->pidToUuid($pid);
        if ($patientUuid === null) {
            return $this->refuse('open patient has no uuid (data integrity issue)');
        }

        // 3. Validate user input shape.
        $message = trim((string) ($requestBody['message'] ?? ''));
        if ($message === '') {
            return $this->refuse('empty message');
        }
        $history = is_array($requestBody['history'] ?? null)
            ? $requestBody['history']
            : [];
        $conversationId = is_string($requestBody['conversation_id'] ?? null)
            ? $requestBody['conversation_id']
            : null;

        // 4. Audit BEFORE the call so we have a record even if the call hangs.
        EventAuditLogger::instance()->newEvent(
            'copilot-turn',
            (string) ($sessionState['authUser'] ?? ''),
            (string) ($sessionState['authProvider'] ?? ''),
            1,
            sprintf('patient_uuid=%s message_len=%d', $patientUuid, strlen($message)),
            $pid,
            'open',
            'copilot',
        );

        // 5. Forward to agent service.
        try {
            $response = $this->client->chat($userId, $patientUuid, [
                'message' => $message,
                'conversation_id' => $conversationId,
                'history' => $history,
            ]);
        } catch (RuntimeException $e) {
            $this->logger->error(
                'copilot agent call failed: ' . $e->getMessage(),
                ['patient_uuid' => $patientUuid, 'user_id' => $userId],
            );
            return $this->refuse(
                'the co-pilot is temporarily unavailable. Please try again in a moment.'
            );
        }

        // 6. TODO(thursday): persist to oe_copilot_messages for history.

        return $response;
    }

    /**
     * @param  array<string, mixed>  $sessionState
     */
    private function requireSessionUser(array $sessionState): int
    {
        $userId = (int) ($sessionState['authUserID'] ?? 0);
        if ($userId <= 0) {
            throw new RuntimeException('no authenticated user in session');
        }
        return $userId;
    }

    private function pidToUuid(int $pid): ?string
    {
        $row = sqlQuery('SELECT uuid FROM patient_data WHERE pid = ?', [$pid]);
        if (!is_array($row) || empty($row['uuid'])) {
            return null;
        }
        return UuidRegistry::uuidToString($row['uuid']);
    }

    /**
     * @return array<string, mixed>
     */
    private function refuse(string $reason): array
    {
        return [
            'text' => $reason,
            'sources' => [],
            'refused' => true,
            'refusal_reason' => $reason,
        ];
    }
}
