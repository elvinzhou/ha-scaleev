"""Starts a charging session and keeps the driver informed."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .api import ScaleEVAuthError, ScaleEVClient, ScaleEVError, StartResult
from .const import (
    CONF_CONNECTOR_ID,
    CONF_NOTIFY_SERVICE,
    CONF_PAY_TIMEOUT,
    CONF_STATION_ID,
    CONF_STATION_NAME,
    CONF_TARGET_KWH,
    DEFAULT_PAY_TIMEOUT,
    NO_PAYMENT_WAIT,
    NOTIFICATION_TAG,
    PAID_STATUSES,
    POLL_SECONDS,
)
from .coordinator import ScaleEVCoordinator

_LOGGER = logging.getLogger(__name__)


async def _pause(seconds: float) -> None:
    """Sleep between polls (patched out in tests)."""
    await asyncio.sleep(seconds)


class ChargeManager:
    """Runs one start-charging sequence at a time."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: ScaleEVClient, coordinator: ScaleEVCoordinator
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.coordinator = coordinator
        self.auto_start = True
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @callback
    def async_trigger(self, manual: bool) -> bool:
        """Start the sequence in the background; False if one is already running."""
        if self.running:
            return False
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(manual), name=f"scaleev start {self.entry.entry_id}"
        )
        return True

    async def _run(self, manual: bool) -> None:
        try:
            await self._start(manual)
        except ScaleEVAuthError:
            await self._notify("ScaleEV: login failed", "Your ScaleEV credentials were rejected. Re-enter your password in Home Assistant.")
            self.entry.async_start_reauth(self.hass)
        except ScaleEVError as err:
            await self._notify("ScaleEV: could not start charging", f"{err} (station {self._name}).")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the background task die silently
            _LOGGER.exception("Unexpected error starting ScaleEV session")
            await self._notify("ScaleEV: could not start charging", "Unexpected error, see the Home Assistant log.")

    @property
    def _name(self) -> str:
        return self.entry.data.get(CONF_STATION_NAME) or self.entry.data[CONF_STATION_ID]

    async def _start(self, manual: bool) -> None:
        opts = self.entry.options
        target = opts[CONF_TARGET_KWH]
        if await self.client.async_live():
            _LOGGER.debug("ScaleEV session already active, not starting another")
            if manual:
                await self._notify("ScaleEV: already charging", f"A session is already in progress at {self._name}.")
            return

        result = await self.client.async_remote_start(
            self.entry.data[CONF_STATION_ID], self.entry.data[CONF_CONNECTOR_ID], target
        )
        needs_payment = bool(result.checkout_url) and result.payment_status == "pending"
        if needs_payment:
            await self._notify_payment(result, target)
            wait_seconds = float(opts.get(CONF_PAY_TIMEOUT, DEFAULT_PAY_TIMEOUT)) * 60
        else:
            wait_seconds = NO_PAYMENT_WAIT

        live = await self._wait_authorized(result.session_id, wait_seconds)
        if needs_payment:
            await self._notify("", "clear_notification", clear=True)
        await self.coordinator.async_request_refresh()
        if live:
            await self._notify("ScaleEV: charging started", f"{self._name} is {live.get('status', 'started')} ({target} kWh target).")
        elif needs_payment:
            await self._notify("ScaleEV: charging did not start", f"Payment was not completed within {int(wait_seconds // 60)} minutes.")
        else:
            await self._notify("ScaleEV: charging did not start", "The session could not be confirmed.")

    async def _wait_authorized(self, session_id: str, seconds: float) -> dict[str, Any] | None:
        polls = max(1, int(seconds / POLL_SECONDS))
        for _ in range(polls):
            await _pause(POLL_SECONDS)
            try:
                live = await self.client.async_live(session_id)
            except ScaleEVAuthError:
                raise
            except ScaleEVError as err:
                _LOGGER.debug("Transient error while waiting for authorization: %s", err)
                continue
            if live and live.get("paymentStatus") in PAID_STATUSES:
                return live
        return None

    async def _notify_payment(self, result: StartResult, target: float) -> None:
        await self._notify(
            "ScaleEV: payment needed",
            f"Tap to authorize payment and start charging ({target} kWh).",
            data={
                "tag": NOTIFICATION_TAG,
                "clickAction": result.checkout_url,
                "url": result.checkout_url,
                "actions": [{"action": "URI", "title": "Pay now", "uri": result.checkout_url}],
            },
        )

    async def _notify(self, title: str, message: str, data: dict | None = None, clear: bool = False) -> None:
        service = (self.entry.options.get(CONF_NOTIFY_SERVICE) or "").removeprefix("notify.")
        payload: dict[str, Any] = {"message": message}
        if clear:
            payload["data"] = {"tag": NOTIFICATION_TAG}
        else:
            payload["title"] = title
            if data:
                payload["data"] = data
        if service and self.hass.services.has_service("notify", service):
            try:
                await self.hass.services.async_call("notify", service, payload, blocking=True)
                return
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Notify service %s failed", service)
        if not clear:
            persistent_notification.async_create(self.hass, message, title=title)
