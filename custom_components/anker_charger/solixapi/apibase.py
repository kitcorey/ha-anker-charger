"""Base Class for interacting with the Anker Power / Solix API."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import logging
from pathlib import Path
from typing import Any

from aiohttp import ClientError, ClientSession

from .apitypes import (
    API_ENDPOINTS,
    API_FILEPREFIXES,
    SolixDeviceType,
    SolixPriceProvider,
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
        # track active devices bound to any site
        self._site_devices: set = set()
        # reset class variables for saving the most recent account, site and device data (Api cache)
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
            if id in self.sites:
                data = self.sites.get(id)
                customized = data.get("customized") or {}
                # merge with existing dict if value is dict
                customized[key] = (
                    ((customized.get(key) or {}) | value)
                    if isinstance(value, dict)
                    else value
                )
                data["customized"] = customized
                # trigger an update of cached data depending on customized value
                # customized keys that are used as alternate value must be handled separately since they may not exist in cache
                if (
                    key in ["dynamic_price_vat", "dynamic_price_fee", "dynamic_price"]
                    and value
                ):
                    # dynamic price related updates should always be triggered if customized, independent of existing keys
                    if key == "dynamic_price":
                        # convert a provider string to dict
                        if isinstance(value, str):
                            customized[key] = SolixPriceProvider(
                                provider=value
                            ).asdict()
                    # update whole dynamic price forecast
                    self._update_site(
                        siteId=id,
                        details={
                            "dynamic_price_details": self.extractPriceData(
                                siteId=id, forceCalc=True
                            )
                        },
                    )
                elif key == "pv_forecast_details" and value:
                    # update whole solar forecast in energy details
                    self.extractSolarForecast(siteId=id)
                elif key in (data.get("site_details") or {}):
                    # trigger dependent updates by rewriting old value to cache update method
                    self._update_site(
                        siteId=id, details={key: data["site_details"][key]}
                    )
                elif key in data:
                    pass
            elif id in self.devices:
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

    def recycleDevices(
        self, extraDevices: set | None = None, activeDevices: set | None = None
    ) -> None:
        """Recycle api device list and remove devices no longer used in sites cache or extra devices."""
        if not extraDevices or not isinstance(extraDevices, set):
            extraDevices = set()
        if not activeDevices or not isinstance(activeDevices, set):
            activeDevices = set()
        # first clear internal site devices cache if active devices are provided
        if activeDevices:
            rem_devices = [
                dev
                for dev in self._site_devices
                if dev not in (activeDevices | extraDevices)
            ]
            for dev in rem_devices:
                self._site_devices.discard(dev)
        # Clear device cache to maintain only active and extra devices
        rem_devices = [
            dev
            for dev in self.devices
            if dev not in (self._site_devices | extraDevices)
        ]
        for dev in rem_devices:
            self.devices.pop(dev, None)
            # check callbacks and notify registered devices about removal from cache
            cbs = self._device_callbacks.pop(dev, {})
            for func in cbs.get("functions", set()):
                if callable(func):
                    func(device={})

    def recycleSites(self, activeSites: set | None = None) -> None:
        """Recycle api site cache and remove sites no longer active according provided activeSites."""
        if activeSites and isinstance(activeSites, set):
            rem_sites = [site for site in self.sites if site not in activeSites]
            for site in rem_sites:
                self.sites.pop(site, None)

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

    def update_device_mqtt(  # noqa: C901
        self,
        deviceSn: str | None = None,
        values: dict | None = None,
    ) -> bool:
        """Update the mqtt data cache of the given device or all devices and return whether update was done.

        This will consolidate various device related mqtt key values under a common set of device keys.
        """
        updated = False
        dyn_desc = False
        if self.mqttsession:
            for sn, device in [
                (sn, device)
                for sn, device in self.devices.items()
                if not deviceSn or sn == deviceSn
            ]:
                # get old MQTT data of device
                device_mqtt = device.get("mqtt_data") or {}
                oldsize = len(device_mqtt)
                # oldtime = device_mqtt.get("last_update") or ""
                # check if newer MQTT data is available from last message timestamp
                # use copy of MQTT dict for device because it may be modified upon received messages
                if (
                    mqtt := (self.mqttsession.mqtt_data.get(sn) or {}).copy()
                ) and values:  # and oldtime < (mqtt.get("last_message") or "")
                    # cycle through all items and extract what is needed for the device type
                    calc_efficiency = False
                    calc_capacity = False
                    for key, value in values.items():
                        # Implement device MQTT merge code with key filtering, conversion, consolidation, calculation or dependency updates
                        # skip value update marker for static fields that may be extracted from various messages
                        value_updated = True
                        if (
                            key
                            in [
                                # keys with value being saved as string
                                "device_sn",
                                "sub_device_sn",
                                "local_datetime",
                                "exp_1_sn",
                                "exp_2_sn",
                                "exp_3_sn",
                                "exp_4_sn",
                                "exp_5_sn",
                                "solarbank_1_sn",
                                "solarbank_2_sn",
                                "solarbank_3_sn",
                                "solarbank_4_sn",
                                "hw_version",
                                "sw_version",
                                "sw_controller",
                                "sw_expansion",
                                "inverter_brand",
                                "inverter_model",
                                "exp_1_type",
                                "exp_2_type",
                                "exp_3_type",
                                "exp_4_type",
                                "exp_5_type",
                                "wifi_name",
                                "power_panel_sn",
                                "pps_1_sn",  # HA not used
                                "pps_2_sn",  # HA not used
                                "pps_1_model",  # HA not used
                                "pps_2_model",  # HA not used
                                "toggle_to_delay_time",  # HA missing, MQTT control not fully described
                                "toggle_to_elapsed_time",  # HA missins, MQTT control not fully described
                                "light_off_start_time",  # HA missing
                                "light_off_end_time",  # HA missing
                                "week_start_time",  # HA missing
                                "week_end_time",  # HA missing
                                "weekend_start_time",  # HA missing
                                "weekend_end_time",  # HA missing
                                "load_balance_monitor_device",  # HA not used
                                "solar_evcharge_monitor_device",  # HA not used
                            ]
                            and value is not None
                        ):
                            device_mqtt.update({key: str(value)})
                            value_updated = bool(
                                key != "wifi_name" and not key.endswith("_sn")
                            )
                        elif (
                            key
                            in [
                                # keys with value that should be saved as int string
                                "battery_soc",
                                "battery_soc_total",
                                "main_battery_soc",
                                "max_soc",
                                "solarbank_1_soc",
                                "solarbank_2_soc",
                                "solarbank_3_soc",
                                "solarbank_4_soc",
                                "solarbank_1_ac_output_power_signed",
                                "solarbank_2_ac_output_power_signed",
                                "solarbank_3_ac_output_power_signed",
                                "solarbank_4_ac_output_power_signed",
                                "solarbank_ac_output_power_signed_total",
                                "temperature",
                                "photovoltaic_power",
                                "pv_to_home_power",
                                "pv_to_battery_power",
                                "pv_1_power",
                                "pv_2_power",
                                "pv_3_power",
                                "pv_4_power",
                                "pv_power_3rd_party",  # HA not usable in system entity
                                "pv_power_total",
                                "dc_input_power",
                                "dc_input_power_total",
                                "dc_output_power_total",
                                "output_power",
                                "output_power_total",
                                "output_power_signed_total",
                                "battery_power_signed",
                                "battery_power_signed_total",
                                "bat_charge_power",
                                "bat_discharge_power",
                                "battery_to_grid_power",
                                "battery_to_home_power",
                                "ac_input_power",
                                "ac_input_power_total",
                                "ac_output_power",
                                "ac_output_power_total",
                                "ac_output_power_signed",
                                "ac_output_power_signed_total",
                                "ac_socket_power",
                                "grid_power_signed",
                                "grid_power_signed_l1",
                                "grid_power_signed_l2",
                                "grid_power_signed_l3",
                                "heating_power",
                                "grid_to_battery_power",
                                "home_demand",
                                "home_demand_total",
                                "generator_to_battery_power",  # HA missing
                                "generator_to_home_power",  # HA missing
                                "generator_power",  # HA missing
                                "charge_priority_limit",
                                "pv_limit",
                                "pv_limit_solarbank_1",  # HA not used for combiner box values
                                "pv_limit_solarbank_2",  # HA not used for combiner box values
                                "pv_limit_solarbank_3",  # HA not used for combiner box values
                                "pv_limit_solarbank_4",  # HA not used for combiner box values
                                "ac_input_limit",
                                "min_load",
                                "max_load",
                                "max_load_legal",
                                "max_load_total",
                                "home_load_preset",
                                "pv_to_grid_power",
                                "grid_to_home_power",
                                "system_output_power_signed_l1",
                                "system_output_power_signed_l2",
                                "wifi_signal",
                                "charging_power",
                                "power_l1",  # HA missing
                                "power_l2",  # HA missing
                                "power_l3",  # HA missing
                                "min_current_limit",  # HA missing
                                "max_current_limit",  # HA missing
                                "main_breaker_limit",  # HA missing
                                "max_evcharge_current",  # HA missing
                                "solar_evcharge_min_current",  # HA missing
                                "light_brightness",
                            ]
                            and str(value)
                            .replace("-", "", 1)
                            .replace(".", "", 1)
                            .isdigit()
                        ):
                            device_mqtt[key] = f"{float(value):.0f}"
                            # trigger device capacity calculation with SOC updates
                            if key in ["battery_soc", "main_battery_soc"]:
                                calc_capacity = True
                        elif (
                            key
                            in [
                                # keys with value that should be saved as rounded 3 decimal float string
                                "battery_soh",
                                "battery_soc_ah",
                                "voltage",
                                "pv_1_voltage",
                                "pv_2_voltage",
                                "pv_3_voltage",
                                "pv_4_voltage",
                                "battery_voltage",
                                "power",
                                "usbc_1_power",
                                "usbc_2_power",
                                "usbc_3_power",
                                "usbc_4_power",
                                "usba_1_power",
                                "usba_2_power",
                                "dc_12v_1_power",
                                "dc_12v_2_power",
                                "usbc_1_voltage",
                                "usbc_2_voltage",
                                "usbc_3_voltage",
                                "usbc_4_voltage",
                                "usba_1_voltage",
                                "usba_2_voltage",
                                "current",
                                "usbc_1_current",
                                "usbc_2_current",
                                "usbc_3_current",
                                "usbc_4_current",
                                "usba_1_current",
                                "usba_2_current",
                                "voltage_l1",
                                "voltage_l2",
                                "voltage_l3",
                                "voltage_l1l2",
                                "voltage_l1l3",
                                "voltage_l2l3",
                                "power_factor",
                                "current_l1",
                                "current_l2",
                                "current_l3",
                                "system_output_current_l1",
                                "system_output_current_l2",
                                "system_output_current_l3",
                            ]
                            and str(value)
                            .replace("-", "", 1)
                            .replace(".", "", 1)
                            .isdigit()
                        ):
                            device_mqtt[key] = f"{float(value):.3f}"
                        elif (
                            key
                            in [
                                # energy keys with value that should be saved as rounded as 3 decimal float string
                                "pv_yield",  # aggregated
                                "charged_energy",  # aggregated
                                "discharged_energy",  # aggregated
                                "output_energy",  # aggregated
                                "bypass_energy",  # aggregated, HA not used
                                "consumed_energy",  # aggregated
                                "home_consumption",  # aggregated
                                "grid_import_energy",  # aggregated
                                "grid_export_energy",  # aggregated
                                "charging_energy",  # HA missing
                                "charging_energy_l1",  # HA missing
                                "charging_energy_l2",  # HA missing
                                "charging_energy_l3",  # HA missing
                                "charged_energy_today",  # HA missing, how to merge to avoid decrease?
                                "discharged_energy_today",  # HA missing, how to merge to avoid decrease?
                                "pv_yield_today",  # HA missing, how to merge to avoid decrease?
                                "pv_consumption_today",  # HA missing, how to merge to avoid decrease?
                                "pv_charge_today",  # HA missing, how to merge to avoid decrease?
                                "pv_export_today",  # HA missing, how to merge to avoid decrease?
                                "battery_consumption_today",  # HA missing, how to merge to avoid decrease?
                                "grid_discharged_today",  # HA missing, how to merge to avoid decrease?
                                "grid_charged_today",  # HA missing, how to merge to avoid decrease?
                                "grid_consumption_today",  # HA missing, how to merge to avoid decrease?
                                "home_consumption_today",  # HA missing, how to merge to avoid decrease?
                                "generator_energy_today",  # HA missing, how to merge to avoid decrease?
                                "generator_charged_today",  # HA missing, how to merge to avoid decrease?
                                "generator_consumed_today",  # HA missing, how to merge to avoid decrease?
                            ]
                        ):
                            # aggregated energies should never decrease, otherwise weird values are sent or description is wrong
                            # 0 value should be ignored for aggregated, since that may reset energy counters if 0 values read on startup
                            if (
                                str(key).startswith("charging_")
                                or str(key).endswith("_today")
                                or 0 < float(value) > float(device_mqtt.get(key, 0))
                            ):
                                device_mqtt[key] = f"{float(value):.3f}"
                                if key in [
                                    "output_energy",
                                    "pv_yield",
                                    "charged_energy",
                                    "discharged_energy",
                                    "consumed_energy",
                                ]:
                                    calc_efficiency = True
                        elif (
                            key
                            in [
                                # keys with value being saved unchanged
                                "topics",
                                "error_code",
                                "charging_status",
                                "dc_charging_status",
                                "battery_status",
                                "grid_status",
                                "plant_status",
                                "usbc_1_status",
                                "usbc_2_status",
                                "usbc_3_status",
                                "usbc_4_status",
                                "usba_1_status",
                                "usba_2_status",
                                "dc_12v_1_status",
                                "dc_12v_2_status",
                                "usbc_1_switch",
                                "usbc_2_switch",
                                "usbc_3_switch",
                                "usbc_4_switch",
                                "usba_switch",
                                "ac_1_switch",
                                "ac_2_switch",
                                "dc_12v_auto_on",  # missing MQTT control command
                                "usage_mode",
                                "energy_saving_mode",  # missing MQTT control command
                                "allow_export_switch",
                                "priority_discharge_switch",
                                "grid_export_disabled",
                                "display_mode",
                                "display_switch",
                                "display_status",  # missing MQTT control command
                                "display_timeout_seconds",
                                "light_off_switch",
                                "light_switch",
                                "light_mode",
                                "ac_socket_switch",
                                "ac_output_power_switch",
                                "dc_output_power_switch",
                                "ac_output_mode",
                                "dc_12v_output_mode",
                                "backup_charge_switch",
                                "ac_fast_charge_switch",
                                "port_memory_switch",
                                "temp_unit_fahrenheit",
                                "expansion_packs",
                                "solarbank_1_exp_packs",
                                "solarbank_2_exp_packs",
                                "solarbank_3_exp_packs",
                                "solarbank_4_exp_packs",
                                "device_timeout_minutes",
                                "dc_output_timeout_seconds",
                                "ac_output_timeout_seconds",
                                "remaining_time_hours",
                                "msg_timestamp",
                                "local_timestamp",
                                "utc_timestamp",
                                "timestamp_backup_start",
                                "timestamp_backup_end",
                                "charging_start_timestamp",  # HA missing
                                "tcp_timeout_seconds",  # HA missing
                                "charging_duration_seconds",  # HA missing
                                "charging_window_seconds",  # HA not used
                                "plug_lock_switch",  # HA missing
                                "plug_countdown_seconds",  # HA missing
                                "start_countdown_seconds",  # HA missing
                                "auto_start_switch",  # HA missing
                                "auto_charge_restart_switch",  # HA missing
                                "start_evcharge_switch",  # HA missing
                                "random_delay_switch",  # HA missing
                                "smart_touch_mode",  # HA missing
                                "wipe_up_mode",  # HA missing
                                "wipe_down_mode",  # HA missing
                                "light_off_schedule_switch",  # HA missing
                                "modbus_switch",  # HA missing
                                "tcp_port",  # HA missing
                                "ip_address",  # HA missing
                                "load_balance_switch",  # HA missing
                                "solar_evcharge_switch",  # HA missing
                                "solar_evcharge_mode",  # HA missing
                                "phase_operating_mode",  # HA missing
                                "auto_phase_switch",  # HA missing
                                "schedule_switch",  # HA missing
                                "weekend_mode",  # HA missing
                                "schedule_mode",  # HA missing
                                "charging_mode",  # HA missing
                                "ev_charger_status",  # HA missing
                                "boost_status",  # HA missing
                                "ocpp_connect_status",  # HA missing
                                "cp_signal_status",  # HA missing
                                "plug_status",  # HA missing
                                "solar_evcharge_monitoring_mode",  # HA not used
                                "working_status",
                                "mode",  # HA missing, meaning not clear
                            ]
                            and value is not None
                        ):
                            device_mqtt[key] = value
                            # determine EV charger model 3 phase capability
                            if key == "charging_duration_seconds":
                                device_mqtt["installed_phases"] = (
                                    int(values.get("voltage_l1", 0) > 0)
                                    + int(values.get("voltage_l2", 0) > 0)
                                    + int(values.get("voltage_l3", 0) > 0)
                                )
                            value_updated = bool(
                                key not in ["topics", "expansion_packs"]
                                and "timestamp" not in key
                            )
                        # use expansion values only if installed
                        elif (
                            (
                                key
                                in [
                                    "exp_1_soc",
                                    "exp_1_temperature",
                                    "exp_1_soh",
                                ]
                                and (
                                    float(mqtt.get("expansion_packs", 0)) >= 1
                                    or float(mqtt.get("exp_1_soc", 0)) > 0
                                )
                            )
                            or (
                                key
                                in [
                                    "exp_2_soc",
                                    "exp_2_temperature",
                                    "exp_2_soh",
                                ]
                                and (
                                    float(mqtt.get("expansion_packs", 0)) >= 2
                                    or float(mqtt.get("exp_2_soc", 0)) > 0
                                )
                            )
                            or (
                                key
                                in [
                                    "exp_3_soc",
                                    "exp_3_temperature",
                                    "exp_3_soh",
                                ]
                                and (
                                    float(mqtt.get("expansion_packs", 0)) >= 3
                                    or float(mqtt.get("exp_3_soc", 0)) > 0
                                )
                            )
                            or (
                                key
                                in [
                                    "exp_4_soc",
                                    "exp_4_temperature",
                                    "exp_4_soh",
                                ]
                                and (
                                    float(mqtt.get("expansion_packs", 0)) >= 4
                                    or float(mqtt.get("exp_4_soc", 0)) > 0
                                )
                            )
                            or (
                                key
                                in [
                                    "exp_5_soc",
                                    "exp_5_temperature",
                                    "exp_5_soh",
                                ]
                                and (
                                    float(mqtt.get("expansion_packs", 0)) >= 5
                                    or float(mqtt.get("exp_5_soc", 0)) > 0
                                )
                            )
                        ) and str(value).replace("-", "", 1).replace(
                            ".", "", 1
                        ).isdigit():
                            if str(key).endswith("_soh"):
                                device_mqtt[key] = f"{float(value):.3f}"
                            else:
                                device_mqtt[key] = f"{float(value):.0f}"
                                # trigger capacity calculation if any soc provided
                                if "_soc" in key:
                                    calc_capacity = True
                        elif key in ["output_cutoff_data", "min_soc"]:
                            device_mqtt["power_cutoff"] = str(value)
                        elif key == "set_port_switch_select":
                            # update port state based on toggle command or confirmation msg for cache update upon passive change
                            # A91B2 uses port 0/1 for AC outlets; A2345 and others use 0-4 for USB ports
                            port_switch_map = (
                                {
                                    0: "ac_1_switch",
                                    1: "ac_2_switch",
                                }
                                if device.get("device_pn") == "A91B2"
                                else {
                                    0: "usbc_1_switch",
                                    1: "usbc_2_switch",
                                    2: "usbc_3_switch",
                                    3: "usbc_4_switch",
                                    4: "usba_switch",
                                }
                            )
                            if (
                                switch_name := port_switch_map.get(value)
                            ) and (
                                switch_value := values.get("set_port_switch")
                            ) is not None:
                                device_mqtt[switch_name] = switch_value
                        else:
                            value_updated = False
                        updated = updated or value_updated
                    # update last message timestamp from mqtt cache
                    device_mqtt["last_update"] = str(mqtt.get("last_message"))
                    # calculate extra fields if required values were updated
                    if calc_efficiency:
                        pv = device_mqtt.get("pv_yield")
                        out = device_mqtt.get("output_energy")
                        charge = device_mqtt.get("charged_energy")
                        if not (discharge := device_mqtt.get("discharged_energy")):
                            # SB2 does not report discharge, but calculates it as Output + Charged - PV
                            if pv and out and charge:
                                discharge = min(
                                    float(charge),
                                    max(0, float(out) + float(charge) - float(pv)),
                                )
                        # consider consumed energy for efficiency, since that probably reduces the reported pv_yield and charge energy
                        consumed = device_mqtt.get("consumed_energy") or 0
                        # First calculate optional AC charge
                        ac_charge = 0
                        if pv and out and charge and discharge:
                            ac_charge = max(
                                0,
                                float(out)
                                - float(pv)
                                + float(charge)
                                - float(discharge),
                            )
                        if pv and out and float(pv) > 0:
                            # Solarbank 3 seem to reduce the reported PV energy by consumed energy (Heating, Socket), so it must be added to input
                            dev_in = float(pv) + float(consumed) + ac_charge
                            device_mqtt["device_efficiency"] = (
                                f"{min(100, float(out) / dev_in * 100):.3f}"
                            )
                        if charge and discharge and float(charge) > 0:
                            # Charge should include PV charge and AC charge if supported by device
                            # Solarbank 3 seems to reduce the reported charge energy by consumed energy
                            device_mqtt["battery_efficiency"] = (
                                f"{min(100, float(discharge) / (float(charge) + float(consumed)) * 100):.3f}"
                            )
                    device["mqtt_data"] = device_mqtt
                    # trigger device cache update for cap calculation with total or main device soc updates
                    if calc_capacity and (cap := device.get("battery_capacity")):
                        # calculate total expansions if expansions are available and no number in mqtt cache
                        if mqtt.get("expansion_packs") is None:
                            # deterministic code assumes expansion if soc or soh > 0
                            device_mqtt["expansion_packs"] = len(
                                [
                                    k
                                    for k in [f"exp_{i!s}_soc" for i in range(1, 6)]
                                    if (
                                        float(device_mqtt.get(k, 0)) > 0
                                        or float(
                                            device_mqtt.get(
                                                k.replace("_soc", "_soh"), 0
                                            )
                                        )
                                        > 0
                                    )
                                ]
                            )
                        # calculate device overall soc if expansions are available and no overall soc in mqtt cache
                        if not (tsoc := mqtt.get("battery_soc")):
                            # calculate overall soc based on expansions
                            if soclist := [
                                float(device_mqtt.get(k))
                                for k in (
                                    ["main_battery_soc"]
                                    + [f"exp_{i!s}_soc" for i in range(1, 6)]
                                )
                                if device_mqtt.get(k)
                            ]:
                                tsoc = round(sum(soclist) / len(soclist))
                                device_mqtt["battery_soc"] = f"{float(tsoc):.0f}"
                        # trigger capacity calculation if no Api SOC available or MQTT overlay
                        if tsoc and (
                            not device.get("battery_soc") or device.get("mqtt_overlay")
                        ):
                            self._update_dev({"device_sn": sn, "battery_capacity": cap})
                    # update marker should also indicate increase in extracted keys
                    updated = updated or (oldsize != len(device_mqtt))
                    # notify registered devices if new mqtt data cache was generated or dynamic description state changed
                    descs = self._device_callbacks.get(deviceSn, {}).get(
                        "dynamic_descriptions", {}
                    )
                    for key, item in descs.items():
                        # check if key in message values and compare old state with new state
                        if values.get(key) is not None and str(
                            device_mqtt.get(key)
                        ) != str(item.get("last_value", "")):
                            # flag to trigger update if dynamic description value changed
                            dyn_desc = True
                            break
                    if oldsize == 0 or dyn_desc:
                        self.notify_device(deviceSn=sn)
            # update MQTT statistic in account cache, convert datetime to json compatible format
            stats = self.mqttsession.mqtt_stats.asdict()
            if (start := stats.pop("start_time")) and isinstance(start, datetime):
                stats["start_time"] = start.strftime("%Y-%m-%d %H:%M")
            self._update_account({"mqtt_statistic": stats})
        return updated

    async def update_sites(
        self,
        siteId: str | None = None,
        fromFile: bool = False,
        exclude: set | None = None,
    ) -> dict:
        """Create/Update api sites cache structure.

        Implement this method to get the latest info for all accessible sites or only the provided siteId and update class cache dictionaries.
        """
        # define excluded categories to skip for queries
        if not exclude or not isinstance(exclude, set):
            exclude = set()
        if siteId and (self.sites.get(siteId) or {}):
            # update only the provided site ID
            self._logger.debug(
                "Updating api %s sites data for site ID %s",
                self.apisession.nickname,
                siteId,
            )
            new_sites = self.sites
            # prepare the site list dictionary for the update loop by copying the requested site from the cache
            sites: dict = {"site_list": [self.sites[siteId].get("site_info") or {}]}
        else:
            # run normal refresh for all sites
            self._logger.debug(
                "Updating api %s sites data",
                self.apisession.nickname,
            )
            new_sites = {}
            self._logger.debug(
                "Getting api %s site list",
                self.apisession.nickname,
            )
            sites = await self.get_site_list(fromFile=fromFile)
            self._site_devices = set()
        for site in sites.get("site_list", []):
            if myid := site.get("site_id"):
                # Update site info
                mysite: dict = self.sites.get(myid, {})
                siteInfo: dict = mysite.get("site_info", {})
                siteInfo.update(site)
                mysite.update(
                    {"type": SolixDeviceType.SYSTEM.value, "site_info": siteInfo}
                )
                admin = (
                    siteInfo.get("ms_type", 0) in [0, 1]
                )  # add boolean key to indicate whether user is site admin (ms_type 1 or not known) and can query device details
                mysite.update({"site_admin": admin})
                # Update scene info for site
                self._logger.debug(
                    "Getting api %s scene info for site",
                    self.apisession.nickname,
                )
                scene = await self.get_scene_info(myid, fromFile=fromFile)
                mysite.update(scene)
                new_sites.update({myid: mysite})
                #
                # Implement site dependent device update code as needed for various device types
                # For each SN found in the site structures, update the internal site_devices set
                # The update device details routine may also find standalone devices and need to merge all active
                # devices for cleanup/removal of extra/obsolete devices in the cache structure
                self._site_devices.add("found_sn")

        # Write back the updated sites
        self.sites = new_sites
        # update account dictionary with number of requests
        self._update_account({"use_files": fromFile})
        return self.sites

    async def update_site_details(
        self, fromFile: bool = False, exclude: set | None = None
    ) -> dict:
        """Get the latest updates for additional account or site related details updated less frequently.

        Implement this method for site related queries that should be used less frequently.
        Most of theses requests return data only when user has admin rights for sites owning the devices.
        To limit API requests, this update site details method should be called less frequently than update site method,
        and it updates just the nested site_details dictionary in the sites dictionary as well as the account dictionary
        """
        # define excluded categories to skip for queries
        if not exclude or not isinstance(exclude, set):
            exclude = set()
        self._logger.debug(
            "Updating api %s sites details",
            self.apisession.nickname,
        )
        #
        # Implement required queries according to exclusion set
        #

        # update account dictionary with number of requests
        self._update_account({"use_files": fromFile})
        return self.sites

    async def update_device_energy(
        self, fromFile: bool = False, exclude: set | None = None
    ) -> dict:
        """Get the site energy statistics for given device types from today and yesterday.

        Implement this method for the required energy query methods to obtain energy data for today and yesterday.
        It was found that energy data is tracked only per site, but not individual devices even if a device SN parameter may be mandatory in the Api request.
        """
        # check exclusion list, default to all energy data
        if not exclude or not isinstance(exclude, set):
            exclude = set()
        for site_id, site in self.sites.items():
            self._logger.debug(
                "Getting api %s energy details for site",
                self.apisession.nickname,
            )
            #
            # Implement required queries according to exclusion set
            #
            # save energy stats with sites dictionary
            site["energy_details"] = {"energy_key": "energy_value"}
            self.sites[site_id] = site

        # update account dictionary with number of requests
        self._update_account({"use_files": fromFile})
        return self.sites

    async def update_device_details(
        self, fromFile: bool = False, exclude: set | None = None
    ) -> dict:
        """Get the latest updates for additional device info updated less frequently.

        Implement this method for the required query methods to fetch device related data and update the device cache accordingly.
        To limit API requests, this update device details method should be called less frequently than update site method,
        which will also update most device details as found in the site data response.
        """
        # define excluded device types or categories to skip for queries
        if not exclude or not isinstance(exclude, set):
            exclude = set()
        self._logger.debug(
            "Updating api %s device details",
            self.apisession.nickname,
        )
        #
        # Implement required queries according to exclusion set
        #

        # update account dictionary with number of requests
        self._update_account({"use_files": fromFile})
        return self.devices





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
            # ensure to get product list once if needed if no device name in response
            if not device.get("device_name") and "products" not in self.account:
                self._update_account(
                    {"products": await self.get_products(fromFile=fromFile)}
                )
            # Bind devices also lists shared devices, device admin cannot longer be assumed per default and must be determined
            if sn := self._update_dev(device.copy()):
                active_devices.add(sn)
        # avoid removal of passive devices from active sites, since they are not listed in bind_devices
        for sn, device in self.devices.items():
            if device.get("is_passive") and (device.get("site_id") or "") in self.sites:
                active_devices.add(sn)
        # recycle api device list and remove devices no longer used in sites or bind devices
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


    async def get_ota_batch(
        self, deviceSns: list | None = None, fromFile: bool = False
    ) -> dict:
        """Get the OTA info for provided list of device serials or for all owning devices in devices dict.

        Example data:
        {"update_infos": [{"device_sn": "9JVB42LJK8J0P5RY","need_update": false,"upgrade_type": 0,"lastPackage": {
                "product_code": "","product_component": "","version": "","is_forced": false,"md5": "","url": "","size": 0},
        "change_log": "","current_version": "v1.6.3","children": [
            {"needUpdate": false,"device_type": "A17C1_esp32","rom_version_name": "v0.1.5.1","force_upgrade": false,"full_package": {
                "file_path": "https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2024/09/06/iot-admin/J7lALfvEQZIiqHyD/A17C1-A17C3_EUOTAWIFI_V0.1.5.1_20240828.bin",
                "file_size": 1270256,"file_md5": "578ac26febb55ee55ffe9dc6819b6c4a"},
            "change_log": "","sub_current_version": ""},
            {"needUpdate": false,"device_type": "A17C1_mcu","rom_version_name": "v1.0.5.16","force_upgrade": false,"full_package": {
                "file_path": "https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2024/09/06/iot-admin/w3ofT0NcpGF3IUcC/A17C1-A17C3_EUOTA_V1.0.5.16_20240904.bin",
                "file_size": 694272,"file_md5": "40913018b3e542c0350e8815951e4a9c"},
            "change_log": "","sub_current_version": ""},
            {"needUpdate": false,"device_type": "A17C1_100Ah","rom_version_name": "v0.1.9.1","force_upgrade": false,"full_package": {
                "file_path": "https://public-aiot-fra-prod.s3.dualstack.eu-central-1.amazonaws.com/anker-power/public/ota/2024/09/06/iot-admin/mmCg3IkHt2YpF8TR/A17C1-A17C3_EUOTA_V0.1.9.1_20240904.bin",
                "file_size": 694272,"file_md5": "40913018b3e542c0350e8815951e4a9c"},
            "change_log": "","sub_current_version": ""}]]}]}
        """
        # default to all admin devices in devices dict if no device serial list provided
        if not deviceSns or not isinstance(deviceSns, list):
            deviceSns = [
                s for s, device in self.devices.items() if device.get("is_admin")
            ]
        if not deviceSns:
            resp = {}
        elif fromFile:
            resp = await self.apisession.loadFromFile(
                Path(self.testDir()) / f"{API_FILEPREFIXES['get_ota_batch']}.json"
            )
        else:
            data = {
                "device_list": [
                    {"device_sn": serial, "version": ""} for serial in deviceSns
                ]
            }
            resp = await self.apisession.request(
                "post", API_ENDPOINTS["get_ota_batch"], json=data
            )
        # update device details only if valid response
        if (data := resp.get("data") or {}) and deviceSns:
            # update devices dict with new ota data
            for dev in data.get("update_infos") or []:
                if deviceSn := dev.get("device_sn"):
                    need_update = bool(dev.get("need_update"))
                    is_forced = bool(dev.get("is_forced"))
                    children: list = []
                    for child in dev.get("children") or []:
                        need_update = need_update or bool(child.get("needUpdate"))
                        is_forced = is_forced or bool(child.get("needUpdate"))
                        children.append(
                            {
                                "device_type": child.get("device_type"),
                                "need_update": bool(child.get("needUpdate")),
                                "force_upgrade": bool(child.get("force_upgrade")),
                                "rom_version_name": child.get("rom_version_name"),
                            }
                        )
                    self._update_dev(
                        {
                            "device_sn": deviceSn,
                            "is_ota_update": need_update,
                            "ota_forced": need_update,
                            "ota_version": (dev.get("lastPackage") or {}).get("version")
                            or dev.get("current_version")
                            or "",
                            "ota_children": children,
                        }
                    )
        return data





    async def get_products(self, fromFile: bool = False) -> dict:
        """Compose the supported Anker and third platform products into a condensed dictionary."""

        products = {}
        self._logger.debug(
            "Getting api %s Anker platform list",
            self.apisession.nickname,
        )
        # Ignore timeouts or other errors wrapped into a ClientError from queries, but return data only if all worked
        try:
            for platform in await self.get_product_platforms_list(fromFile=fromFile):
                plat_name = platform.get("name") or ""
                for prod in platform.get("products") or []:
                    products[prod.get("product_code") or ""] = {
                        "name": str(prod.get("name") or "").strip(),
                        "platform": str(plat_name).strip(),
                        # "img_url": prod.get("img_url"),
                    }
            self._logger.debug(
                "Getting api %s HES product list",
                self.apisession.nickname,
            )
            for platform in await self.get_hes_platforms_list(fromFile=fromFile):
                if (pn := platform.get("code") or "") and pn not in products:
                    products[pn] = {
                        "name": str(platform.get("name") or "").strip(),
                        "platform": str(platform.get("category") or "").strip(),
                        # "img_url": platform.get("imgUrl"),
                    }
            # get_third_platforms_list does no longer show 3rd platform products, skip query until data provided again
            # see https://github.com/thomluther/anker-solix-api/issues/172
            # self._logger.debug(
            #     "Getting api %s 3rd party platform list",
            #     self.apisession.nickname,
            # )
            # for platform in await self.get_third_platforms_list(fromFile=fromFile):
            #     plat_name = platform.get("name") or ""
            #     countries = platform.get("countries") or ""
            #     for prod in platform.get("products") or []:
            #         products[prod.get("product_code") or ""] = {
            #             "name": " ".join([plat_name, prod.get("name")]),
            #             "platform": plat_name,
            #             "countries": countries,
            #             # "img_url": prod.get("img_url"),
            #         }
        except ClientError as err:
            self._logger.error(
                "Api %s failed to get product list: %s",
                self.apisession.nickname,
                err,
            )
        return products
