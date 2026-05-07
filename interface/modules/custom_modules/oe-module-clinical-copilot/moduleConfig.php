<?php

/**
 * Clinical Co-Pilot — module config consumed by the Module Manager.
 */

return [
    'name' => 'Clinical Co-Pilot',
    'description' => 'AI clinical co-pilot embedded in the patient chart. '
        . 'See ARCHITECTURE.md at the repo root for the full design.',
    'version' => '0.1.0',
    'author' => 'AgentForge',
    'email' => 'tylerxia8@gmail.com',
    'license' => 'GPL-3.0',
    'acl_category' => 'patients',
    'acl_section' => 'demo',

    'require' => [
        'openemr' => '>=7.0.0',
    ],

    // Tables created by sql/install.sql.
    'tables' => [
        'oe_copilot_messages',
        'oe_copilot_audit',
    ],

    // Globals — user-editable in Admin → Globals → Clinical Co-Pilot.
    'globals' => [
        [
            'name' => 'copilot_enabled',
            'type' => 'bool',
            'default' => '1',
            'description' => 'Master switch for the Clinical Co-Pilot panel.',
        ],
        [
            'name' => 'copilot_agent_url',
            'type' => 'text',
            'default' => 'http://agent-service:8000',
            'description' => 'Internal URL of the Python agent service.',
        ],
        [
            'name' => 'copilot_agent_shared_secret',
            'type' => 'encrypted',
            'default' => '',
            'description' => 'HMAC shared secret. Must match the AGENT_SHARED_SECRET '
                . 'env var on the Python agent service. Generate with '
                . '`python -c "import secrets;print(secrets.token_hex(32))"`.',
        ],
        [
            'name' => 'copilot_dashboard_url',
            'type' => 'text',
            'default' => '',
            'description' => 'Public URL of the Next.js patient dashboard '
                . '(W2 surprise port). When set, every chart\'s embedded '
                . 'co-pilot panel renders a "Modern Dashboard ↗" link in '
                . 'its header that opens the chart in the modern view '
                . '(in a new tab). Leave blank to hide the link.',
        ],
    ],

    'install' => [
        'sql' => 'sql/install.sql',
    ],

    'uninstall' => [
        'sql' => 'sql/uninstall.sql',
    ],
];
