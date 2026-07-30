-- ============================================================
-- Digital Signage — миграция 031
-- Индивидуальные финансовые условия рекламной кампании
-- ============================================================
-- Кампания фиксирует собственный тариф и цену единицы. Изменение общего
-- тарифа рекламодателя после создания кампании не меняет её условия.
-- Ручная скидка хранится отдельно и обязательно сопровождается причиной.
-- ============================================================

ALTER TABLE campaigns
    ADD COLUMN IF NOT EXISTS billing_mode VARCHAR(16),
    ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS discount_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS discount_note TEXT,
    ADD COLUMN IF NOT EXISTS pricing_updated_by VARCHAR(64),
    ADD COLUMN IF NOT EXISTS pricing_updated_at TIMESTAMP;

-- Существующие кампании получают снимок действующего тарифа рекламодателя.
UPDATE campaigns c
SET billing_mode = COALESCE(c.billing_mode, a.billing_mode, 'per_minute'),
    unit_price = COALESCE(
        c.unit_price,
        CASE WHEN COALESCE(a.billing_mode, 'per_minute') = 'per_play'
             THEN a.price_per_play ELSE a.price_per_minute END,
        0
    ),
    pricing_updated_at = COALESCE(c.pricing_updated_at, c.created_at, NOW())
FROM advertisers a
WHERE a.id = c.advertiser_id
  AND (c.billing_mode IS NULL OR c.unit_price IS NULL);

ALTER TABLE campaigns
    ALTER COLUMN billing_mode SET DEFAULT 'per_minute',
    ALTER COLUMN billing_mode SET NOT NULL,
    ALTER COLUMN unit_price SET DEFAULT 0,
    ALTER COLUMN unit_price SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_billing_mode_check'
    ) THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_billing_mode_check
            CHECK (billing_mode IN ('per_play', 'per_minute'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_unit_price_check'
    ) THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_unit_price_check
            CHECK (unit_price >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'campaigns_discount_amount_check'
    ) THEN
        ALTER TABLE campaigns ADD CONSTRAINT campaigns_discount_amount_check
            CHECK (discount_amount >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_campaigns_financial
    ON campaigns(advertiser_id, billing_mode, date_from, date_to);

-- ============================================================
-- Конец миграции 031
-- ============================================================
