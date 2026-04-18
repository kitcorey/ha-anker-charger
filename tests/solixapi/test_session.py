"""Tests for ``AnkerSolixClientSession`` via mocked aiohttp responses.

``aresponses`` intercepts every outbound request; no real network traffic
happens. Auth-file caching is redirected to ``tmp_path`` so each test
starts from a clean slate.
"""

from __future__ import annotations

import json
from pathlib import Path

from aiohttp import ClientSession
import pytest

from custom_components.anker_charger.solixapi import errors
from custom_components.anker_charger.solixapi.session import (
    AnkerSolixClientSession,
)

# The production code builds the API base URL by looking up the country code
# in API_SERVERS; keep a single known host here so aresponses can register
# against the right authority.
HOST = "ankerpower-api.anker.com"


@pytest.fixture(autouse=True)
def _enable_sockets(socket_enabled):
    """Allow localhost sockets so aresponses can spin up its stub server.

    The pytest-homeassistant-custom-component harness registers a socket
    blocker by default; the ``socket_enabled`` fixture unlocks it.
    """
    yield


@pytest.fixture
async def session(tmp_path: Path):
    """Yield a ClientSession scoped to a test (closed automatically)."""
    async with ClientSession() as s:
        yield s


@pytest.fixture
def client(tmp_path: Path, session: ClientSession) -> AnkerSolixClientSession:
    """Build a fresh API session with auth cache pointed at tmp_path."""
    c = AnkerSolixClientSession(
        email="tester@example.com",
        password="pw",
        countryId="US",
        websession=session,
    )
    c._authFile = str(tmp_path / "auth.json")
    return c


def _login_response_payload() -> dict:
    """Return a plausible successful login envelope from the Anker cloud."""
    return {
        "code": 0,
        "msg": "success!",
        "data": {
            "user_id": "user-abc",
            "auth_token": "tok-123",
            "token_expires_at": 9999999999,
            "nick_name": "tester",
            "email": "tester@example.com",
        },
        "trace_id": "trace",
    }


class TestAuthenticate:
    async def test_fresh_login_hits_server_and_caches(
        self, aresponses, client: AnkerSolixClientSession
    ):
        aresponses.add(
            HOST,
            "/passport/login",
            "POST",
            aresponses.Response(
                status=200,
                text=json.dumps(_login_response_payload()),
                content_type="application/json",
            ),
        )

        assert await client.async_authenticate(restart=True) is True
        assert client._token == "tok-123"
        assert client.nickname == "tester"
        # Cache file should have been populated for subsequent calls.
        assert Path(client._authFile).is_file()

    async def test_cached_login_skips_network(
        self, aresponses, client: AnkerSolixClientSession
    ):
        # Prime the cache. Upstream's cache path populates token/nickname but
        # leaves ``_loggedIn`` False — tests assert on the extracted state,
        # not the boolean return.
        cached = _login_response_payload()["data"]
        Path(client._authFile).write_text(json.dumps(cached))

        # No aresponses endpoints registered → if the code hits the network
        # we'd get an error.
        await client.async_authenticate()
        assert client._token == "tok-123"
        assert client.nickname == "tester"

    async def test_bad_credentials_raises(
        self, aresponses, client: AnkerSolixClientSession
    ):
        aresponses.add(
            HOST,
            "/passport/login",
            "POST",
            aresponses.Response(
                status=200,
                text=json.dumps(
                    {"code": 401, "msg": "invalid credentials", "data": {}}
                ),
                content_type="application/json",
            ),
        )

        with pytest.raises(errors.AuthorizationError):
            await client.async_authenticate(restart=True)

    async def test_server_error_raises(
        self, aresponses, client: AnkerSolixClientSession
    ):
        aresponses.add(
            HOST,
            "/passport/login",
            "POST",
            aresponses.Response(
                status=500,
                text="boom",
                content_type="text/plain",
            ),
        )

        with pytest.raises(Exception):  # noqa: B017
            await client.async_authenticate(restart=True)


class TestRequest:
    async def _login(self, aresponses, client):
        aresponses.add(
            HOST,
            "/passport/login",
            "POST",
            aresponses.Response(
                status=200,
                text=json.dumps(_login_response_payload()),
                content_type="application/json",
            ),
        )
        await client.async_authenticate(restart=True)

    async def test_request_increments_counter(
        self, aresponses, client: AnkerSolixClientSession
    ):
        await self._login(aresponses, client)

        aresponses.add(
            HOST,
            "/power_service/v1/app/get_relate_and_bind_devices",
            "POST",
            aresponses.Response(
                status=200,
                text=json.dumps(
                    {"code": 0, "msg": "success!", "data": {"data": []}}
                ),
                content_type="application/json",
            ),
        )

        before = client.request_count.last_hour()
        resp = await client.request(
            "post",
            "power_service/v1/app/get_relate_and_bind_devices",
        )
        after = client.request_count.last_hour()

        assert resp["code"] == 0
        # The counter may increment by more than one (login call + retries
        # happen inside request); just assert it moves.
        assert after > before
