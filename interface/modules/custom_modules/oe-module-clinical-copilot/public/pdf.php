<?php

/**
 * Clinical Co-Pilot — W2 PDF passthrough.
 *
 * The bbox-overlay frontend (pdf-overlay.js) loads this URL to fetch
 * a PDF that was previously uploaded via upload.php. We don't expose
 * the document store path directly to the browser — we gate the read
 * here on:
 *
 *   - CSRF (so a stolen URL can't be used cross-site)
 *   - patients/demo ACL (same as chat.php / upload.php)
 *   - patient match (the requested PDF must belong to the chart
 *     currently open in the session)
 *   - id format (UUID v4 only — defends against ../ path traversal)
 *
 * Query: GET pdf.php?id=<doc_uuid>
 *
 * The PDF lives under
 *   {OE_SITE_DIR}/documents/copilot_uploads/<pid>/<doc_uuid>.pdf
 * — only the pid in the session is consulted, so a session in
 * Farrah's chart cannot fetch a PDF that was uploaded under
 * another patient's pid even if the UUID is leaked.
 */

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;

// We accept the CSRF token via a query-string param OR header so the
// pdf.js fetcher can stick it on either. Strict comparison after
// retrieval; no fallback to anonymous.
$csrf = $_GET['csrf'] ?? $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '';
if (!CsrfUtils::verifyCsrfToken((string) $csrf)) {
    http_response_code(403);
    header('Content-Type: text/plain');
    echo 'invalid CSRF token';
    exit;
}

if (!AclMain::aclCheckCore('patients', 'demo')) {
    http_response_code(403);
    header('Content-Type: text/plain');
    echo 'not authorized';
    exit;
}

$pid = (int) ($_SESSION['pid'] ?? 0);
if ($pid <= 0) {
    http_response_code(400);
    header('Content-Type: text/plain');
    echo 'no open patient chart';
    exit;
}

$id = (string) ($_GET['id'] ?? '');
// RFC 4122 v4 shape: 8-4-4-4-12 hex chars. Mirrors what upload.php
// generates. Any other shape is suspect.
if (!preg_match('/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i', $id)) {
    http_response_code(400);
    header('Content-Type: text/plain');
    echo 'invalid id';
    exit;
}

// Build the path. Note: $pid is the SESSION pid, never user input —
// even if the JS sends a different pid, we ignore it.
$path = $GLOBALS['OE_SITE_DIR'] . '/documents/copilot_uploads/' . $pid . '/' . $id . '.pdf';
$resolved = realpath($path);
$expectedRoot = realpath($GLOBALS['OE_SITE_DIR'] . '/documents/copilot_uploads/' . $pid);

if (
    $resolved === false
    || $expectedRoot === false
    || strpos($resolved, $expectedRoot . DIRECTORY_SEPARATOR) !== 0
) {
    // Either the file doesn't exist or some path-traversal arithmetic
    // landed us outside the patient's directory. Both: 404.
    http_response_code(404);
    header('Content-Type: text/plain');
    echo 'document not found';
    exit;
}

if (!is_readable($resolved)) {
    http_response_code(404);
    header('Content-Type: text/plain');
    echo 'document not readable';
    exit;
}

// Stream the PDF. Cache headers err on the side of "do not cache" so
// a moved/replaced upload doesn't surface stale bytes.
header('Content-Type: application/pdf');
header('Content-Length: ' . filesize($resolved));
header('Content-Disposition: inline; filename="' . basename($resolved) . '"');
header('X-Content-Type-Options: nosniff');
header('Cache-Control: private, no-cache, no-store, must-revalidate');
header('Pragma: no-cache');
readfile($resolved);
