"""Attack category modules. Each module exposes a single ``SPEC``
constant of type ``CategorySpec`` (defined in ``redteam.red_team``)
that the campaign runner loads at startup.

Adding a new category for the Friday final:
1. Add a new module here, e.g. ``state_corruption.py``
2. Add the enum value to ``ThreatCategory`` in ``redteam/messages.py``
3. Optionally wire a deterministic check in ``redteam/judge.py``
4. Register it in ``redteam/run_campaign.py``'s category map.
"""

from redteam.categories import (
    cost_amplification,
    cross_patient,
    indirect_injection,
    state_corruption,
)

CATEGORY_MODULES = {
    indirect_injection.SPEC.category: indirect_injection,
    cross_patient.SPEC.category: cross_patient,
    cost_amplification.SPEC.category: cost_amplification,
    state_corruption.SPEC.category: state_corruption,
}
