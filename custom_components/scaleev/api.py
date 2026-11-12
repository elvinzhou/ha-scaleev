"""Minimal client for the ScaleEV driver API (unofficial)."""
from __future__ import annotations

import asyncio
import urllib.parse
from dataclasses import dataclass
from typing import Any

import aiohttp

from .const import NETWORK_HOST_SUFFIX, DEFAULT_API_BASE

HEADERS = {"accept": "application/json", "ngrok-skip-browser-warning": "true"}


class ScaleEVError(Exception):
    """Any API failure."""


class ScaleEVAuthError(ScaleEVError):
    """Credentials rejected."""


@dataclass
class StartResult:
    """Outcome of a remote-start request."""

    session_id: str
    checkout_url: str
    payment_status: str


def parse_link(link: str) -> tuple[str, str | None]:
    """Return (tenant domain, guessed API base) from a link to the network's web app."""
    link = link.strip()
    host = urllib.parse.urlparse(link if "//" in link else f"//{link}").hostname or ""
    if not host:
        raise ValueError("no host")
    guess = DEFAULT_API_BASE if host.endswith(NETWORK_HOST_SUFFIX) else None
    return host.split(".")[0], guess


class ScaleEVClient:
    """Talks to the ScaleEV driver API, logging in lazily and on token expiry."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_base: str,
        tenant_slug: str = "",
        email: str = "",
        password: str = "",
    ) -> None:
        self._session = session
        self.api_base = api_base.rstrip("/")
        self.tenant_slug = tenant_slug
        self._email = email
        self._password = password
        self._token: str | None = None

    async def _send(self, method: str, path: str, *, auth: bool, json: Any = None) -> Any:
        headers = dict(HEADERS)
        if auth:
            headers["authorization"] = f"Bearer {self._token}"
        try:
            async with asyncio.timeout(20):
                async with self._session.request(
                    method, f"{self.api_base}{path}", json=json, headers=headers
                ) as resp:
                    try:
                        payload = await resp.json(content_type=None)
                    except ValueError:
                        payload = {}
                    return resp.status, payload
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ScaleEVError(f"Cannot reach ScaleEV: {err}") from err

    @staticmethod
    def _message(payload: Any) -> str:
        return str(payload.get("message", "")) if isinstance(payload, dict) else ""

    async def async_login(self) -> dict[str, Any]:
        """Log in, store the token and return the login data (token and driver)."""
        status, payload = await self._send(
            "POST",
            "/drivers/auth/login",
            auth=False,
            json={"email": self._email, "password": self._password, "tenantSlug": self.tenant_slug},
        )
        if status in (400, 401, 403, 404):
            raise ScaleEVAuthError(self._message(payload) or "Invalid credentials")
        data = payload.get("data") if isinstance(payload, dict) else None
        if status >= 300 or not isinstance(data, dict) or "access_token" not in data:
            raise ScaleEVError(self._message(payload) or f"Login failed (HTTP {status})")
        self._token = data["access_token"]
        return data

    async def _request(self, method: str, path: str, json: Any = None) -> Any:
        for attempt in (0, 1):
            if self._token is None:
                await self.async_login()
            status, payload = await self._send(method, path, auth=True, json=json)
            if status == 401 and attempt == 0:
                self._token = None
                continue
            if status == 401:
                raise ScaleEVAuthError(self._message(payload) or "Unauthorized")
            if status >= 300 or not isinstance(payload, dict) or payload.get("success") is not True:
                raise ScaleEVError(self._message(payload) or f"HTTP {status}")
            return payload.get("data")
        raise ScaleEVError("unreachable")  # pragma: no cover

    async def async_tenant_config(self, domain: str) -> dict[str, Any]:
        """Fetch the public network config (no login needed)."""
        status, payload = await self._send(
            "GET", f"/public/tenants/{urllib.parse.quote(domain)}/config", auth=False
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if status >= 300 or not isinstance(data, dict) or not data.get("slug"):
            raise ScaleEVError(self._message(payload) or "Unknown network")
        return data

    async def async_locations(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/drivers/locations") or []

    async def async_stations(self, location_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/drivers/locations/{location_id}/stations") or []

    async def async_station(self, station_id: str) -> dict[str, Any] | None:
        return await self._request("GET", f"/drivers/stations/{station_id}")

    async def async_live(self, session_id: str | None = None) -> dict[str, Any] | None:
        """Current session, or a specific one; None when nothing is active."""
        path = "/drivers/charging/live"
        if session_id:
            path += f"?sessionId={urllib.parse.quote(session_id)}"
        return await self._request("GET", path)

    async def async_remote_start(
        self, station_id: str, connector_id: int, target_kwh: float
    ) -> StartResult:
        data = await self._request(
            "POST",
            f"/drivers/charging/remote-start/{station_id}",
            json={"connectorId": int(connector_id), "priceFlowType": "kwh", "targetKwh": target_kwh},
        )
        if not isinstance(data, dict) or not data.get("sessionId"):
            raise ScaleEVError("ScaleEV did not return a session")
        return StartResult(
            session_id=data["sessionId"],
            checkout_url=data.get("checkoutUrl") or "",
            payment_status=data.get("paymentStatus") or "",
        )
