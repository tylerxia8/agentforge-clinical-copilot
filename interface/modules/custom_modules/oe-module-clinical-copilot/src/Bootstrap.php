<?php

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\Common\Logging\SystemLogger;
use OpenEMR\Events\PatientDemographics\RenderEvent;
use OpenEMR\Events\PatientDemographics\ViewEvent;
use OpenEMR\Modules\ClinicalCopilot\Listeners\PatientViewedListener;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

/**
 * Clinical Co-Pilot module entry point.
 *
 * Subscribes to two OpenEMR events:
 *   - ViewEvent::EVENT_HANDLE — fires when a patient chart is opened.
 *     We use this to fire `/agent/warm` against the Python service so
 *     the per-patient context bundle is hot before the first chat turn.
 *   - RenderEvent::EVENT_RENDER_POST_PAGELOAD — fires after the
 *     patient demographics page has rendered. We use this to inject
 *     the chat panel HTML and JS without forking demographics.php.
 *
 * Reads from $GLOBALS directly (compatible with OpenEMR 7.0.3, which
 * doesn't ship the OEGlobalsBag wrapper or BC\ServiceContainer that
 * 8.x-master uses). The module loader pre-populates $GLOBALS['webroot']
 * and $GLOBALS['kernel'] before openemr.bootstrap.php runs.
 */
class Bootstrap
{
    public const MODULE_NAME = 'oe-module-clinical-copilot';
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/' . self::MODULE_NAME;

    private readonly string $installPath;
    private readonly PatientViewedListener $listener;

    public function __construct(
        private readonly EventDispatcherInterface $dispatcher
    ) {
        $webRoot = (string) ($GLOBALS['webroot'] ?? '');
        $this->installPath = $webRoot . self::MODULE_INSTALLATION_PATH;
        $this->listener = new PatientViewedListener($this->installPath, new SystemLogger());
    }

    public function subscribeToEvents(): void
    {
        if (!$this->isEnabled()) {
            return;
        }

        // Warm the agent's per-patient context cache on chart open.
        $this->dispatcher->addListener(
            ViewEvent::EVENT_HANDLE,
            $this->listener->onPatientViewed(...),
        );

        // Inject the chat panel after the demographics page renders.
        $this->dispatcher->addListener(
            RenderEvent::EVENT_RENDER_POST_PAGELOAD,
            $this->listener->onRenderPostPageload(...),
        );
    }

    private function isEnabled(): bool
    {
        // Default to true — the global is created by Module Manager on
        // install but won't exist until then.
        return (bool) ($GLOBALS['copilot_enabled'] ?? true);
    }
}
