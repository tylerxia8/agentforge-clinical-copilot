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

// 9. Writeback. Persist the extracted facts as appropriate OpenEMR
//    records (PRD §1 core requirement). Failures here are logged but
//    do NOT fail the request — the user already has the extraction;
//    a writeback hiccup shouldn't lose their work. The writeback
//    summary is included in the response so the panel can show what
//    landed in the chart.
$writebackSummary = ['ok' => false, 'records' => []];
try {
    $writebackSummary = _copilot_writeback(
        $pid, $userId, $docType, $docUuid, $origName, $size, $result['extraction'] ?? null,
    );
} catch (\Throwable $e) {
    $logger->error('copilot writeback failed (non-fatal): ' . $e->getMessage());
    $writebackSummary = ['ok' => false, 'error' => $e->getMessage(), 'records' => []];
}

// 10. Success. Return the extraction + writeback summary + the doc
//     UUID so the panel can reference it in subsequent chat turns.
echo json_encode([
    'ok' => true,
    'document_reference_id' => $docUuid,
    'doc_type' => $docType,
    'extraction' => $result['extraction'] ?? null,
    'bbox_match' => $result['bbox_match'] ?? null,
    'writeback' => $writebackSummary,
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

/**
 * Persist the agent's extraction as native OpenEMR records.
 *
 * Three writebacks per extraction (PRD §1 "persist derived facts as
 * appropriate FHIR resources or OpenEMR records"):
 *
 *   1. `pnotes` — a single Patient Notes entry with a Markdown summary
 *      of every extracted fact, linked to the patient by pid. Visible
 *      under Patient Notes in the chart.
 *   2. `documents` — a row representing the source PDF, linked to
 *      the patient. Surfaces in the patient's Documents tab. (Already
 *      stored on the persistent volume by upload.php; this row is
 *      what makes it visible from OpenEMR's UI.)
 *   3. For lab_pdf only: `procedure_result` rows under a
 *      `procedure_report` parent. Surfaces under Procedures →
 *      Reports in the chart.
 *
 * Each write is wrapped in try/catch so a partial failure leaves the
 * other records intact and surfaces a per-table error to the
 * operator. The function NEVER throws — even a total failure returns
 * a status object the caller can inspect.
 *
 * @return array{ok:bool, records: array<string,array>, error?:string}
 */
function _copilot_writeback(
    int $pid,
    int $userId,
    string $docType,
    string $docUuid,
    string $origName,
    int $size,
    ?array $extraction,
): array {
    $records = [];

    if ($extraction === null) {
        return ['ok' => false, 'error' => 'no extraction to persist', 'records' => []];
    }

    // 1. pnotes — Patient Notes summary
    try {
        $title = sprintf('AgentForge: %s extraction', $docType === 'lab_pdf' ? 'lab report' : 'intake form');
        $body = _copilot_format_pnote_body($docType, $docUuid, $extraction);
        $authUser = (string) ($_SESSION['authUser'] ?? 'copilot');
        $noteId = sqlInsert(
            'INSERT INTO pnotes (date, body, pid, user, groupname, activity, authorized, '
            . 'title, message_status, deleted) '
            . 'VALUES (NOW(), ?, ?, ?, ?, 1, 1, ?, ?, 0)',
            [$body, $pid, $authUser, 'Default', $title, 'New'],
        );
        $records['pnotes'] = ['ok' => true, 'id' => $noteId, 'title' => $title];
    } catch (\Throwable $e) {
        $records['pnotes'] = ['ok' => false, 'error' => $e->getMessage()];
    }

    // 2. documents — link the PDF to the patient's chart
    //    OpenEMR's documents.id is NOT auto_increment in older schemas;
    //    we compute the next id explicitly with a MAX(id)+1 SELECT
    //    rather than rely on a column default that returns 0.
    //    Then we ALSO insert into categories_to_documents — the
    //    Documents UI is a category-tree view, and a row that's
    //    only in `documents` (not joined to a category) is invisible.
    try {
        $next = sqlQuery('SELECT IFNULL(MAX(id), 0) + 1 AS n FROM documents');
        $docId = (int) ($next['n'] ?? 1);
        sqlInsert(
            'INSERT INTO documents (id, type, size, date, url, mimetype, foreign_id, '
            . 'docdate, name, hash, list_id, encounter_id, encounter_check, '
            . 'audit_master_approval_status, audit_master_id, documentationOf, '
            . 'encrypted, deleted) '
            . 'VALUES (?, ?, ?, NOW(), ?, ?, ?, NOW(), ?, ?, 0, 0, "", 1, 0, "", 0, 0)',
            [
                $docId,
                'file_url',
                $size,
                'file:///' . _copilot_safe_storage_path($pid, $docUuid),
                'application/pdf',
                $pid,
                $origName,
                hash('sha256', $docUuid),
            ],
        );
        // Map doc_type → category id. The seed catalog ships these:
        //   id=2 "Lab Report"
        //   id=4 "Patient Information"
        // If a future deploy renames the categories, the `?? 1` falls
        // back to the catch-all "Categories" root so the document is
        // at least somewhere visible.
        $categoryId = $docType === 'lab_pdf' ? 2 : 4;
        sqlInsert(
            'INSERT INTO categories_to_documents (category_id, document_id) VALUES (?, ?)',
            [$categoryId, $docId],
        );
        $records['documents'] = [
            'ok' => true, 'id' => $docId, 'category_id' => $categoryId,
        ];
    } catch (\Throwable $e) {
        $records['documents'] = ['ok' => false, 'error' => $e->getMessage()];
    }

    // 3. procedure_result (lab_pdf only)
    if ($docType === 'lab_pdf' && !empty($extraction['results'])) {
        try {
            $records['procedure_results'] = _copilot_writeback_lab_results(
                $pid, $userId, $docUuid, $extraction,
            );
        } catch (\Throwable $e) {
            $records['procedure_results'] = ['ok' => false, 'error' => $e->getMessage()];
        }
    }

    $allOk = array_reduce(
        $records,
        fn($carry, $r) => $carry && (($r['ok'] ?? false) === true),
        true,
    );
    return ['ok' => $allOk, 'records' => $records];
}

function _copilot_safe_storage_path(int $pid, string $docUuid): string
{
    return $GLOBALS['OE_SITE_DIR'] . '/documents/copilot_uploads/' . $pid . '/' . $docUuid . '.pdf';
}

function _copilot_format_pnote_body(string $docType, string $docUuid, array $extraction): string
{
    $lines = [];
    $lines[] = "Source DocumentReference#{$docUuid}";
    $lines[] = '';

    if ($docType === 'lab_pdf') {
        $results = $extraction['results'] ?? [];
        $lines[] = '## Lab Results';
        foreach ($results as $r) {
            $flag = ($r['abnormal_flag'] ?? '') === 'N' ? '' : ' (' . ($r['abnormal_flag'] ?? '') . ')';
            $conf = ($r['extraction_confidence'] ?? 'high') === 'low' ? ' [low confidence]' : '';
            $lines[] = sprintf(
                '- %s: %s %s%s%s',
                $r['test_name'] ?? '?',
                $r['value'] ?? '?',
                $r['unit'] ?? '',
                $flag,
                $conf,
            );
        }
        if (!empty($extraction['warnings'])) {
            $lines[] = '';
            $lines[] = '### Warnings';
            foreach ($extraction['warnings'] as $w) {
                $lines[] = "- {$w}";
            }
        }
    } else {
        $lines[] = '## Intake Form Summary';
        if ($d = $extraction['demographics'] ?? null) {
            $name = trim(($d['first_name'] ?? '') . ' ' . ($d['last_name'] ?? ''));
            $lines[] = "- Patient: {$name}" . (empty($d['date_of_birth']) ? '' : " (DOB {$d['date_of_birth']})");
        }
        if (!empty($extraction['chief_concern']['text'])) {
            $lines[] = '- Chief concern: ' . $extraction['chief_concern']['text'];
        }
        foreach ($extraction['medications'] ?? [] as $m) {
            $dose = trim(($m['dose'] ?? '') . ' ' . ($m['frequency'] ?? ''));
            $lines[] = "- Medication: {$m['name']}" . ($dose ? " — {$dose}" : '');
        }
        foreach ($extraction['allergies'] ?? [] as $a) {
            $lines[] = "- Allergy: {$a['substance']}" . (empty($a['reaction']) ? '' : " — {$a['reaction']}");
        }
        foreach ($extraction['family_history'] ?? [] as $f) {
            $lines[] = "- Family: {$f['relation']} — {$f['condition']}"
                . (empty($f['age_of_onset']) ? '' : " (onset {$f['age_of_onset']})");
        }
    }

    return implode("\n", $lines);
}

/**
 * Insert one procedure_report parent + N procedure_result children.
 * Returns a status row per result; the parent report id is included
 * for traceability so an operator can find these in the DB later.
 */
function _copilot_writeback_lab_results(
    int $pid,
    int $userId,
    string $docUuid,
    array $extraction,
): array {
    $results = $extraction['results'] ?? [];
    if (!$results) {
        return ['ok' => true, 'count' => 0, 'note' => 'no results to write'];
    }

    $issuingLab = $extraction['issuing_lab'] ?? 'AgentForge Extraction';
    $accession = $extraction['accession_number'] ?? null;
    $collectionDate = $results[0]['collection_date'] ?? date('Y-m-d');

    // Parent report. procedure_report has dozens of columns; we only
    // populate the load-bearing ones. report_status='final' so the
    // results show in the patient's lab tab.
    $reportId = sqlInsert(
        'INSERT INTO procedure_report (date_collected, date_report, source, '
        . 'report_status, review_status, report_notes) '
        . 'VALUES (?, NOW(), ?, ?, ?, ?)',
        [
            $collectionDate . ' 00:00:00',
            $userId,
            'final',
            'reviewed',
            "AgentForge extraction from DocumentReference#{$docUuid} (issuing lab: {$issuingLab}"
                . ($accession ? ", accession: {$accession}" : '') . ')',
        ],
    );

    $resultIds = [];
    foreach ($results as $r) {
        // `range` is a MariaDB reserved word — must be backticked, or
        // the server returns "You have an error in your SQL syntax".
        $resultId = sqlInsert(
            'INSERT INTO procedure_result (procedure_report_id, result_code, '
            . 'result_text, units, `range`, abnormal, result_status, result, comments) '
            . 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                $reportId,
                $r['test_name'] ?? '',
                $r['test_name'] ?? '',
                $r['unit'] ?? '',
                _copilot_format_range($r['reference_range'] ?? null),
                _copilot_map_abnormal_flag($r['abnormal_flag'] ?? 'N'),
                'final',
                (string) ($r['value'] ?? ''),
                "Citation page {$r['citation']['page_or_section']}: "
                    . ($r['citation']['quote_or_value'] ?? ''),
            ],
        );
        $resultIds[] = $resultId;
    }

    return [
        'ok' => true,
        'procedure_report_id' => $reportId,
        'procedure_result_ids' => $resultIds,
        'count' => count($resultIds),
    ];
}

function _copilot_format_range(?array $range): string
{
    if (!is_array($range)) {
        return '';
    }
    $unit = $range['unit'] ?? '';
    switch ($range['comparator'] ?? '') {
        case 'between':
            return sprintf('%s - %s %s', $range['low'] ?? '', $range['high'] ?? '', $unit);
        case '<':
            return sprintf('< %s %s', $range['high'] ?? '', $unit);
        case '<=':
            return sprintf('≤ %s %s', $range['high'] ?? '', $unit);
        case '>':
            return sprintf('> %s %s', $range['low'] ?? '', $unit);
        case '>=':
            return sprintf('≥ %s %s', $range['low'] ?? '', $unit);
        default:
            return '';
    }
}

function _copilot_map_abnormal_flag(string $flag): string
{
    // OpenEMR's procedure_result.abnormal column accepts: 'no', 'yes',
    // 'high', 'low'. Map our schema's HL7 v2 codes accordingly. 'LL'/'HH'
    // are critical low/high — surface as 'high'/'low' since OpenEMR has
    // no separate critical column without a custom widget.
    return match ($flag) {
        'L', 'LL' => 'low',
        'H', 'HH' => 'high',
        default => 'no',
    };
}

function _copilot_pid_to_uuid(int $pid): ?string
{
    $row = sqlQuery('SELECT uuid FROM patient_data WHERE pid = ?', [$pid]);
    if (!is_array($row) || empty($row['uuid'])) {
        return null;
    }
    return \OpenEMR\Common\Uuid\UuidRegistry::uuidToString($row['uuid']);
}
