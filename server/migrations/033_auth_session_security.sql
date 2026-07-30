-- ============================================================
-- Digital Signage — миграция 033
-- Управляемый отзыв пользовательских JWT-сессий
-- ============================================================

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS session_version BIGINT NOT NULL DEFAULT 1;

UPDATE users
SET session_version = 1
WHERE session_version IS NULL OR session_version < 1;

ALTER TABLE users
    ALTER COLUMN session_version SET DEFAULT 1,
    ALTER COLUMN session_version SET NOT NULL;

-- ============================================================
-- Конец миграции 033
-- ============================================================
