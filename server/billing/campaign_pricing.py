"""Индивидуальные финансовые условия рекламной кампании."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException

MONEY_STEP = Decimal("0.01")
MINUTES_STEP = Decimal("0.1")
BILLING_MODES = {"per_play", "per_minute"}


def money(value, field: str) -> Decimal:
    """Преобразовать пользовательское значение в неотрицательную сумму."""
    try:
        result = Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, f"{field}: ожидается денежная сумма")
    if result < 0:
        raise HTTPException(400, f"{field} не может быть отрицательной")
    return result


def billing_mode(value) -> str:
    mode = str(value or "").strip()
    if mode not in BILLING_MODES:
        allowed = ", ".join(sorted(BILLING_MODES))
        raise HTTPException(400, f"billing_mode должен быть одним из: {allowed}")
    return mode


@dataclass(frozen=True)
class CampaignPrice:
    """Снимок условий кампании и рассчитанный итог."""

    billing_mode: str
    unit_price: Decimal
    discount_amount: Decimal
    plays: int
    minutes: Decimal
    base_amount: Decimal
    total_amount: Decimal

    def as_dict(self) -> dict:
        return {
            "billing_mode": self.billing_mode,
            "unit_price": float(self.unit_price),
            "discount_amount": float(self.discount_amount),
            "plays": self.plays,
            "minutes": float(self.minutes),
            "base_amount": float(self.base_amount),
            "total_amount": float(self.total_amount),
        }


def calculate_campaign_price(
    mode,
    unit_price,
    discount_amount,
    plays: int,
    minutes,
) -> CampaignPrice:
    """Рассчитать стоимость фактически оказанного объёма кампании.

    Скидка — фиксированная сумма, подтверждённая администратором. Если на
    промежуточном этапе она больше начисленного объёма, итог показывается как
    ноль, но сама скидка сохраняется без изменения для прозрачного аудита.
    """
    parsed_mode = billing_mode(mode)
    price = money(unit_price, "unit_price")
    discount = money(discount_amount or 0, "discount_amount")
    try:
        plays_value = max(0, int(plays or 0))
        minutes_value = Decimal(str(minutes or 0)).quantize(
            MINUTES_STEP, rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, "Некорректный фактический объём кампании")
    if minutes_value < 0:
        minutes_value = Decimal("0.0")

    volume = Decimal(plays_value) if parsed_mode == "per_play" else minutes_value
    base = (volume * price).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    total = max(Decimal("0.00"), base - discount).quantize(
        MONEY_STEP, rounding=ROUND_HALF_UP
    )
    return CampaignPrice(
        billing_mode=parsed_mode,
        unit_price=price,
        discount_amount=discount,
        plays=plays_value,
        minutes=minutes_value,
        base_amount=base,
        total_amount=total,
    )
