"""Base Class for interacting with the Anker Power / Solix API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from .apitypes import (
    API_ENDPOINTS,
    API_FILEPREFIXES,
    SolixDeviceType,
)
from .mqtt import AnkerSolixMqttSession, MessageCallback
from .session import AnkerSolixClientSession

MqttUpdateCallback = Callable[[str], None]
DeviceCacheCallback = Callable[[dict], None]


class AnkerSolixBaseApi:
    """Define the API base class to handle Anker server communication via AnkerSolixClientSession.

    It will also build internal cache dictionaries with information collected through the Api, those methods can be overwritten.
    It also provides some general Api queries and helpers for classes inheriting the base class
    """

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        countryId: str | None = None,
        websession: ClientSession | None = None,
        logger: logging.Logger | None = None,
        apisession: AnkerSolixClientSession | None = None,
    ) -> None:
        """Initialize."""
        self.apisession: AnkerSolixClientSession
        if apisession:
            # reuse provided client
            self.apisession = apisession
        else:
            # init new client
            self.apisession = AnkerSolixClientSession(
                email=email,
                password=password,
                countryId=countryId,
                websession=websession,
                logger=logger,
            )
        self._logger: logging.Logger = self.apisession.logger()
        self.mqttsession: AnkerSolixMqttSession | None = None
        # callback for device MQTT data update
        self._mqtt_update_callback: MqttUpdateCallback | None = None
        # reset class variables for saving the most recent account and device data (Api cache)
        self.account: dict[str, dict] = {}
        self.sites: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}
        self._device_callbacks: dict[str, dict] = {}

    def testDir(self, subfolder: str | None = None) -> str:
        """Get or set the subfolder for local API test files in the api session."""
        return self.apisession.testDir(subfolder)

    def endpointLimit(self, limit: int | None = None) -> int:
        """Get or set the api request limit per endpoint per minute."""
        return self.apisession.endpointLimit(limit)

    def logger(self, logger: logging.Logger | None = None) -> logging.Logger:
        """Get or set the logger for API client."""
        if logger:
            self._logger = logger
        return self._logger

    def logLevel(self, level: int | None = None) -> int:
        """Get or set the logger log level."""
        if level is not None and isinstance(level, int):
            self._logger.setLevel(level)
            self._logger.info(
                "Set api %s log level to: %s", self.apisession.nickname, level
            )
        return self._logger.getEffectiveLevel()

    def mqtt_update_callback(
        self, func: MqttUpdateCallback | None = ""
    ) -> MqttUpdateCallback | None:
        """Get or set the MqttUpdateCallback for this session."""
        if callable(func) or func is None:
            self._mqtt_update_callback = func
        return self._mqtt_update_callback

    def getCaches(self) -> dict:
        """Return a merged dictionary with api cache dictionaries."""
        return (
            self.sites
            | self.devices
            | {self.apisession.email: self.account}
            | (self.account.get("vehicles") or {})
        )

    def clearCaches(self) -> None:
        """Clear the api cache dictionaries."""
        # check callbacks and notify registered devices about removal from cache
        for callbacks in self._device_callbacks.values():
            for func in callbacks.get("functions", set()):
                if callable(func):
                    func(device={})
        self._device_callbacks = {}
        self.sites = {}
        self.devices = {}
        self.account = {}
        # check active MQTT session and stop it
        if self.mqttsession:
            self.stopMqttSession()

    def customizeCacheId(self, id: str, key: str, value: Any) -> None:
        """Customize a cache identifier with a key and value pair."""
        if isinstance(id, str) and isinstance(key, str):
            if id in self.devices:
                data = self.devices.get(id)
                customized = data.get("customized") or {}
                customized[key] = value
                data["customized"] = customized
                # trigger dependent updates by rewriting old value to cache update method
                # customized keys that are used as alternate value must be handled separately since they may not exist in cache
                if key in data:
                    self._update_dev(devData={"device_sn": id, key: data[key]})
                    # Ensure to update main device capacity as well if sub device was customized
                    if (
                        key == "battery_capacity"
                        and value
                        and data.get("is_subdevice")
                        and (main := data.get("main_sn"))
                        and (cap := (self.devices.get(main) or {}).get(key))
                    ):
                        # first remove any previous customization on main device
                        (self.devices[main].get("customized") or {}).pop(key, None)
                        # trigger calculation update
                        self._update_dev(devData={"device_sn": main, key: cap})
            elif id == self.apisession.email:
                data = self.account
                customized = data.get("customized") or {}
                customized[key] = value
                data["customized"] = customized
                # trigger dependent updates by rewriting old value to cache update method
                # customized keys that are used as alternate value must be handled separately since they may not exist in cache
                if key in data:
                    self._update_account(details={key: data.get(key)})

    def recycleDevices(self, extraDevices: set | None = None) -> None:
        """Drop devices from the cache that are no longer listed in bind_devices."""
        if not extraDevices or not isinstance(extraDevices, set):
            extraDevices = set()
        rem_devices = [dev for dev in self.devices if dev not in extraDevices]
        for dev in rem_devices:
            self.devices.pop(dev, None)
            # notify registered device callbacks about removal from cache
            cbs = self._device_callbacks.pop(dev, {})
            for func in cbs.get("functions", set()):
                if callable(func):
                    func(device={})

    def register_device_callback(
        self, deviceSn: str, func: DeviceCacheCallback, dynamic_descriptions: dict
    ) -> None:
        """Register a device callback function to notify about Api cache object changes."""
        # register callback if callable
        if callable(func):
            cbs = self._device_callbacks.get(deviceSn, {})
            f = cbs.get("functions", set())
            f.add(func)
            cbs["functions"] = f
            cbs["dynamic_descriptions"] = dynamic_descriptions
            self._device_callbacks[deviceSn] = cbs

    def notify_device(self, deviceSn: str) -> None:
        """Notify all callbacks that are registered for a device."""
        for func in self._device_callbacks.get(deviceSn, {}).get("functions", set()):
            if callable(func):
                func(device=self.devices.get(deviceSn, {}))

    async def startMqttSession(
        self, message_callback: MessageCallback | None = None, fromFile: bool = False
    ) -> AnkerSolixMqttSession | None:
        """(Re)Start the MQTT session, and if not fromFile also (Re)connect to server."""
        # Initialize the session if required
        if not self.mqttsession:
            self.mqttsession = AnkerSolixMqttSession(apisession=self.apisession)
        # (Re)Connect the MQTT client
        if not fromFile and not self.mqttsession.is_connected():
            await self.mqttsession.connect_client_async()
            if not self.mqttsession.is_connected():
                self._logger.error(
                    "Api %s failed connecting to MQTT server %s:%s",
                    self.apisession.nickname,
                    self.mqttsession.host,
                    self.mqttsession.port,
                )
                self.mqttsession.cleanup()
                self.mqttsession = None
                return self.mqttsession
            self._logger.debug(
                "Api %s connected successfully to MQTT server %s:%s",
                self.apisession.nickname,
                self.mqttsession.host,
                self.mqttsession.port,
            )
        # register message callback to extract device MQTT data into device Api cache if no custom callback provided and none exists yet
        self.mqttsession.message_callback(
            func=message_callback
            if callable(message_callback)
            else (self.mqttsession.message_callback() or self.mqtt_received)
        )
        # create the mqtt_data field if not existing yet for supported devices
        for dev in [d for d in self.devices.values() if d.get("mqtt_supported")]:
            dev["mqtt_data"] = dev.get("mqtt_data") or {}
        # update mqtt connection in account cache
        self._update_account()
        return self.mqttsession

    def stopMqttSession(self) -> None:
        """Stop and cleanup the MQTT session."""
        if self.mqttsession:
            self._logger.debug(
                "Api %s stopping MQTT session to server %s:%s",
                self.apisession.nickname,
                self.mqttsession.host,
                self.mqttsession.port,
            )
            self.mqttsession.cleanup()
            self.mqttsession = None
            self._mqtt_update_callback = None
            # clear mqtt data from device cache to prevent stale mqtt data
            for dev in self.devices.values():
                dev.pop("mqtt_data", None)
            # update mqtt state in account cache
            self._update_account({"mqtt_statistic": None})

    def _update_account(
        self,
        details: dict | None = None,
    ) -> None:
        """Update the internal account dictionary with data provided in details dictionary.

        This method is used to consolidate acount related details from various less frequent requests that are not covered with the update_sites method.
        """
        if not details or not isinstance(details, dict):
            details = {}
        # lookup old account details if any or update account info if nickname is different (e.g. after authentication)
        if (
            not (account_details := self.account or {})
            or account_details.get("nickname") != self.apisession.nickname
        ):
            # init or update the account details
            account_details.update(
                {
                    "type": SolixDeviceType.ACCOUNT.value,
                    "email": self.apisession.email,
                    "nickname": self.apisession.nickname,
                    "country": self.apisession.countryId,
                    "server": self.apisession.server,
                }
            )
        # update extra details and always request counts and mqtt connection state
        account_details.update(
            details
            | {
                "requests_last_min": self.apisession.request_count.last_minute(),
                "requests_last_hour": self.apisession.request_count.last_hour(),
                "mqtt_connection": self.mqttsession.is_connected()
                if self.mqttsession
                else False,
            }
        )
        self.account = account_details


    def _update_dev(
        self,
        devData: dict,
        devType: str | None = None,
        siteId: str | None = None,
        isAdmin: bool | None = None,
    ) -> str | None:
        """Update the internal device details dictionary with the given data. The device_sn key must be set in the data dict for the update to be applied.

        This method should be implemented to consolidate various device related key values from various requests under a common set of device keys.
        The device SN should be returned if found in devData and an update was done
        """
        if sn := devData.get("device_sn"):
            device: dict = self.devices.get(sn, {})  # lookup old device info if any
            device.update({"device_sn": str(sn)})
            if devType:
                device.update({"type": devType.lower()})
            if siteId:
                device.update({"site_id": str(siteId)})
            if isAdmin is not None:
                # always update admin flag if passed as parameter
                device["is_admin"] = isAdmin
            elif (value := devData.get("ms_device_type")) is not None:
                # update admin flag if recognizable from provided devData
                # Update admin based on ms device type for standalone devices
                device["is_admin"] = value in [0, 1]
                # member devices should only be listed in bind_device query and return owner_user_id
                if value := devData.get("owner_user_id"):
                    device["owner_user_id"] = value
            for key, value in devData.items():
                try:
                    #
                    # Implement device update code with key filtering, conversion, consolidation, calculation or dependency updates
                    #
                    if key == "device_sw_version" and value:
                        # Example for key name conversion when value is given
                        device.update({"sw_version": str(value)})
                    elif key in [
                        # Examples for boolean key values
                        "wifi_online",
                        "auto_upgrade",
                        "is_ota_update",
                    ]:
                        device.update({key: bool(value)})
                    elif key in [
                        # Example for key with string values
                        "wireless_type",
                        "ota_version",
                    ] or (
                        # Example for key with string values that should only be updated if value returned
                        key == "wifi_name" and value
                    ):
                        device.update({key: str(value)})
                    else:
                        # Example for all other keys not filtered or converted
                        device.update({key: value})

                except Exception as err:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                    self._logger.error(
                        "Api %s error %s occurred when updating device details for key %s with value %s: %s",
                        self.apisession.nickname,
                        type(err),
                        key,
                        value,
                        err,
                    )

            self.devices.update({str(sn): device})
        return sn

    def mqtt_received(
        self,
        session: AnkerSolixMqttSession,
        topic: str,
        message: Any,
        data: bytes,
        model: str,
        deviceSn: str,
        extracted_values: dict,
        *args,
        **kwargs,
    ) -> None:
        """Define callback for MQTT session to update device MQTT data in cache and trigger MQTT update callback for device if registered."""
        if extracted_values and deviceSn:
            new_values = self.update_device_mqtt(
                deviceSn=deviceSn, values=extracted_values
            )
            if new_values and callable(self._mqtt_update_callback):
                self._mqtt_update_callback(deviceSn)

    # A91B2 0a00 / 0303 / 0302 decoded keys. Everything else that upstream
    # update_device_mqtt merged (solarbank SOC, HES energy counters, PPS,
    # EV charger phases, expansion packs, …) is not produced by the A91B2
    # field maps in mqttmap.py and has been removed with the refactor.
    _A91B2_STRING_KEYS = frozenset({"sw_version"})
    _A91B2_FLOAT_3DP_KEYS = frozenset(
        f"{prefix}_{port}_{metric}"
        for prefix in ("usbc", "usba")
        for port in (range(1, 5) if prefix == "usbc" else range(1, 3))
        for metric in ("voltage", "current", "power")
    )
    _A91B2_PASSTHROUGH_KEYS = frozenset(
        {
            "topics",
            "msg_timestamp",
            "ac_1_switch",
            "ac_2_switch",
            "usbc_1_status",
            "usbc_2_status",
            "usbc_3_status",
            "usbc_4_status",
            "usba_1_status",
            "usba_2_status",
        }
    )
    _A91B2_PORT_SWITCH_MAP = {0: "ac_1_switch", 1: "ac_2_switch"}

    def update_device_mqtt(
        self,
        deviceSn: str | None = None,
        values: dict | None = None,
    ) -> bool:
        """Merge decoded A91B2 MQTT values into the device's mqtt_data cache."""
        updated = False
        if not self.mqttsession:
            return updated
        for sn, device in [
            (sn, device)
            for sn, device in self.devices.items()
            if not deviceSn or sn == deviceSn
        ]:
            device_mqtt = device.get("mqtt_data") or {}
            oldsize = len(device_mqtt)
            mqtt = (self.mqttsession.mqtt_data.get(sn) or {}).copy()
            if mqtt and values:
                for key, value in values.items():
                    value_updated = True
                    if key in self._A91B2_STRING_KEYS and value is not None:
                        device_mqtt[key] = str(value)
                    elif (
                        key in self._A91B2_FLOAT_3DP_KEYS
                        and str(value)
                        .replace("-", "", 1)
                        .replace(".", "", 1)
                        .isdigit()
                    ):
                        device_mqtt[key] = f"{float(value):.3f}"
                    elif key in self._A91B2_PASSTHROUGH_KEYS and value is not None:
                        device_mqtt[key] = value
                        # topics/msg_timestamp only mark freshness, not state change
                        value_updated = key not in {"topics", "msg_timestamp"}
                    elif key == "set_port_switch_select":
                        # 0302 broadcast after a 0207 AC-outlet toggle: echo the
                        # new state back into the device cache so the switch
                        # entity flips immediately instead of waiting for 0a00.
                        if (
                            switch_name := self._A91B2_PORT_SWITCH_MAP.get(value)
                        ) and (
                            switch_value := values.get("set_port_switch")
                        ) is not None:
                            device_mqtt[switch_name] = switch_value
                    else:
                        value_updated = False
                    updated = updated or value_updated
                device_mqtt["last_update"] = str(mqtt.get("last_message"))
                device["mqtt_data"] = device_mqtt
                updated = updated or (oldsize != len(device_mqtt))
                if oldsize == 0:
                    self.notify_device(deviceSn=sn)
        # keep account cache statistics fresh for the diagnostics sensor
        stats = self.mqttsession.mqtt_stats.asdict()
        if (start := stats.pop("start_time")) and isinstance(start, datetime):
            stats["start_time"] = start.strftime("%Y-%m-%d %H:%M")
        self._update_account({"mqtt_statistic": stats})
        return updated

    async def get_bind_devices(self, fromFile: bool = False) -> dict:
        """Get the bind device information, which will list all devices the account has admin rights for. It also contains firmware level of devices.

        Example data:
        {"data": [{"device_sn":"9JVB42LJK8J0P5RY","product_code":"A17C0","bt_ble_id":"BC:A2:AF:C7:55:F9","bt_ble_mac":"BCA2AFC755F9","device_name":"Solarbank E1600","alias_name":"Solarbank E1600",
        "img_url":"https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/product/anker-power/e9478c2d-e665-4d84-95d7-dd4844f82055/20230719-144818.png",
        "link_time":1695392302068,"wifi_online":false,"wifi_name":"","relate_type":["ble","wifi"],"charge":false,"bws_surplus":0,"device_sw_version":"v1.4.4","has_manual":false}]}
        """
        if fromFile:
            resp = await self.apisession.loadFromFile(
                Path(self.testDir()) / f"{API_FILEPREFIXES['bind_devices']}.json"
            )
        else:
            resp = await self.apisession.request("post", API_ENDPOINTS["bind_devices"])
        data = resp.get("data") or {}
        active_devices = set()
        for device in data.get("data") or []:
            if sn := self._update_dev(device.copy()):
                active_devices.add(sn)
        # recycle api device list and remove devices no longer used in bind_devices
        self.recycleDevices(extraDevices=active_devices)
        return data

    async def get_auto_upgrade(self, fromFile: bool = False) -> dict:
        """Get auto upgrade settings and devices enabled for auto upgrade.

        Example data:
        {'main_switch': True, 'device_list': [{'device_sn': '9JVB42LJK8J0P5RY', 'device_name': 'Solarbank E1600', 'auto_upgrade': True, 'alias_name': 'Solarbank E1600',
        'icon': 'https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/product/anker-power/e9478c2d-e665-4d84-95d7-dd4844f82055/20230719-144818.png'}]}
        """
        if fromFile:
            resp = await self.apisession.loadFromFile(
                Path(self.testDir()) / f"{API_FILEPREFIXES['get_auto_upgrade']}.json"
            )
        else:
            resp = await self.apisession.request(
                "post", API_ENDPOINTS["get_auto_upgrade"]
            )
        data = resp.get("data") or {}
        main = data.get("main_switch")
        devicelist = (
            data.get("device_list") or []
        )  # could be null for non owning account
        for device in devicelist:
            dev_ota = device.get("auto_upgrade")
            if isinstance(dev_ota, bool):
                # update device setting based on main setting if available
                if isinstance(main, bool):
                    device.update({"auto_upgrade": main and dev_ota})
                self._update_dev(device.copy())
        return data

    async def set_auto_upgrade(self, devices: dict[str, bool]) -> bool | dict:
        """Set auto upgrade switches for given device dictionary.

        Example input:
        devices = {'9JVB42LJK8J0P5RY': True}
        The main switch must be set True if any device switch is set True. The main switch does not need to be changed to False if no device is True.
        But if main switch is set to False, all devices will automatically be set to False and individual setting is ignored by Api.
        """
        resp: bool | dict = False
        # get actual settings
        settings = await self.get_auto_upgrade()
        if (main_switch := settings.get("main_switch")) is None:
            return resp
        dev_switches = {}
        main = None
        change_list = []
        for dev_setting in settings.get("device_list") or []:
            if (
                isinstance(dev_setting, dict)
                and (device_sn := dev_setting.get("device_sn"))
                and (dev_upgrade := dev_setting.get("auto_upgrade")) is not None
            ):
                dev_switches[device_sn] = dev_upgrade
        # Loop through provided device list and compose the request data device list that needs to be send
        for sn, upgrade in devices.items():
            if sn in dev_switches:
                if upgrade != dev_switches[sn]:
                    change_list.append({"device_sn": sn, "auto_upgrade": upgrade})
                    if upgrade:
                        main = True
        if change_list:
            # json example for endpoint
            # {"main_switch": False, "device_list": [{"device_sn": "9JVB42LJK8J0P5RY","auto_upgrade": True}]}
            data = {
                "main_switch": main if main is not None else main_switch,
                "device_list": change_list,
            }
            # Make the Api call and check for return code
            code = (
                await self.apisession.request(
                    "post", API_ENDPOINTS["set_auto_upgrade"], json=data
                )
            ).get("code")
            if not isinstance(code, int) or int(code) != 0:
                return resp
            # update the data in api dict
            resp = await self.get_auto_upgrade()
        return resp
