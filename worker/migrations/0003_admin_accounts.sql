-- Operator control of accounts: a disabled account cannot sign in and its sessions end.
-- 0 means enabled; otherwise the time it was disabled (Unix seconds).
ALTER TABLE users ADD COLUMN disabled_at REAL NOT NULL DEFAULT 0;
