"""Tests for ``AnkerSolixBaseApi._update_dev`` and ``_update_account``.

The subclass in ``api.py`` overrides ``_update_dev`` and is covered in
``test_api_update_dev.py``. This suite exercises the base class directly so
changes to the inherited merge/conversion logic surface here.
"""

from __future__ import annotations

import pytest

from custom_components.anker_charger.solixapi.apibase import AnkerSolixBaseApi


@pytest.fixture
def base_api() -> AnkerSolixBaseApi:
    return AnkerSolixBaseApi(email="test@example.com", password="x", countryId="US")


class TestBaseUpdateDev:
    """The base class normalizes a few field names and type-coerces known keys."""

    def test_device_sn_required(self, base_api: AnkerSolixBaseApi):
        assert base_api._update_dev({"device_pn": "A91B2"}) is None
        assert base_api.devices == {}

    def test_returns_sn_on_success(self, base_api: AnkerSolixBaseApi):
        assert (
            base_api._update_dev({"device_sn": "AFCJTB0F00000001"})
            == "AFCJTB0F00000001"
        )

    def test_device_sw_version_renamed(self, base_api: AnkerSolixBaseApi):
        base_api._update_dev(
            {"device_sn": "AFCJTB0F00000001", "device_sw_version": "v1.2.3"}
        )
        assert base_api.devices["AFCJTB0F00000001"]["sw_version"] == "v1.2.3"

    def test_boolean_coercion(self, base_api: AnkerSolixBaseApi):
        # wifi_online / auto_upgrade / is_ota_update get bool-coerced.
        base_api._update_dev(
            {
                "device_sn": "AFCJTB0F00000001",
                "wifi_online": 1,
                "auto_upgrade": 0,
                "is_ota_update": "yes",
            }
        )
        dev = base_api.devices["AFCJTB0F00000001"]
        assert dev["wifi_online"] is True
        assert dev["auto_upgrade"] is False
        assert dev["is_ota_update"] is True

    def test_admin_from_ms_device_type(self, base_api: AnkerSolixBaseApi):
        # ms_device_type 0 or 1 → admin True; other values → False.
        base_api._update_dev(
            {"device_sn": "AFCJTB0F00000001", "ms_device_type": 1}
        )
        assert base_api.devices["AFCJTB0F00000001"]["is_admin"] is True

        base_api._update_dev(
            {"device_sn": "AFCJTB0F00000002", "ms_device_type": 5}
        )
        assert base_api.devices["AFCJTB0F00000002"]["is_admin"] is False

    def test_explicit_is_admin_overrides_ms_device_type(
        self, base_api: AnkerSolixBaseApi
    ):
        base_api._update_dev(
            {"device_sn": "AFCJTB0F00000001", "ms_device_type": 5},
            isAdmin=True,
        )
        assert base_api.devices["AFCJTB0F00000001"]["is_admin"] is True

    def test_site_id_set_when_passed(self, base_api: AnkerSolixBaseApi):
        base_api._update_dev(
            {"device_sn": "AFCJTB0F00000001"},
            siteId="site-abc",
        )
        assert base_api.devices["AFCJTB0F00000001"]["site_id"] == "site-abc"

    def test_type_lowercased(self, base_api: AnkerSolixBaseApi):
        base_api._update_dev(
            {"device_sn": "AFCJTB0F00000001"},
            devType="CHARGER",
        )
        assert base_api.devices["AFCJTB0F00000001"]["type"] == "charger"

    def test_multiple_devices_tracked_independently(
        self, base_api: AnkerSolixBaseApi
    ):
        base_api._update_dev({"device_sn": "SN1", "charge": True})
        base_api._update_dev({"device_sn": "SN2", "charge": False})
        assert base_api.devices["SN1"]["charge"] is True
        assert base_api.devices["SN2"]["charge"] is False


class TestBaseUpdateAccount:
    """The account bookkeeper tracks session identity + request stats."""

    def test_identity_populated_on_first_call(
        self, base_api: AnkerSolixBaseApi
    ):
        base_api._update_account({})
        acc = base_api.account
        assert acc["type"] == "account"
        assert acc["email"] == "test@example.com"
        assert acc["country"] == "US"

    def test_extra_details_merge(self, base_api: AnkerSolixBaseApi):
        base_api._update_account({"custom_key": "abc", "extra_flag": True})
        acc = base_api.account
        assert acc["custom_key"] == "abc"
        assert acc["extra_flag"] is True

    def test_request_counts_populated(self, base_api: AnkerSolixBaseApi):
        base_api._update_account({})
        acc = base_api.account
        # The RequestCounter is empty but the fields exist and are numeric.
        assert isinstance(acc["requests_last_min"], int)
        assert isinstance(acc["requests_last_hour"], int)

    def test_mqtt_connection_false_without_session(
        self, base_api: AnkerSolixBaseApi
    ):
        base_api._update_account({})
        assert base_api.account["mqtt_connection"] is False
