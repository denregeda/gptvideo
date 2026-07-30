"""Разбор HTTP Range для докачки больших медиафайлов."""
import pytest

from file_delivery import _parse_range


def test_open_ended_range():
    assert _parse_range("bytes=100-", 1000) == (100, 999)


def test_bounded_range_is_clamped_to_file():
    assert _parse_range("bytes=100-2000", 1000) == (100, 999)


def test_suffix_range():
    assert _parse_range("bytes=-200", 1000) == (800, 999)


@pytest.mark.parametrize("value", [
    "items=0-10",
    "bytes=1000-",
    "bytes=20-10",
    "bytes=0-10,20-30",
])
def test_invalid_range(value):
    with pytest.raises(ValueError):
        _parse_range(value, 1000)
