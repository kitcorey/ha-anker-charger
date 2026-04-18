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

Install once:

```bash
uv sync --group dev
uv run pre-commit install
```

The pre-commit hook runs ruff (with autofix) plus the standard yaml/toml/json
and whitespace checks on every commit. Configuration lives in
`.pre-commit-config.yaml` and `.ruff.toml`.

To run the full check without staging anything:

```bash
uv run pre-commit run --all-files
```

Or just ruff on its own:

```bash
scripts/lint
```

## Tests

```bash
scripts/test           # quick: runs pytest with coverage
scripts/test -k foo    # pytest args pass through
```

## License

By contributing you agree that your contribution is released under the
[MIT License](./LICENSE) that covers the project.
