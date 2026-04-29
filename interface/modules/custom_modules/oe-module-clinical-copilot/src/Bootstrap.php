<?php

namespace OpenEMR\Modules\ClinicalCopilot;

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Core\OEGlobalsBag;
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
        $this->installPath = OEGlobalsBag::getInstance()->getWebRoot() . self::MODULE_INSTALLATION_PATH;
        $this->listener = new PatientViewedListener($this->installPath, ServiceContainer::getLogger());
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
        $globals = OEGlobalsBag::getInstance();
        return (bool) ($globals->get('copilot_enabled') ?? true);
    }
}
