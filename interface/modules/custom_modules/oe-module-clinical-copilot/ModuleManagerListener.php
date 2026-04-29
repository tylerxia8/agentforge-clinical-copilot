<?php

/**
 * Clinical Co-Pilot — Module Manager hooks.
 *
 * Called by OpenEMR's Module Manager UI when the user clicks
 * Register / Install / Enable / Disable / Unregister.
 *
 * The actual install/enable WORK has already happened by the time these
 * methods run — we just report status. Catch any exceptions and report
 * them in the return value, otherwise they get swallowed.
 */

use OpenEMR\Core\AbstractModuleActionListener;

class ModuleManagerListener extends AbstractModuleActionListener
{
    public function __construct()
    {
        parent::__construct();
    }

    public function moduleManagerAction(
        $methodName,
        $modId,
        string $currentActionStatus = 'Success'
    ): string {
        if (method_exists(self::class, $methodName)) {
            return self::$methodName($modId, $currentActionStatus);
        }
        return $currentActionStatus;
    }

    public static function getModuleNamespace(): string
    {
        return 'OpenEMR\\Modules\\ClinicalCopilot\\';
    }

    public static function initListenerSelf(): ModuleManagerListener
    {
        return new self();
    }

    /** @noinspection PhpUnused — invoked dynamically via moduleManagerAction() */
    private function install($modId, $currentActionStatus): string
    {
        // Any post-install setup goes here. The SQL in sql/install.sql
        // has already been run by the time we get called.
        return $currentActionStatus;
    }

    /** @noinspection PhpUnused */
    private function enable($modId, $currentActionStatus): string
    {
        return $currentActionStatus;
    }

    /** @noinspection PhpUnused */
    private function disable($modId, $currentActionStatus): string
    {
        return $currentActionStatus;
    }

    /** @noinspection PhpUnused */
    private function unregister($modId, $currentActionStatus): string
    {
        return $currentActionStatus;
    }
}
