<?php

namespace OpenEMR\Modules\ClinicalCopilot\Listeners;

use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\PatientDemographics\RenderEvent;
use OpenEMR\Events\PatientDemographics\ViewEvent;
use OpenEMR\Modules\ClinicalCopilot\Auth\AgentTokenMinter;
use OpenEMR\Modules\ClinicalCopilot\Services\AgentClient;
use Psr\Log\LoggerInterface;

/**
 * Two responsibilities:
 *
 * 1. onPatientViewed() — fires when a chart is opened. We trigger an
 *    asynchronous warm against the agent service so the per-patient
 *    context bundle is hot in Redis before the doctor types anything.
 *    See ARCHITECTURE.md §5.
 *
 * 2. onRenderPostPageload() — fires after the patient demographics
 *    page renders. We inject the chat panel HTML + CSS + JS into the
 *    page so we don't have to fork demographics.php.
 */
final class PatientViewedListener
{
    public function __construct(
        private readonly string $installPath,
        private readonly LoggerInterface $logger,
    ) {
    }

    public function onPatientViewed(ViewEvent $event): void
    {
        $pid = (int) ($event->getPid() ?? 0);
        $userId = (int) ($_SESSION['authUserID'] ?? 0);
        if ($pid <= 0 || $userId <= 0) {
            return;
        }

        $patientUuid = $this->pidToUuid($pid);
        if ($patientUuid === null) {
            return;
        }

        $client = $this->buildClient();
        if ($client === null) {
            return;
        }

        // Fire-and-forget — AgentClient::warm() swallows its own errors.
        $client->warm($userId, $patientUuid);
    }

    public function onRenderPostPageload(RenderEvent $event): void
    {
        $pid = (int) ($event->getPid() ?? 0);
        if ($pid <= 0) {
            return;
        }

        // CSRF token the panel will include on every chat POST.
        $csrf = CsrfUtils::collectCsrfToken();
        $endpoint = $this->installPath . '/public/chat.php';

        // Render the panel container + a small launcher script. The
        // heavy JS lives in public/js/copilot-chat.js.
        ?>
        <link rel="stylesheet" href="<?= htmlspecialchars($this->installPath . '/public/css/copilot-panel.css', ENT_QUOTES) ?>">
        <div id="copilot-panel" data-endpoint="<?= htmlspecialchars($endpoint, ENT_QUOTES) ?>"
             data-csrf="<?= htmlspecialchars($csrf, ENT_QUOTES) ?>"
             data-patient-pid="<?= htmlspecialchars((string) $pid, ENT_QUOTES) ?>"
             aria-label="Clinical Co-Pilot">
            <header class="copilot-header">
                <span class="copilot-title">Clinical Co-Pilot</span>
                <button type="button" class="copilot-close" aria-label="Minimize">−</button>
            </header>
            <div class="copilot-messages" id="copilot-messages" role="log" aria-live="polite"></div>
            <form class="copilot-input-row" id="copilot-form" autocomplete="off">
                <input
                    type="text"
                    id="copilot-input"
                    placeholder="Ask about this patient…"
                    aria-label="Ask the co-pilot"
                />
                <button type="submit" class="copilot-send">Send</button>
            </form>
        </div>
        <script src="<?= htmlspecialchars($this->installPath . '/public/js/copilot-chat.js', ENT_QUOTES) ?>" defer></script>
        <?php
    }

    private function buildClient(): ?AgentClient
    {
        try {
            $globals = OEGlobalsBag::getInstance();
            $secret = (string) ($globals->get('copilot_agent_shared_secret') ?? '');
            if ($secret === '') {
                $this->logger->info('copilot warm skipped — shared secret not configured');
                return null;
            }
            return new AgentClient(
                (string) ($globals->get('copilot_agent_url') ?? 'http://agent-service:8000'),
                new AgentTokenMinter($secret),
                $this->logger,
            );
        } catch (\Throwable $e) {
            $this->logger->info('copilot client init failed: ' . $e->getMessage());
            return null;
        }
    }

    private function pidToUuid(int $pid): ?string
    {
        $row = sqlQuery('SELECT uuid FROM patient_data WHERE pid = ?', [$pid]);
        if (!is_array($row) || empty($row['uuid'])) {
            return null;
        }
        return UuidRegistry::uuidToString($row['uuid']);
    }
}
