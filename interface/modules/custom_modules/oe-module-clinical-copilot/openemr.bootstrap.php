<?php

/**
 * Clinical Co-Pilot — module bootstrap.
 *
 * Loaded by OpenEMR's Module Loader on every request once the module is
 * enabled. The Module Loader pre-sets two globals for us:
 *   - $classLoader: an OpenEMR\Core\ModulesClassLoader instance
 *   - $GLOBALS['kernel']: the OpenEMR Kernel exposing getEventDispatcher()
 *
 * We use those instead of OEGlobalsBag (which is 8.x-only). This keeps
 * the module compatible with OpenEMR 7.0.3 — the version that ships in
 * the production Docker image our deployment is based on.
 */

use OpenEMR\Modules\ClinicalCopilot\Bootstrap;

/** @var \OpenEMR\Core\ModulesClassLoader $classLoader */
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\ClinicalCopilot\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

$dispatcher = $GLOBALS['kernel']->getEventDispatcher();
(new Bootstrap($dispatcher))->subscribeToEvents();
