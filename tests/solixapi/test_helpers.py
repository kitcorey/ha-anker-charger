"""Tests for the small helper utilities in ``solixapi/helpers.py``."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum

import pytest

from custom_components.anker_charger.solixapi.helpers import (
    RequestCounter,
    convertToKwh,
    get_enum_name,
    get_enum_value,
    md5,
    round_by_factor,
)


class TestRequestCounter:
    def test_empty_counter(self):
        rc = RequestCounter()
        assert rc.last_minute() == 0
        assert rc.last_hour() == 0

    def test_add_increments_both_windows(self):
        rc = RequestCounter()
        rc.add()
        assert rc.last_minute() == 1
        assert rc.last_hour() == 1

    def test_old_entries_not_counted(self):
        rc = RequestCounter()
        rc.add(request_time=datetime.now() - timedelta(minutes=5))
        assert rc.last_minute() == 0
        assert rc.last_hour() == 1

    def test_throttle_tracking(self):
        rc = RequestCounter()
        rc.add_throttle("power_service/v1/app/get_bind_devices")
        rc.add_throttle("power_service/v1/app/get_bind_devices")
        assert "power_service/v1/app/get_bind_devices" in rc.throttled
        assert len(rc.throttled) == 1  # set, so deduped


class TestMd5:
    def test_string_input(self):
        assert md5("hello") == "5d41402abc4b2a76b9719d911017c592"

    def test_bytes_input(self):
        assert md5(b"hello") == "5d41402abc4b2a76b9719d911017c592"

    def test_empty(self):
        assert md5("") == "d41d8cd98f00b204e9800998ecf8427e"


class TestConvertToKwh:
    @pytest.mark.parametrize(
        ("val", "unit", "expected"),
        [
            (1500, "Wh", 1.5),
            (1500, "wh", 1.5),  # case-insensitive
            (1.5, "kWh", 1.5),  # unrecognized unit defaults to passthrough
            (2.5, "MWh", 2500.0),
            (0.0005, "GWh", 500.0),
        ],
    )
    def test_numeric_input(self, val, unit, expected):
        assert convertToKwh(val, unit) == expected

    def test_string_input_preserves_string_formatting(self):
        # String-in → string-out, padded to the requested decimals.
        assert convertToKwh("1500", "Wh", decimals=2) == "1.50"

    def test_invalid_string_returns_none(self):
        assert convertToKwh("not-a-number", "Wh") is None

    def test_non_string_unit_returns_none(self):
        assert convertToKwh(1.0, None) is None


class _Status(str, Enum):
    on = "on"
    off = "off"


class TestEnumHelpers:
    def test_get_enum_name_found(self):
        assert get_enum_name(_Status, "on") == "on"

    def test_get_enum_name_missing_returns_default(self):
        assert get_enum_name(_Status, "pending", default="unknown") == "unknown"

    def test_get_enum_value_found(self):
        assert get_enum_value(_Status, "on") == "on"

    def test_get_enum_value_missing_returns_default(self):
        assert get_enum_value(_Status, "pending", default="?") == "?"


class TestRoundByFactor:
    @pytest.mark.parametrize(
        ("value", "factor", "expected"),
        [
            (1.2345, 0.01, 1.23),
            (1.2357, 0.001, 1.236),
            (1.49, 1, 1),
            (1.51, 1, 2),
            (0, 0.001, 0),
            (-0.0, 0.001, 0),  # negative zero should stringify as 0
        ],
    )
    def test_round(self, value, factor, expected):
        assert round_by_factor(value, factor) == expected
