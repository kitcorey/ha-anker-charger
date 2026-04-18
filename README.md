# Anker Charger (A91B2) — Home Assistant integration

A focused fork of [`thomluther/ha-anker-solix`](https://github.com/thomluther/ha-anker-solix)
that supports **only** the Anker Prime 8-in-1 240W Charging Station (model
**A91B2**). If you own Solarbanks, HES systems, PPS power stations, EV
chargers, SmartMeters, or anything else in the Anker Solix family — please use
the upstream integration; none of that code is here.

## What it gives you

For each A91B2 charging station on your Anker account, this integration
creates:

| Type | Entity | Source |
|---|---|---|
| sensor | Firmware version (diagnostic) | cloud bind_devices |
| sensor | Wi-Fi signal % (diagnostic, RSSI attribute) | cloud bind_devices |
| sensor | MQTT last-message timestamp (diagnostic) | MQTT |
| sensor (×4) | USB-C port 1-4 power, with voltage/current/port_status attributes | MQTT |
| sensor (×2) | USB-A port 1-2 power, with voltage/current/port_status attributes | MQTT |
| switch (×2) | AC outlet 1, AC outlet 2 | MQTT |

Plus a cloud account device exposing:

| Type | Entity |
|---|---|
| switch | API usage (toggles cloud polling + MQTT session) |
| sensor | MQTT statistics (bytes/h, message counts by type) |

## Requirements

- Home Assistant 2025.3 or newer
- An Anker account that the charging stations are bound to — the integration
  uses the Anker cloud to obtain AWS IoT certs for a real-time MQTT session.

> **Tip:** the Anker app logs a given account off whenever another client logs
> in. Create a second Anker account, share the charger with it, and use the
> second account in this integration so your primary app session stays alive.

## Installation

### HACS (recommended)

1. Add `https://github.com/kitcorey/ha-anker-solix` as a custom repository in
   HACS, category **Integration**.
2. Search HACS for "Anker Charger (A91B2)" and install.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → "Anker Charger (A91B2)"**.

### Manual

Copy `custom_components/anker_charger/` into your HA config's
`custom_components/` folder, restart HA, then add the integration from the UI.

## Configuration

The initial setup asks for your Anker account email, password, country, and a
terms-of-use checkbox. The options step then exposes:

- **Update interval** — seconds between cloud `bind_devices` refreshes.
  Defaults to 60. The cloud only refreshes every 60-300 seconds anyway, so
  anything shorter only matters combined with MQTT streaming.
- **MQTT usage** — on by default; turning it off removes all real-time port
  and outlet entities.
- **Real-time data trigger timeout** — seconds the charger streams `0303`
  realtime frames before the integration re-sends the trigger.

## How it works

On setup the integration logs in with your credentials, fetches the list of
devices bound to the account (`get_relate_and_bind_devices`), and opens an
MQTT session to Anker's AWS IoT broker (`aiot-mqtt-us.anker.com` /
`aiot-mqtt-eu.anker.com` depending on country) using the cert/key material
the cloud returns for your account. The session subscribes to
`dt/anker_power/A91B2/<sn>/#` for each charger and streams:

- `0a00` — full device status (port on/off, firmware, USB port measurements,
  AC outlet state)
- `0303` — realtime USB port consumption (voltage/current/power, ~1/s while a
  trigger is active)
- `0207` — AC outlet switch commands (published by the integration when the
  user toggles a switch in HA)

## Limitations

- Individual USB-C/USB-A port switches and the display / port-memory toggles
  are **not** exposed. The A91B2 firmware publishes port measurements but not
  port-switch states in `0a00`, so there's nothing to drive an HA switch
  entity. Contributions that extend `solixapi/mqttmap.py` with the right
  bytes would unlock these.
- No local-only mode — the charger has no LAN control surface today. Cloud
  auth and the AWS MQTT broker are on the critical path.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the Docker Compose dev workflow
and lint setup.

## License

MIT — see [LICENSE](./LICENSE). Based on the upstream MIT-licensed project.
