<?php

/**
 * Clinical Co-Pilot — chat AJAX endpoint.
 *
 * The browser POSTs JSON here from copilot-chat.js. This script loads
 * OpenEMR's bootstrap (which sets up session, ACL, autoload, DB, the
 * event dispatcher), then hands off to CopilotController.
 *
 * Why a thin .php endpoint instead of a Symfony route: matches how
 * every other custom OpenEMR module exposes endpoints
 * (oe-module-faxsms/contact.php is the canonical example) — keeps the
 * module installable on stock OpenEMR without requiring routing changes.
 */

// Bootstrap OpenEMR. This sets $_SESSION, runs the Kernel, gives us the
// service container, and asserts the user is authenticated.
require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\ClinicalCopilot\Auth\AgentTokenMinter;
use OpenEMR\Modules\ClinicalCopilot\Http\CopilotController;
use OpenEMR\Modules\ClinicalCopilot\Services\AgentClient;

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');

// 1. CSRF — the panel includes a token from the session in the X-CSRF-Token header.
$csrf = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
if (!CsrfUtils::verifyCsrfToken((string) $csrf)) {
    http_response_code(403);
    echo json_encode(['refused' => true, 'refusal_reason' => 'invalid CSRF token']);
    exit;
}

// 2. ACL — same gate as the rest of the patient chart.
if (!AclMain::aclCheckCore('patients', 'demo')) {
    http_response_code(403);
    echo json_encode(['refused' => true, 'refusal_reason' => 'not authorized']);
    exit;
}

// 3. Read JSON body.
$rawBody = file_get_contents('php://input') ?: '';
$body = json_decode($rawBody, true);
if (!is_array($body)) {
    http_response_code(400);
    echo json_encode(['refused' => true, 'refusal_reason' => 'invalid JSON body']);
    exit;
}

// 4. Wire up dependencies and dispatch.
$globals = OEGlobalsBag::getInstance();
$logger = ServiceContainer::getLogger();
try {
    $minter = new AgentTokenMinter((string) ($globals->get('copilot_agent_shared_secret') ?? ''));
    $client = new AgentClient(
        (string) ($globals->get('copilot_agent_url') ?? 'http://agent-service:8000'),
        $minter,
        $logger,
    );
    $controller = new CopilotController($client, $logger);
    $response = $controller->handleChat($_SESSION, $body);
    echo json_encode($response);
} catch (Throwable $e) {
    $logger->error('copilot chat endpoint failed: ' . $e->getMessage());
    http_response_code(500);
    echo json_encode([
        'refused' => true,
        'refusal_reason' => 'internal error — see server logs',
    ]);
}
