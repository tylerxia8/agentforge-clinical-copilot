-- Clinical Co-Pilot — module install schema.
--
-- Two tables:
--   oe_copilot_messages — conversation history per (user, patient).
--                         Lets the doctor scroll back. HIPAA-retained.
--   oe_copilot_audit    — agent-call metadata that doesn't fit the
--                         standard `log` table (token counts, tool list,
--                         verification verdict). Joined to `log.id` so
--                         the existing audit infrastructure stays the
--                         system of record for "who accessed what".

CREATE TABLE IF NOT EXISTS `oe_copilot_messages` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    `conversation_id` varchar(64) NOT NULL,
    `user_id` bigint(20) NOT NULL COMMENT 'users.id',
    `patient_pid` bigint(20) NOT NULL COMMENT 'patient_data.pid',
    `role` enum('user','assistant') NOT NULL,
    `content` mediumtext NOT NULL,
    `sources` text NULL COMMENT 'JSON array of cited row ids',
    `refused` tinyint(1) NOT NULL DEFAULT 0,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_conversation` (`conversation_id`, `created_at`),
    -- Read pattern: "this user's chat history with this patient",
    -- ordered most-recent-first.
    KEY `idx_user_patient_recent` (`user_id`, `patient_pid`, `created_at` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `oe_copilot_audit` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    -- Ties this row back to the standard `log` row written by
    -- EventAuditLogger::newEvent('copilot-turn', …) in CopilotController.
    `log_id` bigint(20) NULL,
    `conversation_id` varchar(64) NOT NULL,
    `user_id` bigint(20) NOT NULL,
    `patient_pid` bigint(20) NOT NULL,
    `tool_calls` text NULL COMMENT 'JSON list of tool names invoked',
    `verification_passed` tinyint(1) NULL,
    `verification_reason` text NULL COMMENT 'populated only on failure',
    `input_tokens` int NULL,
    `output_tokens` int NULL,
    `latency_ms` int NULL,
    `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_log` (`log_id`),
    KEY `idx_user_patient_recent` (`user_id`, `patient_pid`, `created_at` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
