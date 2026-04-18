# Contributing

This is a personal fork of [`thomluther/ha-anker-solix`](https://github.com/thomluther/ha-anker-solix)
trimmed down to support only the Anker Prime 8-in-1 240W Charging Station
(model **A91B2**). PRs targeting the broader Solix device families should go
upstream; PRs that improve A91B2 coverage (additional MQTT messages, missing
port controls, translations) are welcome here.

## Development environment

The quickest way to iterate is the included Docker Compose stack, which boots
an isolated Home Assistant instance with the integration bind-mounted.

Prerequisites:

- Docker and Docker Compose v2+

Bring the stack up:

```bash
cp .env.example .env     # first time only
docker compose up -d
```

Then open <http://localhost:8123>, create an owner account, and add the
integration via **Settings → Devices & Services → Add Integration → "Anker
Charger (A91B2)"**.

Edit files under `custom_components/anker_charger/`, then either restart HA
or press the integration's **Reload** button to pick up changes:

```bash
docker compose restart homeassistant
docker compose logs -f homeassistant
```

The `config/` directory stores HA runtime state (database, auth tokens) and
is gitignored apart from `configuration.yaml`. To start fresh, stop the
container and wipe `config/` except `configuration.yaml`.

Pin a specific HA release by setting `HA_VERSION=2026.4.3` in `.env`, then
`docker compose up -d`.

## Lint

```bash
docker run --rm -v "$PWD":/work -w /work ghcr.io/astral-sh/ruff:latest \
  check custom_components/
```

The config lives in `.ruff.toml` at the repo root.

## License

By contributing you agree that your contribution is released under the
[MIT License](./LICENSE) that covers the project.
