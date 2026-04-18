"""Tests for ``AnkerSolixApi._update_dev``.

Exercises cloud-field normalization, charger metadata defaults, and
rssi → wifi_signal derivation in isolation by constructing an
``AnkerSolixApi`` instance and calling ``_update_dev`` with synthetic
bind-devices payloads.
"""

from __future__ import annotations

import pytest

from custom_components.anker_charger.solixapi.api import AnkerSolixApi


@pytest.fixture
def api() -> AnkerSolixApi:
    """Return a fresh API instance with no real network dependencies.

    The instance is usable without authentication because ``_update_dev``
    only touches ``self.devices`` — the apisession isn't invoked.
    """
    return AnkerSolixApi(email="test@example.com", password="x", countryId="US")


def _dev(**overrides) -> dict:
    """Return a minimal bind_devices-shaped payload with sensible defaults."""
    base = {
        "device_sn": "AFCJTB0F00000001",
        "ms_device_type": 1,  # owner → admin True
        "product_code": "A91B2",
        "device_name": "240W Charging Station",
        "alias_name": "Test Charger",
        "device_sw_version": "v1.1.2.4",
        "wifi_online": True,
        "rssi": "-65",
    }
    base.update(overrides)
    return base


class TestFieldNormalization:
    """Cloud bind_devices payload fields should be renamed to canonical keys."""

    def test_product_code_becomes_device_pn(self, api: AnkerSolixApi):
        api._update_dev(_dev())
        assert api.devices["AFCJTB0F00000001"]["device_pn"] == "A91B2"
        # Source key is stripped from the stored dict.
        assert "product_code" not in api.devices["AFCJTB0F00000001"]

    def test_device_name_becomes_name(self, api: AnkerSolixApi):
        api._update_dev(_dev())
        assert (
            api.devices["AFCJTB0F00000001"]["name"] == "240W Charging Station"
        )
        assert "device_name" not in api.devices["AFCJTB0F00000001"]

    def test_alias_name_becomes_alias(self, api: AnkerSolixApi):
        api._update_dev(_dev(alias_name="Sunroom Charging Station"))
        assert (
            api.devices["AFCJTB0F00000001"]["alias"] == "Sunroom Charging Station"
        )
        assert "alias_name" not in api.devices["AFCJTB0F00000001"]

    def test_device_sw_version_becomes_sw_version(self, api: AnkerSolixApi):
        api._update_dev(_dev(device_sw_version="v2.0.0.1"))
        assert api.devices["AFCJTB0F00000001"]["sw_version"] == "v2.0.0.1"

    def test_missing_device_sn_returns_none(self, api: AnkerSolixApi):
        assert api._update_dev({"product_code": "A91B2"}) is None
        assert api.devices == {}


class TestChargerMetadataDefaults:
    """Admin chargers must surface as MQTT-capable with sane defaults."""

    def test_mqtt_flags_set_for_admin_charger(self, api: AnkerSolixApi):
        api._update_dev(_dev())
        dev = api.devices["AFCJTB0F00000001"]
        assert dev["mqtt_supported"] is True
        assert dev["mqtt_overlay"] is False
        assert dev["mqtt_status_request"] is True

    def test_type_inferred_from_product_code(self, api: AnkerSolixApi):
        api._update_dev(_dev())
        assert api.devices["AFCJTB0F00000001"]["type"] == "charger"

    def test_explicit_devtype_overrides_inference(self, api: AnkerSolixApi):
        api._update_dev(_dev(), devType="custom")
        assert api.devices["AFCJTB0F00000001"]["type"] == "custom"

    def test_passive_device_not_marked_mqtt_supported(self, api: AnkerSolixApi):
        api._update_dev(_dev(is_passive=True))
        dev = api.devices["AFCJTB0F00000001"]
        assert dev.get("mqtt_supported") is not True

    def test_unknown_product_code_has_no_type(self, api: AnkerSolixApi):
        api._update_dev(_dev(product_code="UNKNOWN"))
        dev = api.devices["AFCJTB0F00000001"]
        assert dev["device_pn"] == "UNKNOWN"
        assert "type" not in dev


class TestWifiSignalFromRssi:
    """Map rssi (dBm) to a 0-100 percentage clamped to −85…−50 dBm.

    Formula: ``(rssi − −85) × 100 / (−50 − −85)``
    """

    @pytest.mark.parametrize(
        ("rssi", "expected_pct"),
        [
            ("-50", "100"),
            ("-60", "71"),  # (-60 - -85) * 100 / 35 = 2500/35 ≈ 71.43 → 71
            ("-70", "43"),  # (-70 - -85) * 100 / 35 = 1500/35 ≈ 42.86 → 43
            ("-85", "0"),
            ("-100", "0"),  # clamp
            ("-40", "100"),  # clamp
        ],
    )
    def test_rssi_maps_to_wifi_signal(
        self, api: AnkerSolixApi, rssi: str, expected_pct: str
    ):
        api._update_dev(_dev(rssi=rssi))
        assert api.devices["AFCJTB0F00000001"]["wifi_signal"] == expected_pct

    def test_wifi_signal_not_overridden_when_present(self, api: AnkerSolixApi):
        # If the cloud supplies wifi_signal directly, keep it.
        api._update_dev(_dev(wifi_signal="42", rssi="-50"))
        assert api.devices["AFCJTB0F00000001"]["wifi_signal"] == "42"

    def test_zero_rssi_skips_derivation(self, api: AnkerSolixApi):
        # rssi=0 is a sentinel from the apibase encoder; don't compute a
        # signal percentage from it.
        api._update_dev(_dev(rssi="0"))
        assert "wifi_signal" not in api.devices["AFCJTB0F00000001"]

    def test_non_numeric_rssi_is_tolerated(self, api: AnkerSolixApi):
        # Garbage values shouldn't crash _update_dev; just skip derivation.
        api._update_dev(_dev(rssi="not-a-number"))
        assert "wifi_signal" not in api.devices["AFCJTB0F00000001"]


class TestMerge:
    """Second calls for the same SN should merge, not overwrite."""

    def test_subsequent_update_merges_new_fields(self, api: AnkerSolixApi):
        api._update_dev(_dev())
        api._update_dev(
            {
                "device_sn": "AFCJTB0F00000001",
                "charge": True,
                "ms_device_type": 1,
            }
        )
        dev = api.devices["AFCJTB0F00000001"]
        assert dev["charge"] is True
        assert dev["alias"] == "Test Charger"  # preserved from first call
