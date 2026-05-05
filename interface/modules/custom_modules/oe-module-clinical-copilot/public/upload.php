<?php

/**
 * Clinical Co-Pilot — W2 document upload endpoint.
 *
 * The chat panel POSTs a single PDF here as multipart/form-data
 * along with a doc_type field ('lab_pdf' or 'intake_form'). We:
 *
 *   1. Validate CSRF + ACL + open patient (same defense-in-depth
 *      pattern as chat.php).
 *   2. Sanity-check the upload (size, MIME, extension).
 *   3. Persist the PDF to the OpenEMR documents/ Railway volume so
 *      it survives container restarts (W1 OAuth-keypair fix made
 *      that volume available; we use a copilot_uploads/<pid>/
 *      subdirectory to keep our files separate from upstream
 *      OpenEMR docs).
 *   4. Generate a UUID we can use as the DocumentReference id —
 *      full insertion into OpenEMR's `documents` table is a
 *      Thursday/Sunday task; for MVP the UUID is sufficient for
 *      the agent's citation envelope.
 *   5. Forward the bytes to the agent service's /agent/extract
 *      endpoint (synchronous; the LLM round trip is what users
 *      wait on).
 *   6. Return the validated extraction JSON to the panel.
 *
 * Why a separate endpoint and not a multipart variant of chat.php:
 * uploads have different timeouts, different CSRF expectations
 * (form vs JSON), and different success-shape semantics. Keeping
 * them apart makes both endpoints simpler to read and to test.
 */

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Logging\EventAuditLogger;
use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Modules\ClinicalCopilot\Auth\AgentTokenMinter;
use OpenEMR\Modules\ClinicalCopilot\Services\AgentClient;

header('Content-Type: application/json');
header('X-Content-Type-Options: nosniff');

$logger = new SystemLogger();

// 1. CSRF — the panel includes the token in the X-CSRF-Token header.
$csrf = $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
if (!CsrfUtils::verifyCsrfToken((string) $csrf)) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'invalid CSRF token']);
    exit;
}

// 2. ACL — same gate as the chat endpoint.
if (!AclMain::aclCheckCore('patients', 'demo')) {
    http_response_code(403);
    echo json_encode(['ok' => false, 'error' => 'not authorized']);
    exit;
}

// 3. Open patient required.
$pid = (int) ($_SESSION['pid'] ?? 0);
if ($pid <= 0) {
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => 'no open patient chart in session']);
    exit;
}

// 4. Required form fields.
$docType = (string) ($_POST['doc_type'] ?? '');
if (!in_array($docType, ['lab_pdf', 'intake_form'], true)) {
    http_response_code(400);
    echo json_encode([
        'ok' => false,
        'error' => "doc_type must be 'lab_pdf' or 'intake_form'",
    ]);
    exit;
}

// 5. File upload sanity.
if (empty($_FILES['file']) || ($_FILES['file']['error'] ?? null) !== UPLOAD_ERR_OK) {
    $errCode = $_FILES['file']['error'] ?? 'missing';
    http_response_code(400);
    echo json_encode(['ok' => false, 'error' => "upload error: {$errCode}"]);
    exit;
}
$tmpPath = $_FILES['file']['tmp_name'];
$origName = $_FILES['file']['name'] ?? 'upload.pdf';
$size = (int) ($_FILES['file']['size'] ?? 0);

// 25 MB cap (matches the agent's MAX_PDF_BYTES).
if ($size <= 0 || $size > 25 * 1024 * 1024) {
    http_response_code(413);
    echo json_encode(['ok' => false, 'error' => 'file empty or exceeds 25 MB cap']);
    exit;
}

// MIME sanity. The browser-supplied type is hint-only; we also check
// the magic bytes to refuse obviously-non-PDF uploads.
$fh = fopen($tmpPath, 'rb');
$magic = $fh ? fread($fh, 5) : '';
if ($fh) {
    fclose($fh);
}
if ($magic !== '%PDF-') {
    http_response_code(415);
    echo json_encode(['ok' => false, 'error' => 'file does not look like a PDF']);
    exit;
}

// 6. Persist to the documents/ volume. We generate a random UUID
//    and write under copilot_uploads/<pid>/<uuid>.pdf so our files
//    are isolated from OpenEMR's own document subdirectories. The
//    DocumentReference id we hand the agent IS this uuid; the agent
//    only uses it as a citation envelope, never to dereference back.
$docUuid = _copilot_random_uuid();
$docsRoot = $GLOBALS['OE_SITE_DIR'] . '/documents';
$copilotDir = $docsRoot . '/copilot_uploads/' . $pid;
if (!is_dir($copilotDir) && !mkdir($copilotDir, 0750, true) && !is_dir($copilotDir)) {
    $logger->error("copilot upload dir create failed: {$copilotDir}");
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'storage init failed']);
    exit;
}
$storagePath = $copilotDir . '/' . $docUuid . '.pdf';
if (!move_uploaded_file($tmpPath, $storagePath)) {
    $logger->error("copilot move_uploaded_file failed src={$tmpPath} dst={$storagePath}");
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'storage write failed']);
    exit;
}
@chmod($storagePath, 0640);

// 7. Audit BEFORE the agent call so we have a record even if the
//    agent hangs.
$userId = (int) ($_SESSION['authUserID'] ?? 0);
$patientUuid = _copilot_pid_to_uuid($pid);
EventAuditLogger::instance()->newEvent(
    'copilot-extract',
    (string) ($_SESSION['authUser'] ?? ''),
    (string) ($_SESSION['authProvider'] ?? ''),
    1,
    sprintf(
        'pid=%d patient_uuid=%s doc_type=%s doc_uuid=%s size=%d name=%s',
        $pid, $patientUuid ?? '?', $docType, $docUuid, $size, $origName
    ),
    $pid,
    'open',
    'copilot',
);

if ($patientUuid === null) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'patient has no uuid']);
    exit;
}

// 8. Forward to the agent service.
try {
    $minter = new AgentTokenMinter((string) ($GLOBALS['copilot_agent_shared_secret'] ?? ''));
    $client = new AgentClient(
        (string) ($GLOBALS['copilot_agent_url'] ?? 'http://agent-service:8000'),
        $minter,
        $logger,
    );
    $result = $client->extract($userId, $patientUuid, $storagePath, $docType, $docUuid);
} catch (\Throwable $e) {
    $logger->error('copilot /agent/extract failed: ' . $e->getMessage());
    http_response_code(502);
    echo json_encode([
        'ok' => false,
        'error' => 'extraction failed; the co-pilot is temporarily unavailable',
    ]);
    exit;
}

// 9. Success. Return the extraction + the doc UUID so the panel can
//    reference it in subsequent chat turns.
echo json_encode([
    'ok' => true,
    'document_reference_id' => $docUuid,
    'doc_type' => $docType,
    'extraction' => $result['extraction'] ?? null,
    'bbox_match' => $result['bbox_match'] ?? null,
]);


// ─── helpers ────────────────────────────────────────────────────────────

function _copilot_random_uuid(): string
{
    // RFC 4122 v4. PHP's random_bytes is CSPRNG-backed.
    $bytes = random_bytes(16);
    $bytes[6] = chr((ord($bytes[6]) & 0x0F) | 0x40);
    $bytes[8] = chr((ord($bytes[8]) & 0x3F) | 0x80);
    $hex = bin2hex($bytes);
    return sprintf(
        '%s-%s-%s-%s-%s',
        substr($hex, 0, 8),
        substr($hex, 8, 4),
        substr($hex, 12, 4),
        substr($hex, 16, 4),
        substr($hex, 20, 12),
    );
}

function _copilot_pid_to_uuid(int $pid): ?string
{
    $row = sqlQuery('SELECT uuid FROM patient_data WHERE pid = ?', [$pid]);
    if (!is_array($row) || empty($row['uuid'])) {
        return null;
    }
    return \OpenEMR\Common\Uuid\UuidRegistry::uuidToString($row['uuid']);
}
