-- Clinical Co-Pilot — uninstall.
--
-- Run by the Module Manager on uninstall. Drops the module's own tables.
-- The standard `log` table rows written by EventAuditLogger are HIPAA-
-- retained and intentionally left alone here.

DROP TABLE IF EXISTS `oe_copilot_audit`;
DROP TABLE IF EXISTS `oe_copilot_messages`;
