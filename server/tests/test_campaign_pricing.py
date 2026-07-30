"""Денежная математика индивидуальных условий кампании."""
from decimal import Decimal

import pytest
from fastapi import HTTPException

from billing.campaign_pricing import (
    billing_mode,
    calculate_campaign_price,
    money,
)
from deps import is_path_allowed_for_advertiser


def test_per_play_uses_fact_and_manual_discount():
    result = calculate_campaign_price("per_play", "2.50", "10.00", 12, 999)
    assert result.base_amount == Decimal("30.00")
    assert result.total_amount == Decimal("20.00")


def test_per_minute_uses_minutes_not_plays():
    result = calculate_campaign_price("per_minute", "100.00", "25.00", 999, "1.5")
    assert result.base_amount == Decimal("150.00")
    assert result.total_amount == Decimal("125.00")


def test_discount_never_makes_total_negative():
    result = calculate_campaign_price("per_play", "1.00", "100.00", 3, 0)
    assert result.base_amount == Decimal("3.00")
    assert result.discount_amount == Decimal("100.00")
    assert result.total_amount == Decimal("0.00")


def test_money_rounds_to_kopecks_deterministically():
    assert money("1.005", "price") == Decimal("1.01")


@pytest.mark.parametrize("value", ["-1", "abc", None])
def test_money_rejects_invalid_values(value):
    with pytest.raises(HTTPException):
        money(value, "unit_price")


def test_only_supported_billing_modes_are_allowed():
    assert billing_mode("per_play") == "per_play"
    assert billing_mode("per_minute") == "per_minute"
    with pytest.raises(HTTPException):
        billing_mode("fixed")


def test_advertiser_request_paths_are_explicitly_allowed():
    assert is_path_allowed_for_advertiser("/advertisers/42/requests")
    assert not is_path_allowed_for_advertiser("/placement-requests")
