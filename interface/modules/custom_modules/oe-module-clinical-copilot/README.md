# oe-module-clinical-copilot

OpenEMR custom module — chat panel embedded in the patient chart,
backed by an external Python agent service.

See [../../../../ARCHITECTURE.md](../../../../ARCHITECTURE.md) for the
full design. This module is the PHP half; the Python half lives at
[../../../../agent-service/](../../../../agent-service/).

## What this module does

1. Renders a chat panel into `interface/patient_file/summary/demographics.php`
   via `RenderEvent::EVENT_RENDER_POST_PAGELOAD` — no core file is forked.
2. Exposes `public/chat.php` as the AJAX endpoint the panel posts to.
3. On every turn:
   - Verifies the OpenEMR session and ACL.
   - Mints a short-lived HMAC token (`AgentTokenMinter`) bound to
     `(user_id, patient_uuid)` derived from the session — never from
     the request body. This is the closure for AUDIT.md §1.2.
   - Forwards the message to the agent service over internal HTTP.
   - Logs the turn through `EventAuditLogger` (closes AUDIT.md §5.1).
4. Subscribes to `ViewEvent` so opening a chart fires `/agent/warm` —
   the encounter-open cache trick from ARCHITECTURE.md §5.

## Globals (set in OpenEMR Admin → Globals → Clinical Co-Pilot)

| Setting | Default | Notes |
|---------|---------|-------|
| `copilot_enabled` | on | Master switch |
| `copilot_agent_url` | `http://agent-service:8000` | Internal URL of Python service |
| `copilot_agent_shared_secret` | _(unset — required)_ | Must match `AGENT_SHARED_SECRET` env var on the Python service |

## Files

```
oe-module-clinical-copilot/
├── composer.json, info.txt, version.php, moduleConfig.php
├── openemr.bootstrap.php            registers PSR-4 + Bootstrap
├── ModuleManagerListener.php        install / enable / disable hooks
├── src/
│   ├── Bootstrap.php                subscribes to events
│   ├── Auth/AgentTokenMinter.php    HMAC token, matches Python verify
│   ├── Services/AgentClient.php     HTTP client to agent service
│   ├── Http/CopilotController.php   chat-turn endpoint logic
│   └── Listeners/PatientViewedListener.php   warm + panel injection
├── public/
│   ├── chat.php                     AJAX entry; loads globals.php
│   ├── js/copilot-chat.js
│   └── css/copilot-panel.css
└── sql/
    ├── install.sql                  oe_copilot_messages, oe_copilot_audit
    └── uninstall.sql
```

## Install

After dropping this directory under `interface/modules/custom_modules/`:

1. Log into OpenEMR as admin.
2. **Modules → Manage Modules → Unregistered tab** → Register
   *Clinical Co-Pilot*.
3. **Installed tab** → Install → Enable.
4. **Admin → Globals → Clinical Co-Pilot** → set the agent service
   URL and shared secret.
5. Open any patient chart — the chat panel appears in the lower right.

## Status

This is the Thursday-deliverable skeleton. What works:

- Module registers, installs, and enables.
- Chat panel renders on patient chart open.
- AJAX turn → HMAC token mint → agent-service call → response display.
- Patient-view event fires `/agent/warm`.
- Per-turn audit logging.

What's stubbed (TODO before final):

- Conversation history persistence (the table exists; the
  controller doesn't yet save).
- Streaming responses — turns are synchronous for v1.
- Globals editor UI under the standard Admin → Globals page (today
  the values must be set in `interface/super/edit_globals.php`
  via direct config or in the module's own settings page).
