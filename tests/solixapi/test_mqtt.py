"""Tests for ``AnkerSolixMqttSession`` with the paho client fully mocked.

These tests never bind a socket — the ``mqtt.Client`` is swapped for a
``MagicMock`` so message callbacks can be fired synchronously with
hand-built ``MQTTMessage`` payloads.
"""

from __future__ import annotations

from base64 import b64encode
import json
from types import SimpleNamespace

from aiohttp import ClientSession
import pytest

from custom_components.anker_charger.solixapi.mqtt import AnkerSolixMqttSession
from custom_components.anker_charger.solixapi.session import (
    AnkerSolixClientSession,
)
from tests.fixtures import mqtt_payloads


@pytest.fixture
async def apisession():
    """Return an unauthenticated AnkerSolixClientSession."""
    async with ClientSession() as websession:
        yield AnkerSolixClientSession(
            email="tester@example.com",
            password="pw",
            countryId="US",
            websession=websession,
        )


@pytest.fixture
def mqtt_session(apisession: AnkerSolixClientSession) -> AnkerSolixMqttSession:
    """Return a fresh MQTT session with no real client attached.

    ``client`` is left None so no connect is attempted — tests drive
    ``on_message`` / ``on_connect`` callbacks by hand.
    """
    ms = AnkerSolixMqttSession(apisession=apisession)
    # A fresh stats object so on_message assertions start from zero.
    from custom_components.anker_charger.solixapi.mqtttypes import MqttDataStats

    ms.mqtt_stats = MqttDataStats()
    return ms


def _fake_msg(topic: str, hex_payload: str, pn: str, sn: str, ts: int = 1776485920):
    """Build a fake paho ``MQTTMessage`` shaped like real A91B2 traffic."""
    inner_payload = json.dumps(
        {
            "data": b64encode(bytes.fromhex(hex_payload)).decode(),
            "sn": sn,
            "pn": pn,
        }
    )
    envelope = json.dumps(
        {
            "head": {
                "version": "1.0.0.1",
                "client_id": sn,
                "msg_seq": 1,
                "cmd": 16,
                "cmd_status": 1,
                "sign_code": 0,
                "seed": "null",
                "timestamp": ts,
            },
            "payload": inner_payload,
        }
    ).encode()
    return SimpleNamespace(topic=topic, payload=envelope)


class TestOnMessage:
    def test_0a00_status_populates_mqtt_data(
        self, mqtt_session: AnkerSolixMqttSession
    ):
        hex_str, expected = mqtt_payloads.status_0a00(
            usbc_1=(1, 5000, 2000, 1000),
            ac_1_switch=1,
        )
        msg = _fake_msg(
            topic="dt/anker_power/A91B2/AFCJTB0F00000001/param_info",
            hex_payload=hex_str,
            pn="A91B2",
            sn="AFCJTB0F00000001",
        )

        mqtt_session.on_message(client=None, userdata=None, msg=msg)

        stored = mqtt_session.mqtt_data["AFCJTB0F00000001"]
        assert stored["usbc_1_status"] == 1
        assert stored["usbc_1_voltage"] == pytest.approx(5.0)
        assert stored["ac_1_switch"] == 1
        assert expected["usbc_1_power"] == stored["usbc_1_power"]
        # Topic is stashed so callers can audit which channels a device
        # publishes on.
        assert msg.topic in stored["topics"]

    def test_0303_realtime_updates_stored_values(
        self, mqtt_session: AnkerSolixMqttSession
    ):
        hex_str, _ = mqtt_payloads.realtime_0303(
            usbc_1=(1, 5100, 3000, 1530),
        )
        msg = _fake_msg(
            topic="dt/anker_power/A91B2/AFCJTB0F00000001/param_info",
            hex_payload=hex_str,
            pn="A91B2",
            sn="AFCJTB0F00000001",
        )
        mqtt_session.on_message(client=None, userdata=None, msg=msg)
        assert (
            mqtt_session.mqtt_data["AFCJTB0F00000001"]["usbc_1_power"]
            == pytest.approx(15.3)
        )

    def test_callback_invoked_with_extracted_values(
        self, mqtt_session: AnkerSolixMqttSession
    ):
        seen = []
        mqtt_session._message_callback = (
            lambda *args: seen.append(args)
        )
        hex_str, _ = mqtt_payloads.status_0a00(ac_1_switch=1)
        msg = _fake_msg(
            topic="dt/anker_power/A91B2/AFCJTB0F00000001/param_info",
            hex_payload=hex_str,
            pn="A91B2",
            sn="AFCJTB0F00000001",
        )
        mqtt_session.on_message(client=None, userdata=None, msg=msg)

        assert len(seen) == 1
        _, topic, _, _, model, device_sn, extracted = seen[0]
        assert model == "A91B2"
        assert device_sn == "AFCJTB0F00000001"
        assert extracted["ac_1_switch"] == 1
        assert topic == msg.topic

    def test_unknown_model_does_not_crash(
        self, mqtt_session: AnkerSolixMqttSession
    ):
        hex_str, _ = mqtt_payloads.status_0a00()
        msg = _fake_msg(
            topic="dt/anker_power/UNKNOWN/SN1/param_info",
            hex_payload=hex_str,
            pn="UNKNOWN",
            sn="SN1",
        )
        # Should not raise; unknown model records the topic but no port
        # values get extracted (the SOLIXMQTTMAP lookup returns empty).
        mqtt_session.on_message(client=None, userdata=None, msg=msg)
        stored = mqtt_session.mqtt_data.get("SN1", {})
        assert "usbc_1_power" not in stored
        assert "ac_1_switch" not in stored


class TestTopicPrefix:
    def test_subscription_topic_for_charger(
        self, mqtt_session: AnkerSolixMqttSession
    ):
        # After a successful login the mqtt_info dict contains the app_name
        # returned by the Anker cloud. Stub it here.
        mqtt_session.mqtt_info = {"app_name": "anker_power"}
        prefix = mqtt_session.get_topic_prefix(
            deviceDict={"device_pn": "A91B2", "device_sn": "SN1"}
        )
        assert prefix == "dt/anker_power/A91B2/SN1/"


class TestIsConnected:
    def test_false_without_client(self, mqtt_session: AnkerSolixMqttSession):
        assert mqtt_session.is_connected() is False
