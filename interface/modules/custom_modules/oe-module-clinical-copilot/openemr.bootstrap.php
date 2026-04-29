<?php

/**
 * Clinical Co-Pilot — module bootstrap.
 *
 * Loaded by OpenEMR's Module Loader on every request once the module is
 * enabled. Two responsibilities:
 *   1. Register our PSR-4 namespace so the Bootstrap class can be loaded.
 *   2. Hand the event dispatcher to Bootstrap::subscribeToEvents().
 *
 * Mirror of oe-module-dashboard-context/openemr.bootstrap.php — this is
 * the OpenEMR-blessed shape; don't deviate.
 */

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;

$projectDir = OEGlobalsBag::getInstance()->getProjectDir();
$classLoader = new ModulesClassLoader($projectDir);
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\ClinicalCopilot\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

$dispatcher = OEGlobalsBag::getInstance()->getKernel()->getEventDispatcher();
(new Bootstrap($dispatcher))->subscribeToEvents();
